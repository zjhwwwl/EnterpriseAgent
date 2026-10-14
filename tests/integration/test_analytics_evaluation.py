"""Generate a truthful comparison report from a fixed 500-order PostgreSQL fixture."""

import json

import pytest

from enterprise_agent.analytics.evaluation import DEFAULT_DATASET, evaluate
from enterprise_agent.config import PROJECT_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


async def test_real_sql_evaluation_compares_base_tables_and_records_actual_results(
    analytics_runtime, pg_admin_factory, pg_seed
):
    output = PROJECT_ROOT / "var" / "test-results" / "phase6-sql-evaluation"
    result = await evaluate(analytics_runtime, pg_admin_factory, DEFAULT_DATASET, output)
    assert result["source"] == "human_authored_sql_fixtures"
    assert result["as_of"] == "2026-10-12" and result["timezone"] == "Asia/Shanghai"
    assert result["data"]["database"] == "enterprise_agent_test"
    assert result["data"]["table_counts"]["orders"] == 500
    assert len(result["data"]["data_fingerprint_sha256"]) == 64
    assert result["data_stable"] is True
    assert result["summary"] == {
        "total": 24,
        "passed": 24,
        "failed": 0,
        "correct_query_total": 16,
        "correct_query_passed": 16,
        "rejection_total": 8,
        "rejection_passed": 8,
        "reference_match_rate": 1.0,
        "rejection_rate": 1.0,
    }
    assert result["model_calls"] == 0 and result["provider_token_usage"] is None
    for case in result["cases"]:
        assert case["elapsed_ms"] > 0
        if case["kind"] == "reference_comparison":
            assert "public.orders" in case["reference_sql"]
            assert "ea_analytics" not in case["reference_sql"]
            assert case["actual"]["rows"] == case["expected"]["rows"]
            assert "LIMIT" in case["actual"]["normalized_sql"]
    assert json.loads((output / "results.json").read_text(encoding="utf-8")) == result
    assert "不代表自然语言转 SQL 正确率" in (output / "report.md").read_text(encoding="utf-8")
