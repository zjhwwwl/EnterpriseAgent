"""Official SDK protocol tests with labeled, controlled business Services."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, SecretStr

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.mcp import server as module
from enterprise_agent.models import UserRole
from enterprise_agent.security import Principal

ACTOR = Principal(user_id=42, role=UserRole.CUSTOMER)
CONTROL_TOKEN = "controlled-unit-token-not-a-credential"


@pytest.fixture
def controlled():
    settings = Settings(_env_file=None, mcp_access_token=SecretStr(CONTROL_TOKEN))
    auth = SimpleNamespace(authenticate=AsyncMock(return_value=ACTOR))
    tools = SimpleNamespace(execute=AsyncMock(return_value={"source": "controlled-unit-data"}))
    return settings, auth, tools, module.create_server(tools=tools, auth=auth, settings=settings)


@pytest.mark.asyncio
async def test_discovery_is_static_strict_and_read_only(controlled):
    _, auth, tools, server = controlled
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        discovered = (await client.list_tools()).tools
        assert tuple(tool.name for tool in discovered) == module.TOOL_NAMES
        for tool in discovered:
            assert tool.inputSchema["additionalProperties"] is False
            assert tool.inputSchema["required"] == ["order_no"]
            assert tool.inputSchema["properties"]["order_no"]["type"] == "string"
            assert set(tool.inputSchema["properties"]) <= {"order_no", "reason"}
            assert tool.outputSchema["required"] == ["data"]
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
            assert tool.annotations.idempotentHint is True
            assert tool.annotations.openWorldHint is False
    auth.authenticate.assert_not_awaited()
    tools.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("external", "internal", "arguments", "validated"),
    [
        ("query_order", "get_order", {"order_no": "10086"}, {"order_no": "10086"}),
        ("query_logistics", "get_logistics", {"order_no": "10086"}, {"order_no": "10086"}),
        (
            "query_refund_policy",
            "get_refund_policy",
            {"order_no": "10086"},
            {"order_no": "10086", "reason": "change_mind"},
        ),
        (
            "query_refund_policy",
            "get_refund_policy",
            {"order_no": "10086", "reason": "quality_issue"},
            {"order_no": "10086", "reason": "quality_issue"},
        ),
    ],
)
async def test_calls_dispatch_to_shared_business_tools(
    controlled, external, internal, arguments, validated
):
    _, auth, tools, server = controlled
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool(external, arguments)
    assert result.isError is False
    assert result.structuredContent == {"data": {"source": "controlled-unit-data"}}
    assert json.loads(result.content[0].text) == result.structuredContent
    auth.authenticate.assert_awaited_once_with(CONTROL_TOKEN)
    tools.execute.assert_awaited_once_with(ACTOR, internal, validated)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("query_order", {}),
        ("query_order", {"order_no": 10086}),
        ("query_order", {"order_no": True}),
        ("query_order", {"order_no": None}),
        ("query_order", {"order_no": "1008"}),
        ("query_order", {"order_no": "1" * 31}),
        ("query_order", {"order_no": "10086; DROP TABLE orders"}),
        ("query_order", {"order_no": "10086", "user_id": 1}),
        ("query_order", {"order_no": "10086", "role": "admin"}),
        ("query_logistics", {"order_no": "10086", "confirmed": True}),
        ("query_refund_policy", {"order_no": "10086", "reason": "unknown"}),
        ("query_refund_policy", {"order_no": "10086", "reason": 1}),
        ("query_refund_policy", {"order_no": "10086", "token": "private-value"}),
    ],
)
async def test_strict_arguments_reject_coercion_extra_identity_and_invalid_input(
    controlled, name, arguments
):
    _, auth, tools, server = controlled
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool(name, arguments)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_tool_arguments"
    assert result.structuredContent["error"]["status_code"] == 422
    assert "private-value" not in result.content[0].text
    assert "DROP TABLE" not in result.content[0].text
    auth.authenticate.assert_awaited_once()
    tools.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["create_refund_request", "execute_confirmed_refund", "DROP", "unknown-private"]
)
async def test_unknown_or_mutating_tools_cannot_dispatch(controlled, name):
    _, _, tools, server = controlled
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool(name, {"order_no": "10086"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unknown_tool"
    assert name not in result.content[0].text
    tools.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_call_uses_current_authenticated_principal(controlled):
    _, auth, tools, server = controlled
    changed = Principal(user_id=42, role=UserRole.SUPPORT)
    auth.authenticate.side_effect = [ACTOR, changed]
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        await client.call_tool("query_order", {"order_no": "10086"})
        await client.call_tool("query_order", {"order_no": "10086"})
    assert auth.authenticate.await_count == 2
    assert [call.args[0] for call in tools.execute.await_args_list] == [ACTOR, changed]


@pytest.mark.asyncio
async def test_credential_revocation_after_discovery_and_one_call_is_rechecked(controlled):
    _, auth, tools, server = controlled
    auth.authenticate.side_effect = [ACTOR, BusinessError("invalid_token", "身份凭证已失效", 401)]
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        await client.list_tools()
        assert not (await client.call_tool("query_order", {"order_no": "10086"})).isError
        revoked = await client.call_tool("query_order", {"order_no": "10086"})
    assert revoked.isError is True
    assert revoked.structuredContent["error"]["status_code"] == 401
    assert tools.execute.await_count == 1


@pytest.mark.asyncio
async def test_missing_server_credential_fails_closed_but_allows_discovery(controlled):
    settings, auth, tools, _ = controlled
    server = module.create_server(
        tools=tools, auth=auth, settings=settings.model_copy(update={"mcp_access_token": None})
    )
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        assert len((await client.list_tools()).tools) == 3
        result = await client.call_tool("query_order", {"order_no": "10086"})
    assert result.isError is True
    assert result.structuredContent["error"]["status_code"] == 503
    auth.authenticate.assert_not_awaited()
    tools.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_resource_business_error_is_preserved(controlled):
    _, _, tools, server = controlled
    tools.execute.side_effect = BusinessError("order_not_found", "订单不存在或无权访问", 404)
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool("query_order", {"order_no": "10086"})
    assert result.isError is True
    assert result.structuredContent == {
        "error": {"code": "order_not_found", "message": "订单不存在或无权访问", "status_code": 404}
    }
    assert json.loads(result.content[0].text) == result.structuredContent


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["auth", "business", "serialization"])
async def test_unexpected_errors_never_return_private_details(controlled, origin, caplog):
    _, auth, tools, server = controlled
    private = "postgresql://secret-account:secret-password@internal-host/private-db"
    if origin == "auth":
        auth.authenticate.side_effect = RuntimeError(private)
    elif origin == "business":
        tools.execute.side_effect = RuntimeError(private)
    else:
        tools.execute.return_value = {"invalid": object()}
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool("query_order", {"order_no": "10086"})
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "mcp_tool_unavailable"
    assert private not in result.content[0].text
    assert private not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["auth", "business"])
async def test_timeout_covers_authentication_and_execution(controlled, origin):
    settings, auth, tools, _ = controlled
    cancelled = asyncio.Event()

    async def slow(*args):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    (auth.authenticate if origin == "auth" else tools.execute).side_effect = slow
    server = module.create_server(
        tools=tools,
        auth=auth,
        settings=settings.model_copy(update={"mcp_request_timeout_seconds": 0.02}),
    )
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        result = await client.call_tool("query_order", {"order_no": "10086"})
    assert cancelled.is_set()
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "mcp_request_timeout"
    if origin == "auth":
        tools.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_handler_propagates_to_official_sdk(controlled):
    _, _, tools, server = controlled
    tools.execute.side_effect = asyncio.CancelledError()
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="query_order", arguments={"order_no": "10086"}),
    )
    with pytest.raises(asyncio.CancelledError):
        await server.request_handlers[types.CallToolRequest](request)


@pytest.mark.asyncio
async def test_transport_drop_cancels_inflight_business_query(controlled):
    _, _, tools, server = controlled
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(*args):
        entered.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    tools.execute.side_effect = slow
    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        task = asyncio.create_task(client.call_tool("query_order", {"order_no": "10086"}))
        await asyncio.wait_for(entered.wait(), 1)
    assert cancelled.is_set()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["database", "rag", "transport"])
async def test_runtime_closes_owned_resources_on_startup_and_transport_failure(
    controlled, monkeypatch, failure
):
    settings, _, _, _ = controlled
    database = SimpleNamespace(
        is_ready=AsyncMock(return_value=failure != "database"),
        close=AsyncMock(),
        session_factory=None,
    )
    rag = SimpleNamespace(ready=AsyncMock(return_value=failure != "rag"), close=AsyncMock())
    monkeypatch.setattr(module, "Database", lambda url: database)
    monkeypatch.setattr(module, "RagRuntime", lambda config: rag)
    settings = settings.model_copy(
        update={
            "database_url": SecretStr("postgresql+psycopg://control:control@localhost/control"),
            "jwt_secret": SecretStr("controlled-unit-jwt-secret-length-32"),
            "rag_enabled": True,
        }
    )
    with pytest.raises((BusinessError, RuntimeError)):
        async with module._runtime(settings):
            raise RuntimeError("controlled transport failure")
    database.close.assert_awaited_once()
    if failure == "database":
        rag.ready.assert_not_awaited()
        rag.close.assert_not_awaited()
    else:
        rag.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_still_closes_when_rag_cleanup_fails(controlled, monkeypatch):
    settings, _, _, _ = controlled
    database = SimpleNamespace(
        is_ready=AsyncMock(return_value=True), close=AsyncMock(), session_factory=None
    )
    rag = SimpleNamespace(
        ready=AsyncMock(return_value=True),
        close=AsyncMock(side_effect=RuntimeError("controlled-close")),
    )
    monkeypatch.setattr(module, "Database", lambda url: database)
    monkeypatch.setattr(module, "RagRuntime", lambda config: rag)
    configured = settings.model_copy(
        update={
            "database_url": SecretStr("postgresql+psycopg://control:control@localhost/control"),
            "jwt_secret": SecretStr("controlled-unit-jwt-secret-length-32"),
            "rag_enabled": True,
        }
    )
    with pytest.raises(RuntimeError):
        async with module._runtime(configured):
            pass
    rag.close.assert_awaited_once()
    database.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["database_url", "jwt_secret", "mcp_access_token"])
async def test_runtime_configuration_fails_before_creating_resources(
    controlled, monkeypatch, missing
):
    settings, _, _, _ = controlled
    configured = settings.model_copy(
        update={
            "database_url": SecretStr("postgresql+psycopg://control:control@localhost/control"),
            "jwt_secret": SecretStr("controlled-unit-jwt-secret-length-32"),
            missing: None,
        }
    )

    def forbidden_database(url):
        pytest.fail("No database should be opened for missing MCP configuration")

    monkeypatch.setattr(module, "Database", forbidden_database)
    with pytest.raises(BusinessError, match="尚未配置"):
        async with module._runtime(configured):
            pytest.fail("Missing MCP configuration must fail closed")


@pytest.mark.asyncio
async def test_process_settings_ignore_dotenv_and_unused_credentials(monkeypatch):
    recorded = {}
    monkeypatch.setenv("EA_LLM_ENABLED", "true")
    monkeypatch.setenv("EA_DEEPSEEK_API_KEY", "controlled-provider-key-never-used")
    monkeypatch.setenv("EA_AGENT_ENABLED", "true")
    monkeypatch.setenv("EA_MCP_ACCESS_TOKEN", CONTROL_TOKEN)
    monkeypatch.setenv("EA_REDIS_URL", "redis://controlled-unused-password@localhost/0")
    real_settings = module.Settings

    def record_settings(**kwargs):
        recorded.update(kwargs)
        return real_settings(**kwargs)

    fake_server = SimpleNamespace(run=AsyncMock(), create_initialization_options=lambda: None)

    @asynccontextmanager
    async def runtime(settings):
        assert settings.llm_enabled is False
        assert settings.deepseek_api_key is None
        assert settings.agent_enabled is False
        assert settings.redis_url is None
        yield fake_server

    @asynccontextmanager
    async def transport():
        yield object(), object()

    monkeypatch.setattr(module, "Settings", record_settings)
    monkeypatch.setattr(module, "_configure_protocol_logging", lambda settings: None)
    monkeypatch.setattr(module, "_runtime", runtime)
    monkeypatch.setattr(module, "stdio_server", transport)
    await module.run()
    assert recorded["_env_file"] is None
    for name in ("migration_database_url", "test_database_url", "test_migration_database_url"):
        assert recorded[name] is None
    fake_server.run.assert_awaited_once()


def test_main_startup_failure_has_no_traceback_or_raw_exception(monkeypatch, caplog):
    private = "controlled-secret-startup-value"

    async def fail():
        raise RuntimeError(private)

    monkeypatch.setattr(module, "configure_logging", lambda **kwargs: None)
    monkeypatch.setattr(module, "run", fail)
    assert module.main() == 2
    assert private not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_malformed_protocol_envelope_never_logs_raw_values(controlled, monkeypatch, capsys):
    settings, auth, tools, server = controlled
    # The SDK's envelope validator logs at the root logger, outside call_tool.
    # Preserve test-process logging while applying the dedicated-process policy.
    for name in ("", "enterprise_agent", "uvicorn", "uvicorn.error", "uvicorn.access", "mcp"):
        logger = module.logging.getLogger(name)
        monkeypatch.setattr(logger, "handlers", list(logger.handlers))
        monkeypatch.setattr(logger, "level", logger.level)
        monkeypatch.setattr(logger, "propagate", logger.propagate)
    module._configure_protocol_logging(settings.model_copy(update={"log_level": "DEBUG"}))
    private = "controlled-private-invalid-envelope"

    class RawRequest(BaseModel):
        method: str
        params: dict[str, Any]

    async with create_connected_server_and_client_session(server, raise_exceptions=True) as client:
        with pytest.raises(McpError) as failure:
            await client.send_request(
                RawRequest(
                    method="tools/call", params={"name": "query_order", "arguments": [private]}
                ),
                types.CallToolResult,
            )
        assert failure.value.error.code == types.INVALID_PARAMS
        assert failure.value.error.message == "Invalid request parameters"
        assert private not in str(failure.value)
        unknown = await client.call_tool(private, {"private": private})
        assert unknown.isError is True
    output = capsys.readouterr()
    assert output.out == ""
    assert private not in output.err
    assert CONTROL_TOKEN not in output.err
    assert "mcp_tool_finished" in output.err
    auth.authenticate.assert_awaited_once()
    tools.execute.assert_not_awaited()
