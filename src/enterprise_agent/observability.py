"""Post-execution metadata snapshots, never automatic prompt/tool-argument tracing.

Token usage and steps are cumulative database snapshots. Consumers must select
the latest snapshot for each task, rather than add execution segments together.
Step spans reproduce persisted observations; their timing is not a live trace.
"""

import hashlib
import json
import logging
import math
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

import requests
from langsmith import Client
from urllib3.util.retry import Retry

from enterprise_agent.logging_config import redact_sensitive
from enterprise_agent.request_context import get_request_id

logger = logging.getLogger("enterprise_agent.observability")
NODES = frozenset(
    {
        "intent_router",
        "knowledge_retrieval",
        "query_rewrite",
        "analytics_planner",
        "analytics_execution",
        "planner",
        "tool_execution",
        "human_approval",
        "commit_refund",
        "response_generation",
        "validation",
        "model_call",
        "business_tool",
    }
)
TOOLS = frozenset(
    {
        "get_order",
        "get_order_items",
        "get_logistics",
        "get_refund_policy",
        "create_refund_request",
        "get_refund_status",
        "search_knowledge",
        "execute_analytics_sql",
    }
)
ERROR_CODES = frozenset(
    {
        "agent_checkpoint_error",
        "agent_interrupted",
        "agent_timeout",
        "agent_step_limit",
        "agent_tool_limit",
        "agent_model_invalid_response",
        "agent_model_timeout",
        "agent_model_rate_limited",
        "agent_model_unavailable",
        "agent_model_misconfigured",
        "analytics_unavailable",
        "analytics_forbidden",
        "analytics_sql_rejected",
        "analytics_query_timeout",
        "analytics_query_failed",
        "analytics_result_rejected",
        "analytics_result_too_large",
        "analytics_result_invalid",
        "analytics_metric_invalid",
        "invalid_tool_arguments",
        "order_not_found",
        "order_not_refundable",
        "refund_window_expired",
        "refund_already_exists",
        "refund_not_found",
        "invalid_refund_transition",
        "invalid_order_facts",
        "policy_unavailable",
        "policy_source_unavailable",
        "insufficient_policy_evidence",
        "citation_changed",
        "citation_not_found",
        "refund_result_mismatch",
        "approval_expired",
        "approval_not_found",
        "approval_mismatch",
        "approval_already_used",
        "approval_not_confirmed",
        "idempotency_conflict",
        "forbidden",
        "knowledge_unavailable",
        "knowledge_timeout",
        "invalid_model_plan",
        "ungrounded_model_quote",
        "unknown_tool",
        "rag_not_enabled",
        "refund_lookup_capacity_exceeded",
        "approval_changed",
        "approval_unavailable",
        "agent_task_unavailable",
        "invalid_confirmation",
        "invalid_approval_id",
        "invalid_idempotency_key",
        "invalid_reason",
        "category_restricted",
        "delivery_required",
        "already_delivered",
        "no_refundable_amount",
    }
)
STATUSES = frozenset(
    {"running", "waiting_approval", "completed", "failed", "rejected", "expired", "cancelled"}
)
TASK_TYPES = frozenset({"pending", "unknown", "knowledge", "order", "analytics", "unsupported"})
USAGE_COUNTERS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "model_calls",
    "unknown_usage_calls",
)


def _counter(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _duration(value):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 10**12:
        return round(value, 3)
    return None


def _code(value):
    return (
        value
        if isinstance(value, str) and value in ERROR_CODES
        else None
        if value is None
        else "unclassified_error"
    )


def _allowed(value, allowed, fallback):
    return value if isinstance(value, str) and value in allowed else fallback


def _utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def task_snapshot(task, *, segment_id=None, started_at=None, request_id=None):
    """Construct an explicit allowlist, without serializing TaskResponse itself."""
    task_id = str(UUID(str(task.id)))
    segment_id = str(UUID(str(segment_id))) if segment_id is not None else str(uuid4())
    end = datetime.now(UTC)
    usage = {key: _counter(task.token_usage.get(key)) for key in USAGE_COUNTERS}
    interrupted = task.token_usage.get("interrupted_usage_unknown") is True
    usage_complete = (
        task.token_usage.get("usage_complete") is True
        and not interrupted
        and usage["unknown_usage_calls"] == 0
        and all(value is not None for value in usage.values())
        and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    )
    usage.update(usage_complete=usage_complete, interrupted_usage_unknown=interrupted)
    steps = []
    for index, step in enumerate(task.steps[:256]):
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "span_id": str(uuid5(UUID(segment_id), str(index))),
                "parent_span_id": segment_id,
                "step_index": index,
                "node": _allowed(step.get("node"), NODES, "unclassified_node"),
                "tool": _allowed(step.get("tool"), TOOLS, None),
                "attempt": _counter(step.get("attempt")),
                "elapsed_ms": _duration(step.get("elapsed_ms")),
                "error_code": _code(step.get("error_code")),
            }
        )
    correlation = get_request_id() if request_id is None else request_id
    return {
        "schema_version": 1,
        "event": "agent_task_snapshot",
        "recorded_at": end.isoformat(),
        "trace_id": task_id,
        "task_id": task_id,
        "root_span_id": task_id,
        "segment_id": segment_id,
        "parent_span_id": task_id,
        "request_id_sha256": hashlib.sha256(correlation.encode("utf-8")).hexdigest()
        if correlation
        else None,
        "task_type": _allowed(task.task_type, TASK_TYPES, "unknown"),
        "status": _allowed(task.status, STATUSES, "failed"),
        "error_code": _code(task.error_code),
        "task_started_at": _utc(task.created_at).isoformat(),
        "segment_started_at": _utc(started_at or end).isoformat(),
        "segment_ended_at": end.isoformat(),
        "elapsed_ms_cumulative": _duration(task.elapsed_ms),
        "usage_semantics": "cumulative_snapshot_latest_per_task",
        "token_usage_cumulative": usage,
        "steps_semantics": "post_execution_cumulative_snapshot",
        "steps_total": len(task.steps),
        "steps_truncated": len(task.steps) > 256,
        "steps": steps,
    }


class _OriginSession(requests.Session):
    """No environment proxies/netrc, destination changes or redirect following."""

    def __init__(self, endpoint):
        super().__init__()
        self.trust_env = False
        self._origin = urlsplit(endpoint)

    def request(self, method, url, **kwargs):
        destination = urlsplit(url)
        if (destination.scheme, destination.netloc) != (self._origin.scheme, self._origin.netloc):
            raise requests.RequestException("Trace destination rejected")
        if (
            destination.username is not None
            or destination.password is not None
            or destination.query
            or destination.fragment
        ):
            raise requests.RequestException("Trace destination rejected")
        kwargs["allow_redirects"] = False
        response = super().request(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise requests.RequestException("Trace redirect rejected")
        if response.status_code >= 400:
            # A server can echo a submitted API header in its error body/reason.
            # The SDK formats those values in exceptions; retain only the status.
            response._content = b'{"error":"trace_request_rejected"}'
            response.reason = "Trace request rejected"
        return response


def _langsmith_client(settings):
    """Explicit endpoint/key/project configuration overrides ambient SDK settings."""
    endpoint = str(settings.langsmith_endpoint).rstrip("/")
    key = settings.langsmith_api_key.get_secret_value()
    return Client(
        api_key=key,
        api_urls={endpoint: key},
        session=_OriginSession(endpoint),
        auto_batch_tracing=False,
        retry_config=Retry(total=0, connect=0, read=0, redirect=0, status=0),
        timeout_ms=max(100, round(settings.langsmith_timeout_seconds * 1000)),
        tracing_mode="langsmith",
        tracing_sampling_rate=1.0,
        hide_inputs=True,
        hide_outputs=False,
        hide_metadata=False,
        omit_traced_runtime_info=True,
        info={},
        disable_prompt_cache=True,
    )


class _SummaryExporter:
    """One daemon worker and a bounded queue; remote failure never blocks a turn."""

    def __init__(self, settings, client_factory):
        self._settings = settings
        self._factory = client_factory
        self._queue = Queue(maxsize=settings.langsmith_queue_size)
        self._closed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ea-trace-export", daemon=True)
        self._thread.start()

    def submit(self, payload):
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except Full:
            logger.warning(
                "Trace export queue is full",
                extra={"event": "trace_export_dropped", "error_code": "trace_queue_full"},
            )
            return False

    def _run(self):
        client = None
        try:
            client = self._factory(self._settings)
            while not self._closed.is_set():
                try:
                    payload = self._queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    self._export(client, payload)
                except Exception:
                    logger.warning(
                        "Trace summary export failed",
                        extra={"event": "trace_export_failed", "error_code": "trace_export_failed"},
                    )
                finally:
                    self._queue.task_done()
        except Exception:
            logger.warning(
                "Trace exporter unavailable",
                extra={"event": "trace_export_failed", "error_code": "trace_client_failed"},
            )
        finally:
            self._closed.set()
            if client is not None:
                with suppress(Exception):
                    client.close(timeout=0)

    def _export(self, client, payload):
        project = self._settings.langsmith_project
        root = UUID(payload["root_span_id"])
        segment = UUID(payload["segment_id"])
        start = datetime.fromisoformat(payload["task_started_at"])
        end = datetime.fromisoformat(payload["segment_ended_at"])
        # Root writes reuse the task ID; create_run ignores existing-run conflicts.
        client.create_run(
            "EnterpriseAgent task",
            {},
            "chain",
            id=root,
            trace_id=root,
            project_name=project,
            start_time=start,
        )
        summary = {
            key: payload[key]
            for key in (
                "task_type",
                "status",
                "error_code",
                "elapsed_ms_cumulative",
                "usage_semantics",
                "token_usage_cumulative",
                "steps_total",
                "steps_truncated",
                "request_id_sha256",
            )
        }
        client.update_run(
            root, trace_id=root, end_time=end, outputs=summary, error=payload["error_code"] or ""
        )
        if self._closed.is_set():
            return
        client.create_run(
            "Execution segment",
            {},
            "chain",
            id=segment,
            parent_run_id=root,
            trace_id=root,
            project_name=project,
            start_time=datetime.fromisoformat(payload["segment_started_at"]),
            end_time=end,
            outputs={"status": payload["status"], "timing_kind": "execution_segment"},
        )
        for step in payload["steps"]:
            if self._closed.is_set():
                return
            client.create_run(
                step["tool"] or step["node"],
                {},
                "tool" if step["tool"] else "chain",
                id=UUID(step["span_id"]),
                parent_run_id=segment,
                trace_id=root,
                project_name=project,
                start_time=end,
                end_time=end,
                outputs={
                    "timing_kind": "post_execution_snapshot",
                    **{
                        key: step[key]
                        for key in (
                            "step_index",
                            "node",
                            "tool",
                            "attempt",
                            "elapsed_ms",
                            "error_code",
                        )
                    },
                },
            )

    def close(self):
        self._closed.set()
        # A running HTTP call has finite SDK connect/read timeouts; daemon worker
        # is never replaced. Shutdown does not wait for the entire remote queue.
        self._thread.join(timeout=0.25)


class TaskTraceRecorder:
    def __init__(self, settings, *, allow_remote=True, client_factory=None):
        self._settings = settings
        self.path = Path(settings.data_dir) / "observability" / "agent-traces.jsonl"
        self._lock = threading.Lock()
        self._exporter = (
            _SummaryExporter(settings, client_factory or _langsmith_client)
            if settings.langsmith_enabled and allow_remote
            else None
        )

    def record(self, task, *, segment_id=None, started_at=None):
        if not self._settings.observability_enabled and self._exporter is None:
            return
        try:
            payload = redact_sensitive(
                task_snapshot(task, segment_id=segment_id, started_at=started_at),
                secret_values=self._settings.secret_values(),
            )
            if self._settings.observability_enabled:
                try:
                    with self._lock:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        with self.path.open("a", encoding="utf-8") as stream:
                            stream.write(
                                json.dumps(payload, ensure_ascii=False, allow_nan=False) + "\n"
                            )
                except OSError:
                    logger.warning(
                        "Local trace snapshot write failed",
                        extra={"event": "trace_write_failed", "error_code": "trace_write_failed"},
                    )
            if self._exporter is not None:
                self._exporter.submit(payload)
        except Exception:
            logger.warning(
                "Trace snapshot construction failed",
                extra={"event": "trace_snapshot_failed", "error_code": "trace_snapshot_failed"},
            )

    def close(self):
        if self._exporter is not None:
            self._exporter.close()
