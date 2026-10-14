"""Pure live-smoke checker tests, without API, provider or PostgreSQL requests."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from enterprise_agent.analytics.sql import validate_sql
from enterprise_agent.schemas.analytics import QueryResult, SQLPlan
from enterprise_agent.services.analytics import AnalyticsService

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "analytics_live_checks", ROOT / "scripts" / "evaluate-analytics-live.py"
)
CHECKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKS)


def fixture():
    case = {
        "metric_ids": ["paid_sales"],
        "columns": ["sales_cents"],
        "numeric_columns": ["sales_cents"],
    }
    query = QueryResult(
        normalized_sql=(
            "SELECT SUM(gross_revenue_cents) AS sales_cents "
            "FROM ea_analytics.daily_orders LIMIT 100"
        ),
        columns=["sales_cents"],
        rows=[["9007199254740993.00"]],
        row_count=1,
        truncated=False,
        elapsed_ms=1.0,
    )
    analysis = AnalyticsService(None).render(query, case["metric_ids"])
    task = {
        "status": "completed",
        "task_type": "analytics",
        "error_code": None,
        "result": {
            "answer": analysis["answer"],
            "analysis": analysis,
            "approval": None,
            "refund_result": None,
            "facts": [
                {"name": "execute_analytics_sql", "result": copy.deepcopy(analysis["query"])}
            ],
        },
    }
    return case, task, copy.deepcopy(analysis)


def test_checker_compares_ordered_columns_rows_and_exact_large_decimal_values():
    case, task, reference = fixture()
    reference["query"]["rows"] = [[9007199254740993]]
    assert CHECKS.validate(case, task, reference) == (
        True,
        "ordered_columns_and_rows_match_real_reader_reference",
    )
    reference["query"]["rows"] = [[9007199254740992]]
    assert CHECKS.validate(case, task, reference) == (False, "ordered_exact_decimal_rows_mismatch")


@pytest.mark.parametrize(
    "left,right,numeric,expected",
    [
        (None, None, True, True),
        (None, "0", True, False),
        ("0.00", 0, True, True),
        (True, 1, True, False),
        (1.0, "1", True, False),
        ("NaN", "NaN", True, False),
        ("Infinity", "Infinity", True, False),
        ("x", "x", False, True),
        ("1", 1, False, False),
    ],
)
def test_semantic_comparison_preserves_null_types_and_exact_finite_numbers(
    left, right, numeric, expected
):
    assert CHECKS.equal_cell(left, right, numeric) is expected


@pytest.mark.parametrize(
    "kind",
    [
        "columns",
        "metric",
        "row_count",
        "truncated",
        "answer",
        "chart",
        "definitions",
        "facts",
        "approval",
        "refund",
        "route",
        "failed",
    ],
)
def test_checker_refuses_unrelated_cells_wrong_reports_or_write_paths(kind):
    case, task, reference = fixture()
    analysis = task["result"]["analysis"]
    if kind == "columns":
        analysis["query"]["columns"] = ["wrong_column"]
    elif kind == "metric":
        analysis["metric_ids"] = ["order_count"]
    elif kind == "row_count":
        analysis["query"]["row_count"] = 2
    elif kind == "truncated":
        analysis["query"]["truncated"] = True
    elif kind == "answer":
        task["result"]["answer"] = "销售增长因为广告投放"
    elif kind == "chart":
        analysis["chart"]["series"][0]["data"] = [999]
    elif kind == "definitions":
        analysis["metric_definitions"] = {"paid_sales": "凭空声称净销售额"}
    elif kind == "facts":
        task["result"]["facts"][0]["result"]["rows"] = [[1]]
    elif kind == "approval":
        task["result"]["approval"] = {"id": "unsafe"}
    elif kind == "refund":
        task["result"]["refund_result"] = {"id": "unsafe"}
    elif kind == "route":
        task["task_type"] = "order"
    else:
        task.update(status="failed", error_code="analytics_sql_rejected")
    assert CHECKS.validate(case, task, reference)[0] is False


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8000",
        "http://example.com",
        "http://user:private@localhost:8000",
        "http://@localhost:8000",
        "http://localhost:8000/?token=private",
        "http://localhost#private",
        "http://localhost/path",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:invalid",
        " http://localhost:8000",
        "http://localhost:8000?",
        "http://localhost:8000#",
    ],
)
def test_loopback_guard_rejects_remote_origins_credentials_paths_or_query_secrets(url):
    with pytest.raises(ValueError) as caught:
        CHECKS.loopback_origin(url)
    assert str(caught.value) == "Only a plain loopback HTTP origin is accepted"
    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "http://127.0.0.1:8000/", "http://localhost"]
)
def test_plain_local_origin_is_allowed(url):
    assert CHECKS.loopback_origin(url) == url.rstrip("/")


def test_token_ledger_keeps_known_tokens_and_unknown_consumption_separate():
    complete = {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "model_calls": 2,
        "unknown_usage_calls": 0,
        "usage_complete": True,
    }
    records = [
        {"executed": True, "task": {"token_usage": complete}},
        {
            "executed": True,
            "task": {
                "token_usage": {**complete, "unknown_usage_calls": 1, "usage_complete": False}
            },
        },
        {"executed": True, "task": None},
        {"executed": False, "task": None},
    ]
    result = CHECKS.ledger(records)
    assert result["known_provider_tokens"] == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
    }
    assert result["known_model_call_attempts"] == 4
    assert result["tasks_with_unknown_or_incomplete_consumption"] == 2
    assert result["consumption_complete"] is False


def test_dataset_has_four_schema_guided_contracts_and_parseable_readonly_references():
    dataset = json.loads(CHECKS.DATASET.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    bounds = CHECKS.windows({"today": "2026-10-14", "business_timezone": "Asia/Shanghai"})
    assert len(cases) == 4 and len({case["id"] for case in cases}) == 4
    assert "not_blind" in dataset["scope"]
    for case in cases:
        assert set(case) == {
            "id",
            "message",
            "metric_ids",
            "columns",
            "numeric_columns",
            "reference_sql",
        }
        assert set(case["numeric_columns"]).issubset(case["columns"])
        plan = SQLPlan(sql=case["reference_sql"].format(**bounds), metric_ids=case["metric_ids"])
        assert validate_sql(plan.sql).view_name in {
            "ea_analytics.daily_orders",
            "ea_analytics.daily_product_sales",
        }


@pytest.mark.parametrize("payload", [None, [], {"result": "invalid"}, {"result": []}])
def test_malformed_task_dto_fails_safely(payload):
    case, _, reference = fixture()
    assert CHECKS.validate(case, payload, reference) == (False, "invalid_task_shape")


def test_live_opt_in_is_required_before_any_api_request(monkeypatch):
    monkeypatch.setattr("sys.argv", ["evaluate-analytics-live.py"])
    monkeypatch.setattr(
        CHECKS.httpx, "Client", lambda **kwargs: pytest.fail("No client before opt-in")
    )
    with pytest.raises(SystemExit) as caught:
        CHECKS.main()
    assert caught.value.code == 2
