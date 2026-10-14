"""Deliberately bad observations must fail; these fixtures are not live results."""

import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from enterprise_agent.evaluations.checks import (
    EvaluationEvidence,
    equal_cell,
    evaluate_case,
)
from enterprise_agent.evaluations.dataset import (
    EvaluationDataset,
    load_dataset,
    render_message,
    render_template,
)
from enterprise_agent.schemas.analytics import QueryResult
from enterprise_agent.services.analytics import AnalyticsService

DATASET_PATH = Path(__file__).resolve().parents[2] / "evals/datasets/agent-evaluation.json"
DATASET = load_dataset(DATASET_PATH)


def case(identifier):
    return next(item for item in DATASET.cases if item.id == identifier)


def task(*, route="order", status="completed", facts=None):
    return {
        "id": str(uuid4()),
        "session_id": str(uuid4()),
        "task_type": route,
        "status": status,
        "message": "Controlled unit-test observation, not a provider response",
        "result": {
            "answer": "",
            "facts": facts or [],
            "citations": [],
            "approval": None,
            "refund_result": None,
            "analysis": None,
        },
        "steps": [],
        "token_usage": {},
        "error_code": None,
        "elapsed_ms": 1,
        "created_at": "2026-10-15T01:00:00Z",
        "updated_at": "2026-10-15T01:00:01Z",
        "completed_at": "2026-10-15T01:00:01Z",
    }


def evidence():
    snapshot = {"refund_ids": [], "ticket_ids": [], "order_statuses": {"10086": "delivered"}}
    return {
        "bindings": {
            "own_order_no": "10086",
            "foreign_order_no": "10087",
            "refundable_order_no": "10088",
        },
        "business_before": copy.deepcopy(snapshot),
        "business_after": copy.deepcopy(snapshot),
    }


def logistics_fixture():
    result = {"order_no": "10086", "tracking_no": "SIM10086", "status": "delivered", "events": []}
    observed = task(
        facts=[{"name": "get_logistics", "arguments": {"order_no": "10086"}, "result": result}]
    )
    observed["result"]["answer"] = "订单 10086：物流 delivered，运单 SIM10086。"
    collected = evidence()
    collected["facts_reference"] = {"get_logistics": copy.deepcopy(result)}
    collected["tool_calls"] = [
        {"name": "get_logistics", "arguments": {"order_no": "10086"}, "origin": "model"}
    ]
    return case("dev_own_logistics"), observed, collected


def citation():
    chunk = str(uuid4())
    return {
        "chunk_id": chunk,
        "document_id": str(uuid4()),
        "document_title": "退款与退货规则",
        "document_version": 1,
        "index_generation": "generation-fixture",
        "text": "已签收订单须在签收后 7 日内申请；系统以签收时间起连续 7 × 24 小时为期限。",
        "citation_url": "/api/v1/knowledge/citations/" + chunk,
    }


def knowledge_fixture():
    source = citation()
    observed = task(
        route="knowledge",
        facts=[{"name": "search_knowledge", "result": {"status": "evidence_found"}}],
    )
    observed["result"]["citations"] = [source]
    observed["result"]["answer"] = (
        "根据企业资料：\n- 已签收订单须在签收后 7 日内申请；"
        f" [来源：{source['document_title']}]({source['citation_url']})"
    )
    collected = evidence()
    collected["citations"] = {source["chunk_id"]: copy.deepcopy(source)}
    return case("dev_return_window"), observed, collected


def refund_fixture():
    source = citation()
    policy = {
        "id": 1,
        "policy_code": "STANDARD_RETURN",
        "version": 1,
        "document_id": source["document_id"],
        "document_title": source["document_title"],
        "document_version": 1,
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_until": None,
        "refund_window_days": 7,
        "refund_shipping": False,
        "allow_before_delivery": True,
        "excluded_categories": ["food"],
    }
    eligibility = {
        "order_no": "10088",
        "eligible": True,
        "code": "eligible_pending_staff_review",
        "message": "Unit-test eligible fixture",
        "amount_cents": 19900,
        "policy": policy,
    }
    reference = {
        "eligibility": eligibility,
        "knowledge": {"status": "evidence_found", "citations": [source]},
    }
    arguments = {"order_no": "10088", "reason": "change_mind", "notes": ""}
    observed = task(
        status="waiting_approval",
        facts=[{"name": "get_refund_policy", "arguments": arguments, "result": reference}],
    )
    observed["completed_at"] = None
    observed["steps"] = [{"node": "business_tool", "tool": "create_refund_request"}]
    observed["result"]["citations"] = [source]
    observed["result"]["approval"] = {
        "approval_id": str(uuid4()),
        "order_no": "10088",
        "status": "pending",
        "reason": "change_mind",
        "notes": "",
        "amount_cents": 19900,
        "expires_at": "2026-10-15T01:15:00Z",
        "policy": policy,
        "requires_confirmation": True,
    }
    collected = evidence()
    collected["citations"] = {source["chunk_id"]: copy.deepcopy(source)}
    collected["facts_reference"] = {"get_refund_policy": copy.deepcopy(reference)}
    collected["tool_calls"] = [
        {"name": "create_refund_request", "arguments": arguments, "origin": "model"}
    ]
    return case("validation_simple_refund_preview"), observed, collected


def analytics_fixture():
    selected = case("dev_sales_seven_days")
    query = QueryResult(
        normalized_sql=(
            "SELECT SUM(gross_revenue_cents) AS total_money "
            "FROM ea_analytics.daily_orders LIMIT 100"
        ),
        columns=["total_money"],
        rows=[["9007199254740993.00"]],
        row_count=1,
        truncated=False,
        elapsed_ms=1.0,
    )
    report = AnalyticsService(None).render(query, ["paid_sales"])
    observed = task(
        route="analytics",
        facts=[{"name": "execute_analytics_sql", "result": query.model_dump(mode="json")}],
    )
    observed["result"]["answer"] = report["answer"]
    observed["result"]["analysis"] = report
    collected = evidence()
    reference = query.model_dump(mode="json")
    reference["columns"] = ["sales_cents"]
    reference["rows"] = [[9007199254740993]]
    collected["sql_reference"] = {
        "reference_id": selected.expectations.sql.reference_id,
        "query": reference,
        "actual_column_roles": {"total_money": "sales_cents"},
        "reference_column_roles": {"sales_cents": "sales_cents"},
        "numeric_roles": ["sales_cents"],
        "ordered": False,
        "ast_checks": [
            {"id": identifier, "status": "pass", "detail": "Controlled AST fixture"}
            for identifier in selected.expectations.sql.required_ast_checks
        ],
    }
    return selected, observed, collected


def check_status(report, identifier):
    return next(check.status for check in report.checks if check.id == identifier)


def test_dataset_has_frozen_splits_and_explicit_non_blind_provenance():
    assert len(DATASET.cases) == 18
    assert {
        split: sum(item.split == split for item in DATASET.cases)
        for split in ("development", "regression", "validation")
    } == {
        "development": 3,
        "regression": 2,
        "validation": 13,
    }
    assert DATASET.source == "coding_assistant_curated_synthetic_project_cases"
    assert case("regression_complex_refund_preview").split == "regression"
    assert all("ea_analytics" not in item.message_template for item in DATASET.cases)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "duplicate",
        "missing_split",
        "known_failure_validation",
        "sql_checks",
        "refund_status",
    ],
)
def test_dataset_rejects_silent_contract_drift(mutation):
    raw = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if mutation == "extra":
        raw["fake_quality_score"] = 1.0
    elif mutation == "duplicate":
        raw["cases"][1]["id"] = raw["cases"][0]["id"]
    elif mutation == "missing_split":
        raw["cases"] = [item for item in raw["cases"] if item["split"] != "validation"]
    elif mutation == "known_failure_validation":
        raw["cases"][4]["split"] = "validation"
    elif mutation == "sql_checks":
        raw["cases"][2]["expectations"]["sql"]["required_ast_checks"] = ["date_window"]
    else:
        raw["cases"][4]["expectations"]["statuses"] = ["completed"]
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(raw)


@pytest.mark.parametrize(
    "template",
    [
        "{own_order_no.__class__}",
        "{own_order_no[0]}",
        "{own_order_no!r}",
        "{own_order_no:>9}",
        "{database_url}",
        "{own_order_no",
    ],
)
def test_template_has_no_traversal_conversion_or_arbitrary_environment_bindings(template):
    with pytest.raises(ValueError):
        render_template(template, {"own_order_no": "10086"})


@pytest.mark.parametrize(
    "binding", [10086, "10086;DROP TABLE orders", " 10086", "1234", "０１００８６"]
)
def test_order_binding_requires_canonical_digits(binding):
    with pytest.raises(ValueError):
        render_message(case("dev_own_logistics"), {"own_order_no": binding})


def test_separately_observed_order_fact_and_actual_model_arguments_can_pass():
    selected, observed, collected = logistics_fixture()
    assert evaluate_case(selected, observed, collected).outcome == "pass"


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_order",
        "spoof_actor",
        "fact_mismatch",
        "wrong_intent",
        "refund_write",
        "ticket_write",
        "order_state",
        "forbidden_tool",
        "approval",
        "answer_claim",
    ],
)
def test_business_checker_catches_wrong_facts_authority_and_unconfirmed_effects(mutation):
    selected, observed, collected = logistics_fixture()
    if mutation == "wrong_order":
        collected["tool_calls"][0]["arguments"]["order_no"] = "10087"
    elif mutation == "spoof_actor":
        collected["tool_calls"][0]["arguments"]["user_id"] = 99
    elif mutation == "fact_mismatch":
        observed["result"]["facts"][0]["result"]["status"] = "invented_delivery"
    elif mutation == "wrong_intent":
        observed["task_type"] = "unsupported"
    elif mutation == "refund_write":
        collected["business_after"]["refund_ids"] = [str(uuid4())]
    elif mutation == "ticket_write":
        collected["business_after"]["ticket_ids"] = [str(uuid4())]
    elif mutation == "order_state":
        collected["business_after"]["order_statuses"]["10086"] = "refunded_simulated"
    elif mutation == "forbidden_tool":
        observed["steps"] = [{"node": "business_tool", "tool": "create_refund_request"}]
    elif mutation == "approval":
        observed["result"]["approval"] = {"approval_id": str(uuid4())}
    else:
        observed["result"]["answer"] += "已为您退款到账。"
    assert evaluate_case(selected, observed, collected).outcome == "fail"


def test_public_projection_cannot_prove_original_model_argument_schema():
    selected, observed, collected = logistics_fixture()
    collected["tool_calls"][0]["origin"] = "public_projection"
    result = evaluate_case(selected, observed, collected)
    assert result.outcome == "manual_review"
    assert check_status(result, "tool_model_argument_schema") == "manual_review"


def test_missing_business_snapshots_remain_unverified():
    selected, observed, collected = logistics_fixture()
    del collected["business_after"]
    result = evaluate_case(selected, observed, collected)
    assert result.outcome == "manual_review"


def test_denied_foreign_order_with_leaked_result_cannot_pass():
    selected, observed, collected = logistics_fixture()
    observed["status"] = "failed"
    observed["error_code"] = "order_not_found"
    assert evaluate_case(case("regression_foreign_order"), observed, collected).outcome == "fail"


def test_continuous_quote_is_grounded_but_not_automatic_semantic_success():
    selected, observed, collected = knowledge_fixture()
    result = evaluate_case(selected, observed, collected)
    assert check_status(result, "continuous_original_quotes") == "pass"
    assert check_status(result, "quote_answers_question") == "manual_review"
    assert result.outcome == "manual_review"


@pytest.mark.parametrize(
    "mutation",
    [
        "quote",
        "title",
        "version",
        "generation",
        "url",
        "extra_claim",
        "wrong_document",
        "missing_citation",
    ],
)
def test_citation_checker_rejects_fabricated_stale_or_unbound_quotes(mutation):
    selected, observed, collected = knowledge_fixture()
    source = observed["result"]["citations"][0]
    if mutation == "quote":
        observed["result"]["answer"] = observed["result"]["answer"].replace("7 日", "70 日")
    elif mutation == "title":
        collected["citations"][source["chunk_id"]]["document_title"] = "Other document"
    elif mutation == "version":
        collected["citations"][source["chunk_id"]]["document_version"] = 2
    elif mutation == "generation":
        collected["citations"][source["chunk_id"]]["index_generation"] = "new-generation"
    elif mutation == "url":
        source["citation_url"] = "https://example.invalid/citation"
    elif mutation == "extra_claim":
        observed["result"]["answer"] += "\n系统保证所有客户获得十倍赔偿。"
    elif mutation == "wrong_document":
        source["document_title"] = "会员权益"
    else:
        collected["citations"] = {}
    assert evaluate_case(selected, observed, collected).outcome == "fail"


def test_refusal_keyword_is_not_a_valid_insufficient_evidence_answer():
    observed = task(route="knowledge", facts=[{"name": "search_knowledge", "result": {}}])
    observed["result"]["answer"] = "无法确认，但我觉得所有订单都有十年免费维修。"
    assert evaluate_case(case("validation_missing_policy"), observed, evidence()).outcome == "fail"


def test_refund_preview_uses_real_reference_amount_and_waits_for_a_human():
    selected, observed, collected = refund_fixture()
    assert evaluate_case(selected, observed, collected).outcome == "pass"


def test_policy_fact_comparison_excludes_nondeterministic_retrieval_metadata():
    selected, observed, collected = refund_fixture()
    selected = case("validation_refund_eligibility")
    observed["status"] = "completed"
    observed["result"]["approval"] = None
    observed["steps"] = []
    observed["result"]["answer"] = "订单 10088：Unit-test eligible fixture 申请金额 199.00 元。"
    collected["tool_calls"] = [
        {
            "name": "get_refund_policy",
            "arguments": {"order_no": "10088", "reason": "change_mind"},
            "origin": "model",
        }
    ]
    collected["facts_reference"]["get_refund_policy"]["knowledge"] = None
    observed["result"]["facts"][0]["result"]["knowledge"]["elapsed_ms"] = 9.5
    assert evaluate_case(selected, observed, collected).outcome == "pass"
    collected["facts_reference"]["get_refund_policy"]["eligibility"]["amount_cents"] -= 1
    assert evaluate_case(selected, observed, collected).outcome == "fail"


@pytest.mark.parametrize(
    "mutation", ["amount", "order", "policy", "status", "confirmation", "reason", "commit"]
)
def test_refund_checker_rejects_changed_preview_and_early_commit(mutation):
    selected, observed, collected = refund_fixture()
    approval = observed["result"]["approval"]
    if mutation == "amount":
        approval["amount_cents"] += 1
    elif mutation == "order":
        approval["order_no"] = "10087"
    elif mutation == "policy":
        approval["policy"]["version"] += 1
    elif mutation == "status":
        observed["status"] = "completed"
    elif mutation == "confirmation":
        approval["requires_confirmation"] = False
    elif mutation == "reason":
        approval["reason"] = "quality_issue"
    else:
        observed["result"]["refund_result"] = {"refund": {"id": str(uuid4())}}
    assert evaluate_case(selected, observed, collected).outcome == "fail"


def test_exact_decimal_sql_rows_accept_alias_mapping_without_schema_hints():
    selected, observed, collected = analytics_fixture()
    assert evaluate_case(selected, observed, collected).outcome == "pass"


@pytest.mark.parametrize(
    "mutation",
    [
        "value",
        "null",
        "nan",
        "metric",
        "date",
        "expression",
        "chart",
        "fact",
        "duplicate_ast",
        "truncation",
    ],
)
def test_sql_checker_detects_semantic_and_structural_errors_even_on_equal_empty_data(mutation):
    selected, observed, collected = analytics_fixture()
    report = observed["result"]["analysis"]
    if mutation == "value":
        collected["sql_reference"]["query"]["rows"] = [[9007199254740992]]
    elif mutation == "null":
        collected["sql_reference"]["query"]["rows"] = [[None]]
    elif mutation == "nan":
        report["query"]["rows"] = [["NaN"]]
    elif mutation == "metric":
        report["metric_ids"] = ["order_count"]
    elif mutation in ("date", "expression"):
        identifier = "date_window" if mutation == "date" else "metric_expression"
        next(
            check for check in collected["sql_reference"]["ast_checks"] if check["id"] == identifier
        )["status"] = "fail"
    elif mutation == "chart":
        report["chart"]["series"][0]["data"] = [1]
    elif mutation == "fact":
        observed["result"]["facts"][0]["result"]["rows"] = [[1]]
    elif mutation == "duplicate_ast":
        collected["sql_reference"]["ast_checks"].append(
            copy.deepcopy(collected["sql_reference"]["ast_checks"][0])
        )
    else:
        report["query"]["truncated"] = True
    assert evaluate_case(selected, observed, collected).outcome == "fail"


def test_missing_sql_ast_or_column_mapping_stays_manual_review():
    selected, observed, collected = analytics_fixture()
    collected["sql_reference"]["ast_checks"] = []
    collected["sql_reference"]["actual_column_roles"] = {}
    result = evaluate_case(selected, observed, collected)
    assert result.outcome == "manual_review"
    assert check_status(result, "sql_rows") == "manual_review"
    assert check_status(result, "sql_ast_date_window") == "manual_review"


def test_unknown_evidence_fields_are_not_silently_accepted():
    with pytest.raises(ValidationError):
        EvaluationEvidence.model_validate({"assume_success": True})


@pytest.mark.parametrize(
    "left,right,numeric,expected",
    [
        ("9007199254740993.00", 9007199254740993, True, True),
        ("9007199254740993", 9007199254740992, True, False),
        (None, None, True, True),
        (None, 0, True, False),
        (True, 1, True, False),
        (1.0, "1", True, False),
        ("NaN", "NaN", True, False),
        ("Infinity", "Infinity", True, False),
        ("1", 1, False, False),
        ("x", "x", False, True),
    ],
)
def test_numeric_comparison_preserves_nulls_types_and_large_values(left, right, numeric, expected):
    assert equal_cell(left, right, numeric=numeric) is expected
