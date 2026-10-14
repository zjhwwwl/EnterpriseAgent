"""Metadata privacy and SDK contracts; controlled transports are not cloud proof."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from langsmith.utils import LangSmithError
from pydantic import ValidationError

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings
from enterprise_agent.observability import (
    TaskTraceRecorder,
    _langsmith_client,
    task_snapshot,
)


def task(**overrides):
    values = {
        "id": uuid4(),
        "task_type": "order",
        "status": "waiting_approval",
        "message": "private-message-order-10086",
        "result": {"sql": "private-sql", "citations": ["private-doc"]},
        "user_id": 87654,
        "error_code": None,
        "elapsed_ms": 47,
        "created_at": datetime(2026, 10, 12, tzinfo=UTC),
        "token_usage": {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "total_tokens": 25,
            "model_calls": 2,
            "unknown_usage_calls": 0,
            "usage_complete": True,
            "secret": "private-ledger-secret",
        },
        "steps": [
            {
                "node": "business_tool",
                "tool": "get_order",
                "elapsed_ms": 12.5,
                "error_code": None,
                "arguments": {"order_no": "10086"},
                "result": "private-step-result",
            }
        ],
    }
    return SimpleNamespace(**(values | overrides))


def settings(tmp_path, **overrides):
    return Settings(_env_file=None, data_dir=tmp_path, **overrides)


def remote_settings(tmp_path, **overrides):
    return settings(
        tmp_path,
        **(
            {
                "langsmith_enabled": True,
                "langsmith_api_key": "controlled-sdk-key",
                "langsmith_endpoint": "http://127.0.0.1:8123",
                "langsmith_project": "contract-test",
            }
            | overrides
        ),
    )


def read_records(recorder):
    return [json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()]


def test_default_local_snapshot_is_strict_and_correlates_without_logging_header(
    tmp_path, monkeypatch
):
    configured = settings(tmp_path)
    recorder = TaskTraceRecorder(configured)
    monkeypatch.setattr(
        "enterprise_agent.observability.get_request_id", lambda: "private-correlation-header"
    )
    recorder.record(task())
    record = read_records(recorder)[0]
    text = json.dumps(record)
    assert configured.observability_enabled and not configured.langsmith_enabled
    assert record["request_id_sha256"] == hashlib.sha256(b"private-correlation-header").hexdigest()
    for private in (
        "10086",
        "private-message",
        "private-sql",
        "private-doc",
        "87654",
        "private-step-result",
        "private-ledger-secret",
        "private-correlation-header",
    ):
        assert private not in text
    assert set(record) == {
        "schema_version",
        "event",
        "recorded_at",
        "trace_id",
        "task_id",
        "root_span_id",
        "segment_id",
        "parent_span_id",
        "request_id_sha256",
        "task_type",
        "status",
        "error_code",
        "task_started_at",
        "segment_started_at",
        "segment_ended_at",
        "elapsed_ms_cumulative",
        "usage_semantics",
        "token_usage_cumulative",
        "steps_semantics",
        "steps_total",
        "steps_truncated",
        "steps",
    }
    assert set(record["steps"][0]) == {
        "span_id",
        "parent_span_id",
        "step_index",
        "node",
        "tool",
        "attempt",
        "elapsed_ms",
        "error_code",
    }
    assert record["token_usage_cumulative"]["total_tokens"] == 25
    assert record["token_usage_cumulative"]["usage_complete"] is True


def test_resume_and_restart_use_same_trace_and_latest_cumulative_usage(tmp_path):
    record = task()
    before = TaskTraceRecorder(settings(tmp_path))
    before.record(record)
    before.close()
    record.status, record.elapsed_ms = "completed", 72
    record.steps.append({"node": "commit_refund", "elapsed_ms": 8.5})
    after = TaskTraceRecorder(settings(tmp_path))
    after.record(record)
    snapshots = read_records(after)
    assert len(snapshots) == 2
    assert len({row["trace_id"] for row in snapshots}) == 1
    assert len({row["segment_id"] for row in snapshots}) == 2
    assert snapshots[-1]["elapsed_ms_cumulative"] == 72
    latest = {row["task_id"]: row for row in snapshots}
    assert sum(row["token_usage_cumulative"]["total_tokens"] for row in latest.values()) == 25
    assert snapshots[-1]["usage_semantics"] == "cumulative_snapshot_latest_per_task"
    assert all(
        step["parent_span_id"] == row["segment_id"] for row in snapshots for step in row["steps"]
    )


@pytest.mark.parametrize(
    "usage",
    [
        {"unknown_usage_calls": 1},
        {"interrupted_usage_unknown": True},
        {"usage_complete": False},
        {"total_tokens": None},
        {"total_tokens": True},
        {"total_tokens": -1},
        {"total_tokens": 99},
        {"model_calls": "2"},
        {"prompt_tokens": 1.2},
    ],
)
def test_unknown_or_malformed_usage_never_becomes_complete(usage):
    record = task()
    record.token_usage.update(usage)
    snapshot = task_snapshot(record)
    assert snapshot["token_usage_cumulative"]["usage_complete"] is False
    if usage.get("unknown_usage_calls") == 1:
        assert snapshot["token_usage_cumulative"]["unknown_usage_calls"] == 1
    if usage.get("interrupted_usage_unknown"):
        assert snapshot["token_usage_cumulative"]["interrupted_usage_unknown"] is True


def test_no_usage_is_reported_unknown_not_zero():
    usage = task_snapshot(task(token_usage={}))["token_usage_cumulative"]
    assert usage["total_tokens"] is None
    assert usage["model_calls"] is None
    assert usage["usage_complete"] is False


def test_untrusted_metadata_values_are_classified_and_never_exported():
    snapshot = task_snapshot(
        task(
            task_type="private-type",
            error_code="order_private_10086",
            steps=[
                {
                    "node": "private-node-10086",
                    "tool": "private-tool",
                    "error_code": ["private-error"],
                    "elapsed_ms": float("inf"),
                },
            ],
        )
    )
    text = json.dumps(snapshot, allow_nan=False)
    assert "private" not in text and "10086" not in text
    assert snapshot["steps"][0]["node"] == "unclassified_node"
    assert snapshot["steps"][0]["tool"] is None
    assert snapshot["steps"][0]["elapsed_ms"] is None
    assert snapshot["error_code"] == "unclassified_error"


def test_step_snapshot_is_bounded_and_preserves_observed_indices():
    snapshot = task_snapshot(task(steps=[{"node": "validation", "elapsed_ms": 1}] * 300))
    assert len(snapshot["steps"]) == 256
    assert snapshot["steps_total"] == 300 and snapshot["steps_truncated"] is True
    assert snapshot["steps"][-1]["step_index"] == 255


def test_off_never_constructs_client_even_with_ambient_tracing(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "private-ambient-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://untrusted.example")
    recorder = TaskTraceRecorder(settings(tmp_path), client_factory=lambda _: calls.append(True))
    recorder.record(task())
    recorder.close()
    assert calls == []


def test_disabled_local_and_remote_has_no_directory_side_effect(tmp_path):
    recorder = TaskTraceRecorder(settings(tmp_path, observability_enabled=False))
    recorder.record(task())
    recorder.close()
    assert not recorder.path.parent.exists()


def test_local_failure_does_not_escape_and_raw_exception_is_not_logged(
    tmp_path, monkeypatch, caplog
):
    recorder = TaskTraceRecorder(settings(tmp_path))

    def fail(*args, **kwargs):
        raise OSError("private-io-message sk-private-key")

    monkeypatch.setattr(type(recorder.path), "open", fail)
    recorder.record(task())
    assert any(getattr(row, "error_code", None) == "trace_write_failed" for row in caplog.records)
    assert "private-io-message" not in caplog.text


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.smith.langchain.com",
        "https://untrusted.example",
        "https://api.smith.langchain.com.evil.example",
        "https://api.smith.langchain.com:8443",
        "https://name:password@api.smith.langchain.com",
        "http://127.0.0.1:8123/v1",
        "http://127.0.0.1:8123?key=secret",
        "http://localhost:8123#fragment",
        "http://127.0.0.2:8123",
        "file:///tmp/trace",
    ],
)
def test_endpoint_trust_boundary(endpoint):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, langsmith_endpoint=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.smith.langchain.com",
        "https://eu.api.smith.langchain.com",
        "http://localhost:8123",
        "http://127.0.0.1:8123",
        "http://[::1]:8123",
    ],
)
def test_allowed_explicit_endpoints(endpoint):
    assert Settings(_env_file=None, langsmith_endpoint=endpoint).langsmith_endpoint is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"langsmith_enabled": True},
        {"langsmith_timeout_seconds": 0},
        {"langsmith_timeout_seconds": 6},
        {"langsmith_queue_size": 0},
        {"langsmith_queue_size": 65},
        {"langsmith_project": "private project!"},
    ],
)
def test_trace_configuration_fails_before_network(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_blank_optional_settings_and_secret_redaction():
    configured = Settings(
        _env_file=None, langsmith_api_key="", langsmith_endpoint="", langsmith_project=""
    )
    assert (
        configured.langsmith_api_key
        is configured.langsmith_endpoint
        is configured.langsmith_project
        is None
    )
    configured = Settings(_env_file=None, langsmith_api_key="private-langsmith-key")
    assert "private-langsmith-key" in configured.secret_values()
    assert "private-langsmith-key" not in configured.model_dump_json()


def test_actual_sdk_contract_with_controlled_requests_transport(tmp_path, monkeypatch):
    requests_seen = []

    def transport(session, method, url, **kwargs):
        requests_seen.append((method, url, kwargs))
        response = requests.Response()
        response.status_code, response._content, response.url = 200, b"{}", url
        response.request = requests.Request(method, url).prepare()
        return response

    monkeypatch.setattr(requests.Session, "request", transport)
    # Ambient multi-destination export and runtime metadata must not change this client.
    monkeypatch.setenv("LANGSMITH_RUNS_ENDPOINTS", '{"https://untrusted.example":"ambient-key"}')
    monkeypatch.setenv("LANGSMITH_PROJECT", "ambient-project")
    monkeypatch.setenv("LANGSMITH_TRACING_MODE", "otel")
    configured = remote_settings(tmp_path)
    client = _langsmith_client(configured)
    assert requests_seen == []
    assert client.tracing_queue is None
    recorder = TaskTraceRecorder(configured, client_factory=lambda _: client)
    try:
        recorder.record(task())
        deadline = monotonic() + 3
        while len(requests_seen) < 4 and monotonic() < deadline:
            threading.Event().wait(0.01)
        assert len(requests_seen) == 4  # Task POST, task PATCH, segment POST, step POST.
        bodies = [json.loads(kwargs["data"]) for _, _, kwargs in requests_seen]
        assert all(url.startswith("http://127.0.0.1:8123/runs") for _, url, _ in requests_seen)
        assert all(kwargs["allow_redirects"] is False for _, _, kwargs in requests_seen)
        assert all(
            "private" not in json.dumps(body) and "10086" not in json.dumps(body) for body in bodies
        )
        assert all(body.get("inputs", {}) == {} for body in bodies)
        assert all(body.get("session_name") in (None, "contract-test") for body in bodies)
        assert all("runtime" not in (body.get("extra") or {}) for body in bodies)
        assert bodies[1]["outputs"]["token_usage_cumulative"]["total_tokens"] == 25
        assert bodies[-1]["outputs"]["timing_kind"] == "post_execution_snapshot"
        assert bodies[-1]["parent_run_id"] == bodies[-2]["id"]
    finally:
        recorder.close()


def test_redirect_is_never_followed_by_actual_sdk(tmp_path, monkeypatch):
    requests_seen = []

    def redirect(session, method, url, **kwargs):
        requests_seen.append(kwargs)
        response = requests.Response()
        response.status_code, response._content = 307, b""
        response.headers["Location"] = "https://untrusted.example/collect"
        return response

    monkeypatch.setattr(requests.Session, "request", redirect)
    client = _langsmith_client(remote_settings(tmp_path))
    try:
        with pytest.raises(LangSmithError):
            client.create_run("Metadata", {}, "chain", id=uuid4(), project_name="contract-test")
        assert len(requests_seen) == 1
        assert requests_seen[0]["allow_redirects"] is False
    finally:
        client.close(timeout=0)


def test_sdk_errors_do_not_echo_remote_body_or_header_credentials(tmp_path, monkeypatch):
    def rejection(session, method, url, **kwargs):
        response = requests.Response()
        response.status_code, response._content = 401, b'{"detail":"private-echoed-key"}'
        response.reason = "private-echoed-key"
        response.url = url
        response.request = requests.Request(method, url).prepare()
        return response

    monkeypatch.setattr(requests.Session, "request", rejection)
    client = _langsmith_client(remote_settings(tmp_path))
    try:
        with pytest.raises(LangSmithError) as error:
            client.create_run("Metadata", {}, "chain", id=uuid4(), project_name="contract-test")
        assert "private-echoed-key" not in str(error.value)
        assert "controlled-sdk-key" not in str(error.value)
    finally:
        client.close(timeout=0)


def test_bounded_export_queue_and_shutdown_never_wait_for_network(tmp_path, caplog):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    class SlowClient:
        def create_run(self, *args, **kwargs):
            entered.set()
            release.wait(3)
            raise RuntimeError("private-export-exception")

        def close(self, **kwargs):
            closed.set()

    recorder = TaskTraceRecorder(
        remote_settings(tmp_path, langsmith_queue_size=1), client_factory=lambda _: SlowClient()
    )
    try:
        recorder.record(task())
        assert entered.wait(1)
        for _ in range(3):
            recorder.record(task())
        assert any(getattr(row, "error_code", None) == "trace_queue_full" for row in caplog.records)
        started = monotonic()
        recorder.close()
        assert monotonic() - started < 0.5
    finally:
        recorder.close()
        release.set()
        assert closed.wait(1)
    assert "private-export-exception" not in caplog.text


async def test_lifecycle_recorder_is_created_and_closed_without_remote_network(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        "enterprise_agent.observability.Client", lambda **kwargs: calls.append(kwargs)
    )
    app = create_app(settings(tmp_path), configure_logs=False)
    assert app.state.trace_recorder is None
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.trace_recorder, TaskTraceRecorder)
        assert app.state.ready
    assert calls == []


def test_manual_export_is_only_possible_with_shared_recorder(tmp_path):
    from enterprise_agent.services.agent import AgentService

    service = AgentService(None, remote_settings(tmp_path), None)
    assert service.trace_recorder._exporter is None
