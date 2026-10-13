"""Client safety and real SDK exchanges using controlled Services, without PostgreSQL."""

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr

from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.mcp import client as module
from enterprise_agent.mcp.server import TOOL_NAMES, create_server
from enterprise_agent.models import UserRole
from enterprise_agent.security import Principal

CONTROL_TOKEN = "controlled-client-token-never-a-real-credential"


@pytest.fixture
def configured():
    return Settings(
        _env_file=None,
        database_url=SecretStr(
            "postgresql+psycopg://controlled-app:controlled-db-pass@localhost/demo"
        ),
        jwt_secret=SecretStr("controlled-client-jwt-secret-length-32"),
        mcp_access_token=SecretStr(CONTROL_TOKEN),
    )


@pytest.mark.parametrize(
    "url",
    ["http://localhost", "http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"],
)
def test_accepts_only_plain_loopback_origins(url):
    assert module.loopback_origin(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:8000",
        "http://example.com",
        "http://127.0.0.2:8000",
        "http://localhost.example.com:8000",
        "http://localhost:8000/",
        "http://localhost:8000/path",
        "http://user:private-value@localhost:8000",
        "http://localhost:8000?private-value=yes",
        "http://localhost:8000?",
        "http://localhost:8000#private-value",
        "http://localhost:8000#",
        "http://localhost:65536",
        "http://localhost:0",
        "http://localhost:",
        "http://[::1]suffix",
        "http://localhost:8000\n",
        "file:///private-value",
    ],
)
def test_remote_credential_redirect_and_malformed_origins_are_rejected_without_echo(url):
    with pytest.raises(argparse.ArgumentTypeError) as failure:
        module.loopback_origin(url)
    assert "private-value" not in str(failure.value)


@pytest.mark.parametrize("value", [10086, True, None, "1008", "1" * 31, "10086; DROP TABLE orders"])
def test_order_numbers_use_shared_strict_pydantic_boundary(value):
    with pytest.raises(argparse.ArgumentTypeError):
        module.order_number(value)


def test_child_environment_does_not_copy_cloud_owner_test_or_redis_credentials(
    configured, monkeypatch
):
    monkeypatch.setenv("EA_DEEPSEEK_API_KEY", "private-provider-key")
    monkeypatch.setenv("EA_MIGRATION_DATABASE_URL", "private-owner-url")
    monkeypatch.setenv("EA_TEST_DATABASE_URL", "private-test-url")
    monkeypatch.setenv("EA_REDIS_URL", "private-redis-url")
    monkeypatch.setenv("UNRELATED_PRIVATE_ENV", "private-inherited-value")
    environment = module.child_environment(configured, configured.mcp_access_token)
    assert environment["EA_DATABASE_URL"] == configured.database_url.get_secret_value()
    assert environment["EA_JWT_SECRET"] == configured.jwt_secret.get_secret_value()
    assert environment["EA_MCP_ACCESS_TOKEN"] == CONTROL_TOKEN
    assert environment["EA_RAG_ENABLED"] == "false"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["TEMP"] == environment["TMP"]
    assert environment["TEMP"].startswith(str(PROJECT_ROOT))
    assert not any("private-" in value for value in environment.values())
    assert (
        not {
            "EA_MIGRATION_DATABASE_URL",
            "EA_TEST_DATABASE_URL",
            "EA_REDIS_URL",
            "EA_DEEPSEEK_API_KEY",
            "UNRELATED_PRIVATE_ENV",
        }
        & environment.keys()
    )


@pytest.mark.asyncio
async def test_real_sdk_client_discovers_and_calls_all_three_shared_read_tools(configured):
    actor = Principal(user_id=42, role=UserRole.CUSTOMER)
    tools = SimpleNamespace(execute=AsyncMock(return_value={"source": "controlled-service-data"}))
    server = create_server(
        tools=tools,
        auth=SimpleNamespace(authenticate=AsyncMock(return_value=actor)),
        settings=configured,
    )
    captured = {}

    @asynccontextmanager
    async def factory(parameters):
        captured["parameters"] = parameters
        async with create_connected_server_and_client_session(
            server, raise_exceptions=True
        ) as session:
            yield session

    report = await module.run_demo(configured, session_factory=factory)
    assert report["success"] is True
    assert report["transport"] == "stdio"
    assert report["provider_calls"] == 0
    assert report["token_usage"] is None
    assert [result["tool"] for result in report["results"]] == list(TOOL_NAMES)
    assert [call.args[1] for call in tools.execute.await_args_list] == [
        "get_order",
        "get_logistics",
        "get_refund_policy",
    ]
    assert tools.execute.await_args_list[-1].args[2] == {
        "order_no": "10086",
        "reason": "change_mind",
    }
    parameters = captured["parameters"]
    assert parameters.command == module.sys.executable
    assert parameters.args == ["-m", "enterprise_agent.mcp.server"]
    assert parameters.cwd == PROJECT_ROOT
    assert CONTROL_TOKEN not in json.dumps(report)


@pytest.mark.asyncio
async def test_business_failure_is_reported_without_transport_or_success_forgery(configured):
    tools = SimpleNamespace(
        execute=AsyncMock(side_effect=BusinessError("order_not_found", "订单不存在或无权访问", 404))
    )
    server = create_server(
        tools=tools,
        auth=SimpleNamespace(
            authenticate=AsyncMock(return_value=Principal(user_id=42, role=UserRole.CUSTOMER))
        ),
        settings=configured,
    )

    @asynccontextmanager
    async def factory(parameters):
        async with create_connected_server_and_client_session(
            server, raise_exceptions=True
        ) as session:
            yield session

    report = await module.run_demo(configured, session_factory=factory)
    assert report["success"] is False
    assert len(report["results"]) == 3
    assert all(item["is_error"] for item in report["results"])
    assert all(item["error"]["status_code"] == 404 for item in report["results"])


@pytest.mark.asyncio
async def test_child_session_is_closed_after_transport_failure(configured):
    closed = False

    @asynccontextmanager
    async def broken(parameters):
        nonlocal closed
        try:
            yield SimpleNamespace(
                initialize=AsyncMock(side_effect=RuntimeError("controlled-transport-failure"))
            )
        finally:
            closed = True

    with pytest.raises(RuntimeError):
        await module.run_demo(configured, session_factory=broken)
    assert closed is True


@pytest.mark.asyncio
async def test_total_client_timeout_closes_the_session(configured, monkeypatch):
    closed = False
    monkeypatch.setattr(module, "CLIENT_TIMEOUT_SECONDS", 0.02)

    async def slow():
        await asyncio.sleep(10)

    @asynccontextmanager
    async def session(parameters):
        nonlocal closed
        try:
            yield SimpleNamespace(initialize=slow)
        finally:
            closed = True

    with pytest.raises(TimeoutError):
        await module.run_demo(configured, session_factory=session)
    assert closed is True


@pytest.mark.asyncio
async def test_provider_secret_and_user_token_in_response_are_redacted(configured):
    tools = SimpleNamespace(
        execute=AsyncMock(
            return_value={"access_token": CONTROL_TOKEN, "detail": "JWT " + CONTROL_TOKEN}
        )
    )
    server = create_server(
        tools=tools,
        auth=SimpleNamespace(
            authenticate=AsyncMock(return_value=Principal(user_id=42, role=UserRole.CUSTOMER))
        ),
        settings=configured,
    )

    @asynccontextmanager
    async def factory(parameters):
        async with create_connected_server_and_client_session(
            server, raise_exceptions=True
        ) as session:
            yield session

    report = await module.run_demo(configured, session_factory=factory)
    assert CONTROL_TOKEN not in json.dumps(report)
    assert all(result["data"]["access_token"] == "[REDACTED]" for result in report["results"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 500])
async def test_demo_login_does_not_follow_redirect_or_echo_error_response(
    configured, monkeypatch, status
):
    real_client = httpx.AsyncClient
    captured = {}

    def handle(request):
        captured["request"] = request
        return httpx.Response(
            status,
            headers={"Location": "http://remote.example/private"},
            text="private-login-response",
        )

    def client(**kwargs):
        captured.update(kwargs)
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    with pytest.raises(BusinessError) as failure:
        await module._demo_login(configured, module.DEFAULT_API_URL)
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["request"].url.path == "/api/v1/auth/login"
    assert json.loads(captured["request"].content) == {
        "username": "demo_customer",
        "password": configured.demo_password.get_secret_value(),
    }
    assert "private-login-response" not in str(failure.value)


@pytest.mark.asyncio
async def test_demo_login_sends_fixed_demo_user_and_keeps_received_credential_secret(
    configured, monkeypatch
):
    real_client = httpx.AsyncClient

    def handle(request):
        assert json.loads(request.content)["username"] == "demo_customer"
        return httpx.Response(200, json={"access_token": CONTROL_TOKEN})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    token = await module._demo_login(configured, module.DEFAULT_API_URL)
    assert isinstance(token, SecretStr)
    assert token.get_secret_value() == CONTROL_TOKEN
    assert CONTROL_TOKEN not in repr(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": None},
        {"access_token": 42},
        {"access_token": ""},
        {"access_token": "a" * 8193},
    ],
)
async def test_demo_login_rejects_malformed_or_unbounded_credentials(
    configured, monkeypatch, payload
):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            **kwargs,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ),
    )
    with pytest.raises(BusinessError):
        await module._demo_login(configured, module.DEFAULT_API_URL)


def test_main_reports_only_safe_exception_type(configured, monkeypatch, capsys):
    private = "controlled-private-transport-or-settings-detail"

    async def fail(settings, **kwargs):
        raise RuntimeError(private)

    monkeypatch.setattr(module, "Settings", lambda **kwargs: configured)
    monkeypatch.setattr(module, "run_demo", fail)
    assert module.main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "RuntimeError" in output.err
    assert private not in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("success", [True, False])
def test_main_exit_status_and_json_artifact_follow_business_outcome(
    configured, monkeypatch, capsys, tmp_path, success
):
    report = {"success": success, "provider_calls": 0, "token_usage": None}
    monkeypatch.setattr(module, "Settings", lambda **kwargs: configured)
    monkeypatch.setattr(module, "run_demo", AsyncMock(return_value=report))
    destination = tmp_path / "client-report.json"
    assert module.main(["--output", str(destination)]) == (0 if success else 1)
    assert json.loads(destination.read_text(encoding="utf-8")) == report
    assert json.loads(capsys.readouterr().out) == report


def test_unrecognized_secret_cli_argument_is_not_echoed(capsys):
    with pytest.raises(SystemExit) as failure:
        module.main(["--token", "private-cli-value"])
    assert failure.value.code == 2
    assert "private-cli-value" not in capsys.readouterr().err
