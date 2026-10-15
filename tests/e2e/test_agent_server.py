"""Real TCP/process restart + PostgreSQL/Chroma/CPU models; scripted planner only."""

import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from database_scope import container_scope_environment, require_test_database
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_local_server import _stop_owned_server

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.models import AuditLog, Order, RefundRequest, SupportTicket, User
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.security import Principal
from enterprise_agent.services.knowledge import KnowledgeService
from enterprise_agent.tools import BusinessTools

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.rag]
_PROVIDER_PREFIXES = ("EA_", "LANGSMITH", "LANGCHAIN", "OPENAI", "DEEPSEEK")


@pytest.fixture
def wire_settings(rag_settings, pg_urls):
    database = make_url(pg_urls[0])
    require_test_database(database)
    return rag_settings.model_copy(
        update={
            "agent_enabled": True,
            "database_url": SecretStr(pg_urls[0]),
            "jwt_secret": SecretStr(uuid4().hex + uuid4().hex),
            "rag_collection_prefix": "ea_e2e_" + uuid4().hex,
        }
    )


@pytest_asyncio.fixture
async def wire_runtime(wire_settings, rag_models, request):
    runtime = RagRuntime(wire_settings, models=rag_models)
    if not await runtime.call(runtime.store.ready):
        await runtime.close()
        if request.config.getoption("--require-rag"):
            pytest.fail("The real local Chroma service is required")
        pytest.skip("The real local Chroma service is not running")
    await runtime.call(lambda: runtime.store.collection)
    try:
        yield runtime
    finally:
        await asyncio.to_thread(runtime.store.client.delete_collection, runtime.store.name)
        await runtime.close()


def child_environment(settings, port):
    # A child only receives the ordinary test account, not owner/migration secrets.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(_PROVIDER_PREFIXES)
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EA_APP_ENV": "test",
            "EA_HOST": "127.0.0.1",
            "EA_PORT": str(port),
            "EA_DATABASE_URL": settings.database_url.get_secret_value(),
            "EA_JWT_SECRET": settings.jwt_secret.get_secret_value(),
            "EA_DATA_DIR": str(settings.data_dir),
            "EA_RAG_ENABLED": "true",
            "EA_AGENT_ENABLED": "true",
            "EA_LLM_ENABLED": "false",
            "EA_DEEPSEEK_API_KEY": "",
            "EA_REDIS_URL": "",
            "EA_RAG_MODEL_DIR": str(settings.rag_model_dir),
            "EA_RAG_MODEL_THREADS": str(settings.rag_model_threads),
            "EA_RAG_COLLECTION_PREFIX": settings.rag_collection_prefix,
            "EA_CHROMA_HOST": settings.chroma_host,
            "EA_CHROMA_PORT": str(settings.chroma_port),
            "EA_CHROMA_TIMEOUT_SECONDS": str(settings.chroma_timeout_seconds),
            "EA_RAG_CHUNK_SIZE": str(settings.rag_chunk_size),
            "EA_RAG_CHUNK_OVERLAP": str(settings.rag_chunk_overlap),
            "EA_CORS_ORIGINS": "[]",
            "EA_LOG_LEVEL": "INFO",
        }
    )
    environment.update(container_scope_environment())
    return environment


def start_owned_server(environment, log_file):
    return subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "tests" / "e2e" / "agent_control_server.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


async def await_ready(process, client):
    deadline = monotonic() + 45
    while monotonic() < deadline:
        assert process.poll() is None, "Owned controlled Uvicorn process exited before readiness"
        try:
            response = await client.get("/health/ready", timeout=4)
            if response.status_code == 200:
                body = response.json()
                assert {"database", "knowledge", "agent_checkpoint"} <= body["checks"].keys()
                assert all(value == "ok" for value in body["checks"].values())
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise AssertionError("Owned controlled Uvicorn process did not become ready")


async def login(client, username):
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": "DemoOnly!2026",
        },
    )
    assert response.status_code == 200
    return {
        "Authorization": "Bearer " + response.json()["access_token"],
        "X-Request-ID": "phase4-wire-restart",
    }


async def counts_for_order(factory, order_id):
    async with factory() as session:
        refunds = await session.scalar(
            select(func.count(RefundRequest.id)).where(RefundRequest.order_id == order_id)
        )
        tickets = await session.scalar(
            select(func.count(SupportTicket.id)).where(SupportTicket.order_id == order_id)
        )
        return {"refunds": refunds, "tickets": tickets}


async def test_real_http_confirmation_survives_process_restart_without_duplicate_refunds(
    tmp_path,
    pg_seed,
    pg_factory,
    wire_settings,
    wire_runtime,
):
    async with pg_factory() as session:
        users = list(await session.scalars(select(User)))
        actors = {item.username: Principal(item.id, item.role) for item in users}
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        order_id = order.id
    services = BusinessTools(
        pg_factory, wire_settings, wire_runtime, clock=lambda: datetime(2026, 10, 12, tzinfo=UTC)
    )
    eligibility = await services.refunds.get_eligibility(actors["demo_customer"], "10086")
    assert eligibility.eligible is True and eligibility.policy is not None
    await KnowledgeService(pg_factory, wire_settings, wire_runtime).reindex(
        actors["demo_admin"],
        eligibility.policy.document_id,
    )
    assert await counts_for_order(pg_factory, order_id) == {"refunds": 0, "tickets": 0}
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = child_environment(wire_settings, port)
    base_url = f"http://127.0.0.1:{port}"
    first_log, second_log = tmp_path / "agent-first.jsonl", tmp_path / "agent-second.jsonl"
    events = []
    with first_log.open("w", encoding="utf-8") as log_file:
        process = start_owned_server(environment, log_file)
        first_pid = process.pid
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=40, trust_env=False) as client:
                await await_ready(process, client)
                headers = await login(client, "demo_customer")
                created = await client.post("/api/v1/agent/sessions", headers=headers, json={})
                assert created.status_code == 201
                session_id = created.json()["id"]
                pending = await client.post(
                    f"/api/v1/agent/sessions/{session_id}/messages",
                    headers=headers,
                    json={"message": "为订单10086创建退款申请，原因是改变主意。"},
                )
                assert pending.status_code == 200
                body = pending.json()
                assert body["status"] == "waiting_approval" and body["error_code"] is None
                task_id = body["id"]
                approval_id = body["result"]["approval"]["approval_id"]
                assert body["result"]["approval"]["amount_cents"] == 12900
                assert body["result"]["citations"], (
                    "The actual local policy retrieval must find evidence"
                )
                before = await counts_for_order(pg_factory, order_id)
                assert before == {"refunds": 0, "tickets": 0}
                events.append({"event": "waiting_approval", "status": body["status"], **before})
        finally:
            await asyncio.to_thread(_stop_owned_server, process, port)
    events.append({"event": "first_owned_process_stopped_and_port_closed"})

    with second_log.open("w", encoding="utf-8") as log_file:
        process = start_owned_server(environment, log_file)
        assert process.pid != first_pid
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=40, trust_env=False) as client:
                await await_ready(process, client)
                # Reuse the actual JWT across restart: the signing secret is unchanged.
                restored = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
                assert (
                    restored.status_code == 200 and restored.json()["status"] == "waiting_approval"
                )
                foreign = await login(client, "demo_other")
                for path in (
                    f"/api/v1/agent/sessions/{session_id}",
                    f"/api/v1/agent/tasks/{task_id}",
                ):
                    denied = await client.get(path, headers=foreign)
                    assert denied.status_code == 404
                denied_resume = await client.post(
                    f"/api/v1/agent/tasks/{task_id}/resume",
                    headers=foreign,
                    json={"approval_id": approval_id, "confirmed": True},
                )
                assert denied_resume.status_code == 404
                confirm = {"approval_id": approval_id, "confirmed": True}
                resumed = await client.post(
                    f"/api/v1/agent/tasks/{task_id}/resume", headers=headers, json=confirm
                )
                assert resumed.status_code == 200
                completed = resumed.json()
                assert completed["status"] == "completed" and completed["error_code"] is None
                refund = completed["result"]["refund_result"]["refund"]
                assert refund["status"] == "pending_review" and refund["amount_cents"] == 12900
                replayed = await client.post(
                    f"/api/v1/agent/tasks/{task_id}/resume", headers=headers, json=confirm
                )
                assert replayed.status_code == 200
                assert replayed.json()["result"]["refund_result"]["refund"]["id"] == refund["id"]
                after = await counts_for_order(pg_factory, order_id)
                assert after == {"refunds": 1, "tickets": 1}
                actual_order = await client.get("/api/v1/orders/10086", headers=headers)
                assert (
                    actual_order.status_code == 200 and actual_order.json()["status"] == "delivered"
                )
                async with pg_factory() as session:
                    creates = await session.scalar(
                        select(func.count(AuditLog.id)).where(
                            AuditLog.action == "refund.create", AuditLog.resource_id == refund["id"]
                        )
                    )
                    ticket = await session.scalar(
                        select(SupportTicket).where(
                            SupportTicket.refund_request_id == UUID(refund["id"])
                        )
                    )
                    assert creates == 1 and ticket is not None
                events.append(
                    {
                        "event": "confirmed_after_process_restart",
                        "status": completed["status"],
                        **after,
                    }
                )
                events.append(
                    {
                        "event": "repeat_confirmation",
                        "same_refund": True,
                        "foreign_access_status": denied_resume.status_code,
                    }
                )
        finally:
            await asyncio.to_thread(_stop_owned_server, process, port)
    for log_path in (first_log, second_log):
        content = log_path.read_text(encoding="utf-8")
        for secret in (
            *wire_settings.secret_values(),
            headers["Authorization"].removeprefix("Bearer "),
        ):
            if secret and secret in content:
                raise AssertionError("Owned controlled server logs exposed a configured secret")
    artifact = PROJECT_ROOT / "var" / "test-results" / "phase4-http-restart.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "checked_at_utc": datetime.now(UTC).isoformat(),
                "controlled_model_only": True,
                "online_deepseek_tested": False,
                "provider_token_usage": None,
                "database": "enterprise_agent_test",
                "real_dependencies": [
                    "Uvicorn TCP HTTP",
                    "PostgreSQL ordinary account",
                    "official PostgreSQL checkpoint saver",
                    "Chroma HTTP",
                    "verified local CPU embedding and reranker",
                ],
                "events": events,
                "passed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
