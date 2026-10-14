"""The evaluation oracle must reject coincidentally matching wrong semantics."""

import pytest

from enterprise_agent.evaluations.references import sql_contract

CATALOG = {
    "date_windows": {
        "last_seven_days_including_today": {
            "start_inclusive": "2026-10-09",
            "end_exclusive": "2026-10-16",
        },
        "this_month_to_date": {"start_inclusive": "2026-10-01", "end_exclusive": "2026-10-16"},
        "previous_complete_week": {"start_inclusive": "2026-10-01", "end_exclusive": "2026-10-08"},
        "latest_complete_week": {"start_inclusive": "2026-10-08", "end_exclusive": "2026-10-15"},
    }
}


def contract(reference, sql):
    import sqlglot

    tree = sqlglot.parse_one(sql, read="postgres")
    return sql_contract(
        reference,
        CATALOG,
        {
            "query": {
                "normalized_sql": sql,
                "columns": [node.alias_or_name for node in tree.expressions],
                "truncated": False,
            }
        },
    )


def statuses(result):
    return {item["id"]: item["status"] for item in result[-1]}


def test_sales_alias_is_semantic_and_reference_reads_base_tables():
    result = contract(
        "paid_sales_last_seven_days",
        "SELECT SUM(gross_revenue_cents) AS whatever FROM ea_analytics.daily_orders "
        "WHERE business_date >= DATE '2026-10-09' "
        "AND business_date < DATE '2026-10-16' LIMIT 101",
    )
    assert set(statuses(result).values()) == {"pass"}
    assert result[1] == {"whatever": "sales_cents"}
    assert "public.orders" in result[0] and "ea_analytics" not in result[0]


def test_inclusive_between_matches_date_window():
    result = contract(
        "paid_sales_last_seven_days",
        "SELECT SUM(gross_revenue_cents) AS cents FROM ea_analytics.daily_orders "
        "WHERE business_date BETWEEN DATE '2026-10-09' AND DATE '2026-10-15'",
    )
    assert statuses(result)["date_window"] == "pass"


def test_wrong_literal_window_fails_even_when_rows_might_coincidentally_match():
    result = contract(
        "paid_sales_last_seven_days",
        "SELECT SUM(gross_revenue_cents) AS cents FROM ea_analytics.daily_orders "
        "WHERE business_date >= DATE '2026-10-08' AND business_date < DATE '2026-10-16'",
    )
    assert statuses(result)["date_window"] == "fail"


def test_dynamic_date_arithmetic_requires_review():
    result = contract(
        "paid_sales_last_seven_days",
        "SELECT SUM(gross_revenue_cents) AS cents FROM ea_analytics.daily_orders "
        "WHERE business_date >= CURRENT_DATE - 6 AND business_date < CURRENT_DATE + 1",
    )
    assert statuses(result)["date_window"] == "manual_review"


def test_refund_ratio_uses_order_cohort_not_money():
    good = contract(
        "refund_rate_month_cohort",
        "SELECT ROUND(100.0 * SUM(completed_refund_order_count) / "
        "NULLIF(SUM(paid_order_count), 0), 2) AS pct FROM ea_analytics.daily_orders "
        "WHERE business_date >= DATE '2026-10-01' AND business_date < DATE '2026-10-16'",
    )
    assert statuses(good)["metric_expression"] == "pass"
    bad = contract(
        "refund_rate_month_cohort",
        "SELECT ROUND(100.0 * SUM(completed_refund_cents) / "
        "NULLIF(SUM(gross_revenue_cents), 0), 2) AS pct FROM ea_analytics.daily_orders "
        "WHERE business_date >= DATE '2026-10-01' AND business_date < DATE '2026-10-16'",
    )
    assert statuses(bad)["metric_expression"] != "pass"
    assert "COUNT(DISTINCT r.order_id)" in good[0]


def test_period_case_maps_previous_and_latest_independently():
    result = contract(
        "compare_complete_seven_day_periods",
        "SELECT SUM(CASE WHEN business_date < DATE '2026-10-08' THEN paid_order_count "
        "ELSE 0 END) AS a, SUM(CASE WHEN business_date >= DATE '2026-10-08' "
        "THEN paid_order_count ELSE 0 END) AS b FROM ea_analytics.daily_orders "
        "WHERE business_date >= DATE '2026-10-01' AND business_date < DATE '2026-10-15'",
    )
    assert result[1] == {"a": "previous_orders", "b": "latest_orders"}
    assert set(statuses(result).values()) == {"pass"}


@pytest.mark.parametrize(
    "ending", ["ORDER BY revenue ASC LIMIT 5", "ORDER BY revenue DESC LIMIT 6"]
)
def test_product_rank_direction_and_limit_cannot_be_ignored(ending):
    result = contract(
        "product_sales_top_five",
        "SELECT product_id, product_name, SUM(goods_revenue_cents) AS revenue "
        "FROM ea_analytics.daily_product_sales WHERE business_date >= DATE '2026-10-09' "
        "AND business_date < DATE '2026-10-16' GROUP BY product_id, product_name " + ending,
    )
    assert statuses(result)["ranking_limit"] == "fail"


def test_product_reference_aggregates_independent_item_snapshots():
    result = contract(
        "product_sales_top_five",
        "SELECT product_id, product_name, SUM(goods_revenue_cents) AS revenue "
        "FROM ea_analytics.daily_product_sales WHERE business_date >= DATE '2026-10-09' "
        "AND business_date < DATE '2026-10-16' GROUP BY product_id, product_name "
        "ORDER BY revenue DESC, product_id, product_name LIMIT 5",
    )
    assert set(statuses(result).values()) == {"pass"}
    assert "public.order_items" in result[0] and "i.paid_amount_cents" in result[0]


def test_optional_unprojected_product_id_does_not_create_a_false_numeric_failure():
    result = contract(
        "product_sales_top_five",
        "SELECT product_name, SUM(goods_revenue_cents) AS revenue "
        "FROM ea_analytics.daily_product_sales WHERE business_date >= DATE '2026-10-09' "
        "AND business_date < DATE '2026-10-16' GROUP BY product_id, product_name "
        "ORDER BY revenue DESC, product_id, product_name LIMIT 5",
    )
    assert result[2] == ["sales_cents"]
    assert statuses(result)["result_shape"] == "manual_review"
