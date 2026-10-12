"""Start the actual CLI process and exercise loopback HTTP, not an ASGI transport."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from enterprise_agent.config import PROJECT_ROOT


def _assert_server_stopped(port):
    """A terminated launcher does not prove its child stopped listening."""
    # Windows can take about two seconds to report WSAECONNREFUSED for a
    # closed local port. Repeated short timeouts never reach that evidence.
    deadline = monotonic() + 5
    while monotonic() < deadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", port), timeout=max(0.1, deadline - monotonic())
            ):
                pass
        except ConnectionRefusedError:
            return
        except OSError:
            # Only a refused connection proves the listener is absent.
            pass
        sleep(0.05)
    raise AssertionError("Could not confirm the owned test server port is closed")


def _stop_owned_server(process, port):
    """Stop only this test's process tree, including Windows venv launchers."""
    try:
        if process.poll() is None:
            if os.name == "nt":
                taskkill = (
                    Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
                ).resolve()
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                process.wait(timeout=5)
            else:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    finally:
        _assert_server_stopped(port)


def test_real_local_server_startup_and_http_contract(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "EA_APP_ENV": "test",
        "EA_HOST": "127.0.0.1",
        "EA_PORT": str(port),
        "EA_LOG_LEVEL": "INFO",
        "EA_DOCS_ENABLED": "true",
        "EA_CORS_ORIGINS": "[]",
        "EA_DATA_DIR": str(tmp_path / "data"),
        "EA_LLM_ENABLED": "false",
        "EA_DEEPSEEK_API_KEY": "",
        "EA_DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "EA_DATABASE_URL": "",
        "EA_REDIS_URL": "",
    }
    opener = build_opener(ProxyHandler({}))
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "server.jsonl"

    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "main.py")],
            cwd=tmp_path,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = monotonic() + 20
            while True:
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                try:
                    with opener.open(base_url + "/health/live", timeout=1) as response:
                        assert response.status == 200
                        assert json.load(response)["checks"] == {"application": "ok"}
                    break
                except (URLError, TimeoutError):
                    if monotonic() >= deadline:
                        raise AssertionError("Local server startup timed out") from None
                    sleep(0.1)

            request = Request(base_url + "/", headers={"X-Request-ID": "smoke-local-123"})
            with opener.open(request, timeout=3) as response:
                assert response.headers["X-Request-ID"] == "smoke-local-123"
                assert json.load(response)["name"] == "EnterpriseAgent"
            with opener.open(base_url + "/health/live", timeout=3) as response:
                assert json.load(response)["status"] == "ok"
            with opener.open(base_url + "/openapi.json", timeout=3) as response:
                schema = json.load(response)
                assert {"/", "/health/live", "/health/ready", "/api/v1/orders"} <= set(
                    schema["paths"]
                )
            try:
                opener.open(base_url + "/health/ready", timeout=3)
            except HTTPError as error:
                assert error.code == 503
                assert json.load(error)["checks"]["database"] == "not_ready"
            else:
                raise AssertionError("Unconfigured business database must not claim readiness")
            with opener.open(base_url + "/docs", timeout=3) as response:
                assert "swagger-ui" in response.read().decode("utf-8")
            try:
                opener.open(base_url + "/missing", timeout=3)
            except HTTPError as error:
                assert error.code == 404
                body = json.load(error)
                assert body["error"]["code"] == "NOT_FOUND"
                assert body["request_id"] == error.headers["X-Request-ID"]
            else:
                raise AssertionError("Unknown path should return HTTP 404")
        finally:
            _stop_owned_server(process, port)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("event") == "application_started" for record in records)
    assert any(record.get("request_id") == "smoke-local-123" for record in records)


@pytest.mark.postgres
@pytest.mark.integration
async def test_real_uvicorn_authentication_and_refund_flow(tmp_path, pg_seed, pg_api_app):
    """Use real TCP HTTP and PostgreSQL, with an explicit reproducible test clock."""
    import asyncio

    import httpx

    configured = pg_api_app.state.settings
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    # The child receives only runtime credentials, never the owner/migration URL.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("EA_")}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EA_APP_ENV": "test",
            "EA_HOST": "127.0.0.1",
            "EA_PORT": str(port),
            "EA_LOG_LEVEL": "INFO",
            "EA_DOCS_ENABLED": "true",
            "EA_CORS_ORIGINS": "[]",
            "EA_DATA_DIR": str(tmp_path / "live-data"),
            "EA_LLM_ENABLED": "false",
            "EA_DATABASE_URL": configured.database_url.get_secret_value(),
            "EA_JWT_SECRET": configured.jwt_secret.get_secret_value(),
            "EA_MIGRATION_DATABASE_URL": "",
            "EA_TEST_DATABASE_URL": "",
            "EA_TEST_MIGRATION_DATABASE_URL": "",
            "EA_REDIS_URL": "",
            "EA_DEEPSEEK_API_KEY": "",
        }
    )
    # A fixed business clock makes the dated test fixture valid in future CI runs.
    # JWT issuance and expiry still use their actual wall clock.
    child_code = """
import asyncio
import sys
from datetime import UTC, datetime
import uvicorn
from enterprise_agent.application import create_app
from enterprise_agent.config import Settings
app = create_app(Settings(_env_file=None))
app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=UTC)
server = uvicorn.Server(uvicorn.Config(
    app, host="127.0.0.1", port=int(app.state.settings.port), access_log=False
))
loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
with asyncio.Runner(loop_factory=loop_factory) as runner:
    runner.run(server.serve())
"""
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "live-business-server.jsonl"
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=5, trust_env=False) as client:
                deadline = monotonic() + 25
                while True:
                    if process.poll() is not None:
                        raise AssertionError("Real Uvicorn process exited before readiness")
                    try:
                        ready = await client.get("/health/ready")
                        if ready.status_code == 200:
                            assert ready.json()["checks"]["database"] == "ok"
                            break
                    except httpx.TransportError:
                        pass
                    if monotonic() >= deadline:
                        raise AssertionError("Real Uvicorn business server startup timed out")
                    await asyncio.sleep(0.1)

                customer = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "demo_customer", "password": "DemoOnly!2026"},
                )
                assert customer.status_code == 200
                owner_headers = {
                    "Authorization": f"Bearer {customer.json()['access_token']}",
                    "X-Request-ID": "live-refund-flow",
                }
                me = await client.get("/api/v1/auth/me", headers=owner_headers)
                assert me.status_code == 200
                assert me.json()["role"] == "customer"
                foreign = await client.get("/api/v1/orders/10087", headers=owner_headers)
                assert foreign.status_code == 404

                pending = await client.post(
                    "/api/v1/orders/10086/refund-preview",
                    headers=owner_headers,
                    json={"reason": "change_mind", "notes": "真实本地进程模拟退款验证"},
                )
                assert pending.status_code == 201
                assert pending.json()["requires_confirmation"] is True
                assert pending.json()["amount_cents"] == 12900
                assert pending.json()["policy"]["document_id"]
                submit_headers = owner_headers | {"Idempotency-Key": "live-refund-confirm"}
                submit_body = {
                    "approval_id": pending.json()["approval_id"],
                    "confirmed": True,
                }
                created = await client.post(
                    "/api/v1/refunds", headers=submit_headers, json=submit_body
                )
                assert created.status_code == 200
                assert created.json()["refund"]["status"] == "pending_review"
                refund_id = created.json()["refund"]["id"]
                replay = await client.post(
                    "/api/v1/refunds", headers=submit_headers, json=submit_body
                )
                assert replay.status_code == 200
                assert replay.json()["replayed"] is True
                assert replay.json()["refund"]["id"] == refund_id

                support = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "demo_support", "password": "DemoOnly!2026"},
                )
                assert support.status_code == 200
                support_headers = {"Authorization": f"Bearer {support.json()['access_token']}"}
                approved = await client.post(
                    f"/api/v1/refunds/{refund_id}/review",
                    headers=support_headers,
                    json={"decision": "approve", "note": "仅模拟售后审核"},
                )
                assert approved.status_code == 200
                assert approved.json()["status"] == "approved"
                completed = await client.post(
                    f"/api/v1/refunds/{refund_id}/complete-simulated", headers=support_headers
                )
                assert completed.status_code == 200
                assert completed.json()["status"] == "completed_simulated"
                detail = await client.get(f"/api/v1/refunds/{refund_id}", headers=owner_headers)
                assert detail.status_code == 200
                assert detail.json()["status"] == "completed_simulated"
                order = await client.get("/api/v1/orders/10086", headers=owner_headers)
                assert order.status_code == 200
                assert order.json()["status"] == "delivered"
        finally:
            await asyncio.to_thread(_stop_owned_server, process, port)

    log_text = log_path.read_text(encoding="utf-8")
    for configured_secret in [
        configured.database_url.get_secret_value(),
        configured.jwt_secret.get_secret_value(),
        "DemoOnly!2026",
    ]:
        if configured_secret in log_text:
            raise AssertionError("Structured server logs exposed a configured secret")
    records = [json.loads(line) for line in log_text.splitlines() if line.startswith("{")]
    assert any(record.get("event") == "application_started" for record in records)
    assert any(record.get("request_id") == "live-refund-flow" for record in records)
