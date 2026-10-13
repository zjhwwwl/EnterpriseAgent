"""Real graph, checkpoints, PostgreSQL and CPU RAG with a controlled chat script.

The script is a deterministic test driver, not an LLM and not evidence of model
quality, provider latency or token efficiency. Every business mutation, policy
retrieval and checkpoint in these tests uses the real implementation.
"""

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from enterprise_agent.agents.checkpoint import AgentCheckpointRuntime
from enterprise_agent.agents.llm import ChatReply, ModelCallError, ModelToolCall
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    AgentSession,
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    Order,
    RefundPolicyVersion,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    User,
)
from enterprise_agent.schemas.agent import ResumeInput
from enterprise_agent.security import Principal
from enterprise_agent.tools import BusinessTools

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.rag]
NOW = datetime(2026, 10, 12, tzinfo=UTC)
REFUND_MESSAGE = "订单 10086 的商品不想要了，请申请无理由退款。"
REFUND_ARGUMENTS = {"order_no": "10086", "reason": "change_mind"}


def tool_reply(name: str, arguments: dict[str, Any]) -> ChatReply:
    call = ModelToolCall("test_" + uuid4().hex, name, arguments)
    return ChatReply(
        content="",
        tool_calls=[call],
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        },
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        elapsed_ms=0,
    )


def final_reply(content="查询已完成") -> ChatReply:
    return ChatReply(
        content=content,
        tool_calls=[],
        message={"role": "assistant", "content": content},
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        elapsed_ms=0,
    )


class ScriptedChatModel:
    """Consumes explicit control replies; exhaustion catches unexpected model calls."""

    def __init__(self, replies: list[ChatReply | Exception]):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, *, tools=None) -> ChatReply:
        self.calls.append({"messages": messages, "tools": tools})
        if not self.replies:
            raise AssertionError("The controlled model script has no remaining replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        offered = {tool["function"]["name"] for tool in tools or []}
        assert all(call.name in offered for call in reply.tool_calls)
        return reply


def refund_model() -> ScriptedChatModel:
    return ScriptedChatModel(
        [
            tool_reply("route_task", {"intent": "order", "query": REFUND_MESSAGE}),
            tool_reply("create_refund_request", REFUND_ARGUMENTS),
        ]
    )


@dataclass
class WorkflowEnvironment:
    sessions: Any
    settings: Any
    checkpoint: AgentCheckpointRuntime
    rag_runtime: Any
    actors: dict[str, Principal]

    def service(self, model, *, checkpoint=None, settings=None, clock=lambda: NOW):
        from enterprise_agent.services.agent import AgentService

        return AgentService(
            self.sessions,
            settings or self.settings,
            checkpoint or self.checkpoint,
            model,
            self.rag_runtime,
            clock=clock,
        )


@pytest_asyncio.fixture
async def workflow_env(pg_factory, pg_urls, pg_seed, rag_settings, rag_runtime, pg_rag_service):
    async with pg_factory() as session:
        users = await session.scalars(select(User))
        actors = {user.username: Principal(user.id, user.role) for user in users}
        policy = await session.scalar(
            select(RefundPolicyVersion).where(RefundPolicyVersion.policy_code == "STANDARD_RETURN")
        )
        document_id = policy.document_id
    await pg_rag_service.reindex(actors["demo_admin"], document_id)
    checkpoint = AgentCheckpointRuntime(pg_urls[0])
    await checkpoint.open()
    try:
        assert await checkpoint.is_ready()
        yield WorkflowEnvironment(pg_factory, rag_settings, checkpoint, rag_runtime, actors)
    finally:
        await checkpoint.close()


async def order_snapshot(factory):
    """Inspect actual rows for the fixed order; no seeded historical refunds qualify."""
    async with factory() as session:
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        approvals = list(
            await session.scalars(
                select(ApprovalRequest).where(ApprovalRequest.order_id == order.id)
            )
        )
        refunds = list(
            await session.scalars(select(RefundRequest).where(RefundRequest.order_id == order.id))
        )
        tickets = await session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.order_id == order.id)
        )
        audits = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.details["order_id"].as_integer() == order.id)
            )
        )
        return {
            "order": order,
            "approvals": approvals,
            "refunds": refunds,
            "tickets": tickets,
            "audits": audits,
        }


async def pending_task_id(factory, session_id):
    async with factory() as session:
        record = await session.get(AgentSession, session_id)
        assert record.current_task_id is not None
        return record.current_task_id


async def test_waiting_approval_has_no_refund_and_task_bound_prepare_is_idempotent(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    service = env.service(model)
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, REFUND_MESSAGE)
    assert task.status == "waiting_approval"
    summary = task.result["approval"]
    approval_id = UUID(summary["approval_id"])
    assert summary["requires_confirmation"] is True and summary["amount_cents"] == 12900
    assert len(model.calls) == 2 and not model.replies
    snapshot = await order_snapshot(env.sessions)
    assert not snapshot["refunds"] and snapshot["tickets"] == 0
    assert len(snapshot["approvals"]) == 1
    assert snapshot["approvals"][0].status == ApprovalStatus.PENDING
    assert snapshot["approvals"][0].task_id == task.id
    tools = BusinessTools(env.sessions, env.settings, env.rag_runtime, clock=lambda: NOW)
    replay = await tools.prepare_refund(actor, REFUND_ARGUMENTS, task_id=task.id)
    assert replay.approval_id == approval_id
    repeated = await order_snapshot(env.sessions)
    assert len(repeated["approvals"]) == 1
    assert sum(audit.action == "refund.prepare" for audit in repeated["audits"]) == 1
    assert task.result["citations"]
    for citation in task.result["citations"]:
        actual = await tools.knowledge.citation(actor, UUID(citation["chunk_id"]))
        assert actual.text == citation["text"]


async def test_checkpoint_restart_confirms_once_and_blocks_another_customer(workflow_env, pg_urls):
    env = workflow_env
    actor = env.actors["demo_customer"]
    service = env.service(refund_model())
    session = await service.create_session(actor)
    waiting = await service.send(actor, session.id, REFUND_MESSAGE)
    assert waiting.status == "waiting_approval"
    decision = ResumeInput(approval_id=waiting.result["approval"]["approval_id"], confirmed=True)
    other = env.actors["demo_other"]
    for operation in (
        lambda: service.get_task(other, waiting.id),
        lambda: service.resume(other, waiting.id, decision),
        lambda: service.send(other, session.id, "查询订单 10086"),
    ):
        with pytest.raises(BusinessError) as caught:
            await operation()
        assert caught.value.status_code == 404
    await env.checkpoint.close()
    restarted = AgentCheckpointRuntime(pg_urls[0])
    await restarted.open()
    try:
        model_after_restart = ScriptedChatModel([])
        restored = env.service(model_after_restart, checkpoint=restarted)
        completed = await restored.resume(actor, waiting.id, decision)
        assert completed.status == "completed"
        repeated = await restored.resume(actor, waiting.id, decision)
        assert repeated.id == completed.id and repeated.result == completed.result
        assert repeated.status == "completed"
        with pytest.raises(BusinessError) as caught:
            await restored.resume(
                actor,
                waiting.id,
                ResumeInput(approval_id=decision.approval_id, confirmed=False),
            )
        assert caught.value.status_code == 409
        assert not model_after_restart.calls
        snapshot = await order_snapshot(env.sessions)
        assert len(snapshot["refunds"]) == snapshot["tickets"] == 1
        assert snapshot["refunds"][0].status == RefundStatus.PENDING_REVIEW
        assert snapshot["refunds"][0].amount_cents == 12900
        assert snapshot["approvals"][0].status == ApprovalStatus.CONSUMED
        assert sum(audit.action == "refund.create" for audit in snapshot["audits"]) == 1
        assert snapshot["order"].status.value == "delivered"
        assert "待审核" in completed.result["answer"]
    finally:
        await restarted.close()


async def test_human_rejection_is_audited_without_creating_refund(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    service = env.service(model)
    session = await service.create_session(actor)
    waiting = await service.send(actor, session.id, REFUND_MESSAGE)
    decision = ResumeInput(approval_id=waiting.result["approval"]["approval_id"], confirmed=False)
    rejected = await service.resume(actor, waiting.id, decision)
    assert rejected.status == "rejected"
    replay = await service.resume(actor, waiting.id, decision)
    assert replay.status == "rejected" and replay.result == rejected.result
    assert len(model.calls) == 2
    snapshot = await order_snapshot(env.sessions)
    assert snapshot["approvals"][0].status == ApprovalStatus.REJECTED
    assert not snapshot["refunds"] and snapshot["tickets"] == 0
    async with env.sessions() as session:
        assert (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "refund.confirm_rejected",
                    AuditLog.resource_id == str(decision.approval_id),
                )
            )
            == 1
        )


async def test_one_waiting_turn_blocks_same_session_and_other_user_lists_are_empty(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    service = env.service(model)
    session = await service.create_session(actor)
    waiting = await service.send(actor, session.id, REFUND_MESSAGE)
    assert waiting.status == "waiting_approval"
    with pytest.raises(BusinessError) as caught:
        await service.send(actor, session.id, "查询订单 10086 物流")
    assert caught.value.code == "agent_session_busy" and caught.value.status_code == 409
    assert len(model.calls) == 2
    assert (await service.list_tasks(actor)).total == 1
    assert (await service.list_sessions(actor)).total == 1
    other = env.actors["demo_other"]
    assert (await service.list_tasks(other)).total == 0
    assert (await service.list_sessions(other)).total == 0


async def test_worker_loss_after_prepare_recovers_same_approval(workflow_env, monkeypatch):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    original = BusinessTools.prepare_refund
    prepared_ids = []

    async def lose_worker_after_commit(self, actor, arguments, *, task_id=None):
        result = await original(self, actor, arguments, task_id=task_id)
        prepared_ids.append(result.approval_id)
        if len(prepared_ids) == 1:
            # The database transaction has actually committed. Cancellation models
            # losing this worker before the graph can checkpoint its node result.
            raise asyncio.CancelledError("Controlled worker loss after database commit")
        return result

    monkeypatch.setattr(BusinessTools, "prepare_refund", lose_worker_after_commit)
    service = env.service(model)
    session = await service.create_session(actor)
    interrupted = await service.send(actor, session.id, REFUND_MESSAGE)
    assert interrupted.status == "failed" and interrupted.error_code == "agent_checkpoint_error"
    assert interrupted.token_usage["usage_complete"] is False
    task_id = await pending_task_id(env.sessions, session.id)
    first = await order_snapshot(env.sessions)
    assert len(first["approvals"]) == 1 and not first["refunds"]
    recovered = await service.recover(actor, task_id)
    assert recovered.status == "waiting_approval"
    assert recovered.token_usage["usage_complete"] is False
    assert recovered.token_usage["interrupted_usage_unknown"] is True
    assert UUID(recovered.result["approval"]["approval_id"]) == first["approvals"][0].id
    assert len(set(prepared_ids)) == 1
    final = await order_snapshot(env.sessions)
    assert len(final["approvals"]) == 1 and not final["refunds"]
    assert sum(audit.action == "refund.prepare" for audit in final["audits"]) == 1
    assert len(model.calls) == 2


async def test_worker_loss_after_refund_commit_recovers_without_duplicate_write(
    workflow_env, monkeypatch
):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    service = env.service(model)
    session = await service.create_session(actor)
    waiting = await service.send(actor, session.id, REFUND_MESSAGE)
    original = BusinessTools.execute_confirmed_refund
    committed_ids = []

    async def lose_worker_after_commit(
        self, actor, approval_id, idempotency_key, *, confirmed=True
    ):
        result = await original(self, actor, approval_id, idempotency_key, confirmed=confirmed)
        committed_ids.append(result.refund.id)
        if len(committed_ids) == 1:
            raise asyncio.CancelledError("Controlled worker loss after refund commit")
        return result

    monkeypatch.setattr(BusinessTools, "execute_confirmed_refund", lose_worker_after_commit)
    decision = ResumeInput(approval_id=waiting.result["approval"]["approval_id"], confirmed=True)
    interrupted = await service.resume(actor, waiting.id, decision)
    assert interrupted.status == "failed" and interrupted.error_code == "agent_checkpoint_error"
    assert interrupted.result["refund_result"]["refund"]["id"] == str(committed_ids[0])
    snapshot = await order_snapshot(env.sessions)
    assert len(snapshot["refunds"]) == snapshot["tickets"] == 1
    recovered = await service.recover(actor, waiting.id)
    assert recovered.status == "completed"
    assert recovered.token_usage["usage_complete"] is False
    assert recovered.token_usage["interrupted_usage_unknown"] is True
    stored = await service.get_task(actor, waiting.id)
    assert stored.token_usage == recovered.token_usage
    assert len(set(committed_ids)) == 1
    snapshot = await order_snapshot(env.sessions)
    assert len(snapshot["refunds"]) == snapshot["tickets"] == 1
    assert sum(audit.action == "refund.create" for audit in snapshot["audits"]) == 1
    assert len(model.calls) == 2


async def test_bad_tool_arguments_fail_without_approval_or_refund(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = ScriptedChatModel(
        [
            tool_reply("route_task", {"intent": "order", "query": REFUND_MESSAGE}),
            tool_reply("create_refund_request", {"order_no": 10086, "reason": "change_mind"}),
        ]
    )
    service = env.service(model)
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, REFUND_MESSAGE)
    assert task.status == "failed" and task.error_code == "invalid_tool_arguments"
    snapshot = await order_snapshot(env.sessions)
    assert not snapshot["approvals"] and not snapshot["refunds"]
    assert snapshot["tickets"] == 0 and len(model.calls) == 2


async def test_endless_read_tool_planning_stops_at_explicit_tool_limit(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = ScriptedChatModel(
        [tool_reply("route_task", {"intent": "order", "query": "查询订单 10086"})]
        + [tool_reply("get_order", {"order_no": "10086"}) for _ in range(10)]
    )
    settings = env.settings.model_copy(update={"agent_max_tool_calls": 2})
    service = env.service(model, settings=settings)
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, "查询订单 10086")
    assert task.status == "completed" and task.error_code is None
    assert len(model.calls) == 3  # router + two permitted reads; server reports known facts.
    assert len(model.calls) == 3
    snapshot = await order_snapshot(env.sessions)
    assert not snapshot["approvals"] and not snapshot["refunds"]


async def test_provider_retries_stop_with_explicit_unknown_usage(workflow_env):
    env = workflow_env
    model = ScriptedChatModel(
        [ModelCallError("agent_model_timeout", "模型请求超时", elapsed_ms=25) for _ in range(4)]
    )
    service = env.service(model)
    actor = env.actors["demo_customer"]
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, "查询订单 10086")
    assert task.status == "failed" and task.error_code == "agent_model_timeout"
    assert len(model.calls) == env.settings.agent_max_retries + 1 == 3
    assert task.token_usage["unknown_usage_calls"] == 3
    assert task.token_usage["model_calls"] == 3


async def test_sensitive_operation_reserves_steps_before_preparing_approval(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = refund_model()
    settings = env.settings.model_copy(update={"agent_max_steps": 6})
    service = env.service(model, settings=settings)
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, REFUND_MESSAGE)
    assert task.status == "failed" and task.error_code == "agent_step_limit"
    snapshot = await order_snapshot(env.sessions)
    assert not snapshot["approvals"] and not snapshot["refunds"] and snapshot["tickets"] == 0
    assert len(model.calls) == 2


async def test_reindex_during_approval_keeps_committed_business_result_and_warns(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    service = env.service(refund_model())
    session = await service.create_session(actor)
    waiting = await service.send(actor, session.id, REFUND_MESSAGE)
    old_citations = waiting.result["citations"]
    assert old_citations
    policy_document = UUID(waiting.result["approval"]["policy"]["document_id"])
    await service.tools.knowledge.reindex(env.actors["demo_admin"], policy_document)
    decision = ResumeInput(approval_id=waiting.result["approval"]["approval_id"], confirmed=True)
    completed = await service.resume(actor, waiting.id, decision)
    assert completed.status == "completed" and completed.error_code is None
    assert completed.result["citations"] == []
    assert "citation_not_found" in completed.result["warning_codes"]
    assert completed.result["refund_result"]["refund"]["status"] == "pending_review"
    assert "待审核" in completed.result["answer"]
    snapshot = await order_snapshot(env.sessions)
    assert len(snapshot["refunds"]) == snapshot["tickets"] == 1
    assert sum(audit.action == "refund.create" for audit in snapshot["audits"]) == 1


async def test_invalid_router_arguments_keep_controlled_usage_accounting(workflow_env):
    env = workflow_env
    invalid = tool_reply("route_task", {"intent": "delete_everything", "query": "查询订单 10086"})
    # Explicit fixture metadata tests arithmetic, not real provider consumption.
    invalid = replace(
        invalid, usage={"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}
    )
    model = ScriptedChatModel([invalid])
    service = env.service(model)
    actor = env.actors["demo_customer"]
    session = await service.create_session(actor)
    task = await service.send(actor, session.id, "查询订单 10086")
    assert task.status == "failed" and task.error_code == "invalid_model_plan"
    assert task.token_usage["model_calls"] == 1
    assert task.token_usage["prompt_tokens"] == 13
    assert task.token_usage["completion_tokens"] == 7
    assert task.token_usage["total_tokens"] == 20
    assert len(model.calls) == 1


async def test_multi_turn_history_is_persisted_and_order_reports_use_database_facts(workflow_env):
    env = workflow_env
    actor = env.actors["demo_customer"]
    model = ScriptedChatModel(
        [
            tool_reply("route_task", {"intent": "order", "query": "查询订单 10086"}),
            tool_reply("get_order", {"order_no": "10086"}),
            final_reply("模型控制脚本声称物流丢失，这不是数据库事实"),
            tool_reply("route_task", {"intent": "order", "query": "查询订单 10086 物流"}),
            tool_reply("get_logistics", {"order_no": "10086"}),
            final_reply(),
        ]
    )
    service = env.service(model)
    session = await service.create_session(actor)
    first = await service.send(actor, session.id, "查询订单 10086")
    assert first.status == "completed"
    assert "delivered" in first.result["answer"]
    assert "物流丢失" not in first.result["answer"]
    second = await service.send(actor, session.id, "那物流呢")
    assert second.status == "completed" and second.id != first.id
    assert "SIM-10086" in second.result["answer"]
    history = model.calls[3]["messages"]
    assert {"role": "assistant", "content": first.result["answer"]} in history
    assert {"role": "user", "content": "查询订单 10086"} in history
    assert first.token_usage["model_calls"] == second.token_usage["model_calls"] == 3
    assert (await service.list_tasks(actor, session_id=session.id)).total == 2
