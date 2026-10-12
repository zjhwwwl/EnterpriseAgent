"""Refund queries; callers hold the order lock before checking active refunds."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models import (
    ApprovalRequest,
    IdempotencyRecord,
    KnowledgeDocument,
    RefundPolicyVersion,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    UserRole,
)
from enterprise_agent.security import Principal

ACTIVE_REFUND_STATUSES = (
    RefundStatus.PENDING_REVIEW,
    RefundStatus.APPROVED,
    RefundStatus.COMPLETED_SIMULATED,
)


class RefundRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def visible_statement(actor: Principal):
        statement = select(RefundRequest)
        if actor.role == UserRole.CUSTOMER:
            statement = statement.where(RefundRequest.applicant_id == actor.user_id)
        return statement

    async def get_visible(self, actor: Principal, refund_id: UUID):
        return await self.session.scalar(
            self.visible_statement(actor).where(RefundRequest.id == refund_id)
        )

    async def get_locked(self, refund_id: UUID):
        return await self.session.scalar(
            select(RefundRequest)
            .where(RefundRequest.id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def list_visible(self, actor: Principal, *, offset: int, limit: int):
        statement = self.visible_statement(actor)
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        result = await self.session.scalars(
            statement.order_by(RefundRequest.created_at.desc(), RefundRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result), total or 0

    async def active_for_order(self, order_id: int) -> RefundRequest | None:
        return await self.session.scalar(
            select(RefundRequest).where(
                RefundRequest.order_id == order_id,
                RefundRequest.status.in_(ACTIVE_REFUND_STATUSES),
            )
        )

    async def policies_at(self, paid_at: datetime):
        statement = select(RefundPolicyVersion).where(
            RefundPolicyVersion.policy_code == "STANDARD_RETURN",
            RefundPolicyVersion.valid_from <= paid_at,
        )
        # A newly published version supersedes older versions from valid_from.
        # Pick before checking expiry, otherwise an expired newest version could
        # incorrectly cause a fallback to an older, open-ended policy.
        policies = list(
            await self.session.scalars(
                statement.order_by(RefundPolicyVersion.valid_from.desc()).limit(1)
            )
        )
        if policies and policies[0].valid_until is not None and paid_at >= policies[0].valid_until:
            return []
        return policies

    async def document(self, document_id: UUID):
        return await self.session.get(KnowledgeDocument, document_id)

    async def approval(self, actor: Principal, approval_id: UUID):
        return await self.session.scalar(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == actor.user_id)
            .with_for_update()
        )

    async def lock_idempotency_key(self, actor_id: int, operation: str, key: str):
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"{actor_id}:{operation}:{key}"},
        )

    async def idempotency(self, actor_id: int, operation: str, key: str):
        return await self.session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.key == key,
            )
        )

    async def ticket(self, refund_id: UUID) -> SupportTicket | None:
        return await self.session.scalar(
            select(SupportTicket)
            .where(SupportTicket.refund_request_id == refund_id)
            .with_for_update()
        )
