"""Service invariants against migrated, explicitly configured PostgreSQL.

These tests do not substitute SQLite for row locks or use create_all. The shared
fixtures seed and reset only the dedicated enterprise_agent_test database.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    IdempotencyRecord,
    Order,
    OrderItem,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    User,
)
from enterprise_agent.repositories.orders import OrderRepository
from enterprise_agent.schemas.business import PolicyResponse
from enterprise_agent.security import Principal
from enterprise_agent.services.orders import OrderService
from enterprise_agent.services.refunds import RefundService

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def actors(pg_factory, pg_seed):
    async with pg_factory() as session:
        users = list(await session.scalars(select(User)))
        return {user.username: Principal(user_id=user.id, role=user.role) for user in users}


@pytest.fixture
def refunds(pg_factory):
    return RefundService(pg_factory, clock=lambda: NOW)


async def expect_error(awaitable, code, status=409):
    with pytest.raises(BusinessError) as raised:
        await awaitable
    assert raised.value.code == code
    assert raised.value.status_code == status


async def create_request(refunds, actor, *, key="create-first-0001"):
    approval = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    confirmed = await refunds.confirm_refund(
        actor, approval.approval_id, idempotency_key=key, confirmed=True
    )
    return approval, confirmed.refund


async def count_rows(factory, entity, *criteria):
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(entity).where(*criteria))


async def test_customer_cannot_enumerate_another_customers_order(pg_factory, actors):
    orders = OrderService(pg_factory)
    actor = actors["demo_customer"]
    await expect_error(orders.get_order(actor, "10087"), "order_not_found", 404)
    await expect_error(orders.get_order(actor, "does-not-exist"), "order_not_found", 404)
    await expect_error(orders.get_order_items(actor, "10087"), "order_not_found", 404)
    await expect_error(orders.get_logistics(actor, "10087"), "order_not_found", 404)
    page = await orders.list_orders(actor, limit=100)
    assert page.items and all(order.user_id == actor.user_id for order in page.items)
    staff_order = await orders.get_order(actors["demo_support"], "10087")
    assert staff_order.order_no == "10087"
    await expect_error(
        orders.get_order(actor=actors["demo_analyst"], order_no="10086"), "forbidden", 403
    )


async def test_order_reads_use_the_business_database(pg_factory, actors):
    orders = OrderService(pg_factory)
    actor = actors["demo_customer"]
    order = await orders.get_order(actor, "10086")
    items = await orders.get_order_items(actor, "10086")
    logistics = await orders.get_logistics(actor, "10086")
    assert sum(item.paid_amount_cents for item in items) == order.goods_paid_cents == 12900
    assert order.paid_total_cents == 13700
    assert logistics.order_no == "10086" and logistics.events


async def test_eligibility_and_policy_source_are_real_records(refunds, actors):
    result = await refunds.get_eligibility(actors["demo_customer"], "10086")
    assert result.eligible and result.amount_cents == 12900
    assert result.policy.policy_code == "STANDARD_RETURN"
    assert result.policy.document_id and result.policy.document_title
    await expect_error(
        refunds.prepare_refund(actors["demo_customer"], "10088", reason="change_mind"),
        "refund_window_expired",
    )
    await expect_error(
        refunds.prepare_refund(actors["demo_customer"], "10089", reason="change_mind"),
        "order_not_refundable",
    )
    await expect_error(
        refunds.prepare_refund(actors["demo_customer"], "10090", reason="quality_issue"),
        "refund_already_exists",
    )


async def test_policy_uses_purchase_time_even_when_a_new_version_is_published(
    refunds, actors, pg_admin_factory
):
    from enterprise_agent.models import RefundPolicyVersion

    async with pg_admin_factory() as session, session.begin():
        policy = await session.scalar(
            select(RefundPolicyVersion).where(RefundPolicyVersion.policy_code == "STANDARD_RETURN")
        )
        session.add(
            RefundPolicyVersion(
                policy_code="STANDARD_RETURN",
                version=policy.version + 1,
                document_id=policy.document_id,
                valid_from=NOW,
                refund_window_days=1,
                refund_shipping=True,
                allow_before_delivery=False,
                excluded_categories=["food", "clothing"],
            )
        )
    result = await refunds.get_eligibility(actors["demo_customer"], "10086")
    assert result.eligible and result.policy.version == 1 and result.amount_cents == 12900


async def test_food_exception_uses_item_category_snapshot(refunds, actors, pg_admin_factory):
    async with pg_admin_factory() as session, session.begin():
        order_id = await session.scalar(select(Order.id).where(Order.order_no == "10086"))
        await session.execute(
            update(OrderItem).where(OrderItem.order_id == order_id).values(category="food")
        )
    result = await refunds.get_eligibility(actors["demo_customer"], "10086")
    assert not result.eligible and result.code == "category_restricted"
    quality = await refunds.get_eligibility(
        actors["demo_customer"], "10086", reason="quality_issue"
    )
    assert quality.eligible


async def test_latest_expired_policy_cannot_fall_back_to_old_open_ended_version(
    refunds, actors, pg_admin_factory
):
    from enterprise_agent.models import RefundPolicyVersion

    async with pg_admin_factory() as session, session.begin():
        paid_at = await session.scalar(select(Order.paid_at).where(Order.order_no == "10086"))
        policy = await session.scalar(
            select(RefundPolicyVersion).where(RefundPolicyVersion.policy_code == "STANDARD_RETURN")
        )
        session.add(
            RefundPolicyVersion(
                policy_code="STANDARD_RETURN",
                version=2,
                document_id=policy.document_id,
                valid_from=paid_at - timedelta(days=1),
                valid_until=paid_at,
                refund_window_days=7,
                refund_shipping=True,
                allow_before_delivery=True,
                excluded_categories=["food"],
            )
        )
    result = await refunds.get_eligibility(actors["demo_customer"], "10086")
    assert not result.eligible and result.code == "policy_unavailable"
    await expect_error(
        refunds.prepare_refund(actors["demo_customer"], "10086", reason="change_mind"),
        "policy_unavailable",
    )


async def test_prepare_has_no_refund_side_effect_and_requires_owner(refunds, actors, pg_factory):
    baseline = await count_rows(pg_factory, RefundRequest)
    approval = await refunds.prepare_refund(
        actors["demo_customer"], "10086", reason="quality_issue", notes="模拟商品破损"
    )
    assert approval.requires_confirmation and approval.status == ApprovalStatus.PENDING
    assert await count_rows(pg_factory, RefundRequest) == baseline
    await expect_error(
        refunds.prepare_refund(actors["demo_other"], "10086", reason="change_mind"),
        "order_not_found",
        404,
    )
    await expect_error(
        refunds.prepare_refund(actors["demo_support"], "10086", reason="change_mind"),
        "forbidden",
        403,
    )


async def test_confirmation_rejects_cross_user_expiry_and_unknown_approval(
    refunds, actors, pg_factory
):
    actor = actors["demo_customer"]
    approval = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    await expect_error(
        refunds.confirm_refund(
            actors["demo_other"],
            approval.approval_id,
            idempotency_key="cross-user-key",
            confirmed=True,
        ),
        "approval_not_found",
        404,
    )
    await expect_error(
        refunds.confirm_refund(actor, uuid4(), idempotency_key="unknown-key-0001", confirmed=True),
        "approval_not_found",
        404,
    )
    late = RefundService(pg_factory, clock=lambda: NOW + timedelta(minutes=30))
    await expect_error(
        late.confirm_refund(
            actor, approval.approval_id, idempotency_key="expired-key-0001", confirmed=True
        ),
        "approval_expired",
    )
    async with pg_factory() as session:
        saved = await session.get(ApprovalRequest, approval.approval_id)
        assert saved.status == ApprovalStatus.EXPIRED


@pytest.mark.parametrize("deadline", ["approval", "refund_window"])
async def test_confirmation_rechecks_deadlines_after_waiting_for_order_lock(
    pg_factory, pg_admin_factory, actors, monkeypatch, deadline
):
    current_time = [NOW]
    service = RefundService(pg_factory, clock=lambda: current_time[0])
    actor = actors["demo_customer"]
    if deadline == "refund_window":
        async with pg_admin_factory() as session, session.begin():
            await session.execute(
                update(Order)
                .where(Order.order_no == "10086")
                .values(
                    created_at=NOW - timedelta(days=8, hours=1),
                    paid_at=NOW - timedelta(days=8),
                    delivered_at=NOW - timedelta(days=7) + timedelta(seconds=15),
                )
            )
    approval = await service.prepare_refund(actor, "10086", reason="change_mind")
    baseline_refunds = await count_rows(pg_factory, RefundRequest)
    baseline_tickets = await count_rows(pg_factory, SupportTicket)
    lock_requested = asyncio.Event()
    original_get_visible = OrderRepository.get_visible

    async def observe_order_lock(repository, principal, order_no, *, lock=False):
        if lock and order_no == "10086":
            lock_requested.set()
        return await original_get_visible(repository, principal, order_no, lock=lock)

    monkeypatch.setattr(OrderRepository, "get_visible", observe_order_lock)
    key = f"wait-{deadline}-deadline"
    task = None
    try:
        async with pg_admin_factory() as session, session.begin():
            await session.scalar(select(Order).where(Order.order_no == "10086").with_for_update())
            task = asyncio.create_task(
                service.confirm_refund(
                    actor, approval.approval_id, idempotency_key=key, confirmed=True
                )
            )
            await asyncio.wait_for(lock_requested.wait(), timeout=5)
            assert not task.done()
            # Advance a business clock while the actual PostgreSQL row lock is held.
            current_time[0] = (
                NOW + timedelta(minutes=30, seconds=1)
                if deadline == "approval"
                else NOW + timedelta(seconds=30)
            )
        expected_code = "approval_expired" if deadline == "approval" else "refund_window_expired"
        await expect_error(asyncio.wait_for(task, timeout=5), expected_code)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert await count_rows(pg_factory, RefundRequest) == baseline_refunds
    assert await count_rows(pg_factory, SupportTicket) == baseline_tickets
    assert await count_rows(pg_factory, IdempotencyRecord, IdempotencyRecord.key == key) == 0
    async with pg_factory() as session:
        saved = await session.get(ApprovalRequest, approval.approval_id)
        assert saved.status == (
            ApprovalStatus.EXPIRED if deadline == "approval" else ApprovalStatus.PENDING
        )
    if deadline == "approval":
        assert (
            await count_rows(
                pg_factory,
                AuditLog,
                AuditLog.resource_id == str(approval.approval_id),
                AuditLog.action == "refund.confirm_expired",
            )
            == 1
        )


async def test_seed_histories_have_policy_sources_and_consistent_ticket_states(pg_factory, pg_seed):
    async with pg_factory() as session:
        histories = list(await session.scalars(select(RefundRequest)))
        assert histories
        for refund in histories:
            snapshot = refund.policy_snapshot
            source = PolicyResponse.model_validate(snapshot["policy"])
            assert source.id == refund.policy_version_id
            facts = snapshot["facts"]
            assert facts["order_id"] == refund.order_id
            assert facts["user_id"] == refund.applicant_id
            assert (
                sum(item["paid_amount_cents"] for item in facts["items"])
                == facts["goods_paid_cents"]
            )
            assert refund.amount_cents == facts["goods_paid_cents"]
            assert snapshot["eligibility_checked_at"] and snapshot["decision"]
            ticket = await session.scalar(
                select(SupportTicket).where(SupportTicket.refund_request_id == refund.id)
            )
            assert (
                ticket.status
                == {
                    RefundStatus.PENDING_REVIEW: "open",
                    RefundStatus.APPROVED: "approved",
                    RefundStatus.REJECTED: "closed",
                    RefundStatus.CANCELLED: "closed",
                    RefundStatus.COMPLETED_SIMULATED: "closed",
                }[refund.status]
            )
            audits = list(
                await session.scalars(
                    select(AuditLog).where(AuditLog.resource_id == str(refund.id))
                )
            )
            assert audits and all(audit.resource_type == "refund" for audit in audits)


async def test_snapshot_tampering_and_changed_order_are_rejected(refunds, actors, pg_admin_factory):
    actor = actors["demo_customer"]
    tampered = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    async with pg_admin_factory() as session, session.begin():
        record = await session.get(ApprovalRequest, tampered.approval_id)
        record.action_payload = record.action_payload | {"amount_cents": 1}
    await expect_error(
        refunds.confirm_refund(
            actor, tampered.approval_id, idempotency_key="tampered-key-001", confirmed=True
        ),
        "approval_changed",
    )
    changed = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    async with pg_admin_factory() as session, session.begin():
        await session.execute(
            update(Order)
            .where(Order.order_no == "10086")
            .values(delivered_at=NOW - timedelta(days=3))
        )
    await expect_error(
        refunds.confirm_refund(
            actor, changed.approval_id, idempotency_key="changed-order-01", confirmed=True
        ),
        "approval_changed",
    )


async def test_rejection_is_idempotent_and_creates_no_refund(refunds, actors, pg_factory):
    actor = actors["demo_customer"]
    baseline = await count_rows(pg_factory, RefundRequest)
    approval = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    first = await refunds.confirm_refund(
        actor, approval.approval_id, idempotency_key="reject-key-00001", confirmed=False
    )
    again = await refunds.confirm_refund(
        actor, approval.approval_id, idempotency_key="reject-key-00001", confirmed=False
    )
    assert first.status == ApprovalStatus.REJECTED and first.refund is None
    assert again.replayed and again.refund is None
    assert await count_rows(pg_factory, RefundRequest) == baseline
    await expect_error(
        refunds.confirm_refund(
            actor, approval.approval_id, idempotency_key="reject-key-00001", confirmed=True
        ),
        "idempotency_conflict",
    )


async def test_confirmation_is_atomic_and_same_key_returns_same_result(refunds, actors, pg_factory):
    actor = actors["demo_customer"]
    approval = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    results = await asyncio.gather(
        *[
            refunds.confirm_refund(
                actor, approval.approval_id, idempotency_key="concurrent-same-key", confirmed=True
            )
            for _ in range(2)
        ]
    )
    assert results[0].refund.id == results[1].refund.id
    assert sorted(result.replayed for result in results) == [False, True]
    refund = results[0].refund
    assert refund.status == RefundStatus.PENDING_REVIEW and refund.amount_cents == 12900
    assert (
        await count_rows(pg_factory, SupportTicket, SupportTicket.refund_request_id == refund.id)
        == 1
    )
    assert (
        await count_rows(
            pg_factory, IdempotencyRecord, IdempotencyRecord.key == "concurrent-same-key"
        )
        == 1
    )
    assert (
        await count_rows(
            pg_factory,
            AuditLog,
            AuditLog.action == "refund.create",
            AuditLog.resource_id == str(refund.id),
        )
        == 1
    )
    await expect_error(
        refunds.confirm_refund(
            actor, uuid4(), idempotency_key="concurrent-same-key", confirmed=True
        ),
        "idempotency_conflict",
    )
    await expect_error(
        refunds.confirm_refund(
            actor, approval.approval_id, idempotency_key="different-replay-key", confirmed=True
        ),
        "approval_unavailable",
    )


async def test_distinct_approvals_cannot_concurrently_create_two_active_refunds(
    refunds, actors, pg_factory
):
    actor = actors["demo_customer"]
    approvals = [
        await refunds.prepare_refund(actor, "10086", reason="change_mind") for _ in range(2)
    ]
    results = await asyncio.gather(
        *[
            refunds.confirm_refund(
                actor,
                approval.approval_id,
                idempotency_key=f"distinct-key-{index:08}",
                confirmed=True,
            )
            for index, approval in enumerate(approvals)
        ],
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, BusinessError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code == "refund_already_exists"
    async with pg_factory() as session:
        order_id = await session.scalar(select(Order.id).where(Order.order_no == "10086"))
    assert await count_rows(pg_factory, RefundRequest, RefundRequest.order_id == order_id) == 1


async def test_transaction_failure_rolls_back_refund_ticket_and_consumption(
    refunds, actors, pg_factory, monkeypatch
):
    actor = actors["demo_customer"]
    approval = await refunds.prepare_refund(actor, "10086", reason="change_mind")
    baseline = await count_rows(pg_factory, RefundRequest)
    baseline_audits = await count_rows(pg_factory, AuditLog, AuditLog.action == "refund.create")
    original_flush = AsyncSession.flush

    async def fail_ticket_flush(session, *args, **kwargs):
        if any(isinstance(record, SupportTicket) for record in session.new):
            raise RuntimeError("Injected database-boundary failure after refund insert")
        return await original_flush(session, *args, **kwargs)

    with monkeypatch.context() as changes:
        changes.setattr(AsyncSession, "flush", fail_ticket_flush)
        with pytest.raises(RuntimeError, match="Injected"):
            await refunds.confirm_refund(
                actor, approval.approval_id, idempotency_key="rollback-test-key", confirmed=True
            )
    assert await count_rows(pg_factory, RefundRequest) == baseline
    assert (
        await count_rows(
            pg_factory, IdempotencyRecord, IdempotencyRecord.key == "rollback-test-key"
        )
        == 0
    )
    assert (
        await count_rows(pg_factory, AuditLog, AuditLog.action == "refund.create")
        == baseline_audits
    )
    async with pg_factory() as session:
        assert (
            await session.get(ApprovalRequest, approval.approval_id)
        ).status == ApprovalStatus.PENDING
    retry = await refunds.confirm_refund(
        actor, approval.approval_id, idempotency_key="rollback-test-key", confirmed=True
    )
    assert retry.refund.status == RefundStatus.PENDING_REVIEW


async def test_staff_review_and_simulated_completion_are_audited(refunds, actors, pg_factory):
    actor = actors["demo_customer"]
    staff = actors["demo_support"]
    _, refund = await create_request(refunds, actor)
    await expect_error(refunds.get_refund(actors["demo_other"], refund.id), "refund_not_found", 404)
    await expect_error(
        refunds.review_refund(actor, refund.id, decision="approve", note="客户越权"),
        "forbidden",
        403,
    )
    await expect_error(
        refunds.complete_simulated_refund(staff, refund.id), "invalid_refund_transition"
    )
    approved = await refunds.review_refund(
        staff, refund.id, decision="approve", note="模拟审核通过"
    )
    assert approved.status == RefundStatus.APPROVED and approved.reviewer_id == staff.user_id
    await expect_error(
        refunds.review_refund(staff, refund.id, decision="reject", note="重复审核"),
        "invalid_refund_transition",
    )
    complete = await refunds.complete_simulated_refund(staff, refund.id)
    assert complete.status == RefundStatus.COMPLETED_SIMULATED and complete.completed_at == NOW
    await expect_error(
        refunds.complete_simulated_refund(staff, refund.id), "invalid_refund_transition"
    )
    async with pg_factory() as session:
        actions = set(
            await session.scalars(
                select(AuditLog.action).where(AuditLog.resource_id == str(refund.id))
            )
        )
        ticket = await session.scalar(
            select(SupportTicket).where(SupportTicket.refund_request_id == refund.id)
        )
        assert {"refund.create", "refund.approve", "refund.complete_simulated"}.issubset(actions)
        assert ticket.status == "closed" and ticket.assigned_to == staff.user_id
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        assert order.status.value == "delivered"


async def test_rejected_request_permits_a_new_confirmed_application(refunds, actors):
    actor = actors["demo_customer"]
    _, first = await create_request(refunds, actor)
    rejected = await refunds.review_refund(
        actors["demo_support"], first.id, decision="reject", note="补充说明后可再申请"
    )
    assert rejected.status == RefundStatus.REJECTED
    approval = await refunds.prepare_refund(
        actor, "10086", reason="quality_issue", notes="补充模拟证据"
    )
    second = await refunds.confirm_refund(
        actor, approval.approval_id, idempotency_key="second-request-key", confirmed=True
    )
    assert second.refund.id != first.id


async def test_concurrent_staff_decisions_do_not_overwrite_a_committed_review(
    refunds, actors, pg_factory
):
    _, refund = await create_request(refunds, actors["demo_customer"])
    staff = actors["demo_support"]
    results = await asyncio.gather(
        refunds.review_refund(staff, refund.id, decision="approve", note="并发模拟批准"),
        refunds.review_refund(staff, refund.id, decision="reject", note="并发模拟拒绝"),
        return_exceptions=True,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, BusinessError)]
    assert len(successes) == len(failures) == 1
    assert failures[0].code == "invalid_refund_transition"
    saved = await refunds.get_refund(staff, refund.id)
    assert saved.status == successes[0].status
    assert (
        await count_rows(
            pg_factory,
            AuditLog,
            AuditLog.resource_id == str(refund.id),
            AuditLog.action.in_(["refund.approve", "refund.reject"]),
        )
        == 1
    )
