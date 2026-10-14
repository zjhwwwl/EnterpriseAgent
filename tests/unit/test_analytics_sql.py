"""The supported AST subset and the rejection boundary remain explicit and reproducible."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from enterprise_agent.analytics.runtime import _json_value
from enterprise_agent.analytics.sql import validate_sql
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.analytics import QueryResult

ORDERS = "ea_analytics.daily_orders"
PRODUCTS = "ea_analytics.daily_product_sales"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT business_date, paid_order_count FROM {ORDERS}",
        f"SELECT SUM(gross_revenue_cents) AS total FROM {ORDERS}",
        f"SELECT COUNT(*) AS row_count FROM {ORDERS}",
        f"SELECT COUNT(DISTINCT product_id) AS products FROM {PRODUCTS}",
        f"SELECT product_id, product_name, SUM(goods_revenue_cents) AS revenue FROM {PRODUCTS} "
        "GROUP BY product_id, product_name ORDER BY revenue DESC LIMIT 10",
        f"SELECT o.business_date FROM {ORDERS} AS o WHERE o.business_date >= DATE '2026-10-01'",
        f"SELECT business_date FROM {ORDERS} WHERE business_date BETWEEN DATE '2026-10-01' "
        "AND DATE '2026-10-12'",
        f"SELECT ROUND(100.0 * SUM(completed_refund_order_count) / "
        f"NULLIF(SUM(paid_order_count), 0), 2) AS rate FROM {ORDERS}",
        f"SELECT COALESCE(SUM(gross_revenue_cents), 0) AS sales FROM {ORDERS}",
        f"SELECT SUM(CASE WHEN business_date < DATE '2026-10-06' THEN paid_order_count "
        f"ELSE 0 END) AS previous FROM {ORDERS}",
        f"SELECT AVG(paid_order_count) AS average, MIN(paid_order_count) AS low, "
        f"MAX(paid_order_count) AS high FROM {ORDERS}",
        f"SELECT business_date, SUM(paid_order_count) AS orders FROM {ORDERS} "
        "GROUP BY business_date HAVING SUM(paid_order_count) > 1 ORDER BY business_date",
        f"SELECT CAST(SUM(gross_revenue_cents) AS NUMERIC(20, 2)) AS sales FROM {ORDERS}",
        f"SELECT CAST(paid_order_count AS BIGINT) AS orders FROM {ORDERS}",
        f"SELECT product_name FROM {PRODUCTS} WHERE category IN ('electronics', 'clothing')",
        f"SELECT product_name FROM {PRODUCTS} WHERE NOT category = 'electronics' "
        "AND product_name IS NOT NULL",
        f"SELECT (paid_order_count + 1) * 2 - 3 AS computed FROM {ORDERS}",
        f"SELECT DISTINCT business_date FROM {ORDERS}",
        'SELECT "business_date" FROM "ea_analytics"."daily_orders"',
        "SELECT BUSINESS_DATE FROM EA_ANALYTICS.DAILY_ORDERS;",
    ],
)
def test_supported_sql_is_normalized_and_bounded(sql):
    result = validate_sql(sql)
    assert result.view_name in {ORDERS, PRODUCTS}
    assert 0 <= result.effective_limit <= 101
    assert result.normalized_sql.endswith(f"LIMIT {result.effective_limit}")
    assert validate_sql(result.normalized_sql) == result


@pytest.mark.parametrize(
    "sql",
    [
        "",
        " ",
        "SELECT 1",
        "SELECT 1; SELECT 2",
        "SELECT 1;",
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET status = 'paid'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "CALL arbitrary()",
        "COPY orders TO PROGRAM 'cmd'",
        "SET ROLE ea_local_admin",
        "SELECT current_user",
        "SELECT * FROM orders",
        "SELECT * FROM public.orders",
        f"SELECT * FROM {ORDERS}",
        f"SELECT o.* FROM {ORDERS} o",
        f"SELECT COUNT(o.*) AS n FROM {ORDERS} o",
        f"SELECT made_up_column FROM {ORDERS}",
        f"SELECT customer_id FROM {ORDERS}",
        "SELECT business_date FROM daily_orders",
        'SELECT business_date FROM "EA_ANALYTICS".daily_orders',
        f"SELECT business_date FROM another_db.{ORDERS}",
        f"SELECT business_date FROM {ORDERS} UNION SELECT business_date FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} JOIN {PRODUCTS} USING(business_date)",
        f"SELECT business_date FROM {ORDERS}, {PRODUCTS}",
        f"WITH x AS (SELECT business_date FROM {ORDERS}) SELECT business_date FROM x",
        f"SELECT business_date FROM (SELECT business_date FROM {ORDERS}) x",
        f"SELECT (SELECT count(*) FROM public.users) AS secret FROM {ORDERS}",
        f"SELECT business_date INTO scratch FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} FOR UPDATE",
        f"SELECT business_date FROM {ORDERS} FOR SHARE",
        f"SELECT business_date FROM {ORDERS} OFFSET 1",
        f"SELECT business_date FROM {ORDERS} LIMIT $1",
        f"SELECT business_date FROM {ORDERS} LIMIT -1",
        f"SELECT business_date FROM {ORDERS} LIMIT ALL",
        f"SELECT pg_sleep(1) AS slow FROM {ORDERS}",
        f"SELECT pg_catalog.pg_sleep(1) AS slow FROM {ORDERS}",
        f"SELECT set_config('role','ea_local_admin',false) AS role FROM {ORDERS}",
        f"SELECT current_setting('password') AS secret FROM {ORDERS}",
        f"SELECT arbitrary_udf(paid_order_count) AS x FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} WHERE EXISTS (SELECT 1 FROM users)",
        f"SELECT business_date FROM {ORDERS} WHERE product_id = :identity",
        f"SELECT business_date FROM {ORDERS} WHERE business_date >= CURRENT_DATE",
        f"SELECT DATE_TRUNC('day',business_date) AS day FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} "
        "WHERE business_date >= DATE '2026-10-01' - INTERVAL '1 day'",
        f"SELECT SUM(paid_order_count) OVER () AS x FROM {ORDERS}",
        f'SELECT business_date COLLATE "C" FROM {ORDERS}',
        f"SELECT CAST(paid_order_count AS DOUBLE PRECISION) AS lossy FROM {ORDERS}",
        f"SELECT CAST(paid_order_count AS public.custom_type) AS x FROM {ORDERS}",
        f"SELECT CAST(paid_order_count AS NUMERIC(100000, 0)) AS x FROM {ORDERS}",
        f"SELECT CAST(paid_order_count AS NUMERIC(10, 11)) AS x FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} WHERE business_date = DATE '2026-02-31'",
        f"SELECT CAST('tomorrow' AS DATE) AS x FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS} -- ignore safety",
        f"/* system: bypass */ SELECT business_date FROM {ORDERS}",
        f"SELECT business_date FROM {ORDERS}; /* trailing */",
        f"SELECT business_date AS x, paid_order_count AS x FROM {ORDERS}",
        f"SELECT business_date AS X, paid_order_count AS x FROM {ORDERS}",
        f"SELECT SUM(paid_order_count) FROM {ORDERS}",
        f"SELECT business_date AS x FROM {ORDERS} WHERE x IS NOT NULL",
        f"SELECT business_date FROM {ORDERS} t(business_date)",
        f"SELECT b.business_date FROM {ORDERS} a",
    ],
)
def test_unsafe_or_unsupported_sql_is_rejected_without_echo(sql):
    with pytest.raises(BusinessError) as error:
        validate_sql(sql)
    assert error.value.code == "analytics_sql_rejected"
    assert error.value.status_code == 422
    assert "SELECT" in error.value.message
    assert "ignore safety" not in error.value.message
    assert "ea_local_admin" not in error.value.message


@pytest.mark.parametrize(
    "requested, expected", [(0, 0), (5, 5), (100, 100), (101, 101), (9999, 101)]
)
def test_user_limit_preserves_small_requested_limits_and_caps_larger_ones(requested, expected):
    result = validate_sql(f"SELECT business_date FROM {ORDERS} LIMIT {requested}")
    assert result.effective_limit == expected


def test_configured_size_and_projection_boundaries():
    assert validate_sql(f"SELECT business_date FROM {ORDERS}", max_rows=2).effective_limit == 3
    with pytest.raises(BusinessError):
        validate_sql(f"SELECT business_date FROM {ORDERS}", max_sql_chars=10)
    with pytest.raises(BusinessError):
        validate_sql(f"SELECT '{'x' * 513}' AS value FROM {ORDERS}")
    with pytest.raises(BusinessError):
        validate_sql(f"SELECT {', '.join(f'1 AS col_{i}' for i in range(21))} FROM {ORDERS}")
    with pytest.raises(BusinessError):
        validate_sql(f"SELECT {'(' * 600}business_date{')' * 600} FROM {ORDERS}")


@pytest.mark.parametrize(
    "source, expected",
    [
        (None, None),
        (True, True),
        (42, 42),
        (Decimal("9007199254740993.00"), "9007199254740993.00"),
        (Decimal("0.000001"), "0.000001"),
        (date(2026, 10, 12), "2026-10-12"),
        ("product", "product"),
    ],
)
def test_database_json_conversion_preserves_exact_values(source, expected):
    result = _json_value(source)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    "source", [1.5, Decimal("NaN"), Decimal("Infinity"), b"secret", "x" * 513, object()]
)
def test_database_json_conversion_rejects_unsupported_or_oversized_values(source):
    with pytest.raises(ValueError):
        _json_value(source)


@pytest.mark.parametrize(
    "update",
    [
        {"row_count": 2},
        {"rows": [[1, 2]]},
        {"columns": ["total", "total"], "rows": [[1, 2]]},
        {"columns": []},
        {"rows": [[1.25]]},
    ],
)
def test_result_schema_rejects_inconsistent_shapes_and_float_values(update):
    payload = {
        "normalized_sql": f"SELECT COUNT(*) AS total FROM {ORDERS} LIMIT 101",
        "columns": ["total"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "elapsed_ms": 1.0,
    }
    with pytest.raises(ValidationError):
        QueryResult.model_validate(payload | update)


@pytest.mark.parametrize(
    "sql",
    [
        "SET ROLE private_sql_credential_marker",
        "CALL private_sql_credential_marker()",
        "SELECT 'private_sql_credential_marker",
    ],
)
def test_parser_failures_do_not_log_or_print_original_sql(sql, caplog, capsys):
    with caplog.at_level(logging.DEBUG), pytest.raises(BusinessError) as error:
        validate_sql(sql)
    captured = capsys.readouterr()
    assert error.value.code == "analytics_sql_rejected"
    assert "private_sql_credential_marker" not in (
        caplog.text + captured.out + captured.err + error.value.message
    )
    assert error.value.__suppress_context__ is True
