"""Money, dates and chart values are obtained from rows, never model narration."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole
from enterprise_agent.schemas.analytics import QueryResult
from enterprise_agent.security import Principal
from enterprise_agent.services.analytics import AnalyticsService


def result(columns, rows, *, truncated=False):
    return QueryResult(
        normalized_sql="SELECT gross_revenue_cents FROM ea_analytics.daily_orders LIMIT 101",
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=1.5,
    )


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.SUPPORT])
async def test_non_operators_are_denied_before_database_or_catalog(role):
    runtime = AsyncMock()
    runtime.verified = True
    service = AnalyticsService(runtime)
    actor = Principal(1, role)
    with pytest.raises(BusinessError) as error:
        await service.execute(actor, "SELECT gross_revenue_cents FROM ea_analytics.daily_orders")
    assert error.value.status_code == 403
    runtime.execute.assert_not_awaited()
    with pytest.raises(BusinessError):
        service.catalog(actor)


def test_catalog_uses_shanghai_calendar_and_explicit_inclusive_exclusive_windows():
    service = AnalyticsService(None, clock=lambda: datetime(2026, 10, 12, 18, tzinfo=UTC))
    catalog = service.catalog(Principal(1, UserRole.ANALYST))
    assert catalog["today"] == "2026-10-13"
    assert catalog["date_windows"]["last_seven_days_including_today"] == {
        "start_inclusive": "2026-10-07",
        "end_exclusive": "2026-10-14",
    }
    assert catalog["date_windows"]["latest_complete_week"]["end_exclusive"] == "2026-10-13"
    catalog["views"]["daily_orders"].clear()
    assert service.catalog(Principal(1, UserRole.ADMIN))["views"]["daily_orders"]


def test_exact_large_money_and_chart_values_do_not_pass_through_float():
    exact = "9007199254740993.00"
    report = AnalyticsService(None).render(
        result(["gross_revenue_cents"], [[exact]]), ["paid_sales"]
    )
    assert exact in report["answer"]
    assert report["chart"]["series"][0]["data"] == [exact]
    assert report["chart"]["source"] == "query_rows"
    assert "不扣退款" in report["answer"] and "不能据此推断" in report["answer"]


def test_dates_select_line_chart_and_nulls_stay_null_in_data():
    report = AnalyticsService(None).render(
        result(["business_date", "paid_order_count"], [["2026-10-11", 5], ["2026-10-12", None]]),
        ["order_count"],
    )
    assert report["chart"]["type"] == "line"
    assert report["chart"]["labels"] == ["2026-10-11", "2026-10-12"]
    assert report["chart"]["series"][0]["data"] == [5, None]


def test_wide_numeric_result_keeps_every_measure_as_a_series():
    report = AnalyticsService(None).render(
        result(["goods_cents", "shipping_cents"], [["12000", "800"]]), ["paid_sales"]
    )
    assert report["chart"]["labels"] == ["1"]
    assert [(series["name"], series["data"]) for series in report["chart"]["series"]] == [
        ("goods_cents", ["12000"]),
        ("shipping_cents", ["800"]),
    ]


def test_numeric_identifier_is_a_dimension_and_not_a_measure():
    report = AnalyticsService(None).render(
        result(["product_id", "product_name", "sales_cents"], [[1, "模拟商品", "12000"]]),
        ["product_sales"],
    )
    assert report["chart"]["labels"] == ["1"]
    assert report["chart"]["series"] == [
        {"name": "sales_cents", "column_index": 2, "data": ["12000"]}
    ]


def test_text_dimension_after_a_measure_does_not_drop_the_measure():
    report = AnalyticsService(None).render(
        result(["order_count", "period"], [[5, "上周"], [8, "本周"]]), ["order_count"]
    )
    assert report["chart"]["labels"] == ["上周", "本周"]
    assert report["chart"]["series"] == [{"name": "order_count", "column_index": 0, "data": [5, 8]}]


def test_empty_result_and_all_null_numeric_columns_have_no_invented_chart():
    service = AnalyticsService(None)
    assert service.render(result(["total"], []), ["paid_sales"])["chart"] is None
    null = service.render(result(["refund_rate_percent"], [[None]]), ["refund_rate"])
    assert null["chart"] is None and "NULL" in null["answer"]


def test_truncation_is_explicit_and_markdown_preview_does_not_change_query_rows():
    report = AnalyticsService(None).render(
        result(
            ["product_name", "goods_revenue_cents"],
            [["商品|A\nB", index] for index in range(25)],
            truncated=True,
        ),
        ["product_sales"],
    )
    assert "截断" in report["answer"] and "前 20 行" in report["answer"]
    assert "商品\\|A B" in report["answer"]
    assert len(report["query"]["rows"]) == 25
    assert report["chart"]["series"][0]["data"] == list(range(25))


async def test_unavailable_reader_fails_before_query():
    with pytest.raises(BusinessError) as error:
        await AnalyticsService(None).execute(Principal(1, UserRole.ANALYST), "SELECT 1")
    assert error.value.code == "analytics_unavailable"
