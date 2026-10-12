"""Start the actual CLI process and exercise loopback HTTP, not an ASGI transport."""

import json
import os
import socket
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from enterprise_agent.config import PROJECT_ROOT


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
                    with opener.open(base_url + "/health/ready", timeout=1) as response:
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
                assert set(schema["paths"]) == {"/", "/health/live", "/health/ready"}
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
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("event") == "application_started" for record in records)
    assert any(record.get("request_id") == "smoke-local-123" for record in records)
