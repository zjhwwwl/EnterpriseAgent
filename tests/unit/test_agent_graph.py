"""Real LangGraph with scripted control replies, not live Agent success metrics.

Service controls only record effects and exercise graph limits, resume semantics
and evidence fences. Database/model quality is covered separately by integration
and explicitly enabled live evaluation.
"""

import copy
import json
from dataclasses import dataclass
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from enterprise_agent.agents.graph import AgentContext, build_graph, initial_state
from enterprise_agent.agents.llm import ChatReply, ModelCallError, ModelToolCall
from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole
from enterprise_agent.schemas.knowledge import Citation
from enterprise_agent.security import Principal
from enterprise_agent.tools.business import BusinessTools

_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
_ACTOR = Principal(user_id=7, role=UserRole.CUSTOMER)


def planned(name, arguments, *, text=""):
    call_id = "call_control_" + uuid4().hex
    return ChatReply(
        content=text,
        tool_calls=[ModelToolCall(call_id, name, arguments)],
        message={
            "role": "assistant",
            "content": text or None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        },
        usage=dict(_USAGE),
        elapsed_ms=1,
    )


def stopped(text="The model's freeform statement must not become trusted business facts."):
    return ChatReply(text, [], {"role": "assistant", "content": text}, dict(_USAGE), 1)


class ScriptedChatModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, messages, *, tools=None):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        if not self.replies:
            raise AssertionError("The bounded workflow requested an unexpected model call")
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@dataclass
class ControlPayload:
    value: dict

    def model_dump(self, *, mode):
        assert mode == "json"
        return copy.deepcopy(self.value)


def evidence():
    return Citation(
        document_id=uuid4(),
        document_title="Control policy fixture",
        document_version=1,
        index_generation=2,
        chunk_id=uuid4(),
        chunk_index=0,
        text="收到商品后七天内，可以提交待审核退货申请。审核前不会进行退款。",
        source_location={"section": 1},
        citation_url="/api/v1/knowledge/citations/control",
        vector_similarity=0.7,
        rerank_score=0.8,
    )


class ControlKnowledge:
    def __init__(self, citations):
        self.citations = {str(item.chunk_id): item for item in citations}
        self.failure = None
        self.validations = []

    async def citation(self, actor, chunk_id):
        self.validations.append((actor, chunk_id))
        if self.failure:
            raise self.failure
        if str(chunk_id) not in self.citations:
            raise BusinessError("citation_not_found", "Control citation no longer available", 404)
        return self.citations[str(chunk_id)]


class ControlTools:
    openai_tools = staticmethod(BusinessTools.openai_tools)
    validate_arguments = staticmethod(BusinessTools.validate_arguments)

    def __init__(self, citations=()):
        self.citations = [item.model_dump(mode="json") for item in citations]
        self.knowledge = ControlKnowledge(citations)
        self.effects = []
        self.preparations = []
        self.commits = []
        self.approval_id = uuid4()
        self.policy_evidence = True

    def search_result(self):
        return {
            "status": "evidence_found" if self.citations else "insufficient_evidence",
            "message": "Controlled evidence search",
            "citations": copy.deepcopy(self.citations),
        }

    async def execute(self, actor, name, arguments):
        payload = self.validate_arguments(name, arguments)
        self.effects.append((actor, name, payload))
        if name == "search_knowledge":
            return self.search_result()
        if name == "get_logistics":
            return {
                "order_no": payload["order_no"],
                "tracking_no": "SIM-CONTROL",
                "status": "in_transit",
                "events": [
                    {
                        "at": "2026-01-01T08:30:00+00:00",
                        "status": "in_transit",
                        "note": "控制物流事件",
                    }
                ],
            }
        if name == "get_refund_policy":
            knowledge = self.search_result()
            if not self.policy_evidence:
                knowledge = {
                    "status": "insufficient_evidence",
                    "message": "Control lacks policy",
                    "citations": [],
                }
            return {
                "eligibility": {
                    "eligible": True,
                    "code": "eligible",
                    "message": "符合退款条件",
                    "order_no": payload["order_no"],
                    "amount_cents": 10000,
                    "policy": {"document_id": self.citations[0]["document_id"]}
                    if self.citations
                    else None,
                },
                "knowledge": knowledge,
            }
        if name == "get_order":
            return {
                "order_no": payload["order_no"],
                "status": "shipped",
                "goods_paid_cents": 10000,
                "shipping_paid_cents": 600,
            }
        raise AssertionError("Unexpected control Service action")

    async def prepare_refund(self, actor, arguments, *, task_id):
        payload = self.validate_arguments("create_refund_request", arguments)
        self.preparations.append((actor, payload, task_id))
        return ControlPayload(
            {
                "approval_id": str(self.approval_id),
                "order_no": payload["order_no"],
                "amount_cents": 10000,
                "requires_confirmation": True,
            }
        )

    async def execute_confirmed_refund(self, actor, approval_id, idempotency_key, *, confirmed):
        self.commits.append((actor, approval_id, idempotency_key, confirmed))
        refund = (
            {
                "id": str(uuid4()),
                "order_no": "10086",
                "amount_cents": 10000,
                "status": "pending_review",
            }
            if confirmed
            else None
        )
        return ControlPayload({"refund": refund, "status": "consumed" if confirmed else "rejected"})


def graph_case(replies, *, tools=None, **settings):
    control = tools or ControlTools()
    model = ScriptedChatModel(replies)
    context = AgentContext(_ACTOR, control, model, Settings(_env_file=None, **settings))
    graph = build_graph(InMemorySaver())
    task_id = uuid4()
    config = {"configurable": {"thread_id": str(task_id)}, "recursion_limit": 80}
    return graph, context, config, initial_state(task_id, "查询订单 10086 或企业售后政策。", [])


def route(intent):
    return planned("route_task", {"intent": intent, "query": "查询订单10086售后政策"})


async def test_empty_knowledge_rewrites_once_then_refuses_without_making_a_policy():
    graph, ctx, config, state = graph_case(
        [
            route("knowledge"),
            planned("rewrite_query", {"query": "退货政策 期限"}),
        ]
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert result["rewrite_count"] == 1
    assert result["citations"] == []
    assert result["quotes"] == []
    assert "无法确认" in result["answer"]
    assert [item[1] for item in ctx.tools.effects] == ["search_knowledge", "search_knowledge"]
    assert result["tool_count"] == 2
    assert result["token_usage"]["total_tokens"] == 16
    assert len(ctx.model.requests) == 2


async def test_grounded_answer_quotes_only_authorized_original_text_and_discards_freeform_content():
    source = evidence()
    quote = "可以提交待审核退货申请。"
    graph, ctx, config, state = graph_case(
        [
            route("knowledge"),
            planned(
                "select_knowledge_quotes",
                {"quotes": [{"chunk_id": str(source.chunk_id), "quote": quote}]},
                text="自动批准并退还全部金额。",
            ),
        ],
        tools=ControlTools([source]),
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert quote in result["answer"]
    assert "自动批准" not in result["answer"]
    assert result["citations"] == [source.model_dump(mode="json")]
    assert ctx.tools.knowledge.validations == [(_ACTOR, source.chunk_id)]
    assert not ctx.tools.preparations and not ctx.tools.commits


@pytest.mark.parametrize("case", ["fabricated_quote", "unknown_chunk"])
async def test_untraceable_quote_fails_closed(case):
    source = evidence()
    quote = {"chunk_id": str(source.chunk_id), "quote": "可以直接批准并完成真实退款。"}
    if case == "unknown_chunk":
        quote = {"chunk_id": str(uuid4()), "quote": "可以提交待审核退货申请。"}
    graph, ctx, config, state = graph_case(
        [
            route("knowledge"),
            planned("select_knowledge_quotes", {"quotes": [quote]}),
        ],
        tools=ControlTools([source]),
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "ungrounded_model_quote"
    assert quote["quote"] not in result["answer"]
    assert result["token_usage"]["model_calls"] == 2
    assert result["token_usage"]["total_tokens"] == 16


@pytest.mark.parametrize("case", ["generation_changed", "permission_removed"])
async def test_citations_are_revalidated_against_current_permissions_and_published_generation(case):
    source = evidence()
    control = ControlTools([source])
    if case == "generation_changed":
        control.knowledge.citations[str(source.chunk_id)] = source.model_copy(
            update={"index_generation": 3}
        )
    else:
        control.knowledge.failure = BusinessError("citation_not_found", "Citation unavailable", 404)
    graph, ctx, config, state = graph_case(
        [
            route("knowledge"),
            planned(
                "select_knowledge_quotes",
                {
                    "quotes": [
                        {"chunk_id": str(source.chunk_id), "quote": "可以提交待审核退货申请。"}
                    ]
                },
            ),
        ],
        tools=control,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == (
        "citation_changed" if case == "generation_changed" else "citation_not_found"
    )
    assert not control.commits


@pytest.mark.parametrize("stage", ["route", "rewrite", "quote"])
async def test_successful_response_usage_survives_invalid_workflow_arguments(stage):
    source = evidence()
    if stage == "route":
        replies = [
            planned("route_task", {"intent": "knowledge", "query": "退货政策", "actor_id": 999})
        ]
    elif stage == "rewrite":
        replies = [
            route("knowledge"),
            planned("rewrite_query", {"query": "退货政策", "approved": True}),
        ]
    else:
        replies = [route("knowledge"), planned("select_knowledge_quotes", {"quotes": []})]
    control = ControlTools([source] if stage == "quote" else [])
    graph, ctx, config, state = graph_case(replies, tools=control)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_model_plan"
    assert result["token_usage"]["model_calls"] == len(replies)
    assert result["token_usage"]["total_tokens"] == len(replies) * 8


@pytest.mark.parametrize(
    "arguments",
    [
        {"order_no": "10086", "actor_id": 999},
        {"order_no": "10086", "confirmed": True},
        {"order_no": 10086},
    ],
    ids=["forged_actor", "forged_confirmation", "wrong_type"],
)
async def test_malicious_or_wrongly_typed_tool_arguments_never_reach_a_service(arguments):
    graph, ctx, config, state = graph_case([route("order"), planned("get_logistics", arguments)])
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_tool_arguments"
    assert ctx.tools.effects == []
    assert ctx.tools.preparations == [] and ctx.tools.commits == []
    assert result["token_usage"]["total_tokens"] == 16


async def test_infinite_tool_planning_stops_at_the_actual_graph_step_limit():
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            *[planned("get_logistics", {"order_no": "10086"}) for _ in range(3)],
        ],
        agent_max_steps=6,
        agent_max_tool_calls=8,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "agent_step_limit"
    assert result["step_count"] == 6
    assert len(ctx.tools.effects) == 2
    assert result["tool_count"] == 2
    assert len(ctx.model.requests) == 4


async def test_oversized_batch_is_rejected_before_any_tool_executes():
    batch = planned("get_logistics", {"order_no": "10086"})
    extra = planned("get_order", {"order_no": "10086"})
    batch.tool_calls.append(extra.tool_calls[0])
    batch.message["tool_calls"].extend(extra.message["tool_calls"])
    graph, ctx, config, state = graph_case([route("order"), batch], agent_max_tool_calls=1)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "agent_tool_limit"
    assert ctx.tools.effects == []


async def test_knowledge_rewrite_cannot_bypass_the_retrieval_tool_budget():
    graph, ctx, config, state = graph_case(
        [
            route("knowledge"),
            planned("rewrite_query", {"query": "退货政策期限"}),
        ],
        agent_max_tool_calls=1,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert len(ctx.tools.effects) == 1
    assert result["tool_count"] == 1
    assert result["status"] == "failed"
    assert result["error_code"] == "agent_tool_limit"


async def test_retry_records_both_known_and_unknown_consumption_without_extra_attempts():
    known = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
    graph, ctx, config, state = graph_case(
        [
            ModelCallError("agent_model_unavailable", "Control unavailable"),
            ModelCallError("agent_model_rate_limited", "Control rate limit", usage=known),
            route("unsupported"),
        ],
        agent_max_retries=2,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert result["token_usage"] == {
        "model_calls": 3,
        "prompt_tokens": 7,
        "completion_tokens": 4,
        "total_tokens": 11,
        "unknown_usage_calls": 1,
    }
    assert len(ctx.model.requests) == 3


async def test_retry_exhaustion_ends_the_graph_after_three_unknown_requests():
    graph, ctx, config, state = graph_case(
        [ModelCallError("agent_model_unavailable", "Control unavailable") for _ in range(3)],
        agent_max_retries=2,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "agent_model_unavailable"
    assert result["token_usage"]["model_calls"] == 3
    assert result["token_usage"]["unknown_usage_calls"] == 3
    assert result["token_usage"]["total_tokens"] == 0
    assert ctx.tools.effects == []


async def test_invalid_provider_response_is_not_retried_and_keeps_known_usage():
    known = {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}
    graph, ctx, config, state = graph_case(
        [
            ModelCallError("agent_model_invalid_response", "Control invalid response", usage=known),
        ]
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["token_usage"]["model_calls"] == 1
    assert result["token_usage"]["unknown_usage_calls"] == 0
    assert result["token_usage"]["total_tokens"] == 3


@pytest.mark.parametrize("confirmed", [True, False])
async def test_human_interrupt_pauses_before_commit_and_resumption_uses_trusted_actor_once(
    confirmed,
):
    source = evidence()
    tools = ControlTools([source])
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("create_refund_request", {"order_no": "10086", "reason": "change_mind"}),
        ],
        tools=tools,
    )
    paused = await graph.ainvoke(state, config, context=ctx)
    assert paused["__interrupt__"]
    assert len(tools.preparations) == 1
    assert tools.commits == []
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("human_approval",)
    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_id": str(tools.approval_id),
                "confirmed": confirmed,
            }
        ),
        config,
        context=ctx,
    )
    assert resumed["status"] == ("completed" if confirmed else "rejected")
    assert len(tools.preparations) == 1
    assert tools.commits == [
        (_ACTOR, tools.approval_id, "agent:" + state["task_id"] + ":refund", confirmed)
    ]
    assert len(ctx.model.requests) == 2
    await graph.ainvoke(None, config, context=ctx)
    assert len(tools.commits) == 1
    assert all(item[0] == _ACTOR for item in tools.effects)


async def test_resuming_with_a_different_approval_cannot_commit():
    tools = ControlTools([evidence()])
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("create_refund_request", {"order_no": "10086", "reason": "change_mind"}),
        ],
        tools=tools,
    )
    await graph.ainvoke(state, config, context=ctx)
    result = await graph.ainvoke(
        Command(
            resume={
                "approval_id": str(uuid4()),
                "confirmed": True,
            }
        ),
        config,
        context=ctx,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "approval_changed"
    assert tools.commits == []


async def test_order_reply_does_not_publish_model_freeform_claims():
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("get_logistics", {"order_no": "10086"}),
            stopped("因为仓库爆炸，你的订单丢失并已退款。"),
        ]
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert "in_transit" in result["answer"]
    assert "2026-01-01T08:30:00+00:00" in result["answer"]
    assert "控制物流事件" in result["answer"]
    assert "仓库爆炸" not in result["answer"] and "已退款" not in result["answer"]


async def test_router_rejects_a_different_function_even_when_its_arguments_look_valid():
    graph, ctx, config, state = graph_case(
        [
            planned("rewrite_query", {"intent": "knowledge", "query": "退货政策"}),
        ]
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_model_plan"
    assert result["token_usage"]["total_tokens"] == 8
    assert ctx.tools.effects == []


async def test_one_allowed_read_can_finish_without_an_extra_model_or_tool_call():
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("get_logistics", {"order_no": "10086"}),
        ],
        agent_max_tool_calls=1,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert result["tool_count"] == 1
    assert len(ctx.model.requests) == 2
    assert len(ctx.tools.effects) == 1
    assert "in_transit" in result["answer"]


async def test_partial_read_batch_keeps_executed_facts_counts_and_trace_when_later_arguments_fail():
    first = planned("get_logistics", {"order_no": "10086"})
    second = planned("get_order", {"order_no": "10086", "actor_id": 999})
    first.tool_calls.extend(second.tool_calls)
    first.message["tool_calls"].extend(second.message["tool_calls"])
    graph, ctx, config, state = graph_case([route("order"), first])
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_tool_arguments"
    assert result["tool_count"] == 1
    assert len(ctx.tools.effects) == 1
    assert result["facts"][0]["name"] == "get_logistics"
    tool_trace = [step for step in result["steps"] if step["node"] == "business_tool"]
    assert [step["error_code"] for step in tool_trace] == [None, "invalid_tool_arguments"]


async def test_positive_refund_policy_needs_original_knowledge_evidence_before_a_conclusion():
    control = ControlTools([evidence()])
    control.policy_evidence = False
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("get_refund_policy", {"order_no": "10086", "reason": "change_mind"}),
        ],
        tools=control,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "insufficient_policy_evidence"
    assert "符合退款条件" not in result["answer"]
    assert control.preparations == [] and control.commits == []


@pytest.mark.parametrize("change", ["removed", "reindexed"])
async def test_expired_display_reference_cannot_erase_an_already_committed_refund(change):
    source = evidence()
    control = ControlTools([source])
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("create_refund_request", {"order_no": "10086", "reason": "change_mind"}),
        ],
        tools=control,
    )
    await graph.ainvoke(state, config, context=ctx)
    if change == "removed":
        control.knowledge.failure = BusinessError(
            "citation_not_found", "Control citation expired", 404
        )
    else:
        control.knowledge.citations[str(source.chunk_id)] = source.model_copy(
            update={"index_generation": 3}
        )
    result = await graph.ainvoke(
        Command(
            resume={
                "approval_id": str(control.approval_id),
                "confirmed": True,
            }
        ),
        config,
        context=ctx,
    )
    assert result["status"] == "completed"
    assert result["refund_result"]["refund"]["status"] == "pending_review"
    assert "已创建" in result["answer"]
    assert result["citations"] == []
    assert result["warning_codes"] == [
        "citation_not_found" if change == "removed" else "citation_changed"
    ]
    assert len(control.commits) == 1


@pytest.mark.parametrize("step_budget", [6, 7])
async def test_sensitive_write_reserves_enough_steps_before_preparing_an_approval(step_budget):
    control = ControlTools([evidence()])
    graph, ctx, config, state = graph_case(
        [
            route("order"),
            planned("create_refund_request", {"order_no": "10086", "reason": "change_mind"}),
        ],
        tools=control,
        agent_max_steps=step_budget,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    if step_budget == 6:
        assert result["status"] == "failed"
        assert result["error_code"] == "agent_step_limit"
        assert control.effects == [] and control.preparations == [] and control.commits == []
    else:
        assert result["__interrupt__"]
        resumed = await graph.ainvoke(
            Command(
                resume={
                    "approval_id": str(control.approval_id),
                    "confirmed": True,
                }
            ),
            config,
            context=ctx,
        )
        assert resumed["status"] == "completed"
        assert resumed["step_count"] == 7
        assert len(control.commits) == 1


async def test_history_budget_keeps_complete_user_assistant_pairs():
    graph, ctx, config, state = graph_case([route("unsupported")], agent_max_history_messages=3)
    state["history"] = [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ]
    result = await graph.ainvoke(state, config, context=ctx)
    assert len(result["history"]) == 2
    assert [item["role"] for item in result["history"]] == ["user", "assistant"]
    assert result["history"][0]["content"] == state["input"]


async def test_sensitive_write_cannot_be_mixed_with_other_tools_in_the_same_model_turn():
    first = planned("create_refund_request", {"order_no": "10086", "reason": "change_mind"})
    extra = planned("get_logistics", {"order_no": "10086"})
    first.tool_calls.extend(extra.tool_calls)
    first.message["tool_calls"].extend(extra.message["tool_calls"])
    graph, ctx, config, state = graph_case(
        [route("order"), first], tools=ControlTools([evidence()])
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == "invalid_model_plan"
    assert ctx.tools.effects == [] and ctx.tools.preparations == [] and ctx.tools.commits == []
