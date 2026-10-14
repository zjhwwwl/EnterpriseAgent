"""Real PostgreSQL proves the reader permissions, exact results and bounded lifecycle."""

import asyncio
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from enterprise_agent.analytics.catalog import SCHEMA_MARKER
from enterprise_agent.analytics.runtime import AnalyticsRuntime
from enterprise_agent.domain_errors import BusinessError

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
ORDERS = "ea_analytics.daily_orders"
PRODUCTS = "ea_analytics.daily_product_sales"


async def test_real_reader_result_matches_owner_aggregate_without_money_rounding(
    analytics_runtime, pg_admin_factory, pg_seed
):
    result = await analytics_runtime.execute(
        f"SELECT SUM(paid_order_count) AS orders, SUM(gross_revenue_cents) AS revenue FROM {ORDERS}"
    )
    async with pg_admin_factory() as session:
        expected = (
            await session.execute(
                text(
                    "SELECT count(*)::numeric, sum(paid_total_cents::numeric) FROM public.orders "
                    "WHERE paid_at IS NOT NULL AND status IN ('paid', 'shipped', 'delivered')"
                )
            )
        ).one()
    assert result.columns == ["orders", "revenue"]
    assert result.rows == [[format(value, "f") for value in expected]]
    assert result.row_count == 1 and not result.truncated
    assert result.elapsed_ms > 0
    assert "LIMIT 101" in result.normalized_sql


async def test_row_limit_reports_truncation_and_respects_explicit_smaller_limit(
    analytics_settings, pg_seed
):
    runtime = AnalyticsRuntime(analytics_settings.model_copy(update={"analytics_max_rows": 2}))
    try:
        assert await runtime.is_ready()
        full = await runtime.execute(f"SELECT business_date FROM {ORDERS} ORDER BY business_date")
        assert full.row_count == 2 and full.truncated
        assert full.normalized_sql.endswith("LIMIT 3")
        limited = await runtime.execute(
            f"SELECT business_date FROM {ORDERS} ORDER BY business_date LIMIT 1"
        )
        assert limited.row_count == 1 and not limited.truncated
        empty = await runtime.execute(f"SELECT business_date FROM {ORDERS} LIMIT 0")
        assert empty.row_count == 0 and not empty.truncated
    finally:
        await runtime.close()


async def test_execute_revalidates_raw_sql_and_does_not_reuse_a_trusted_plan(
    analytics_runtime, pg_seed
):
    with pytest.raises(BusinessError) as error:
        await analytics_runtime.execute("SELECT username FROM public.users")
    assert error.value.code == "analytics_sql_rejected"
    with pytest.raises(BusinessError):
        await analytics_runtime.execute(f"SELECT business_date FROM {ORDERS}; DELETE FROM orders")
    assert (
        await analytics_runtime.execute(f"SELECT COUNT(*) AS dates FROM {ORDERS}")
    ).row_count == 1


@pytest.mark.parametrize(
    "unsafe_sql",
    [
        "SELECT username FROM public.users",
        "SELECT status FROM public.orders",
        "UPDATE public.orders SET status = 'paid' WHERE false",
        "INSERT INTO public.users (username, password_hash) VALUES ('intruder', 'invalid')",
        "DELETE FROM public.audit_logs WHERE false",
        "CREATE TEMP TABLE forbidden (id integer)",
        "CREATE TABLE public.forbidden (id integer)",
        "DROP VIEW ea_analytics.daily_orders",
        "SELECT * FROM public.alembic_version",
    ],
)
async def test_database_role_blocks_base_reads_and_writes_even_without_readonly_default(
    unsafe_sql, analytics_runtime
):
    async with analytics_runtime.engine.connect() as connection, connection.begin():
        # Read-only defaults are user-changeable, so privileges must stand alone.
        await connection.execute(text("SET TRANSACTION READ WRITE"))
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(unsafe_sql))
        assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize(
    "grant, revoke",
    [
        (
            "GRANT SELECT ON public.users TO ea_test_analytics_reader",
            "REVOKE SELECT ON public.users FROM ea_test_analytics_reader",
        ),
        (
            "GRANT SELECT(username) ON public.users TO ea_test_analytics_reader",
            "REVOKE SELECT(username) ON public.users FROM ea_test_analytics_reader",
        ),
        (
            "GRANT UPDATE(action) ON public.audit_logs TO ea_test_analytics_reader",
            "REVOKE UPDATE(action) ON public.audit_logs FROM ea_test_analytics_reader",
        ),
        (
            "GRANT CREATE ON SCHEMA public TO ea_test_analytics_reader",
            "REVOKE CREATE ON SCHEMA public FROM ea_test_analytics_reader",
        ),
        (
            "GRANT TEMP ON DATABASE enterprise_agent_test TO ea_test_analytics_reader",
            "REVOKE TEMP ON DATABASE enterprise_agent_test FROM ea_test_analytics_reader",
        ),
        (
            "GRANT ea_test_app TO ea_test_analytics_reader",
            "REVOKE ea_test_app FROM ea_test_analytics_reader",
        ),
        (
            "GRANT INSERT ON ea_analytics.daily_orders TO ea_test_analytics_reader",
            "REVOKE INSERT ON ea_analytics.daily_orders FROM ea_test_analytics_reader",
        ),
        (
            "REVOKE SELECT ON ea_analytics.daily_orders FROM ea_test_analytics_reader",
            "GRANT SELECT ON ea_analytics.daily_orders TO ea_test_analytics_reader",
        ),
    ],
)
async def test_readiness_rejects_excessive_or_missing_grants_and_recovers(
    grant, revoke, analytics_runtime, pg_admin_factory
):
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text(grant))
    try:
        assert not await analytics_runtime.is_ready()
        with pytest.raises(BusinessError) as error:
            await analytics_runtime.execute(f"SELECT business_date FROM {ORDERS}")
        assert error.value.code == "analytics_unavailable"
    finally:
        async with pg_admin_factory() as session, session.begin():
            await session.execute(text(revoke))
    assert await analytics_runtime.is_ready()


async def test_readiness_checks_schema_marker_without_granting_migration_table_access(
    analytics_runtime, pg_admin_factory
):
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text("COMMENT ON SCHEMA ea_analytics IS 'wrong revision'"))
    try:
        assert not await analytics_runtime.is_ready()
    finally:
        async with pg_admin_factory() as session, session.begin():
            await session.execute(text(f"COMMENT ON SCHEMA ea_analytics IS '{SCHEMA_MARKER}'"))
    assert await analytics_runtime.is_ready()


@pytest.mark.parametrize("url_index", [0, 1])
async def test_ordinary_business_and_migration_accounts_are_rejected(
    url_index, analytics_settings, pg_urls
):
    runtime = AnalyticsRuntime(
        analytics_settings.model_copy(
            update={
                "analytics_database_url": SecretStr(pg_urls[url_index]),
            }
        )
    )
    try:
        assert not await runtime.is_ready()
    finally:
        await runtime.close()


async def test_each_execution_explicitly_enters_a_read_only_transaction(analytics_runtime, pg_seed):
    async with analytics_runtime.engine.connect() as connection:
        await connection.execute(text("SET SESSION default_transaction_read_only = off"))
        await connection.commit()
    observed = []
    query = f"SELECT COUNT(*) AS dates FROM {ORDERS}"

    def capture_transaction_readonly(connection, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT COUNT(*) AS dates"):
            cursor.execute("SHOW transaction_read_only")
            observed.append(cursor.fetchone()[0])

    event.listen(
        analytics_runtime.engine.sync_engine, "before_cursor_execute", capture_transaction_readonly
    )
    try:
        assert (await analytics_runtime.execute(query)).row_count == 1
        assert observed == ["on"]
    finally:
        event.remove(
            analytics_runtime.engine.sync_engine,
            "before_cursor_execute",
            capture_transaction_readonly,
        )
        async with analytics_runtime.engine.connect() as connection:
            await connection.execute(text("SET SESSION default_transaction_read_only = on"))
            await connection.commit()
    assert await analytics_runtime.is_ready()


async def test_actual_database_lock_timeout_is_safe_and_pool_is_reusable(
    analytics_runtime, pg_admin_factory, pg_seed
):
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text("LOCK TABLE public.orders IN ACCESS EXCLUSIVE MODE"))
        with pytest.raises(BusinessError) as error:
            await analytics_runtime.execute(f"SELECT SUM(paid_order_count) AS orders FROM {ORDERS}")
        assert error.value.code == "analytics_query_timeout"
        assert error.value.status_code == 504
        assert "postgresql" not in error.value.message
    assert analytics_runtime.engine.pool.checkedout() == 0
    assert (
        await analytics_runtime.execute(f"SELECT COUNT(*) AS dates FROM {ORDERS}")
    ).row_count == 1


async def test_cancelled_database_operation_rolls_back_and_releases_connection(
    analytics_runtime, pg_admin_factory, pg_seed
):
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text("LOCK TABLE public.orders IN ACCESS EXCLUSIVE MODE"))
        task = asyncio.create_task(
            analytics_runtime.execute(f"SELECT COUNT(*) AS dates FROM {ORDERS}")
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert analytics_runtime.engine.pool.checkedout() == 0
    assert (
        await analytics_runtime.execute(f"SELECT COUNT(*) AS dates FROM {ORDERS}")
    ).row_count == 1


async def test_division_by_zero_returns_static_error_then_healthy_query_works(
    analytics_runtime, pg_seed
):
    with pytest.raises(BusinessError) as error:
        await analytics_runtime.execute(f"SELECT paid_order_count / 0 AS invalid FROM {ORDERS}")
    assert error.value.code == "analytics_query_failed"
    assert "division" not in error.value.message and "paid_order_count" not in error.value.message
    result = await analytics_runtime.execute(
        f"SELECT SUM(goods_revenue_cents) AS sales FROM {PRODUCTS}"
    )
    assert isinstance(Decimal(result.rows[0][0]), Decimal)


async def test_actual_result_payload_is_bounded_even_when_sql_and_row_count_are_valid(
    analytics_runtime, pg_seed
):
    projections = ", ".join(f"'{'x' * 500}' AS col_{index}" for index in range(10))
    with pytest.raises(BusinessError) as error:
        await analytics_runtime.execute(f"SELECT {projections} FROM {ORDERS}")
    assert error.value.code == "analytics_result_too_large"
