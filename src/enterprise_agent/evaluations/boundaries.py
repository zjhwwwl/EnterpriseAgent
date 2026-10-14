"""Offline fault injection against the real graph, never a provider-quality score.

The model and Service responses here are declared fixtures. The production graph,
tool parameter validators, interrupts and checkpoint replay execute unchanged.
No database, HTTP client, external model or tracing exporter is constructed.
"""

import asyncio
import csv
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid5

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, model_validator

from enterprise_agent.agents.graph import AgentContext, build_graph, initial_state
from enterprise_agent.agents.llm import ChatReply, ModelCallError, ModelToolCall
from enterprise_agent.config import Settings
from enterprise_agent.models import UserRole
from enterprise_agent.schemas.knowledge import Citation
from enterprise_agent.security import Principal
from enterprise_agent.tools.business import BusinessTools

_NAMESPACE = UUID("7a6ed3a6-0b57-4051-b795-48ed6520758d")
_ACTOR = Principal(user_id=7, role=UserRole.CUSTOMER)
_FIXTURE_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Usage(_Strict):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.prompt_tokens + self.completion_tokens != self.total_tokens:
            raise ValueError("Fixture usage totals are inconsistent")
        return self


class _Route(_Strict):
    kind: Literal["route"]
    intent: Literal["order", "unsupported"]


class _Call(_Strict):
    kind: Literal["call"]
    name: Literal["get_order", "get_logistics", "create_refund_request"]
    arguments: dict


class _Batch(_Strict):
    kind: Literal["batch"]
    calls: list[_Call] = Field(min_length=2, max_length=4)


class _Error(_Strict):
    kind: Literal["error"]
    code: Literal[
        "agent_model_unavailable", "agent_model_rate_limited", "agent_model_invalid_response"
    ]
    usage: _Usage | None = None


class _Stop(_Strict):
    kind: Literal["stop"]


class _Timeout(_Strict):
    kind: Literal["timeout"]


_Event = Annotated[_Route | _Call | _Batch | _Error | _Stop | _Timeout, Field(discriminator="kind")]


class _Limits(_Strict):
    agent_max_retries: int = Field(default=2, ge=0, le=2)
    agent_max_steps: int = Field(default=24, ge=6, le=60)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=16)


class _Expected(_Strict):
    status: Literal["completed", "running", "failed", "rejected"]
    error_code: str | None
    model_calls: int = Field(ge=0)
    unknown_usage_calls: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_count: int = Field(ge=0)
    service_calls: int = Field(ge=0)
    prepare_calls: int = Field(ge=0)
    confirmation_calls: int = Field(ge=0)
    refund_writes: int = Field(ge=0)
    paused_before_commit: bool
    step_count: int | None = Field(default=None, ge=0)
    answer_contains: list[str] = Field(default_factory=list, max_length=5)


class _Case(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,70}$")
    category: str = Field(min_length=1, max_length=70)
    description: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=2, max_length=500)
    events: list[_Event] = Field(min_length=1, max_length=32)
    settings: _Limits = Field(default_factory=_Limits)
    approval_action: Literal["none", "reject", "confirm", "mismatch"] = "none"
    replay_after_completion: bool = False
    expected: _Expected


class _Dataset(_Strict):
    version: Literal[1]
    evaluation_kind: Literal["controlled_fault_injection"]
    description: str
    cases: list[_Case] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_ids(self):
        identifiers = [case.id for case in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Boundary case identifiers must be unique")
        return self


class _ScriptedModel:
    """Each request consumes one dataset event; all usage below is synthetic."""

    def __init__(self, events):
        self.events = list(events)
        self.requests = 0
        self.timeout_cancelled = False

    def _reply(self, calls, *, content=""):
        tool_calls = [
            ModelToolCall(f"fixture_{self.requests}_{index}", name, arguments)
            for index, (name, arguments) in enumerate(calls)
        ]
        message = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments, ensure_ascii=False, allow_nan=False
                        ),
                    },
                }
                for call in tool_calls
            ]
        return ChatReply(content, tool_calls, message, dict(_FIXTURE_USAGE), 0)

    async def complete(self, messages, *, tools=None):
        self.requests += 1
        if not self.events:
            raise AssertionError("Unexpected model request beyond the declared fixture")
        event = self.events.pop(0)
        if isinstance(event, _Error):
            usage = event.usage.model_dump() if event.usage is not None else None
            raise ModelCallError(event.code, "Controlled injected model failure", usage=usage)
        if isinstance(event, _Timeout):
            started = perf_counter()
            try:
                async with asyncio.timeout(0.01):
                    try:
                        await asyncio.sleep(1)
                    finally:
                        self.timeout_cancelled = True
            except TimeoutError:
                raise ModelCallError(
                    "agent_model_timeout",
                    "Controlled local timeout, no provider request",
                    elapsed_ms=(perf_counter() - started) * 1000,
                ) from None
        if isinstance(event, _Route):
            return self._reply([("route_task", {"intent": event.intent, "query": "查询订单10086"})])
        if isinstance(event, _Call):
            return self._reply([(event.name, event.arguments)])
        if isinstance(event, _Batch):
            return self._reply([(call.name, call.arguments) for call in event.calls])
        return self._reply([], content="模拟模型声称已真实退款；这段自由文本不应成为业务事实。")


class _Payload:
    def __init__(self, value):
        self.value = value

    def model_dump(self, *, mode):
        if mode != "json":
            raise AssertionError("The graph must request a JSON payload")
        return self.value


class _FixtureKnowledge:
    def __init__(self, citation):
        self.source = citation
        self.actors = []

    async def citation(self, actor, chunk_id):
        self.actors.append(actor)
        if chunk_id != self.source.chunk_id:
            raise AssertionError("Unexpected fixture citation identifier")
        return self.source


class _FixtureTools:
    """Only effect accounting is simulated; parameter validation is production code."""

    openai_tools = staticmethod(BusinessTools.openai_tools)
    validate_arguments = staticmethod(BusinessTools.validate_arguments)

    def __init__(self, identifier):
        self.actors = []
        self.service_calls = 0
        self.prepare_calls = 0
        self.confirmation_calls = 0
        self.refund_writes = 0
        self.approval_id = uuid5(_NAMESPACE, identifier + ":approval")
        source = Citation(
            document_id=uuid5(_NAMESPACE, "fixture-policy"),
            document_title="明确标记的离线政策 fixture",
            document_version=1,
            index_generation=1,
            chunk_id=uuid5(_NAMESPACE, "fixture-policy-chunk"),
            chunk_index=0,
            text="收到商品后七天内可以提交待审核退款申请，审核前不会执行退款。",
            source_location={"section": 1},
            citation_url="/api/v1/knowledge/citations/"
            + str(uuid5(_NAMESPACE, "fixture-policy-chunk")),
            vector_similarity=0.7,
            rerank_score=0.8,
        )
        self.knowledge = _FixtureKnowledge(source)

    async def execute(self, actor, name, arguments):
        payload = self.validate_arguments(name, arguments)
        self.actors.append(actor)
        self.service_calls += 1
        if name == "get_logistics":
            return {
                "order_no": payload["order_no"],
                "tracking_no": "SIM-OFFLINE-FIXTURE",
                "status": "in_transit",
                "events": [],
            }
        if name == "get_order":
            return {
                "order_no": payload["order_no"],
                "status": "shipped",
                "goods_paid_cents": 10000,
                "shipping_paid_cents": 600,
            }
        if name == "get_refund_policy":
            return {
                "eligibility": {
                    "eligible": True,
                    "code": "eligible",
                    "message": "fixture 可提交待审核申请",
                    "order_no": payload["order_no"],
                    "amount_cents": 10000,
                    "policy": {"document_id": str(self.knowledge.source.document_id)},
                },
                "knowledge": {
                    "status": "evidence_found",
                    "message": "明确标记的离线政策 fixture",
                    "citations": [self.knowledge.source.model_dump(mode="json")],
                },
            }
        raise AssertionError("Unsupported fixture Service request")

    async def prepare_refund(self, actor, arguments, *, task_id):
        payload = self.validate_arguments("create_refund_request", arguments)
        self.actors.append(actor)
        self.prepare_calls += 1
        return _Payload(
            {
                "approval_id": str(self.approval_id),
                "order_no": payload["order_no"],
                "amount_cents": 10000,
                "requires_confirmation": True,
            }
        )

    async def execute_confirmed_refund(self, actor, approval_id, idempotency_key, *, confirmed):
        self.actors.append(actor)
        self.confirmation_calls += 1
        if approval_id != self.approval_id or not idempotency_key.startswith("agent:"):
            raise AssertionError("Graph changed the trusted approval or idempotency identity")
        # Deliberately count every invocation. Replay safety must come from the
        # actual checkpoint, rather than an idempotent fixture hiding duplicate calls.
        self.refund_writes += int(confirmed)
        refund = (
            {
                "id": str(uuid5(_NAMESPACE, idempotency_key)),
                "order_no": "10086",
                "amount_cents": 10000,
                "status": "pending_review",
            }
            if confirmed
            else None
        )
        return _Payload({"refund": refund, "status": "consumed" if confirmed else "rejected"})


async def _run_case(case, base_limits):
    started = perf_counter()
    # Construct an isolated settings object without reading .env or process secrets.
    limits = {**base_limits, **case.settings.model_dump(exclude_unset=True)}
    settings = Settings.model_construct(app_env="test", **limits)
    model, tools = _ScriptedModel(case.events), _FixtureTools(case.id)
    graph = build_graph(InMemorySaver())
    task_id = uuid5(_NAMESPACE, case.id)
    config = {"configurable": {"thread_id": str(task_id)}, "recursion_limit": 80}
    context = AgentContext(_ACTOR, tools, model, settings)
    result = initial_state(task_id, case.message, [])
    runner_error = None
    paused = False
    pre_resume_writes = None
    snapshot_next = []
    try:
        result = await graph.ainvoke(result, config, context=context)
        paused = bool(result.get("__interrupt__"))
        snapshot = await graph.aget_state(config)
        snapshot_next = list(snapshot.next)
        if paused:
            pre_resume_writes = tools.refund_writes
        if case.approval_action != "none":
            approval_id = (
                uuid5(_NAMESPACE, "wrong-approval")
                if case.approval_action == "mismatch"
                else tools.approval_id
            )
            result = await graph.ainvoke(
                Command(
                    resume={
                        "approval_id": str(approval_id),
                        "confirmed": case.approval_action != "reject",
                    }
                ),
                config,
                context=context,
            )
        if case.replay_after_completion:
            result = await graph.ainvoke(None, config, context=context)
    except Exception as error:
        # Never serialize arbitrary exception messages, credentials or model requests.
        runner_error = type(error).__name__

    usage = result["token_usage"]
    observed = {
        "status": result["status"],
        "error_code": result["error_code"],
        "model_calls": model.requests,
        "unknown_usage_calls": usage["unknown_usage_calls"],
        "total_tokens": usage["total_tokens"],
        "tool_count": result["tool_count"],
        "service_calls": tools.service_calls,
        "prepare_calls": tools.prepare_calls,
        "confirmation_calls": tools.confirmation_calls,
        "refund_writes": tools.refund_writes,
        "paused_before_commit": paused,
        "step_count": result["step_count"],
    }
    expected = case.expected.model_dump(exclude={"answer_contains"}, exclude_none=True)
    # error_code=None remains an explicit assertion, not an omitted expectation.
    expected["error_code"] = case.expected.error_code
    checks = {key: observed[key] == value for key, value in expected.items()}
    checks.update(
        runner_completed=runner_error is None,
        trusted_actor_preserved=all(
            actor == _ACTOR for actor in tools.actors + tools.knowledge.actors
        ),
        graph_usage_call_count_matches_requests=usage["model_calls"] == model.requests,
        scripted_events_consumed=not model.events,
        model_freeform_not_published="模拟模型声称已真实退款" not in result["answer"],
        no_write_before_human_resume=pre_resume_writes in (None, 0),
        interrupt_points_to_human_approval=not paused or snapshot_next == ["human_approval"],
        fixture_timeout_released=not any(isinstance(event, _Timeout) for event in case.events)
        or model.timeout_cancelled,
    )
    for index, text in enumerate(case.expected.answer_contains):
        checks[f"answer_contains_{index}"] = text in result["answer"]
    return {
        "id": case.id,
        "category": case.category,
        "description": case.description,
        "passed": all(checks.values()),
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        "expected": case.expected.model_dump(),
        "observed": observed,
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if not passed],
        "checkpoint_next_before_resume": snapshot_next,
        "pre_resume_fixture_refund_writes": pre_resume_writes,
        "fixture_usage": dict(usage),
        "provider_usage": None,
        "runner_error": runner_error,
    }


async def run_boundary_evaluation(dataset_path: Path | str, *, settings: Settings | None = None):
    """Return actual controlled outcomes, with provider consumption not applicable."""
    started = perf_counter()
    raw = Path(dataset_path).read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("Boundary dataset exceeds the input size limit")
    dataset = _Dataset.model_validate_json(raw)
    base_limits = _Limits.model_validate(
        settings.model_dump(include=set(_Limits.model_fields)) if settings else {}
    ).model_dump()
    cases = [await _run_case(case, base_limits) for case in dataset.cases]
    fixture_usage = dict.fromkeys(
        (
            "model_calls",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "unknown_usage_calls",
        ),
        0,
    )
    for case in cases:
        for key in fixture_usage:
            fixture_usage[key] += case["fixture_usage"][key]
    categories = Counter(case["category"] for case in cases)
    passed = sum(case["passed"] for case in cases)
    return {
        "schema_version": 1,
        "evaluation_kind": "controlled_fault_injection",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "description": dataset.description,
        "executed_components": ["production_langgraph", "production_tool_argument_validation"],
        "fixture_components": ["scripted_chat_model", "service_payloads", "knowledge_policy"],
        "limitations": [
            "Not a DeepSeek or other provider quality benchmark.",
            "No database executes; ownership and business rules need separate integration tests.",
            "Fixture token totals are scripted bookkeeping, not billing or tokenizer measurement.",
            "Timeout is a local controlled model fixture; provider transport is not exercised.",
            "Replay uses InMemorySaver; PostgreSQL persistence needs separate integration tests.",
        ],
        "provider_usage": None,
        "provider_usage_status": "not_applicable_no_provider_requests",
        "fixture_usage": fixture_usage,
        "summary": {
            "cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "controlled_expectation_pass_rate": passed / len(cases),
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
            "categories": dict(sorted(categories.items())),
        },
        "cases": cases,
    }


def write_boundary_artifacts(report: dict, output_dir: Path | str) -> dict[str, str]:
    """Write machine-readable outcomes and a report retaining failed expectations."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path, csv_path, markdown_path = (
        output / "results.json",
        output / "results.csv",
        output / "report.md",
    )
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    columns = [
        "id",
        "category",
        "passed",
        "elapsed_ms",
        "status",
        "error_code",
        "model_calls",
        "unknown_usage_calls",
        "total_tokens",
        "tool_count",
        "service_calls",
        "prepare_calls",
        "confirmation_calls",
        "refund_writes",
        "paused_before_commit",
        "step_count",
        "failed_checks",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for case in report["cases"]:
            writer.writerow(
                {
                    **{key: case[key] for key in ("id", "category", "passed", "elapsed_ms")},
                    **case["observed"],
                    "failed_checks": ";".join(case["failed_checks"]),
                }
            )
    summary = report["summary"]
    lines = [
        "# Controlled workflow boundary evaluation",
        "",
        "Evaluation kind: `controlled_fault_injection`. Real LangGraph and parameter validators",
        "run with scripted model and Service fixtures. Provider usage: **not applicable**.",
        "",
        f"Cases: {summary['cases']}; passed: {summary['passed']}; failed: {summary['failed']}; "
        f"elapsed: {summary['elapsed_ms']} ms.",
        "",
        "These outcomes measure declared workflow boundary expectations, not model task completion",
        "quality, database authorization, production latency or billed tokens.",
        "",
        f"Fixture bookkeeping: `{json.dumps(report['fixture_usage'])}`.",
        "",
        "| Case | Boundary passed | State | Error | Local elapsed ms | Failed checks |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for case in report["cases"]:
        observed = case["observed"]
        lines.append(
            f"| {case['id']} | {case['passed']} | {observed['status']} | "
            f"{observed['error_code'] or '—'} | {case['elapsed_ms']} | "
            f"{', '.join(case['failed_checks']) or '—'} |"
        )
    lines.extend(["", *[f"- {item}" for item in report["limitations"]], ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}
