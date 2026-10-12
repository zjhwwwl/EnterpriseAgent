"""PostgreSQL itself enforces money, refund uniqueness and application privileges."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from enterprise_agent.models import RefundRequest, RefundStatus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.usefixtures("pg_seed"),
]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


async def expect_sql_rejection(factory, statement, sqlstate, *, constraint=None):
    async with factory() as session:
        try:
            with pytest.raises(DBAPIError) as caught:
                await session.execute(text(statement))
        finally:
            # Roll back even when an expected denial is accidentally permitted.
            await session.rollback()
    assert caught.value.orig.sqlstate == sqlstate
    if constraint is not None:
        assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "UPDATE orders SET goods_paid_cents = -1, "
            "paid_total_cents = shipping_paid_cents - 1 WHERE order_no = '10086'",
            "ck_orders_goods_paid_nonnegative",
        ),
        (
            "UPDATE orders SET paid_total_cents = paid_total_cents + 1 WHERE order_no = '10086'",
            "ck_orders_paid_total_matches",
        ),
        (
            "UPDATE order_items SET quantity = 0, paid_amount_cents = 0 WHERE "
            "order_id = (SELECT id FROM orders WHERE order_no = '10086')",
            "ck_order_items_quantity_positive",
        ),
    ],
    ids=["negative-money", "inconsistent-total", "zero-quantity"],
)
async def test_business_checks_are_enforced_by_postgresql(statement, constraint, pg_admin_factory):
    await expect_sql_rejection(pg_admin_factory, statement, "23514", constraint=constraint)


def copied_refund(existing, status):
    return RefundRequest(
        id=uuid4(),
        order_id=existing.order_id,
        applicant_id=existing.applicant_id,
        amount_cents=existing.amount_cents,
        reason=existing.reason,
        notes="仅用于专用测试数据库约束验证",
        status=status,
        policy_version_id=existing.policy_version_id,
        policy_snapshot=existing.policy_snapshot,
        created_at=NOW,
    )


async def test_active_refund_unique_constraint_allows_only_rejected_history(pg_admin_factory):
    with pytest.raises(IntegrityError) as caught:
        async with pg_admin_factory() as session, session.begin():
            existing = await session.scalar(
                select(RefundRequest)
                .where(RefundRequest.status == RefundStatus.COMPLETED_SIMULATED)
                .limit(1)
            )
            assert existing is not None
            session.add(copied_refund(existing, RefundStatus.PENDING_REVIEW))
            await session.flush()
    assert caught.value.orig.sqlstate == "23505"
    assert caught.value.orig.diag.constraint_name == "uq_refund_active_order"

    async with pg_admin_factory() as session, session.begin():
        existing = await session.scalar(
            select(RefundRequest)
            .where(RefundRequest.status == RefundStatus.COMPLETED_SIMULATED)
            .limit(1)
        )
        original_count = await session.scalar(
            select(func.count())
            .select_from(RefundRequest)
            .where(RefundRequest.order_id == existing.order_id)
        )
        session.add_all(
            [
                copied_refund(existing, RefundStatus.REJECTED),
                copied_refund(existing, RefundStatus.REJECTED),
            ]
        )
        await session.flush()
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == existing.order_id)
            )
            == original_count + 2
        )


async def test_application_account_is_ordinary_and_owns_no_application_tables(pg_factory):
    async with pg_factory() as session:
        role = (
            (
                await session.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert not any(role.values())
        assert not await session.scalar(
            text(
                "SELECT pg_get_userbyid(datdba) = current_user "
                "FROM pg_database WHERE datname = current_database()"
            )
        )
        owned = await session.scalar(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S') "
                "AND pg_get_userbyid(c.relowner) = current_user"
            )
        )
        assert owned == 0
        assert not await session.scalar(
            text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
        )


async def test_application_grants_allow_services_but_exclude_facts_and_audit_mutation(pg_factory):
    async with pg_factory() as session:
        for table in ["orders", "users", "products", "logistics", "refund_policy_versions"]:
            assert await session.scalar(
                text("SELECT has_table_privilege(current_user, :table_name, 'SELECT')"),
                {"table_name": table},
            )
            for privilege in ["INSERT", "UPDATE", "DELETE"]:
                assert not await session.scalar(
                    text("SELECT has_table_privilege(current_user, :table_name, :privilege)"),
                    {"table_name": table, "privilege": privilege},
                )
        assert not await session.scalar(
            text("SELECT has_column_privilege(current_user, 'orders', 'status', 'UPDATE')")
        )
        assert await session.scalar(
            text("SELECT has_column_privilege(current_user, 'orders', 'id', 'UPDATE')")
        )
        for privilege in ["SELECT", "INSERT"]:
            assert await session.scalar(
                text("SELECT has_table_privilege(current_user, 'audit_logs', :privilege)"),
                {"privilege": privilege},
            )
        for privilege in ["UPDATE", "DELETE"]:
            assert not await session.scalar(
                text("SELECT has_table_privilege(current_user, 'audit_logs', :privilege)"),
                {"privilege": privilege},
            )
        for table in [
            "approval_requests",
            "refund_requests",
            "support_tickets",
            "idempotency_records",
            "agent_tasks",
            "agent_sessions",
        ]:
            for privilege in ["SELECT", "INSERT", "UPDATE", "DELETE"]:
                assert await session.scalar(
                    text("SELECT has_table_privilege(current_user, :table_name, :privilege)"),
                    {"table_name": table, "privilege": privilege},
                )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE orders SET status = 'cancelled' WHERE order_no = '10086'",
        "DELETE FROM audit_logs WHERE false",
        "UPDATE audit_logs SET action = 'tampered' WHERE false",
        "UPDATE refund_policy_versions SET refund_window_days = refund_window_days + 1",
        "UPDATE users SET role = 'admin' WHERE username = 'demo_customer'",
        "CREATE TABLE public.ea_permission_probe (id integer)",
    ],
    ids=[
        "order-status",
        "audit-delete",
        "audit-update",
        "policy-edit",
        "user-role-edit",
        "schema-create",
    ],
)
async def test_application_writes_are_rejected_at_the_database_boundary(statement, pg_factory):
    # The helper always rolls back, including unexpected permission successes.
    await expect_sql_rejection(pg_factory, statement, "42501")


async def test_published_policy_is_immutable_even_for_the_initialization_account(
    pg_admin_factory, pg_factory
):
    async with pg_factory() as session:
        before = await session.scalar(
            text("SELECT refund_window_days FROM refund_policy_versions ORDER BY id LIMIT 1")
        )
    await expect_sql_rejection(
        pg_admin_factory,
        "UPDATE refund_policy_versions SET refund_window_days = refund_window_days + 1 "
        "WHERE id = (SELECT min(id) FROM refund_policy_versions)",
        "23514",
    )
    async with pg_factory() as session:
        after = await session.scalar(
            text("SELECT refund_window_days FROM refund_policy_versions ORDER BY id LIMIT 1")
        )
    assert after == before
