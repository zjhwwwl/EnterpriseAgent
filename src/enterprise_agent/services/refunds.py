"""Deterministic refund rules and confirmed, audited transactional mutations.

The Service is the authority for refund creation. Approval payloads are generated
here, never accepted from a model, and checked again under the order row lock.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    IdempotencyRecord,
    Order,
    OrderStatus,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    UserRole,
)
from enterprise_agent.repositories.orders import OrderRepository
from enterprise_agent.repositories.refunds import RefundRepository
from enterprise_agent.request_context import get_request_id
from enterprise_agent.schemas.business import (
    ApprovalResponse,
    ConfirmResponse,
    EligibilityResponse,
    PolicyResponse,
    RefundPage,
    RefundPrepareInput,
    RefundResponse,
    RefundReviewInput,
)
from enterprise_agent.security import Principal
from enterprise_agent.services.orders import require_order_reader, validate_pagination

OPERATION = "create_refund_request"


@dataclass(frozen=True)
class RefundDecision:
    eligible: bool
    code: str
    message: str
    amount_cents: int = 0


def evaluate_refund_rule(
    *,
    status: OrderStatus,
    paid_at: datetime | None,
    delivered_at: datetime | None,
    goods_paid_cents: int,
    shipping_paid_cents: int,
    categories: Iterable[str],
    reason: str,
    now: datetime,
    refund_window_days: int,
    refund_shipping: bool,
    allow_before_delivery: bool,
    excluded_categories: Iterable[str],
) -> RefundDecision:
    """Calculate eligibility only; a successful result is not staff approval."""
    for value in (now, paid_at, delivered_at):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Refund timestamps must be timezone-aware")
    if reason not in ("change_mind", "quality_issue", "not_received"):
        return RefundDecision(False, "invalid_reason", "退款原因不合法。")
    if status in (OrderStatus.UNPAID, OrderStatus.CANCELLED) or paid_at is None:
        return RefundDecision(False, "order_not_refundable", "未支付或已取消的订单不能申请退款。")
    if paid_at > now or (
        delivered_at is not None and (delivered_at > now or delivered_at < paid_at)
    ):
        return RefundDecision(False, "invalid_order_facts", "订单时间记录不一致，请联系售后。")
    if status == OrderStatus.DELIVERED and delivered_at is None:
        return RefundDecision(False, "invalid_order_facts", "缺少签收时间，无法判断退款期限。")
    if set(categories).intersection(excluded_categories) and reason != "quality_issue":
        return RefundDecision(
            False, "category_restricted", "此订单含限制退货类目，仅支持质量问题申请。"
        )
    if delivered_at is None:
        if not allow_before_delivery:
            return RefundDecision(False, "delivery_required", "适用政策不支持签收前申请。")
    elif now > delivered_at + timedelta(days=refund_window_days):
        return RefundDecision(False, "refund_window_expired", "订单已超过适用政策的售后申请期限。")
    if reason == "not_received" and delivered_at is not None:
        return RefundDecision(
            False, "already_delivered", "物流记录已签收，请使用相符原因申请售后。"
        )
    amount = goods_paid_cents + (shipping_paid_cents if refund_shipping else 0)
    if amount <= 0:
        return RefundDecision(False, "no_refundable_amount", "订单没有可申请退款的金额。")
    return RefundDecision(True, "eligible", "可提交待审核退款申请，实际处理需客服审核。", amount)


def canonical_digest(value: dict[str, Any]) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _customer(actor: Principal) -> None:
    if actor.role != UserRole.CUSTOMER:
        raise BusinessError(
            code="forbidden", message="退款申请须由订单所属客户确认。", status_code=403
        )


def _staff(actor: Principal) -> None:
    if actor.role not in (UserRole.SUPPORT, UserRole.ADMIN):
        raise BusinessError(code="forbidden", message="当前角色无权审核退款。", status_code=403)


class RefundService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.sessions = sessions
        self.clock = clock

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Service clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _audit(
        session: AsyncSession,
        actor: Principal,
        action: str,
        resource_type: str,
        resource_id: UUID | int,
        details: dict[str, Any],
        now: datetime,
    ) -> None:
        session.add(
            AuditLog(
                actor_id=actor.user_id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                request_id=get_request_id(),
                details=details,
                created_at=now,
            )
        )

    @staticmethod
    async def _visible_order(session: AsyncSession, actor: Principal, order_no: str, *, lock=False):
        order = await OrderRepository(session).get_visible(actor, order_no, lock=lock)
        if order is None:
            raise BusinessError(code="order_not_found", message="订单不存在。", status_code=404)
        return order

    async def _assess(
        self,
        session: AsyncSession,
        order: Order,
        reason: str,
        notes: str,
        now: datetime,
    ) -> tuple[EligibilityResponse, dict[str, Any] | None]:
        repositories = RefundRepository(session)
        if await repositories.active_for_order(order.id) is not None:
            return EligibilityResponse(
                order_no=order.order_no,
                eligible=False,
                code="refund_already_exists",
                message="此订单已有处理中或已完成的退款申请。",
                amount_cents=0,
                policy=None,
            ), None
        if order.paid_at is None or order.status in (OrderStatus.UNPAID, OrderStatus.CANCELLED):
            return EligibilityResponse(
                order_no=order.order_no,
                eligible=False,
                code="order_not_refundable",
                message="未支付或已取消的订单不能申请退款。",
                amount_cents=0,
                policy=None,
            ), None
        # Published policy rows are immutable and the application account is
        # SELECT-only here. Row locking would require unnecessary UPDATE grants.
        policies = await repositories.policies_at(order.paid_at)
        if len(policies) != 1:
            return EligibilityResponse(
                order_no=order.order_no,
                eligible=False,
                code="policy_unavailable",
                message="缺少唯一适用政策，不能判断退款条件，请联系售后。",
                amount_cents=0,
                policy=None,
            ), None
        policy = policies[0]
        document = await repositories.document(policy.document_id)
        if document is None:
            raise BusinessError(
                code="policy_source_unavailable", message="适用政策来源不可用。", status_code=503
            )
        source = PolicyResponse(
            id=policy.id,
            policy_code=policy.policy_code,
            version=policy.version,
            document_id=document.id,
            document_title=document.title,
            document_version=document.version,
            valid_from=policy.valid_from,
            valid_until=policy.valid_until,
            refund_window_days=policy.refund_window_days,
            refund_shipping=policy.refund_shipping,
            allow_before_delivery=policy.allow_before_delivery,
            excluded_categories=policy.excluded_categories,
        )
        items = await OrderRepository(session).items(order.id)
        if not items or sum(item.paid_amount_cents for item in items) != order.goods_paid_cents:
            raise BusinessError(
                code="invalid_order_facts",
                message="订单与明细金额不一致，请联系售后。",
                status_code=409,
            )
        decision = evaluate_refund_rule(
            status=order.status,
            paid_at=order.paid_at,
            delivered_at=order.delivered_at,
            goods_paid_cents=order.goods_paid_cents,
            shipping_paid_cents=order.shipping_paid_cents,
            categories=[item.category for item in items],
            reason=reason,
            now=now,
            refund_window_days=policy.refund_window_days,
            refund_shipping=policy.refund_shipping,
            allow_before_delivery=policy.allow_before_delivery,
            excluded_categories=policy.excluded_categories,
        )
        result = EligibilityResponse(
            order_no=order.order_no,
            eligible=decision.eligible,
            code=decision.code,
            message=decision.message,
            amount_cents=decision.amount_cents,
            policy=source,
        )
        # Stable facts bind the confirmation. The current time is intentionally
        # excluded; eligibility is recalculated separately at confirmation time.
        payload = {
            "operation": OPERATION,
            "order_no": order.order_no,
            "reason": reason,
            "notes": notes,
            "amount_cents": decision.amount_cents,
            "policy_version_id": policy.id,
            "policy": source.model_dump(mode="json"),
            "order_facts": {
                "order_id": order.id,
                "user_id": order.user_id,
                "status": order.status.value,
                "paid_at": _timestamp(order.paid_at),
                "delivered_at": _timestamp(order.delivered_at),
                "goods_paid_cents": order.goods_paid_cents,
                "shipping_paid_cents": order.shipping_paid_cents,
                "paid_total_cents": order.paid_total_cents,
                "items": [
                    {
                        "id": item.id,
                        "product_id": item.product_id,
                        "category": item.category,
                        "quantity": item.quantity,
                        "paid_amount_cents": item.paid_amount_cents,
                    }
                    for item in items
                ],
            },
        }
        return result, payload

    async def get_eligibility(self, actor: Principal, order_no: str, *, reason="change_mind"):
        require_order_reader(actor)
        validated = RefundPrepareInput(reason=reason)
        async with self.sessions() as session, session.begin():
            order = await self._visible_order(session, actor, order_no)
            result, _ = await self._assess(session, order, validated.reason, "", self._now())
            return result

    async def prepare_refund(self, actor: Principal, order_no: str, *, reason: str, notes=""):
        _customer(actor)
        validated = RefundPrepareInput(reason=reason, notes=notes)
        async with self.sessions() as session, session.begin():
            order = await self._visible_order(session, actor, order_no, lock=True)
            now = self._now()
            result, payload = await self._assess(
                session, order, validated.reason, validated.notes, now
            )
            if not result.eligible or payload is None or result.policy is None:
                raise BusinessError(code=result.code, message=result.message, status_code=409)
            approval = ApprovalRequest(
                id=uuid4(),
                user_id=actor.user_id,
                order_id=order.id,
                operation=OPERATION,
                action_payload=payload,
                action_digest=canonical_digest(payload),
                expires_at=now + timedelta(minutes=30),
                status=ApprovalStatus.PENDING,
                created_at=now,
            )
            session.add(approval)
            self._audit(
                session,
                actor,
                "refund.prepare",
                "approval",
                approval.id,
                {"order_id": order.id, "amount_cents": result.amount_cents},
                now,
            )
            await session.flush()
            return ApprovalResponse(
                approval_id=approval.id,
                order_no=order.order_no,
                status=approval.status,
                reason=validated.reason,
                notes=validated.notes,
                amount_cents=result.amount_cents,
                expires_at=approval.expires_at,
                policy=result.policy,
            )

    async def confirm_refund(
        self, actor: Principal, approval_id: UUID, *, idempotency_key: str, confirmed: bool
    ):
        _customer(actor)
        if not isinstance(confirmed, bool):
            raise BusinessError(
                code="invalid_confirmation", message="确认参数须为布尔值。", status_code=422
            )
        if not isinstance(approval_id, UUID):
            raise BusinessError(
                code="invalid_approval_id", message="确认标识不合法。", status_code=422
            )
        if not isinstance(idempotency_key, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key
        ):
            raise BusinessError(
                code="invalid_idempotency_key",
                message="幂等键须为 8–128 位安全字符。",
                status_code=422,
            )
        request_digest = canonical_digest({"approval_id": str(approval_id), "confirmed": confirmed})
        expired_error = None
        result = None
        async with self.sessions() as session, session.begin():
            repository = RefundRepository(session)
            await repository.lock_idempotency_key(actor.user_id, OPERATION, idempotency_key)
            previous = await repository.idempotency(actor.user_id, OPERATION, idempotency_key)
            if previous is not None:
                if not hmac.compare_digest(previous.request_digest, request_digest):
                    raise BusinessError(
                        code="idempotency_conflict",
                        message="同一幂等键不能用于不同请求。",
                        status_code=409,
                    )
                return ConfirmResponse.model_validate(previous.result).model_copy(
                    update={"replayed": True}
                )
            approval = await repository.approval(actor, approval_id)
            if approval is None:
                raise BusinessError(
                    code="approval_not_found", message="待确认操作不存在。", status_code=404
                )
            if approval.status != ApprovalStatus.PENDING:
                raise BusinessError(
                    code="approval_unavailable",
                    message="待确认操作已处理，请重新发起。",
                    status_code=409,
                )
            now = self._now()
            order = None
            if approval.expires_at > now:
                if approval.operation != OPERATION or not hmac.compare_digest(
                    approval.action_digest, canonical_digest(approval.action_payload)
                ):
                    raise BusinessError(
                        code="approval_changed",
                        message="待确认操作内容已变化，请重新发起。",
                        status_code=409,
                    )
                if confirmed:
                    payload = approval.action_payload
                    order = await self._visible_order(
                        session, actor, payload["order_no"], lock=True
                    )
                    if order.id != approval.order_id:
                        raise BusinessError(
                            code="approval_changed", message="订单确认内容不匹配。", status_code=409
                        )
                    # Lock waits may cross the approval or refund deadline.
                    # Assess and timestamp the mutation using time after the wait.
                    now = self._now()
            if approval.expires_at <= now:
                approval.status = ApprovalStatus.EXPIRED
                self._audit(
                    session, actor, "refund.confirm_expired", "approval", approval.id, {}, now
                )
                expired_error = BusinessError(
                    code="approval_expired",
                    message="待确认操作已过期，请重新发起。",
                    status_code=409,
                )
            else:
                if not confirmed:
                    approval.status = ApprovalStatus.REJECTED
                    approval.consumed_at = now
                    self._audit(
                        session, actor, "refund.confirm_rejected", "approval", approval.id, {}, now
                    )
                    result = ConfirmResponse(
                        approval_id=approval.id, status=approval.status, refund=None
                    )
                else:
                    payload = approval.action_payload
                    assert order is not None
                    inputs = RefundPrepareInput(reason=payload["reason"], notes=payload["notes"])
                    eligibility, current_payload = await self._assess(
                        session, order, inputs.reason, inputs.notes, now
                    )
                    if not eligibility.eligible:
                        raise BusinessError(
                            code=eligibility.code, message=eligibility.message, status_code=409
                        )
                    if current_payload is None or not hmac.compare_digest(
                        approval.action_digest, canonical_digest(current_payload)
                    ):
                        raise BusinessError(
                            code="approval_changed",
                            message="订单或政策已变化，请重新确认。",
                            status_code=409,
                        )
                    refund = RefundRequest(
                        id=uuid4(),
                        order_id=order.id,
                        applicant_id=actor.user_id,
                        amount_cents=eligibility.amount_cents,
                        reason=inputs.reason,
                        notes=inputs.notes,
                        status=RefundStatus.PENDING_REVIEW,
                        policy_version_id=payload["policy_version_id"],
                        policy_snapshot={
                            "policy": payload["policy"],
                            "facts": payload["order_facts"],
                            "eligibility_checked_at": _timestamp(now),
                            "decision": "eligible_pending_staff_review",
                        },
                        created_at=now,
                    )
                    session.add(refund)
                    await session.flush()
                    session.add(
                        SupportTicket(
                            id=uuid4(),
                            order_id=order.id,
                            refund_request_id=refund.id,
                            created_by=actor.user_id,
                            status="open",
                            created_at=now,
                        )
                    )
                    approval.status = ApprovalStatus.CONSUMED
                    approval.consumed_at = now
                    self._audit(
                        session,
                        actor,
                        "refund.create",
                        "refund",
                        refund.id,
                        {
                            "order_id": order.id,
                            "approval_id": str(approval.id),
                            "amount_cents": refund.amount_cents,
                            "status": refund.status.value,
                        },
                        now,
                    )
                    result = ConfirmResponse(
                        approval_id=approval.id,
                        status=approval.status,
                        refund=self._refund_response(refund, order.order_no),
                    )
                session.add(
                    IdempotencyRecord(
                        id=uuid4(),
                        actor_id=actor.user_id,
                        operation=OPERATION,
                        key=idempotency_key,
                        request_digest=request_digest,
                        refund_request_id=result.refund.id if result.refund else None,
                        result=result.model_dump(mode="json"),
                        created_at=now,
                    )
                )
            await session.flush()
        if expired_error is not None:
            raise expired_error
        assert result is not None
        return result

    @staticmethod
    def _refund_response(refund: RefundRequest, order_no: str) -> RefundResponse:
        return RefundResponse(
            id=refund.id,
            order_no=order_no,
            applicant_id=refund.applicant_id,
            amount_cents=refund.amount_cents,
            reason=refund.reason,
            notes=refund.notes,
            status=refund.status,
            policy_version_id=refund.policy_version_id,
            policy_snapshot=refund.policy_snapshot,
            created_at=refund.created_at,
            reviewer_id=refund.reviewer_id,
            reviewed_at=refund.reviewed_at,
            review_note=refund.review_note,
            completed_at=refund.completed_at,
        )

    async def get_refund(self, actor: Principal, refund_id: UUID):
        require_order_reader(actor)
        async with self.sessions() as session, session.begin():
            refund = await RefundRepository(session).get_visible(actor, refund_id)
            if refund is None:
                raise BusinessError(
                    code="refund_not_found", message="退款申请不存在。", status_code=404
                )
            order = await OrderRepository(session).get_by_id(refund.order_id)
            return self._refund_response(refund, order.order_no)

    async def list_refunds(self, actor: Principal, *, offset=0, limit=20):
        require_order_reader(actor)
        validate_pagination(offset, limit)
        async with self.sessions() as session, session.begin():
            refunds, total = await RefundRepository(session).list_visible(
                actor, offset=offset, limit=limit
            )
            items = []
            orders = OrderRepository(session)
            for refund in refunds:
                order = await orders.get_by_id(refund.order_id)
                items.append(self._refund_response(refund, order.order_no))
            return RefundPage(items=items, total=total, offset=offset, limit=limit)

    async def review_refund(self, actor: Principal, refund_id: UUID, *, decision: str, note: str):
        _staff(actor)
        validated = RefundReviewInput(decision=decision, note=note)
        return await self._staff_transition(actor, refund_id, validated.decision, validated.note)

    async def complete_simulated_refund(self, actor: Principal, refund_id: UUID):
        _staff(actor)
        return await self._staff_transition(actor, refund_id, "complete_simulated", "")

    async def _staff_transition(self, actor: Principal, refund_id: UUID, action: str, note: str):
        now = self._now()
        async with self.sessions() as session, session.begin():
            repository = RefundRepository(session)
            visible = await repository.get_visible(actor, refund_id)
            if visible is None:
                raise BusinessError(
                    code="refund_not_found", message="退款申请不存在。", status_code=404
                )
            # Every mutation locks order first, then refund, then its ticket.
            order = await OrderRepository(session).get_by_id(visible.order_id, lock=True)
            refund = await repository.get_locked(refund_id)
            ticket = await repository.ticket(refund_id)
            if ticket is None:
                raise BusinessError(
                    code="ticket_not_found", message="退款缺少关联工单。", status_code=409
                )
            previous_status = refund.status
            if action == "complete_simulated":
                if refund.status != RefundStatus.APPROVED:
                    raise BusinessError(
                        code="invalid_refund_transition",
                        message="仅已批准的退款可模拟完成。",
                        status_code=409,
                    )
                refund.status = RefundStatus.COMPLETED_SIMULATED
                refund.completed_at = now
                ticket.status = "closed"
            else:
                if refund.status != RefundStatus.PENDING_REVIEW:
                    raise BusinessError(
                        code="invalid_refund_transition",
                        message="仅待审核的退款可审核。",
                        status_code=409,
                    )
                refund.status = (
                    RefundStatus.APPROVED if action == "approve" else RefundStatus.REJECTED
                )
                refund.reviewer_id = actor.user_id
                refund.reviewed_at = now
                refund.review_note = note
                ticket.status = "approved" if action == "approve" else "closed"
            ticket.assigned_to = actor.user_id
            self._audit(
                session,
                actor,
                f"refund.{action}",
                "refund",
                refund.id,
                {
                    "from": previous_status.value,
                    "to": refund.status.value,
                    "simulated": action == "complete_simulated",
                },
                now,
            )
            await session.flush()
            return self._refund_response(refund, order.order_no)
