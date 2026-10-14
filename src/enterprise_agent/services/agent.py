"""Owned sessions, synchronous bounded turns and explicit persisted recovery."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import select

from enterprise_agent.agents.graph import AgentContext, build_graph, initial_state
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import ApprovalRequest, AuditLog, IdempotencyRecord
from enterprise_agent.observability import TaskTraceRecorder
from enterprise_agent.repositories.agent import ACTIVE_TASK_STATUSES, AgentRepository
from enterprise_agent.schemas.agent import (
    MessageInput,
    ResumeInput,
    SessionPage,
    SessionResponse,
    TaskPage,
    TaskResponse,
)
from enterprise_agent.security import Principal
from enterprise_agent.services.analytics import AnalyticsService
from enterprise_agent.services.orders import validate_pagination
from enterprise_agent.tools import BusinessTools

logger = logging.getLogger("enterprise_agent.agent")
RECOVERABLE = frozenset({"agent_checkpoint_error", "agent_interrupted", "agent_timeout"})


class AgentService:
    def __init__(
        self,
        sessions,
        settings,
        checkpoint,
        model=None,
        rag_runtime=None,
        clock=lambda: datetime.now(UTC),
        analytics_runtime=None,
        trace_recorder=None,
    ):
        self.sessions, self.settings, self.checkpoint, self.model = (
            sessions,
            settings,
            checkpoint,
            model,
        )
        self.tools = BusinessTools(sessions, settings, rag_runtime, clock)
        self.analytics = (
            AnalyticsService(analytics_runtime, clock=clock)
            if analytics_runtime is not None
            else None
        )
        self.graph = build_graph(checkpoint.saver) if checkpoint is not None else None
        # CLI/test Services write local snapshots. Remote export requires an
        # explicitly supplied shared recorder with application-managed lifecycle.
        self.trace_recorder = trace_recorder or TaskTraceRecorder(settings, allow_remote=False)

    async def _session(self, actor, identifier, *, lock=False, session=None):
        if session is None:
            async with self.sessions() as query:
                return await self._session(actor, identifier, lock=lock, session=query)
        record = await AgentRepository(session).owned_session(actor, identifier, lock=lock)
        if record is None:
            raise BusinessError("agent_session_not_found", "会话不存在。", 404)
        return record

    async def _task(self, actor, identifier, *, lock=False, session=None):
        if session is None:
            async with self.sessions() as query:
                return await self._task(actor, identifier, lock=lock, session=query)
        record = await AgentRepository(session).owned_task(actor, identifier, lock=lock)
        if record is None:
            raise BusinessError("agent_task_not_found", "任务不存在。", 404)
        return record

    async def create_session(self, actor: Principal):
        async with self.sessions() as session, session.begin():
            record = await AgentRepository(session).create_session(actor)
            session.add(
                AuditLog(
                    actor_id=actor.user_id,
                    action="agent.session_created",
                    resource_type="agent_session",
                    resource_id=str(record.id),
                    details={},
                )
            )
            await session.refresh(record)
            return SessionResponse.model_validate(record)

    async def list_sessions(self, actor, *, offset=0, limit=20):
        validate_pagination(offset, limit)
        async with self.sessions() as session:
            records, total = await AgentRepository(session).page_sessions(
                actor, offset=offset, limit=limit
            )
            return SessionPage(
                items=[SessionResponse.model_validate(row) for row in records],
                total=total,
                offset=offset,
                limit=limit,
            )

    async def get_session(self, actor, session_id):
        return SessionResponse.model_validate(await self._session(actor, session_id))

    async def list_tasks(self, actor, *, offset=0, limit=20, session_id=None):
        validate_pagination(offset, limit)
        if session_id is not None:
            await self._session(actor, session_id)
        async with self.sessions() as session:
            records, total = await AgentRepository(session).page_tasks(
                actor, offset=offset, limit=limit, session_id=session_id
            )
            return TaskPage(
                items=[TaskResponse.model_validate(row) for row in records],
                total=total,
                offset=offset,
                limit=limit,
            )

    async def get_task(self, actor, task_id):
        return TaskResponse.model_validate(await self._task(actor, task_id))

    async def _available(self):
        if not self.settings.agent_enabled or self.checkpoint is None or self.graph is None:
            raise BusinessError("agent_unavailable", "Agent 工作流尚未启用。", 503)
        if self.model is None:
            raise BusinessError(
                "agent_model_not_configured", "请在本机配置 DeepSeek API Key 并启用 LLM。", 503
            )
        if not await self.checkpoint.is_ready():
            raise BusinessError("agent_unavailable", "Agent 持久化未就绪。", 503)

    def _config(self, record):
        return {
            "configurable": {"thread_id": record.thread_id},
            "recursion_limit": self.settings.agent_max_steps + 8,
        }

    def _context(self, actor):
        return AgentContext(actor, self.tools, self.model, self.settings, self.analytics)

    async def send(self, actor, session_id, message):
        message = MessageInput(message=message).message
        record = await self._session(actor, session_id)
        await self._available()
        async with self.checkpoint.thread_lock(record.thread_id):
            snapshot = await self.graph.aget_state(self._config(record))
            async with self.sessions() as session, session.begin():
                current = await self._session(actor, session_id, lock=True, session=session)
                if current.current_task_id is not None:
                    previous = await self._task(actor, current.current_task_id, session=session)
                    if previous.error_code in RECOVERABLE:
                        raise BusinessError("agent_session_busy", "请先恢复当前中断任务。", 409)
                if snapshot.next:
                    raise BusinessError(
                        "agent_session_busy", "会话有待恢复任务，请先确认或恢复。", 409
                    )
                task = await AgentRepository(session).create_task(
                    actor,
                    current,
                    message,
                    expires_at=datetime.now(UTC)
                    + timedelta(seconds=self.settings.agent_timeout_seconds),
                )
                task_id = task.id
                self._audit(session, actor, task_id, "created", {})
            history = snapshot.values.get("history", []) if snapshot.values else []
            return await self._run(actor, record, task_id, initial_state(task_id, message, history))

    async def resume(self, actor, task_id, payload: ResumeInput):
        task = await self._task(actor, task_id)
        record = await self._session(actor, task.session_id)
        await self._available()
        async with self.checkpoint.thread_lock(record.thread_id):
            task = await self._task(actor, task_id)
            if task.status not in ACTIVE_TASK_STATUSES:
                previous = task.result.get("refund_result")
                same = previous and previous["approval_id"] == str(payload.approval_id)
                expected = task.status == "completed"
                if (
                    same
                    and task.status in ("completed", "rejected")
                    and payload.confirmed == expected
                ):
                    return TaskResponse.model_validate(task)
                raise BusinessError("agent_task_unavailable", "任务已结束或确认内容不匹配。", 409)
            snapshot = await self._snapshot_for(record, task_id)
            approval = snapshot.values.get("approval")
            if not approval or approval["approval_id"] != str(payload.approval_id):
                raise BusinessError("approval_changed", "确认内容与当前任务不匹配。", 409)
            if not any(item.interrupts for item in snapshot.tasks):
                raise BusinessError(
                    "agent_not_waiting_approval", "任务尚未处于人工确认节点，请先恢复。", 409
                )
            async with self.sessions() as session:
                stored = await session.scalar(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id == payload.approval_id,
                        ApprovalRequest.task_id == task_id,
                        ApprovalRequest.user_id == actor.user_id,
                    )
                )
                if stored is None:
                    raise BusinessError("approval_changed", "服务器确认记录与任务不匹配。", 409)
            return await self._run(
                actor, record, task_id, Command(resume=payload.model_dump(mode="json"))
            )

    async def recover(self, actor, task_id):
        task = await self._task(actor, task_id)
        record = await self._session(actor, task.session_id)
        await self._available()
        async with self.checkpoint.thread_lock(record.thread_id):
            task = await self._task(actor, task_id)
            if task.status not in ACTIVE_TASK_STATUSES and task.error_code not in RECOVERABLE:
                return TaskResponse.model_validate(task)
            snapshot = await self._snapshot_for(record, task_id, allow_empty=True)
            if any(item.interrupts for item in snapshot.tasks):
                return await self._sync(actor, task_id, snapshot.values, waiting=True, elapsed_ms=0)
            async with self.sessions() as session, session.begin():
                current = await self._task(actor, task_id, lock=True, session=session)
                current.status = "running"
                current.error_code = None
                current.completed_at = None
                self._audit(session, actor, task_id, "recovered", {})
            if not snapshot.values or snapshot.values.get("task_id") != str(task_id):
                history = snapshot.values.get("history", []) if snapshot.values else []
                return await self._run(
                    actor, record, task_id, initial_state(task_id, task.message, history)
                )
            if not snapshot.next:
                return await self._sync(
                    actor, task_id, snapshot.values, waiting=False, elapsed_ms=0
                )
            return await self._run(actor, record, task_id, None)

    async def _snapshot_for(self, record, task_id, *, allow_empty=False):
        current = await self._session_for_id(record.id)
        if current.current_task_id != task_id:
            raise BusinessError(
                "agent_task_superseded", "会话已开始其他任务，不能覆盖当前状态。", 409
            )
        snapshot = await self.graph.aget_state(self._config(record))
        if (
            snapshot.values
            and snapshot.values.get("task_id") != str(task_id)
            and not (allow_empty and not snapshot.next)
        ):
            raise BusinessError("agent_checkpoint_mismatch", "任务与持久状态不一致。", 409)
        if not snapshot.values and not allow_empty:
            raise BusinessError("agent_checkpoint_missing", "任务尚无可用检查点，请恢复任务。", 409)
        return snapshot

    async def _session_for_id(self, session_id):
        # Internal record was already authorized before this thread lock.
        from enterprise_agent.models import AgentSession

        async with self.sessions() as session:
            return await session.get(AgentSession, session_id)

    async def _sync(self, actor, task_id, state, *, waiting, elapsed_ms):
        async with self.sessions() as session, session.begin():
            task = await self._task(actor, task_id, lock=True, session=session)
            task.status = "waiting_approval" if waiting else state.get("status", "failed")
            task.task_type = state.get("intent", "unknown")
            task.steps = state.get("steps", [])
            usage = state.get("token_usage", {})
            interrupted_unknown = task.token_usage.get(
                "interrupted_usage_unknown", False
            ) or usage.get("interrupted_usage_unknown", False)
            task.token_usage = {
                **usage,
                "usage_complete": (
                    usage.get("unknown_usage_calls", 0) == 0 and not interrupted_unknown
                ),
                "interrupted_usage_unknown": interrupted_unknown,
            }
            task.error_code = state.get("error_code")
            task.elapsed_ms = (task.elapsed_ms or 0) + elapsed_ms
            task.updated_at = datetime.now(UTC)
            task.completed_at = None if waiting else datetime.now(UTC)
            task.result = {
                key: state.get(key)
                for key in (
                    "answer",
                    "approval",
                    "refund_result",
                    "citations",
                    "facts",
                    "warning_codes",
                    "analysis",
                )
            }
            session.add(
                AuditLog(
                    actor_id=actor.user_id,
                    action="agent.task_" + task.status,
                    resource_type="agent_task",
                    resource_id=str(task.id),
                    details={"task_type": task.task_type, "error_code": task.error_code},
                )
            )
            await session.flush()
            return TaskResponse.model_validate(task)

    @staticmethod
    def _audit(session, actor, task_id, action, details):
        session.add(
            AuditLog(
                actor_id=actor.user_id,
                action="agent.task_" + action,
                resource_type="agent_task",
                resource_id=str(task_id),
                details=details,
            )
        )

    async def _interrupted(self, actor, record, task_id, code, elapsed_ms, *, cancelled=False):
        state = {}
        try:
            async with asyncio.timeout(3):
                snapshot = await self.graph.aget_state(self._config(record))
                if snapshot.values.get("task_id") == str(task_id):
                    state = snapshot.values
        except Exception:
            pass
        async with self.sessions() as session, session.begin():
            task = await self._task(actor, task_id, lock=True, session=session)
            receipt = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.actor_id == actor.user_id,
                    IdempotencyRecord.operation == "create_refund_request",
                    IdempotencyRecord.key == "agent:" + str(task_id) + ":refund",
                )
            )
            task.status = "running" if cancelled else "failed"
            task.error_code = code
            task.steps = state.get("steps", task.steps)
            task.token_usage = {
                **state.get("token_usage", task.token_usage),
                "usage_complete": False,
                "interrupted_usage_unknown": True,
            }
            task.updated_at = datetime.now(UTC)
            task.completed_at = None if cancelled else task.updated_at
            task.elapsed_ms = (task.elapsed_ms or 0) + elapsed_ms
            task.result = {
                **task.result,
                **{
                    key: state[key]
                    for key in (
                        "approval",
                        "refund_result",
                        "citations",
                        "facts",
                        "warning_codes",
                        "analysis",
                    )
                    if key in state
                },
            }
            if receipt is not None:
                task.result = {**task.result, "refund_result": receipt.result}
            task.result = {
                **task.result,
                "answer": (
                    "任务执行中断，请通过恢复接口核对持久状态；"
                    "已提交业务由幂等记录保护，请核对 refund_result 中的实际申请记录。"
                ),
            }
            self._audit(session, actor, task_id, "interrupted", {"error_code": code})
            await session.flush()
            return TaskResponse.model_validate(task)

    async def _run(self, actor, record, task_id, graph_input):
        started = perf_counter()
        segment_id, started_at = uuid4(), datetime.now(UTC)
        try:
            async with self.sessions() as session, session.begin():
                task = await self._task(actor, task_id, lock=True, session=session)
                task.expires_at = datetime.now(UTC) + timedelta(
                    seconds=self.settings.agent_timeout_seconds
                )
                self._audit(
                    session,
                    actor,
                    task_id,
                    "execution_started",
                    {"timeout_seconds": self.settings.agent_timeout_seconds},
                )
            async with asyncio.timeout(self.settings.agent_timeout_seconds):
                with tracing_context(enabled=False):
                    await self.graph.ainvoke(
                        graph_input,
                        config=self._config(record),
                        context=self._context(actor),
                        durability="sync",
                    )
                snapshot = await self.graph.aget_state(self._config(record))
                waiting = any(item.interrupts for item in snapshot.tasks)
                response = await self._sync(
                    actor,
                    task_id,
                    snapshot.values,
                    waiting=waiting,
                    elapsed_ms=round((perf_counter() - started) * 1000),
                )
        except asyncio.CancelledError:
            # The request can die after a business commit. Leave this owned task
            # reserved for explicit recovery instead of starting a duplicate turn.
            response = await asyncio.shield(
                self._interrupted(
                    actor,
                    record,
                    task_id,
                    "agent_interrupted",
                    round((perf_counter() - started) * 1000),
                    cancelled=True,
                )
            )
            self.trace_recorder.record(response, segment_id=segment_id, started_at=started_at)
            raise
        except Exception as error:
            code = (
                "agent_timeout"
                if isinstance(error, TimeoutError)
                else "agent_step_limit"
                if isinstance(error, GraphRecursionError)
                else "agent_checkpoint_error"
            )
            response = await self._interrupted(
                actor, record, task_id, code, round((perf_counter() - started) * 1000)
            )
            logger.warning(
                "Agent execution interrupted: %s",
                type(error).__name__,
                extra={
                    "event": "agent_failed",
                    "error_code": code,
                    "exception_type": type(error).__name__,
                },
            )
        logger.info(
            "Agent turn finished",
            extra={
                "event": "agent_turn",
                "task_id": str(task_id),
                "task_status": response.status,
                "elapsed_ms": response.elapsed_ms,
            },
        )
        self.trace_recorder.record(response, segment_id=segment_id, started_at=started_at)
        return response
