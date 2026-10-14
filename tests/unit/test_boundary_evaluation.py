"""Meaningful report/schema regressions for controlled graph boundary evaluation."""

import csv
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.evaluations.boundaries import (
    run_boundary_evaluation,
    write_boundary_artifacts,
)

_DATASET = PROJECT_ROOT / "evals" / "datasets" / "agent-boundaries.json"


@pytest.fixture
async def boundary_report():
    return await run_boundary_evaluation(_DATASET)


async def test_real_graph_executes_every_declared_boundary_and_keeps_provider_usage_separate(
    boundary_report,
):
    assert boundary_report["evaluation_kind"] == "controlled_fault_injection"
    assert boundary_report["summary"]["cases"] == 16
    assert boundary_report["summary"]["passed"] == 16
    assert boundary_report["summary"]["failed"] == 0
    assert boundary_report["provider_usage"] is None
    assert boundary_report["provider_usage_status"] == "not_applicable_no_provider_requests"
    assert all(case["elapsed_ms"] > 0 for case in boundary_report["cases"])
    assert all(case["checks"]["trusted_actor_preserved"] for case in boundary_report["cases"])


async def test_unknown_fixture_consumption_is_retained_instead_of_called_zero_tokens(
    boundary_report,
):
    cases = {case["id"]: case for case in boundary_report["cases"]}
    exhausted = cases["retry_budget_exhausted"]["fixture_usage"]
    assert exhausted["unknown_usage_calls"] == 3
    assert exhausted["model_calls"] == 3
    assert exhausted["total_tokens"] == 0  # Known subtotal only; all three usages are unknown.
    recovered = cases["retryable_recovers_with_usage"]["fixture_usage"]
    assert recovered["total_tokens"] == 11
    assert recovered["unknown_usage_calls"] == 1
    assert cases["local_timeout_usage_unknown"]["checks"]["fixture_timeout_released"]
    for key, value in boundary_report["fixture_usage"].items():
        assert value == sum(case["fixture_usage"][key] for case in boundary_report["cases"])


async def test_interrupted_write_is_zero_before_resume_and_replay_never_hides_a_duplicate_effect(
    boundary_report,
):
    cases = {case["id"]: case for case in boundary_report["cases"]}
    for case in boundary_report["cases"]:
        if case["observed"]["paused_before_commit"]:
            assert case["checkpoint_next_before_resume"] == ["human_approval"]
            assert case["pre_resume_fixture_refund_writes"] == 0
    assert cases["human_rejection_no_refund_write"]["observed"]["refund_writes"] == 0
    assert cases["human_confirmation_replay_once"]["observed"]["refund_writes"] == 1
    assert cases["human_confirmation_replay_once"]["observed"]["confirmation_calls"] == 1
    assert cases["mismatched_approval_rejected"]["observed"]["confirmation_calls"] == 0


async def test_wrong_expected_outcome_is_recorded_as_failure_and_remains_in_all_artifacts(tmp_path):
    source = json.loads(_DATASET.read_text(encoding="utf-8"))
    source["cases"] = [source["cases"][0]]
    source["cases"][0]["expected"]["refund_writes"] = 1
    dataset = tmp_path / "wrong-expectation.json"
    dataset.write_text(json.dumps(source), encoding="utf-8")
    report = await run_boundary_evaluation(dataset)
    assert report["summary"]["failed"] == 1
    assert report["cases"][0]["failed_checks"] == ["refund_writes"]
    files = write_boundary_artifacts(report, tmp_path / "artifacts")
    restored = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
    assert restored == report
    with Path(files["csv"]).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["passed"] == "False"
    assert rows[0]["failed_checks"] == "refund_writes"
    markdown = Path(files["markdown"]).read_text(encoding="utf-8")
    assert "failed: 1" in markdown and "refund_writes" in markdown
    assert "not applicable" in markdown and "not model task completion" in markdown


@pytest.mark.parametrize("change", ["duplicate_id", "undeclared_field", "unsafe_retry_budget"])
async def test_malformed_datasets_fail_before_any_graph_request(tmp_path, change):
    source = json.loads(_DATASET.read_text(encoding="utf-8"))
    if change == "duplicate_id":
        source["cases"][1]["id"] = source["cases"][0]["id"]
    elif change == "undeclared_field":
        source["cases"][0]["api_key"] = "must-not-be-accepted"
    else:
        source["cases"][0]["settings"] = {"agent_max_retries": 100}
    dataset = tmp_path / "invalid-dataset.json"
    dataset.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValidationError):
        await run_boundary_evaluation(dataset)


async def test_unexpected_extra_model_request_is_a_visible_runner_failure(tmp_path):
    source = json.loads(_DATASET.read_text(encoding="utf-8"))
    source["cases"] = [source["cases"][0]]
    source["cases"][0]["events"].pop()
    dataset = tmp_path / "exhausted-script.json"
    dataset.write_text(json.dumps(source), encoding="utf-8")
    report = await run_boundary_evaluation(dataset)
    assert report["summary"]["failed"] == 1
    assert report["cases"][0]["runner_error"] == "AssertionError"
    assert "runner_completed" in report["cases"][0]["failed_checks"]


async def test_oversized_dataset_is_rejected(tmp_path):
    dataset = tmp_path / "too-large.json"
    dataset.write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="size limit"):
        await run_boundary_evaluation(dataset)
