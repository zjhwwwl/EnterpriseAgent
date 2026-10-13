"""Owner-scoped Agent persistence; callers own the transaction and execution lock."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import AgentSession, AgentTask
from enterprise_agent.security import Principal

ACTIVE_TASK_STATUSES = frozenset({"running", "waiting_approval"})


class AgentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def owned_session(
        self, actor: Principal, session_id: UUID, *, lock: bool = False
    ) -> AgentSession | None:
        statement = select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.user_id == actor.user_id,
            AgentSession.role_at_creation == actor.role,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(statement)

    async def owned_task(
        self, actor: Principal, task_id: UUID, *, lock: bool = False
    ) -> AgentTask | None:
        statement = (
            select(AgentTask)
            .join(AgentSession, AgentTask.session_id == AgentSession.id)
            .where(
                AgentTask.id == task_id,
                AgentTask.user_id == actor.user_id,
                AgentSession.user_id == actor.user_id,
                AgentSession.role_at_creation == actor.role,
            )
        )
        if lock:
            statement = statement.with_for_update(of=AgentTask).execution_options(
                populate_existing=True
            )
        return await self.session.scalar(statement)

    async def page_sessions(self, actor: Principal, *, offset: int, limit: int):
        scope = (
            AgentSession.user_id == actor.user_id,
            AgentSession.role_at_creation == actor.role,
        )
        total = await self.session.scalar(
            select(func.count()).select_from(AgentSession).where(*scope)
        )
        rows = await self.session.scalars(
            select(AgentSession)
            .where(*scope)
            .order_by(AgentSession.updated_at.desc(), AgentSession.id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows), total or 0

    async def page_tasks(
        self, actor: Principal, *, offset: int, limit: int, session_id: UUID | None = None
    ):
        scopes = [
            AgentTask.user_id == actor.user_id,
            AgentSession.user_id == actor.user_id,
            AgentSession.role_at_creation == actor.role,
        ]
        if session_id is not None:
            scopes.append(AgentTask.session_id == session_id)
        statement = (
            select(AgentTask)
            .join(AgentSession, AgentTask.session_id == AgentSession.id)
            .where(*scopes)
        )
        total = await self.session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await self.session.scalars(
            statement.order_by(AgentTask.created_at.desc(), AgentTask.id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows), total or 0

    async def create_session(self, actor: Principal) -> AgentSession:
        record = AgentSession(
            id=uuid4(), user_id=actor.user_id, thread_id=str(uuid4()), role_at_creation=actor.role
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_task(
        self,
        actor: Principal,
        agent_session: AgentSession,
        message: str,
        *,
        task_type: str = "unknown",
        status: str = "running",
        expires_at: datetime | None = None,
    ) -> AgentTask:
        """Reserve a turn after the caller acquires thread_lock and the row lock.

        No graph or business-service call may run while this transaction is open.
        The current task stays attached after completion, for crash recovery.
        """
        if agent_session.user_id != actor.user_id or agent_session.role_at_creation != actor.role:
            raise BusinessError("agent_session_not_found", "会话不存在。", 404)
        if agent_session.current_task_id is not None:
            current = await self.owned_task(actor, agent_session.current_task_id)
            if current is not None and current.status in ACTIVE_TASK_STATUSES:
                raise BusinessError("agent_session_busy", "会话有任务尚未结束。", 409)
        task = AgentTask(
            id=uuid4(),
            session_id=agent_session.id,
            user_id=actor.user_id,
            task_type=task_type,
            status=status,
            message=message,
            expires_at=expires_at,
        )
        self.session.add(task)
        await self.session.flush()
        agent_session.current_task_id = task.id
        agent_session.updated_at = datetime.now(UTC)
        return task
