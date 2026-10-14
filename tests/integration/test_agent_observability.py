"""Real PostgreSQL/LangGraph snapshots with a controlled model, not model evaluation."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langsmith import Client
from pydantic import SecretStr
from sqlalchemy import select

from enterprise_agent.agents.checkpoint import AgentCheckpointRuntime
from enterprise_agent.agents.llm import ChatReply, ModelCallError, ModelToolCall
from enterprise_agent.application import create_app
from enterprise_agent.config import Settings
from enterprise_agent.models import RefundPolicyVersion, User
from enterprise_agent.observability import TaskTraceRecorder
from enterprise_agent.schemas.agent import ResumeInput
from enterprise_agent.security import Principal
from enterprise_agent.services.agent import AgentService

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


def reply(name=None, arguments=None):
    return ChatReply(
        content="" if name else "controlled-model-text",
        tool_calls=[ModelToolCall("control-" + uuid4().hex, name, arguments)] if name else [],
        message={"role": "assistant", "content": "controlled-model-message"},
        # Synthetic counters test accounting only, never provider billing.
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        elapsed_ms=0.5,
    )


class ControlledReplies:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def complete(self, messages, *, tools=None):
        self.calls += 1
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def trace_rows(recorder):
    return [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()]


async def test_real_api_execution_records_request_hash_and_never_auto_exports(
    pg_urls, pg_seed, tmp_path, monkeypatch
):
    export_calls = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "private-ambient-unit-key")
    monkeypatch.setattr(Client, "create_run", lambda *args, **kwargs: export_calls.append(True))
    model = ControlledReplies(
        [
            reply("route_task", {"intent": "order", "query": "查询订单10086物流"}),
            reply("get_logistics", {"order_no": "10086"}),
            reply(),
        ]
    )
    configured = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path,
        database_url=SecretStr(pg_urls[0]),
        jwt_secret=SecretStr(uuid4().hex + uuid4().hex),
    )
    app = create_app(configured, configure_logs=False, agent_model=model)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
    ):
        login = await client.post(
            "/api/v1/auth/login", json={"username": "demo_customer", "password": "DemoOnly!2026"}
        )
        headers = {"Authorization": "Bearer " + login.json()["access_token"]}
        session = await client.post("/api/v1/agent/sessions", headers=headers)
        request_id = "private-request-correlation-10086"
        response = await client.post(
            "/api/v1/agent/sessions/" + session.json()["id"] + "/messages",
            headers={**headers, "X-Request-ID": request_id},
            json={"message": "查询订单10086物流"},
        )
        assert response.status_code == 200 and response.json()["status"] == "completed"
        assert "SIM-10086" in response.json()["result"]["answer"]
        snapshot = trace_rows(app.state.trace_recorder)[0]
        assert snapshot["task_id"] == response.json()["id"]
        assert snapshot["request_id_sha256"] == hashlib.sha256(request_id.encode()).hexdigest()
        assert snapshot["token_usage_cumulative"]["total_tokens"] == 30
        assert any(step["tool"] == "get_logistics" for step in snapshot["steps"])
        text = json.dumps(snapshot)
        assert all(
            private not in text
            for private in (
                "10086",
                "SIM-",
                "demo_customer",
                "查询",
                "controlled-model-message",
                request_id,
            )
        )
    assert export_calls == []


@pytest.mark.rag
async def test_real_approval_restart_retains_trace_id_and_cumulative_ledger(
    pg_factory, pg_urls, pg_seed, rag_settings, rag_runtime, pg_rag_service
):
    async with pg_factory() as session:
        user = await session.scalar(select(User).where(User.username == "demo_customer"))
        admin = await session.scalar(select(User).where(User.username == "demo_admin"))
        policy = await session.scalar(
            select(RefundPolicyVersion).where(RefundPolicyVersion.policy_code == "STANDARD_RETURN")
        )
        actor, administrator = Principal(user.id, user.role), Principal(admin.id, admin.role)
        document_id = policy.document_id
    await pg_rag_service.reindex(administrator, document_id)
    checkpoint = AgentCheckpointRuntime(pg_urls[0])
    await checkpoint.open()
    recorder = TaskTraceRecorder(rag_settings)
    model = ControlledReplies(
        [
            reply("route_task", {"intent": "order", "query": "订单10086申请无理由退款"}),
            reply("create_refund_request", {"order_no": "10086", "reason": "change_mind"}),
        ]
    )
    service = AgentService(
        pg_factory,
        rag_settings,
        checkpoint,
        model,
        rag_runtime,
        clock=lambda: NOW,
        trace_recorder=recorder,
    )
    try:
        session = await service.create_session(actor)
        waiting = await service.send(actor, session.id, "订单10086申请无理由退款")
        assert waiting.status == "waiting_approval"
        first = trace_rows(recorder)[0]
        assert first["token_usage_cumulative"]["total_tokens"] == 20
    finally:
        await checkpoint.close()
        recorder.close()
    restarted = AgentCheckpointRuntime(pg_urls[0])
    await restarted.open()
    after_recorder = TaskTraceRecorder(rag_settings)
    after_model = ControlledReplies([])
    after_service = AgentService(
        pg_factory,
        rag_settings,
        restarted,
        after_model,
        rag_runtime,
        clock=lambda: NOW,
        trace_recorder=after_recorder,
    )
    try:
        completed = await after_service.resume(
            actor,
            waiting.id,
            ResumeInput(
                approval_id=UUID(waiting.result["approval"]["approval_id"]), confirmed=True
            ),
        )
        assert completed.status == "completed" and after_model.calls == 0
        assert completed.result["refund_result"]["refund"]["status"] == "pending_review"
        rows = trace_rows(after_recorder)
        assert len(rows) == 2
        assert {row["trace_id"] for row in rows} == {str(waiting.id)}
        assert len({row["segment_id"] for row in rows}) == 2
        assert rows[-1]["token_usage_cumulative"]["total_tokens"] == 20
        assert rows[-1]["token_usage_cumulative"]["model_calls"] == 2
        assert rows[-1]["token_usage_cumulative"]["usage_complete"] is True
        assert rows[-1]["elapsed_ms_cumulative"] >= rows[0]["elapsed_ms_cumulative"]
        assert rows[-1]["steps_total"] > rows[0]["steps_total"]
        assert "10086" not in json.dumps(rows)
    finally:
        await restarted.close()
        after_recorder.close()


async def test_real_failed_task_records_unknown_usage_and_safe_failure_code(
    pg_factory, pg_urls, pg_seed, tmp_path
):
    async with pg_factory() as session:
        user = await session.scalar(select(User).where(User.username == "demo_customer"))
        actor = Principal(user.id, user.role)
    configured = Settings(_env_file=None, app_env="test", data_dir=tmp_path, agent_max_retries=0)
    checkpoint = AgentCheckpointRuntime(pg_urls[0])
    await checkpoint.open()
    model = ControlledReplies(
        [ModelCallError("agent_model_unavailable", "private-provider-error", elapsed_ms=0.5)]
    )
    service = AgentService(pg_factory, configured, checkpoint, model)
    try:
        session = await service.create_session(actor)
        failed = await service.send(actor, session.id, "查询本人订单物流")
        assert failed.status == "failed"
        snapshot = trace_rows(service.trace_recorder)[0]
        assert snapshot["error_code"] == "agent_model_unavailable"
        usage = snapshot["token_usage_cumulative"]
        assert usage["model_calls"] == usage["unknown_usage_calls"] == 1
        assert usage["total_tokens"] == 0 and usage["usage_complete"] is False
        assert "private-provider-error" not in json.dumps(snapshot)
    finally:
        await checkpoint.close()
