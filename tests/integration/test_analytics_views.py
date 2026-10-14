"""Compare real aggregate views with raw facts and probe the actual reader role."""

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_agent.models import Order, OrderItem, OrderStatus, RefundRequest, RefundStatus

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.usefixtures("pg_seed")]
SHANGHAI = ZoneInfo("Asia/Shanghai")
PAID = {OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED}


@pytest_asyncio.fixture
async def reader_engine(analytics_urls):
    engine = create_async_engine(analytics_urls[0], poolclass=NullPool, hide_parameters=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_daily_orders_matches_independent_python_fact_aggregation(
    pg_admin_factory, reader_engine
):
    expected = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    async with pg_admin_factory() as session:
        orders = (await session.scalars(select(Order))).all()
        refunds = (await session.scalars(select(RefundRequest))).all()
    completed = defaultdict(int)
    for refund in refunds:
        if refund.status == RefundStatus.COMPLETED_SIMULATED:
            completed[refund.order_id] += refund.amount_cents
    for order in orders:
        if order.paid_at is None or order.status not in PAID:
            continue
        values = expected[order.paid_at.astimezone(SHANGHAI).date()]
        values[0] += 1
        values[1] += order.goods_paid_cents
        values[2] += order.shipping_paid_cents
        values[3] += order.paid_total_cents
        values[4] += order.id in completed
        values[5] += completed.get(order.id, 0)
    async with reader_engine.connect() as connection:
        rows = (await connection.execute(text("SELECT * FROM ea_analytics.daily_orders"))).all()
    actual = {row.business_date: list(row[1:]) for row in rows}
    assert actual == dict(expected)
    assert sum(values[4] for values in actual.values()) > 0
    assert all(values[3] == values[1] + values[2] for values in actual.values())


async def test_product_sales_matches_snapshot_facts_without_shipping(
    pg_admin_factory, reader_engine
):
    expected = defaultdict(lambda: [0, 0])
    async with pg_admin_factory() as session:
        orders = {order.id: order for order in (await session.scalars(select(Order))).all()}
        items = (await session.scalars(select(OrderItem))).all()
    for item in items:
        order = orders[item.order_id]
        if order.paid_at is None or order.status not in PAID:
            continue
        key = (
            order.paid_at.astimezone(SHANGHAI).date(),
            item.product_id,
            item.product_name,
            item.category,
        )
        expected[key][0] += item.quantity
        expected[key][1] += item.paid_amount_cents
    async with reader_engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT * FROM ea_analytics.daily_product_sales"))
        ).all()
    actual = {tuple(row[:4]): list(row[4:]) for row in rows}
    assert actual == dict(expected)


async def test_payment_day_boundary_status_filter_and_snapshot_groups(pg_admin_factory):
    async with pg_admin_factory() as session:
        transaction = await session.begin()
        try:
            original = await session.scalar(select(Order).where(Order.order_no == "10086"))
            item = await session.scalar(select(OrderItem).where(OrderItem.order_id == original.id))
            for index, (moment, status, paid_at, snapshot) in enumerate(
                [
                    (
                        datetime(2030, 1, 1, 15, 59, 59, tzinfo=UTC),
                        OrderStatus.PAID,
                        True,
                        "旧名称",
                    ),
                    (datetime(2030, 1, 1, 16, tzinfo=UTC), OrderStatus.SHIPPED, True, "新名称"),
                    (
                        datetime(2030, 1, 1, 16, 1, tzinfo=UTC),
                        OrderStatus.DELIVERED,
                        True,
                        "旧名称",
                    ),
                    (datetime(2030, 1, 1, 16, 2, tzinfo=UTC), OrderStatus.CANCELLED, True, "排除"),
                    (datetime(2030, 1, 1, 16, 3, tzinfo=UTC), OrderStatus.UNPAID, False, "排除"),
                ]
            ):
                order = Order(
                    order_no="view-boundary-" + uuid4().hex[:16],
                    user_id=original.user_id,
                    status=status,
                    goods_paid_cents=101,
                    shipping_paid_cents=7,
                    paid_total_cents=108,
                    created_at=moment,
                    paid_at=moment if paid_at else None,
                )
                session.add(order)
                await session.flush()
                session.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=item.product_id,
                        product_name=snapshot,
                        category="snapshot-category",
                        quantity=index + 1,
                        unit_price_cents=101,
                        paid_amount_cents=101,
                    )
                )
            await session.flush()
            rows = (
                await session.execute(
                    text(
                        "SELECT business_date, paid_order_count, gross_revenue_cents "
                        "FROM ea_analytics.daily_orders WHERE business_date >= '2030-01-01' "
                        "ORDER BY business_date"
                    )
                )
            ).all()
            assert [tuple(row) for row in rows] == [
                (date(2030, 1, 1), 1, Decimal(108)),
                (date(2030, 1, 2), 2, Decimal(216)),
            ]
            snapshots = (
                await session.execute(
                    text(
                        "SELECT product_name, category, units_sold, goods_revenue_cents "
                        "FROM ea_analytics.daily_product_sales "
                        "WHERE business_date = '2030-01-02' ORDER BY product_name"
                    )
                )
            ).all()
            assert {tuple(row) for row in snapshots} == {
                ("新名称", "snapshot-category", 2, Decimal(101)),
                ("旧名称", "snapshot-category", 3, Decimal(101)),
            }
        finally:
            await transaction.rollback()


async def test_completed_refund_uses_payment_cohort_and_does_not_multiply_revenue(
    pg_admin_factory,
):
    async with pg_admin_factory() as session:
        transaction = await session.begin()
        try:
            refund = await session.scalar(
                select(RefundRequest).where(
                    RefundRequest.status == RefundStatus.COMPLETED_SIMULATED
                )
            )
            order = await session.get(Order, refund.order_id)
            cohort = order.paid_at.astimezone(SHANGHAI).date()
            before = (
                await session.execute(
                    text("SELECT * FROM ea_analytics.daily_orders WHERE business_date = :day"),
                    {"day": cohort},
                )
            ).one()
            refund.completed_at = datetime(2031, 1, 1, tzinfo=UTC)
            # Rejected/cancelled historical applications must not duplicate a
            # paid order or count as another completed refund.
            for status in (RefundStatus.REJECTED, RefundStatus.CANCELLED):
                session.add(
                    RefundRequest(
                        order_id=refund.order_id,
                        applicant_id=refund.applicant_id,
                        amount_cents=refund.amount_cents,
                        reason=refund.reason,
                        status=status,
                        policy_version_id=refund.policy_version_id,
                        policy_snapshot=refund.policy_snapshot,
                    )
                )
            await session.flush()
            after = (
                await session.execute(
                    text("SELECT * FROM ea_analytics.daily_orders WHERE business_date = :day"),
                    {"day": cohort},
                )
            ).one()
            assert tuple(after) == tuple(before)
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM ea_analytics.daily_orders "
                        "WHERE business_date = '2031-01-01'"
                    )
                )
                == 0
            )
            # Pending review removes this one order from the completed cohort,
            # while the original paid revenue remains unchanged.
            refund.status = RefundStatus.PENDING_REVIEW
            await session.flush()
            pending = (
                await session.execute(
                    text("SELECT * FROM ea_analytics.daily_orders WHERE business_date = :day"),
                    {"day": cohort},
                )
            ).one()
            assert tuple(pending[1:5]) == tuple(before[1:5])
            assert pending.completed_refund_order_count == before.completed_refund_order_count - 1
            assert (
                pending.completed_refund_cents
                == before.completed_refund_cents - refund.amount_cents
            )
        finally:
            await transaction.rollback()


async def test_views_have_only_typed_aggregate_columns_and_owner_security(pg_admin_factory):
    async with pg_admin_factory() as session:
        columns = (
            await session.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'ea_analytics' ORDER BY table_name, ordinal_position"
                )
            )
        ).all()
        views = (
            await session.execute(
                text(
                    "SELECT relname, reloptions, pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE relnamespace = 'ea_analytics'::regnamespace ORDER BY relname"
                )
            )
        ).all()
    assert [tuple(row) for row in columns] == [
        ("daily_orders", "business_date", "date"),
        ("daily_orders", "paid_order_count", "bigint"),
        ("daily_orders", "goods_revenue_cents", "numeric"),
        ("daily_orders", "shipping_revenue_cents", "numeric"),
        ("daily_orders", "gross_revenue_cents", "numeric"),
        ("daily_orders", "completed_refund_order_count", "bigint"),
        ("daily_orders", "completed_refund_cents", "numeric"),
        ("daily_product_sales", "business_date", "date"),
        ("daily_product_sales", "product_id", "bigint"),
        ("daily_product_sales", "product_name", "text"),
        ("daily_product_sales", "category", "text"),
        ("daily_product_sales", "units_sold", "bigint"),
        ("daily_product_sales", "goods_revenue_cents", "numeric"),
    ]
    assert len(views) == 2
    for row in views:
        assert set(row.reloptions) == {"security_barrier=true", "security_invoker=false"}
        assert row.pg_get_userbyid == "ea_local_admin"


async def test_reader_has_separate_identity_database_and_safe_defaults(reader_engine):
    async with reader_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT current_user, current_database(), "
                    "current_setting('default_transaction_read_only'), "
                    "current_setting('statement_timeout'), current_setting('lock_timeout'), "
                    "current_setting('idle_in_transaction_session_timeout'), "
                    "has_database_privilege(current_user, 'enterprise_agent', 'CONNECT'), "
                    "has_database_privilege(current_user, current_database(), 'CREATE'), "
                    "has_database_privilege(current_user, current_database(), 'TEMP'), "
                    "has_schema_privilege(current_user, 'public', 'CREATE')"
                )
            )
        ).one()
        role = (
            await connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolreplication, "
                    "rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        memberships = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members WHERE member = "
                "(SELECT oid FROM pg_roles WHERE rolname = current_user)"
            )
        )
    assert tuple(row) == (
        "ea_test_analytics_reader",
        "enterprise_agent_test",
        "on",
        "5s",
        "1s",
        "10s",
        False,
        False,
        False,
        False,
    )
    assert tuple(role) == (False,) * 6
    assert memberships == 0


@pytest.mark.parametrize(
    "statement, sqlstate",
    [
        ("SELECT id, password_hash FROM public.users LIMIT 1", "42501"),
        ("SELECT id FROM public.orders LIMIT 1", "42501"),
        ("SELECT v FROM ea_checkpoint.checkpoint_migrations LIMIT 1", "42501"),
        ("CREATE TEMP TABLE analytics_forbidden_temp (id int)", "42501"),
        ("UPDATE public.orders SET id = id WHERE false", "42501"),
        ("DELETE FROM public.audit_logs WHERE false", "42501"),
        ("CREATE TABLE ea_analytics.analytics_forbidden_ddl (id int)", "42501"),
        ("DROP VIEW ea_analytics.daily_orders", "42501"),
    ],
)
async def test_reader_cannot_bypass_acl_by_turning_off_default_read_only(
    reader_engine, statement, sqlstate
):
    async with reader_engine.connect() as connection:
        # Default read-only is not the sole safeguard: with it disabled, ACLs
        # must still deny business writes, DDL, temp tables and raw customer data.
        await connection.exec_driver_sql("SET SESSION default_transaction_read_only = off")
        await connection.commit()
        try:
            with pytest.raises(DBAPIError) as caught:
                await connection.execute(text(statement))
            assert caught.value.orig.sqlstate == sqlstate
        finally:
            # Always undo the probe, including an accidentally permitted write.
            await connection.rollback()


async def test_default_read_only_rejects_writes_before_the_acl_probe(reader_engine):
    async with reader_engine.connect() as connection:
        try:
            with pytest.raises(DBAPIError) as caught:
                await connection.execute(text("DELETE FROM public.audit_logs WHERE false"))
            assert caught.value.orig.sqlstate == "25006"
        finally:
            await connection.rollback()


async def test_business_api_role_has_no_analytics_schema_permission(pg_factory):
    async with pg_factory() as session:
        assert (
            await session.scalar(
                text("SELECT has_schema_privilege(current_user, 'ea_analytics', 'USAGE')")
            )
            is False
        )
        with pytest.raises(DBAPIError) as caught:
            await session.execute(text("SELECT * FROM ea_analytics.daily_orders LIMIT 1"))
        assert caught.value.orig.sqlstate == "42501"
        await session.rollback()
