"""Actual SDK stdio child, ordinary PostgreSQL account and real scoped RAG.

There is no controlled LLM: these read-only MCP tools never invoke a provider.
On Windows the SDK subprocess client runs on its own Proactor loop, while SQL
fixtures and the child server use the Selector loop required by psycopg.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client import stdio as sdk_stdio
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from enterprise_agent.config import PROJECT_ROOT
from enterprise_agent.models import ApprovalRequest, RefundRequest, SupportTicket, User
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.security import Principal, create_access_token
from enterprise_agent.tools import BusinessTools

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.rag]


@pytest.fixture
def mcp_wire_settings(rag_settings, pg_urls):
    database = make_url(pg_urls[0])
    assert database.database == "enterprise_agent_test" and database.username == "ea_test_app"
    assert database.host in {"127.0.0.1", "localhost"}
    return rag_settings.model_copy(
        update={
            "database_url": SecretStr(pg_urls[0]),
            "jwt_secret": SecretStr(uuid4().hex + uuid4().hex),
            "rag_collection_prefix": "ea_e2e_" + uuid4().hex,
            "mcp_request_timeout_seconds": 45,
            "llm_enabled": False,
            "agent_enabled": False,
        }
    )


@pytest_asyncio.fixture
async def mcp_wire_runtime(mcp_wire_settings, rag_models, request):
    runtime = RagRuntime(mcp_wire_settings, models=rag_models)
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


def child_environment(settings, token):
    # Keep OS process necessities, and allowlist only ordinary business settings.
    # SDK may merge its own platform defaults; no owner/provider/test URL is sent.
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "PATH",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "HOMEDRIVE",
            "HOMEPATH",
        }
    }
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "EA_APP_ENV": "test",
            "EA_DATABASE_URL": settings.database_url.get_secret_value(),
            "EA_JWT_SECRET": settings.jwt_secret.get_secret_value(),
            "EA_MCP_ACCESS_TOKEN": token,
            "EA_MCP_REQUEST_TIMEOUT_SECONDS": str(settings.mcp_request_timeout_seconds),
            "EA_DATA_DIR": str(settings.data_dir),
            "EA_RAG_ENABLED": "true",
            "EA_LLM_ENABLED": "false",
            "EA_AGENT_ENABLED": "false",
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
            "EA_LOG_LEVEL": "DEBUG",
        }
    )
    return environment


def data_of(result):
    assert result.isError is False
    assert result.structuredContent is not None
    return result.structuredContent["data"]


def assert_error(result, code, status):
    assert result.isError is True
    error = result.structuredContent["error"]
    assert error["code"] == code and error["status_code"] == status


async def write_counts(factory):
    async with factory() as session:
        return {
            model.__tablename__: await session.scalar(select(func.count(model.id)))
            for model in (RefundRequest, SupportTicket, ApprovalRequest)
        }


def run_sdk_client(environment, log_path, token):
    async def protocol():
        observed_processes = []
        stdout_chunks = []
        original_create = sdk_stdio._create_platform_compatible_process
        original_text_stream = sdk_stdio.TextReceiveStream

        async def observe_process(*args, **kwargs):
            # Delegate to the actual SDK, including its Windows Job Object logic.
            process = await original_create(*args, **kwargs)
            observed_processes.append(process)
            return process

        def observe_stdout(*args, **kwargs):
            stream = original_text_stream(*args, **kwargs)

            async def chunks():
                async for chunk in stream:
                    stdout_chunks.append(chunk)
                    yield chunk

            return chunks()

        sdk_stdio._create_platform_compatible_process = observe_process
        sdk_stdio.TextReceiveStream = observe_stdout
        events = []
        try:
            with log_path.open("w", encoding="utf-8") as stderr:
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "enterprise_agent.mcp.server"],
                    env=environment,
                    cwd=str(PROJECT_ROOT),
                )
                async with (
                    sdk_stdio.stdio_client(parameters, errlog=stderr) as (reader, writer),
                    ClientSession(
                        reader, writer, read_timeout_seconds=timedelta(seconds=60)
                    ) as session,
                ):
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "EnterpriseAgent"
                    manifest = await session.list_tools()
                    names = sorted(tool.name for tool in manifest.tools)
                    assert names == ["query_logistics", "query_order", "query_refund_policy"]
                    for tool in manifest.tools:
                        assert tool.annotations.readOnlyHint is True
                        assert tool.annotations.destructiveHint is False
                        assert tool.inputSchema["additionalProperties"] is False
                        assert tool.inputSchema["properties"]["order_no"]["type"] == "string"
                    events.append({"event": "sdk_initialization_and_discovery", "tools": names})
                    order = data_of(await session.call_tool("query_order", {"order_no": "10086"}))
                    logistics = data_of(
                        await session.call_tool("query_logistics", {"order_no": "10086"})
                    )
                    policy = data_of(
                        await session.call_tool("query_refund_policy", {"order_no": "10086"})
                    )
                    events.append({"event": "three_real_business_reads"})
                    for name in names:
                        assert_error(
                            await session.call_tool(name, {"order_no": "10087"}),
                            "order_not_found",
                            404,
                        )
                    assert_error(
                        await session.call_tool(
                            "query_order",
                            {"order_no": "10087"},
                            meta={"role": "admin", "user_id": 999, "authorization": token},
                        ),
                        "order_not_found",
                        404,
                    )
                    events.append({"event": "foreign_order_refused", "tools_tested": 3})
                    for arguments in (
                        {"order_no": 10086},
                        {"order_no": "１００８６"},
                        {"order_no": "10086", "role": "admin", "user_id": 999},
                        {"order_no": "10086", "actor": token},
                    ):
                        assert_error(
                            await session.call_tool("query_order", arguments),
                            "invalid_tool_arguments",
                            422,
                        )
                    for name in ("create_refund_request", token):
                        refused = await session.call_tool(name, {"order_no": "10086"})
                        assert_error(refused, "unknown_tool", 422)
                        assert token not in json.dumps(refused.model_dump(mode="json"))
                    events.append({"event": "strict_arguments_and_write_tool_refusal"})
            assert len(observed_processes) == 1
            process = observed_processes[0]
            # Use the actual child process handle; no unrelated PID is terminated.
            returncode = await asyncio.wait_for(process.wait(), timeout=5)
            assert process.returncode is not None and returncode == 0
            frames = "".join(stdout_chunks).splitlines()
            assert frames
            for frame in frames:
                assert json.loads(frame)["jsonrpc"] == "2.0"
            events.append({"event": "stdout_contains_only_jsonrpc_frames", "frames": len(frames)})
            events.append(
                {
                    "event": "sdk_shutdown_actual_child_exited",
                    "pid": process.pid,
                    "returncode": returncode,
                    "windows_job_object_assigned": bool(getattr(process, "_job_object", None))
                    if sys.platform == "win32"
                    else None,
                }
            )
            return {
                "protocol_version": initialized.protocolVersion,
                "server_version": initialized.serverInfo.version,
                "events": events,
                "order": order,
                "logistics": logistics,
                "policy": policy,
            }
        finally:
            sdk_stdio._create_platform_compatible_process = original_create
            sdk_stdio.TextReceiveStream = original_text_stream

    factory = asyncio.ProactorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(protocol())


async def test_official_stdio_client_discovers_and_calls_real_authorized_business_services(
    tmp_path, pg_seed, pg_factory, mcp_wire_settings, mcp_wire_runtime
):
    async with pg_factory() as session:
        users = list(await session.scalars(select(User)))
    users = {user.username: user for user in users}
    customer, admin = users["demo_customer"], users["demo_admin"]
    actor = Principal(customer.id, customer.role)
    tools = BusinessTools(pg_factory, mcp_wire_settings, mcp_wire_runtime)
    eligibility = await tools.refunds.get_eligibility(actor, "10086")
    document_id = eligibility.policy.document_id
    await tools.knowledge.reindex(Principal(admin.id, admin.role), document_id)
    expected_order = await tools.execute(actor, "get_order", {"order_no": "10086"})
    expected_logistics = await tools.execute(actor, "get_logistics", {"order_no": "10086"})
    before = await write_counts(pg_factory)
    token = create_access_token(customer.id, customer.token_version, mcp_wire_settings)
    environment = child_environment(mcp_wire_settings, token)
    stderr_path = tmp_path / "mcp-stderr.jsonl"
    result = await asyncio.to_thread(run_sdk_client, environment, stderr_path, token)
    assert result["order"] == expected_order and result["logistics"] == expected_logistics
    assert result["policy"]["eligibility"] == eligibility.model_dump(mode="json")
    knowledge = result["policy"]["knowledge"]
    assert knowledge["status"] == "evidence_found" and knowledge["citations"]
    assert knowledge["llm_calls"] == 0
    for citation in knowledge["citations"]:
        assert UUID(citation["document_id"]) == document_id
        current = await tools.knowledge.citation(actor, UUID(citation["chunk_id"]))
        assert current.text == citation["text"]
    after = await write_counts(pg_factory)
    assert after == before
    stderr_text = stderr_path.read_text(encoding="utf-8")
    for secret in (*mcp_wire_settings.secret_values(), token):
        if secret and secret in stderr_text:
            raise AssertionError("The actual MCP process stderr exposed a configured secret")
    result["events"].append(
        {"event": "read_only_business_counts_unchanged", "before": before, "after": after}
    )
    result["events"].append({"event": "stderr_secret_scan_passed"})
    artifact = PROJECT_ROOT / "var" / "test-results" / "phase5-mcp-stdio.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "checked_at_utc": datetime.now(UTC).isoformat(),
                "sdk_version": version("mcp"),
                "protocol_version": result["protocol_version"],
                "server_version": result["server_version"],
                "database": "enterprise_agent_test",
                "real_dependencies": [
                    "Official MCP ClientSession and stdio transport",
                    "Production MCP server child process",
                    "PostgreSQL ordinary account",
                    "Chroma HTTP",
                    "Verified local CPU embedding and reranker",
                ],
                "online_deepseek_tested": False,
                "model_calls": 0,
                "provider_token_usage": None,
                "events": result["events"],
                "passed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
