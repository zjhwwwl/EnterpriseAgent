"""Pure checks over collected evidence; missing evidence never implies success.

These checks do not make HTTP/model requests or access a database. Structural
grounding, permissions and exact query values are machine-checkable. Whether a
quote actually answers the human question needs recorded human review.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, ValidationError

from enterprise_agent.agents.graph import fact_report
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.evaluations.dataset import EvaluationCase, StrictContract, render_template
from enterprise_agent.schemas.agent import TaskResponse
from enterprise_agent.schemas.analytics import QueryResult
from enterprise_agent.schemas.analytics_report import AnalyticsReport
from enterprise_agent.schemas.business import ApprovalResponse
from enterprise_agent.services.analytics import AnalyticsService
from enterprise_agent.tools.business import BusinessTools


class CheckResult(StrictContract):
    id: str = Field(min_length=1, max_length=100)
    status: Literal["pass", "fail", "manual_review"]
    detail: str = Field(min_length=1, max_length=500)


class BusinessSnapshot(StrictContract):
    refund_ids: list[str]
    ticket_ids: list[str]
    order_statuses: dict[str, str]


class ToolObservation(StrictContract):
    name: str
    arguments: dict[str, Any]
    origin: Literal["model", "workflow", "public_projection"]


class SQLReference(StrictContract):
    reference_id: str
    query: QueryResult
    actual_column_roles: dict[str, str]
    reference_column_roles: dict[str, str]
    numeric_roles: list[str]
    ordered: bool
    ast_checks: list[CheckResult]


class EvaluationEvidence(StrictContract):
    bindings: dict[str, str] = Field(default_factory=dict)
    citations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    business_before: BusinessSnapshot | None = None
    business_after: BusinessSnapshot | None = None
    facts_reference: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolObservation] = Field(default_factory=list)
    sql_reference: SQLReference | None = None
    semantic_reviews: dict[str, CheckResult] = Field(default_factory=dict)


class CaseEvaluation(StrictContract):
    case_id: str
    split: str
    outcome: Literal["pass", "fail", "manual_review"]
    checks: list[CheckResult]


def equal_cell(actual: Any, expected: Any, *, numeric: bool) -> bool:
    """Preserve NULL and exact finite Decimal values, including integers > 2**53."""
    if actual is None or expected is None:
        return actual is None and expected is None
    if type(actual) is bool or type(expected) is bool:
        return False
    if not numeric:
        return type(actual) is type(expected) and actual == expected
    if type(actual) not in (int, str) or type(expected) not in (int, str):
        return False
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected))
    except InvalidOperation:
        return False
    return left.is_finite() and right.is_finite() and left == right


def _normalized_rows(query: QueryResult, roles: dict[str, str], numeric_roles: list[str]):
    if set(roles) != set(query.columns) or len(set(roles.values())) != len(roles):
        raise ValueError("Column roles must bijectively cover actual query columns")
    ordered_roles = sorted(roles.values())
    if not set(numeric_roles).issubset(ordered_roles):
        raise ValueError("Numeric role missing from result")
    indices = [
        query.columns.index(next(key for key in roles if roles[key] == role))
        for role in ordered_roles
    ]
    values = []
    for row in query.rows:
        normalized = []
        for role, index in zip(ordered_roles, indices, strict=True):
            cell = row[index]
            if not equal_cell(cell, cell, numeric=role in numeric_roles):
                raise ValueError("Invalid exact value")
            if cell is None:
                normalized.append(("null", ""))
            elif role in numeric_roles:
                normalized.append(("numeric", Decimal(str(cell))))
            else:
                normalized.append((type(cell).__name__, cell))
        values.append(tuple(normalized))
    return ordered_roles, values


def _sql_checks(case, task, evidence, add):
    expectation = case.expectations.sql
    reference = evidence.sql_reference
    if reference is None:
        add("sql_reference", "manual_review", "No independently executed reader reference")
        return
    add(
        "sql_reference_id",
        reference.reference_id == expectation.reference_id,
        "Reference identifier must match the frozen case",
    )
    try:
        report = AnalyticsReport.model_validate(task["result"].get("analysis"))
    except ValidationError:
        add("analysis_schema", False, "Missing or invalid analytics report")
        return
    add(
        "metric_ids",
        report.metric_ids == expectation.metric_ids,
        "Metric identities must match the question contract",
    )
    try:
        mapping_complete = set(reference.actual_column_roles) == set(report.query.columns) and set(
            reference.reference_column_roles
        ) == set(reference.query.columns)
        if not mapping_complete:
            raise LookupError
        actual_roles, actual_rows = _normalized_rows(
            report.query, reference.actual_column_roles, reference.numeric_roles
        )
        expected_roles, expected_rows = _normalized_rows(
            reference.query, reference.reference_column_roles, reference.numeric_roles
        )
    except LookupError:
        add("sql_rows", "manual_review", "A reliable column-to-metric mapping is missing")
    except (ValueError, TypeError):
        add(
            "sql_rows",
            False,
            "Result values or semantic mappings violate the exact result contract",
        )
    else:
        equal_rows = (
            actual_rows == expected_rows
            if reference.ordered
            else sorted(actual_rows) == sorted(expected_rows)
        )
        add(
            "sql_rows",
            actual_roles == expected_roles
            and equal_rows
            and not report.query.truncated
            and not reference.query.truncated,
            "Exact canonical cells and complete row counts must equal the real reference",
        )
    ast_by_id = {check.id: check for check in reference.ast_checks}
    if len(ast_by_id) != len(reference.ast_checks):
        add("sql_ast_checks", False, "Duplicate AST check identifiers")
    for identifier in expectation.required_ast_checks:
        check = ast_by_id.get(identifier)
        add(
            "sql_ast_" + identifier,
            check.status if check else "manual_review",
            check.detail if check else "Required AST evidence has not been collected",
        )
    rendered = AnalyticsService(None).render(report.query, report.metric_ids)
    add(
        "analysis_grounding",
        report.model_dump(mode="json") == rendered
        and task["result"].get("answer") == report.answer,
        "Report, metric definitions and chart must derive from executed query facts",
    )
    sql_facts = [
        fact.get("result")
        for fact in task["result"].get("facts") or []
        if isinstance(fact, dict) and fact.get("name") == "execute_analytics_sql"
    ]
    add(
        "analysis_query_facts",
        sql_facts == [report.query.model_dump(mode="json")],
        "The exposed query fact must equal the report query",
    )


def _citations(case, task, evidence, add):
    mode = case.expectations.citation_mode
    sources = task["result"].get("citations") or []
    if mode == "none":
        add("citations_absent", sources == [], "This case expects no knowledge source")
        return
    if not isinstance(sources, list) or not sources:
        add("citations_present", False, "Knowledge or policy sources are required")
        return
    add(
        "citation_expected_document",
        any(
            isinstance(source, dict)
            and source.get("document_title") in case.expectations.document_titles
            for source in sources
        ),
        "At least one expected seed document must be cited",
    )
    valid = True
    for source in sources:
        if not isinstance(source, dict):
            valid = False
            continue
        chunk = source.get("chunk_id")
        try:
            canonical = str(UUID(chunk))
        except (ValueError, TypeError, AttributeError):
            valid = False
            continue
        actual = evidence.citations.get(chunk)
        valid = (
            valid
            and canonical == chunk
            and source.get("citation_url") == ("/api/v1/knowledge/citations/" + chunk)
            and isinstance(actual, dict)
            and all(
                source.get(key) == actual.get(key) and key in source and key in actual
                for key in (
                    "chunk_id",
                    "document_id",
                    "document_title",
                    "document_version",
                    "index_generation",
                    "text",
                )
            )
        )
    add(
        "citation_current_version",
        valid,
        "Every citation must equal the authorized current citation API record",
    )
    if mode != "grounded_quote":
        return
    answer = task["result"].get("answer")
    pattern = re.compile(
        r"(?:^|\n)- (.*?) \[来源：([^\]\n]+)\]"
        r"\((/api/v1/knowledge/citations/[0-9a-f-]{36})\)",
        re.DOTALL,
    )
    source_by_url = {
        source.get("citation_url"): source for source in sources if isinstance(source, dict)
    }
    body = answer.removeprefix("根据企业资料：\n") if isinstance(answer, str) else ""
    quotes = list(pattern.finditer(body))
    quoted_urls = {match.group(3) for match in quotes}
    grounded = (
        bool(quotes) and quoted_urls == set(source_by_url) and not pattern.sub("", body).strip()
    )
    for match in quotes:
        source = source_by_url.get(match.group(3))
        grounded = (
            grounded
            and isinstance(source, dict)
            and match.group(2) == source.get("document_title")
            and bool(match.group(1).strip())
            and isinstance(source.get("text"), str)
            and (match.group(1) in source["text"])
        )
    add(
        "continuous_original_quotes",
        grounded,
        "Every rendered statement must be a continuous quote from its own verified source",
    )


def evaluate_case(
    case: EvaluationCase,
    task: dict[str, Any],
    evidence: EvaluationEvidence | dict[str, Any],
) -> CaseEvaluation:
    checks = []

    def add(identifier, value, detail):
        status = value if isinstance(value, str) else ("pass" if value else "fail")
        checks.append(CheckResult(id=identifier, status=status, detail=detail))

    try:
        observed = TaskResponse.model_validate(task).model_dump(mode="json")
    except (ValidationError, TypeError):
        add("task_schema", False, "Result is not a valid public TaskResponse")
        return CaseEvaluation(case_id=case.id, split=case.split, outcome="fail", checks=checks)
    evidence = EvaluationEvidence.model_validate(evidence)
    expected = case.expectations
    add("task_status", observed["status"] in expected.statuses, "Frozen terminal status contract")
    add("task_intent", observed["task_type"] in expected.intents, "Frozen intent routing contract")
    add("task_error_code", observed["error_code"] in expected.error_codes, "Frozen error contract")
    facts = observed["result"].get("facts") or []
    add("facts_shape", isinstance(facts, list), "Exposed business facts must be a list")
    fact_records = (
        [fact for fact in facts if isinstance(fact, dict)] if isinstance(facts, list) else []
    )
    tool_names = {fact.get("name") for fact in fact_records}
    tool_names.update(
        step.get("tool")
        for step in observed["steps"]
        if isinstance(step, dict) and step.get("node") == "business_tool"
    )
    tool_names.update(call.name for call in evidence.tool_calls)
    for name in expected.required_tools:
        add("tool_required_" + name, name in tool_names, "Required execution must be observed")
    for name in expected.forbidden_tools:
        add("tool_forbidden_" + name, name not in tool_names, "Forbidden tool must not run")
    before, after = evidence.business_before, evidence.business_after
    if before is None or after is None:
        add("business_unchanged", "manual_review", "Before/after business snapshots are missing")
    else:
        unchanged = (
            set(before.refund_ids) == set(after.refund_ids)
            and set(before.ticket_ids) == set(after.ticket_ids)
            and before.order_statuses == after.order_statuses
        )
        add(
            "business_unchanged",
            unchanged,
            "Refunds, tickets and order states must remain unchanged; preview rows are allowed",
        )
    add(
        "no_refund_commit",
        observed["result"].get("refund_result") is None,
        "Evaluation never resumes or confirms a refund",
    )
    calls = evidence.tool_calls
    model_calls = [call for call in calls if call.origin == "model"]
    schema_valid = True
    for call in model_calls:
        if call.name == "execute_analytics_sql":
            continue
        try:
            BusinessTools.validate_arguments(call.name, call.arguments)
        except BusinessError:
            schema_valid = False
    if model_calls:
        add(
            "tool_model_argument_schema",
            schema_valid,
            "Actual collected model arguments must satisfy the existing business tool schemas",
        )
    elif any(name not in {"search_knowledge", "execute_analytics_sql"} for name in tool_names):
        add(
            "tool_model_argument_schema",
            "manual_review",
            "Public task projection does not prove complete original model arguments",
        )
    for name, fields in expected.tool_arguments.items():
        candidates = [call.arguments for call in calls if call.name == name]
        candidates.extend(
            fact.get("arguments")
            for fact in fact_records
            if fact.get("name") == name and isinstance(fact.get("arguments"), dict)
        )
        wanted = {key: render_template(value, evidence.bindings) for key, value in fields.items()}
        add(
            "tool_arguments_" + name,
            bool(candidates)
            and all(
                all(arguments.get(key) == value for key, value in wanted.items())
                for arguments in candidates
            ),
            "Observed argument fields must equal trusted case bindings",
        )
    if expected.reference_tool:
        actual = [
            fact.get("result")
            for fact in fact_records
            if fact.get("name") == expected.reference_tool
        ]
        if expected.reference_tool not in evidence.facts_reference:
            add(
                "business_fact_reference",
                "manual_review",
                "Independent business reference is missing",
            )
        else:
            reference = evidence.facts_reference[expected.reference_tool]
            if expected.reference_tool == "get_refund_policy":
                # Retrieval timing, ranks and scores can change on a second call.
                # Business facts are the eligibility object; citations are checked
                # independently against the authorized current source endpoint.
                actual = [
                    value.get("eligibility") if isinstance(value, dict) else None
                    for value in actual
                ]
                reference = reference.get("eligibility") if isinstance(reference, dict) else None
            add(
                "business_fact_reference",
                bool(actual) and all(value == reference for value in actual),
                "Tool results must equal separately collected authorized business facts",
            )
    approval = observed["result"].get("approval")
    if not expected.approval_required:
        add(
            "approval_absent",
            approval is None,
            "Read-only and refused cases cannot create an approval",
        )
    else:
        try:
            parsed = ApprovalResponse.model_validate(approval)
        except ValidationError:
            add("approval_schema", False, "A valid server approval preview is required")
        else:
            add(
                "approval_fields",
                parsed.requires_confirmation is True
                and parsed.status.value == "pending"
                and parsed.order_no == evidence.bindings.get("refundable_order_no")
                and parsed.reason == "change_mind",
                "Approval must await human confirmation for the selected order",
            )
            policy_reference = evidence.facts_reference.get("get_refund_policy")
            if isinstance(policy_reference, dict) and isinstance(
                policy_reference.get("eligibility"), dict
            ):
                eligibility = policy_reference["eligibility"]
                add(
                    "approval_policy_and_amount",
                    eligibility.get("eligible") is True
                    and parsed.amount_cents == eligibility.get("amount_cents")
                    and parsed.policy.model_dump(mode="json") == eligibility.get("policy"),
                    "Preview amount and policy must equal the independently checked eligibility",
                )
            else:
                add(
                    "approval_policy_and_amount",
                    "manual_review",
                    "Trusted eligibility reference is missing",
                )
    _citations(case, observed, evidence, add)
    if case.category == "order_read":
        try:
            grounded_answer = fact_report(fact_records)
        except (KeyError, TypeError, ValueError):
            add("business_answer_grounding", False, "Business report contains malformed facts")
        else:
            add(
                "business_answer_grounding",
                observed["result"].get("answer") == grounded_answer,
                "Business answer must equal the report derived from checked facts",
            )
    if expected.answer_contract is not None:
        answers = {
            "insufficient_evidence": "未找到足够的企业资料证据，无法确认该政策，请联系相关负责人。",
            "unsupported": "当前支持企业知识、订单物流、退款申请和授权运营数据分析。",
        }
        add(
            "answer_contract",
            observed["result"].get("answer") == answers[expected.answer_contract],
            "Expected deterministic refusal must match exactly; keyword presence is insufficient",
        )
    if case.category == "order_denied":
        add(
            "no_foreign_facts",
            fact_records == [],
            "Denied ownership must not expose business facts",
        )
    if case.category in {"analytics_denied", "sql_denied"}:
        add(
            "no_analytics_result",
            observed["result"].get("analysis") is None and not fact_records,
            "Denied SQL and unauthorized analytics must expose no query rows",
        )
    if expected.sql is not None:
        _sql_checks(case, observed, evidence, add)
    for identifier in expected.manual_review:
        review = evidence.semantic_reviews.get(identifier)
        add(
            identifier,
            review.status if review and review.id == identifier else "manual_review",
            review.detail
            if review and review.id == identifier
            else "No human review recorded; structural grounding does not prove semantic relevance",
        )
    outcome = (
        "fail"
        if any(check.status == "fail" for check in checks)
        else "manual_review"
        if any(check.status == "manual_review" for check in checks)
        else "pass"
    )
    return CaseEvaluation(case_id=case.id, split=case.split, outcome=outcome, checks=checks)
