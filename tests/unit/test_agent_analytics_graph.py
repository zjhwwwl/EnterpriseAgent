"""Actual LangGraph with controlled responses; these are not live model metrics."""

import copy
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from enterprise_agent.agents.graph import (
    AgentContext,
    SQLPlan,
    analytics_execution,
    build_graph,
    definition,
    initial_state,
    validation,
)
from enterprise_agent.agents.llm import (
    ChatReply,
    DeepSeekChatModel,
    ModelCallError,
    ModelToolCall,
)
from enterprise_agent.analytics.sql import validate_sql
from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole
from enterprise_agent.schemas.analytics import QueryResult
from enterprise_agent.security import Principal

USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
SQL = "SELECT SUM(gross_revenue_cents) AS gross_revenue_cents FROM ea_analytics.daily_orders"


def reply(name, arguments, *, content=""):
    identifier = "controlled_" + uuid4().hex
    return ChatReply(
        content,
        [ModelToolCall(identifier, name, arguments)],
        {
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": identifier,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
        dict(USAGE),
        1,
    )


def route(intent="analytics", query="统计最近七天的销售额"):
    return reply("route_task", {"intent": intent, "query": query})


def plan(**overrides):
    return reply("plan_analytics_sql", {"sql": SQL, "metric_ids": ["paid_sales"], **overrides})


class ControlledModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    async def complete(self, messages, *, tools=None):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        assert self.replies, "Unexpected model invocation in bounded analysis workflow"
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class ControlTools:
    def __init__(self):
        self.executions = []

    async def execute(self, actor, name, arguments):
        self.executions.append((actor, name, arguments))
        assert name == "search_knowledge"
        return {"citations": [], "status": "insufficient_evidence"}


class ControlledAnalytics:
    """A node contract control, never evidence of SQL or database correctness."""

    def __init__(self):
        self.catalog_calls, self.executions, self.renders = [], [], []
        self.failure = None
        self.query = {
            "normalized_sql": SQL,
            "columns": ["gross_revenue_cents"],
            "rows": [[123456]],
            "row_count": 1,
            "truncated": False,
            "elapsed_ms": 1.0,
        }

    def catalog(self, actor):
        self.catalog_calls.append(actor)
        if actor.role not in (UserRole.ANALYST, UserRole.ADMIN):
            raise BusinessError("analytics_forbidden", "当前用户没有运营分析权限。", 403)
        return {
            "today_shanghai": "2026-10-12",
            "trusted_now": "2026-10-12T00:00:00+00:00",
            "views": {"ea_analytics.daily_orders": {"gross_revenue_cents": "bigint"}},
            "metrics": {"paid_sales": "商品及运费实付，单位分，不扣减退款"},
        }

    async def execute(self, actor, sql):
        self.executions.append((actor, sql))
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.query)

    def render(self, result: Any, metric_ids):
        serialized = result.model_dump(mode="json") if isinstance(result, QueryResult) else result
        self.renders.append((copy.deepcopy(serialized), list(metric_ids)))
        return {
            "answer": "查询结果：支付销售额 1234.56 元。数据未提供业务变化原因。",
            "query": serialized,
            "chart": None,
            "metric_ids": list(metric_ids),
            "metric_definitions": {"paid_sales": "商品及运费实付，不扣减退款"},
        }


def case(replies, *, role=UserRole.ANALYST, analytics=True, **overrides):
    service = ControlledAnalytics() if analytics is True else analytics
    model, tools = ControlledModel(replies), ControlTools()
    ctx = AgentContext(
        Principal(7, role), tools, model, Settings(_env_file=None, **overrides), service
    )
    identifier = uuid4()
    config = {"configurable": {"thread_id": str(identifier)}, "recursion_limit": 80}
    state = initial_state(identifier, "统计最近七天的销售额", [])
    return build_graph(InMemorySaver()), ctx, config, state


@pytest.mark.parametrize("role", [UserRole.ANALYST, UserRole.ADMIN])
async def test_analysis_uses_one_query_and_database_report_without_a_generation_model(role):
    graph, ctx, config, state = case(
        [
            route(),
            reply(
                "plan_analytics_sql",
                {"sql": SQL, "metric_ids": ["paid_sales"]},
                content="销售增长一定因为广告！",
            ),
        ],
        role=role,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed"
    assert result["intent"] == "analytics"
    assert len(ctx.model.requests) == 2
    assert ctx.analytics.executions == [(ctx.actor, SQL)]
    assert result["tool_count"] == 1 and result["step_count"] == 5
    assert result["analysis"]["query"]["rows"] == [[123456]]
    assert result["facts"][0]["result"] == result["analysis"]["query"]
    assert result["answer"] == result["analysis"]["answer"]
    assert "广告" not in result["answer"]
    assert result["token_usage"] == {
        "model_calls": 2,
        **{key: value * 2 for key, value in USAGE.items()},
        "unknown_usage_calls": 0,
    }
    assert ctx.tools.executions == []
    assert result["history"][-1] == {"role": "assistant", "content": result["answer"]}


@pytest.mark.parametrize("role", [UserRole.CUSTOMER, UserRole.SUPPORT])
async def test_analysis_authorization_precedes_catalog_disclosure_and_sql_planning(role):
    graph, ctx, config, state = case([route()], role=role)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed" and result["error_code"] == "analytics_forbidden"
    assert len(ctx.model.requests) == 1
    assert ctx.analytics.executions == [] and ctx.analytics.renders == []
    assert result["analysis"] is None and result["tool_count"] == 0
    assert "daily_orders" not in json.dumps(ctx.model.requests)


async def test_missing_analytics_runtime_is_safe_and_does_not_plan_sql():
    graph, ctx, config, state = case([route()], analytics=None)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed" and result["error_code"] == "analytics_unavailable"
    assert len(ctx.model.requests) == 1 and not ctx.tools.executions


@pytest.mark.parametrize(
    "arguments",
    [
        {"sql": SQL, "metric_ids": []},
        {"sql": SQL, "metric_ids": ["made_up_metric"]},
        {"sql": 42, "metric_ids": ["paid_sales"]},
        {"sql": "", "metric_ids": ["paid_sales"]},
        {"sql": "a" * 8001, "metric_ids": ["paid_sales"]},
        {"sql": SQL, "metric_ids": ["paid_sales"], "actor_id": 1},
    ],
)
async def test_invalid_plan_is_terminal_retains_usage_and_never_executes(arguments):
    graph, ctx, config, state = case([route(), reply("plan_analytics_sql", arguments)])
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed" and result["error_code"] == "invalid_model_plan"
    assert result["token_usage"]["total_tokens"] == 16
    assert result["token_usage"]["model_calls"] == 2
    assert not ctx.analytics.executions and result["analysis"] is None


@pytest.mark.parametrize("variant", ["wrong_name", "multiple", "no_call"])
async def test_analysis_plan_requires_one_correct_tool_call(variant):
    selected = plan()
    if variant == "wrong_name":
        selected = reply("get_order", {"order_no": "10086"})
    elif variant == "multiple":
        selected.tool_calls.extend(plan().tool_calls)
    else:
        selected = ChatReply(
            "猜测金额 900000 元", [], {"role": "assistant", "content": "猜测"}, dict(USAGE), 1
        )
    graph, ctx, config, state = case([route(), selected])
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["error_code"] == "invalid_model_plan" and result["status"] == "failed"
    assert not ctx.analytics.executions
    assert result["token_usage"]["total_tokens"] == 16


async def test_retryable_model_failure_retries_only_planning_and_keeps_unknown_usage():
    graph, ctx, config, state = case(
        [route(), ModelCallError("agent_model_unavailable", "模型暂时不可用。"), plan()],
        agent_max_retries=1,
    )
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "completed" and len(ctx.analytics.executions) == 1
    assert result["token_usage"]["model_calls"] == 3
    assert result["token_usage"]["unknown_usage_calls"] == 1
    assert result["token_usage"]["total_tokens"] == 16


async def test_model_retry_budget_is_finite_and_known_usage_is_retained():
    failure = ModelCallError("agent_model_rate_limited", "模型请求过于频繁。", usage=dict(USAGE))
    graph, ctx, config, state = case([route(), failure, failure], agent_max_retries=1)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed" and result["error_code"] == "agent_model_rate_limited"
    assert result["token_usage"]["model_calls"] == 3
    assert result["token_usage"]["total_tokens"] == 24
    assert not ctx.analytics.executions


@pytest.mark.parametrize("code", ["analytics_sql_rejected", "analytics_query_timeout"])
async def test_query_failure_terminates_without_sql_retry_and_records_attempt(code):
    graph, ctx, config, state = case([route(), plan()], agent_max_retries=2)
    ctx.analytics.failure = BusinessError(code, "查询无法安全执行。", 422)
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed" and result["error_code"] == code
    assert len(ctx.analytics.executions) == 1 and len(ctx.model.requests) == 2
    assert result["tool_count"] == 1 and result["analysis"] is None
    assert result["calls"] == []
    assert [step["error_code"] for step in result["steps"] if step["node"] == "business_tool"] == [
        code
    ]


@pytest.mark.parametrize("limit", ["tools", "steps"])
async def test_existing_execution_budgets_stop_analysis_before_database_work(limit):
    graph, ctx, config, state = case([route(), plan()], agent_max_steps=6, agent_max_tool_calls=1)
    if limit == "tools":
        state["tool_count"] = 1
    else:
        state["step_count"] = 3
    result = await graph.ainvoke(state, config, context=ctx)
    assert result["status"] == "failed"
    assert result["error_code"] == ("agent_tool_limit" if limit == "tools" else "agent_step_limit")
    assert ctx.analytics.executions == []


async def test_followup_requeries_facts_uses_resolved_intent_and_keeps_complete_history():
    graph, ctx, config, state = case(
        [route(), plan(), route(query="统计最近十四天的销售额"), plan()],
        agent_max_history_messages=3,
    )
    first = await graph.ainvoke(state, config, context=ctx)
    followup = initial_state(uuid4(), "那最近十四天呢？", first["history"])
    second = await graph.ainvoke(followup, config, context=ctx)
    assert second["status"] == "completed" and len(ctx.analytics.executions) == 2
    planner_payload = json.loads(ctx.model.requests[3]["messages"][1]["content"])
    assert planner_payload["resolved_query"] == "统计最近十四天的销售额"
    assert planner_payload["original_request"] == "那最近十四天呢？"
    assert planner_payload["catalog"]["today_shanghai"] == "2026-10-12"
    assert len(ctx.model.requests[3]["messages"]) == 2
    assert second["analysis"] is not None
    assert len(second["history"]) == 2 and second["history"][0]["content"] == followup["input"]


async def test_new_knowledge_turn_clears_previous_analytics_result_in_same_checkpoint():
    graph, ctx, config, state = case(
        [route(), plan(), route("knowledge"), reply("rewrite_query", {"query": "会员权益"})]
    )
    first = await graph.ainvoke(state, config, context=ctx)
    second = await graph.ainvoke(
        initial_state(uuid4(), "会员有哪些权益？", first["history"]), config, context=ctx
    )
    assert second["status"] == "completed" and second["analysis"] is None
    assert "无法确认" in second["answer"] and "1234.56" not in second["answer"]
    assert len(ctx.analytics.executions) == 1


async def test_execution_revalidates_corrupt_checkpoint_plan_before_calling_service():
    _, ctx, _, state = case([])
    state["calls"] = [
        {
            "name": "plan_analytics_sql",
            "arguments": {"sql": SQL, "metric_ids": ["paid_sales"], "confirmed": True},
        }
    ]
    with pytest.raises(BusinessError) as caught:
        await analytics_execution(state, ctx)
    assert caught.value.code == "invalid_model_plan" and not ctx.analytics.executions


async def test_validation_rejects_missing_query_or_answer_inconsistent_with_analysis():
    _, ctx, _, state = case([])
    state.update(
        intent="analytics", analysis={"answer": "数据报告", "query": None}, answer="伪造回答"
    )
    with pytest.raises(BusinessError) as caught:
        await validation(state, ctx)
    assert caught.value.code == "analytics_result_invalid"


async def test_validation_accepts_valid_input_expanded_by_sql_normalization():
    columns = [chr(ord("a") + index) for index in range(16)]
    literal = "x" * 489
    sql = "SELECT " + ",".join(f"'{literal}'AS {column}" for column in columns)
    sql += " FROM ea_analytics.daily_orders LIMIT 1"
    SQLPlan(sql=sql, metric_ids=["paid_sales"])
    normalized = validate_sql(sql).normalized_sql
    assert len(sql) <= 8000 < len(normalized)
    _, ctx, _, state = case([])
    query = QueryResult(
        normalized_sql=normalized,
        columns=columns,
        rows=[[literal] * len(columns)],
        row_count=1,
        truncated=False,
        elapsed_ms=1.0,
    )
    analysis = ctx.analytics.render(query, ["paid_sales"])
    state.update(
        intent="analytics",
        analysis=analysis,
        answer=analysis["answer"],
        facts=[{"name": "execute_analytics_sql", "result": copy.deepcopy(analysis["query"])}],
    )
    await validation(state, ctx)


@pytest.mark.parametrize("corruption", ["chart", "row_count", "row_width", "answer", "facts"])
async def test_validation_checks_report_chart_row_shape_and_independent_query_facts(corruption):
    _, ctx, _, state = case([])
    query = QueryResult.model_validate(ctx.analytics.query)
    analysis = ctx.analytics.render(query, ["paid_sales"])
    state.update(
        intent="analytics",
        analysis=analysis,
        answer=analysis["answer"],
        facts=[{"name": "execute_analytics_sql", "result": copy.deepcopy(analysis["query"])}],
    )
    if corruption == "chart":
        analysis["chart"] = {"type": "bar", "labels": ["1"], "series": [{"data": [999999]}]}
    elif corruption == "row_count":
        analysis["query"]["row_count"] = 2
    elif corruption == "row_width":
        analysis["query"]["rows"][0].append(1)
    elif corruption == "answer":
        state["answer"] = "金额凭空增加"
    else:
        state["facts"][-1]["result"]["rows"][0][0] = 999999
    with pytest.raises(BusinessError) as caught:
        await validation(state, ctx)
    assert caught.value.code == "analytics_result_invalid"


async def test_sql_control_tool_uses_named_choice_with_thinking_disabled():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": plan().message}],
                "usage": dict(USAGE),
            },
        )

    model = DeepSeekChatModel(
        Settings(_env_file=None, llm_enabled=True, deepseek_api_key="protocol-only-not-real"),
        transport=httpx.MockTransport(handler),
    )
    try:
        selected = await model.complete(
            [{"role": "user", "content": "统计销售额"}],
            tools=definition("plan_analytics_sql", "只读分析规划", SQLPlan),
        )
    finally:
        await model.aclose()
    assert selected.tool_calls[0].name == "plan_analytics_sql"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "plan_analytics_sql"},
    }
    assert len(requests) == 1
