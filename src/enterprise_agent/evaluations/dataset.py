"""Author-written evaluation cases; validation is frozen before its first live run.

The author also knows the implementation, so this split is not an independently
annotated or blind benchmark. Order identifiers come from trusted runtime facts;
the user questions never receive SQL schema hints from the reference evaluator.
"""

import json
import re
from pathlib import Path
from string import Formatter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from enterprise_agent.schemas.agent import TaskStatus
from enterprise_agent.schemas.analytics import MetricId

BindingKey = Literal["own_order_no", "foreign_order_no", "refundable_order_no"]
Intent = Literal["knowledge", "order", "analytics", "unsupported"]
Split = Literal["development", "regression", "validation"]
Category = Literal[
    "knowledge",
    "insufficient_evidence",
    "order_read",
    "order_denied",
    "refund_preview",
    "analytics",
    "analytics_denied",
    "sql_denied",
    "unsupported",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class SQLExpectation(StrictContract):
    reference_id: Literal[
        "paid_sales_last_seven_days",
        "refund_rate_month_cohort",
        "compare_complete_seven_day_periods",
        "product_sales_top_five",
    ]
    metric_ids: list[MetricId] = Field(min_length=1, max_length=4)
    window_ids: list[
        Literal[
            "last_seven_days_including_today",
            "this_month_to_date",
            "previous_complete_week",
            "latest_complete_week",
        ]
    ] = Field(min_length=1, max_length=2)
    required_ast_checks: list[str] = Field(min_length=4, max_length=8)

    @model_validator(mode="after")
    def required_checks(self):
        core = {"read_views", "date_window", "metric_expression", "result_shape"}
        if not core.issubset(self.required_ast_checks):
            raise ValueError("SQL evaluation requires structural and date checks")
        if len(set(self.required_ast_checks)) != len(self.required_ast_checks):
            raise ValueError("Duplicate SQL checks")
        if len(set(self.metric_ids)) != len(self.metric_ids):
            raise ValueError("Duplicate metrics")
        if len(set(self.window_ids)) != len(self.window_ids):
            raise ValueError("Duplicate date windows")
        return self


class Expectations(StrictContract):
    intents: list[Intent] = Field(min_length=1, max_length=4)
    statuses: list[TaskStatus] = Field(min_length=1, max_length=7)
    error_codes: list[str | None] = Field(min_length=1, max_length=12)
    required_tools: list[str] = Field(default_factory=list, max_length=7)
    forbidden_tools: list[str] = Field(default_factory=list, max_length=7)
    tool_arguments: dict[str, dict[str, str]] = Field(default_factory=dict)
    reference_tool: str | None = None
    citation_mode: Literal["none", "grounded_quote", "policy"] = "none"
    document_titles: list[str] = Field(default_factory=list, max_length=5)
    approval_required: bool = False
    answer_contract: Literal["insufficient_evidence", "unsupported"] | None = None
    sql: SQLExpectation | None = None
    manual_review: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def consistent(self):
        supported_tools = {
            "get_order",
            "get_order_items",
            "get_logistics",
            "get_refund_policy",
            "get_refund_status",
            "create_refund_request",
            "search_knowledge",
            "execute_analytics_sql",
        }
        if not set(self.required_tools + self.forbidden_tools).issubset(supported_tools):
            raise ValueError("Unknown expected tool")
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("A tool cannot be required and forbidden")
        if not set(self.tool_arguments).issubset(supported_tools):
            raise ValueError("Unknown argument tool")
        if self.reference_tool is not None and self.reference_tool not in self.required_tools:
            raise ValueError("Reference tool must be required")
        if self.approval_required and (
            self.statuses != ["waiting_approval"]
            or "create_refund_request" not in self.required_tools
            or self.citation_mode != "policy"
        ):
            raise ValueError("Refund preview requires a waiting status and policy evidence")
        if self.sql is not None and self.intents != ["analytics"]:
            raise ValueError("SQL reference requires the analytics route")
        if self.citation_mode != "none" and not self.document_titles:
            raise ValueError("Cited cases need an expected document")
        if len(set(self.manual_review)) != len(self.manual_review):
            raise ValueError("Duplicate manual checks")
        return self


class EvaluationCase(StrictContract):
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]+$")
    split: Split
    category: Category
    actor: Literal["customer", "analyst"]
    message_template: str = Field(min_length=2, max_length=2000)
    provenance: Literal["development", "historical_regression", "new_assistant_written"]
    expectations: Expectations

    @model_validator(mode="after")
    def safe_template(self):
        _validate_template(self.message_template)
        for arguments in self.expectations.tool_arguments.values():
            for value in arguments.values():
                _validate_template(value)
        if self.provenance == "historical_regression" and self.split != "regression":
            raise ValueError("Previously observed scenarios belong to regression")
        if self.split == "validation" and self.provenance != "new_assistant_written":
            raise ValueError("Validation cases must be newly written")
        if (self.category == "refund_preview") != self.expectations.approval_required:
            raise ValueError("Refund category and preview expectation disagree")
        if (self.category == "analytics") != (self.expectations.sql is not None):
            raise ValueError("Analytics success cases require a SQL reference")
        return self


class EvaluationDataset(StrictContract):
    schema_version: Literal[1]
    dataset_id: Literal["enterprise-agent-phase8"]
    seed_base_date: Literal["2026-10-12"]
    source: Literal["coding_assistant_curated_synthetic_project_cases"]
    scope: str = Field(min_length=30, max_length=1000)
    validation_policy: Literal["freeze_sha256_before_first_live_run_no_posthoc_tuning"]
    cases: list[EvaluationCase] = Field(min_length=12, max_length=24)

    @model_validator(mode="after")
    def unique_and_split(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case identifiers")
        if {case.split for case in self.cases} != {"development", "regression", "validation"}:
            raise ValueError("All three splits are required")
        return self


_BINDINGS = {"own_order_no", "foreign_order_no", "refundable_order_no"}


def _validate_template(value: str) -> None:
    try:
        fields = list(Formatter().parse(value))
    except ValueError:
        raise ValueError("Invalid evaluation template") from None
    for _, key, format_spec, conversion in fields:
        if key is not None and (key not in _BINDINGS or format_spec or conversion):
            raise ValueError("Only trusted order number bindings are allowed")


def render_template(value: str, bindings: dict[str, str]) -> str:
    """No attribute traversal, formatting expressions, SQL or untrusted interpolation."""
    _validate_template(value)
    required = {key for _, key, _, _ in Formatter().parse(value) if key is not None}
    if any(
        key not in bindings
        or not isinstance(bindings[key], str)
        or re.fullmatch(r"[0-9]{5,30}", bindings[key]) is None
        for key in required
    ):
        raise ValueError("A trusted canonical order number binding is missing")
    return value.format_map(bindings)


def render_message(case: EvaluationCase, bindings: dict[str, str]) -> str:
    return render_template(case.message_template, bindings)


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
