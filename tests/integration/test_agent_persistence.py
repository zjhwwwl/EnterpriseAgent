"""Real SDK/PostgreSQL durability, least privilege, ownership and worker exclusion."""

import asyncio
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import errors as pg_errors
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from enterprise_agent.agents.checkpoint import AgentCheckpointRuntime
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import AgentSession, AgentTask, Order, User, UserRole
from enterprise_agent.repositories.agent import AgentRepository
from enterprise_agent.security import Principal

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


class DurableState(TypedDict):
    counter: int
    message: dict


def interrupted_graph(saver):
    def approve(state):
        decision = interrupt({"operation": "test_no_business_write"})
        return {"counter": state["counter"] + (1 if decision["confirmed"] else 0)}

    graph = StateGraph(DurableState)
    graph.add_node("approval", approve)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=saver)


async def test_real_checkpoint_survives_runtime_restart_and_interrupt_resume(pg_urls):
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    first = AgentCheckpointRuntime(pg_urls[0])
    await first.open()
    try:
        assert await first.is_ready()
        graph = interrupted_graph(first.saver)
        async with first.thread_lock(thread_id):
            result = await graph.ainvoke(
                {"counter": 41, "message": {"role": "user", "content": "持久化恢复"}}, config
            )
        assert result["__interrupt__"]
    finally:
        await first.close()

    second = AgentCheckpointRuntime(pg_urls[0])
    await second.open()
    try:
        graph = interrupted_graph(second.saver)
        snapshot = await graph.aget_state(config)
        assert snapshot.values["counter"] == 41
        assert snapshot.values["message"]["content"] == "持久化恢复"
        assert snapshot.next == ("approval",)
        async with second.thread_lock(thread_id):
            result = await graph.ainvoke(Command(resume={"confirmed": True}), config)
        assert result["counter"] == 42
        assert not (await graph.aget_state(config)).next
    finally:
        await second.close()


async def test_checkpoint_app_account_cannot_create_alter_delete_or_change_schema_versions(pg_urls):
    runtime = AgentCheckpointRuntime(pg_urls[0])
    await runtime.open()
    try:
        assert await runtime.is_ready()
        async with runtime.pool.connection() as connection:
            for statement in (
                "CREATE TABLE ea_checkpoint.forbidden_test_table (id int)",
                "ALTER TABLE ea_checkpoint.checkpoints ADD COLUMN forbidden_test_column int",
                "DELETE FROM ea_checkpoint.checkpoints WHERE false",
                "INSERT INTO ea_checkpoint.checkpoint_migrations VALUES (999)",
                "UPDATE ea_checkpoint.checkpoint_migrations SET v = v WHERE false",
            ):
                with pytest.raises(pg_errors.InsufficientPrivilege):
                    await connection.execute(statement)
            row = await (await connection.execute("SHOW search_path")).fetchone()
            assert row["search_path"] == "ea_checkpoint,public"
    finally:
        await runtime.close()


async def test_advisory_lock_excludes_other_runtime_and_releases_on_cancellation(pg_urls):
    left = AgentCheckpointRuntime(pg_urls[0])
    right = AgentCheckpointRuntime(pg_urls[0])
    await left.open()
    await right.open()
    thread_id = str(uuid4())
    entered = asyncio.Event()

    async def cancelled_worker():
        async with left.thread_lock(thread_id):
            entered.set()
            await asyncio.Event().wait()

    worker = asyncio.create_task(cancelled_worker())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(BusinessError) as caught:
            async with right.thread_lock(thread_id):
                pytest.fail("A concurrent runtime acquired the same thread")
        assert caught.value.code == "agent_session_busy"
        assert caught.value.status_code == 409
        # Separate threads can progress while the first graph is paused.
        async with right.thread_lock(str(uuid4())):
            pass
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        async with right.thread_lock(thread_id):
            pass
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await left.close()
        await right.close()


async def test_saturated_thread_capacity_keeps_sdk_checkpoint_connection_available(pg_urls):
    runtime = AgentCheckpointRuntime(pg_urls[0], pool_size=2)
    await runtime.open()
    attempted = asyncio.Event()
    entered = asyncio.Event()

    async def competing_thread():
        attempted.set()
        async with runtime.thread_lock(str(uuid4())):
            entered.set()
            await asyncio.Event().wait()

    competitor = None
    try:
        thread_id = str(uuid4())
        async with runtime.thread_lock(thread_id):
            competitor = asyncio.create_task(competing_thread())
            await attempted.wait()
            # Actual SDK reads/writes must still proceed while another caller
            # waits for execution capacity. A guard must not consume the last
            # connection needed to persist this running graph.
            result = await asyncio.wait_for(
                interrupted_graph(runtime.saver).ainvoke(
                    {"counter": 0, "message": {}},
                    {"configurable": {"thread_id": thread_id}},
                ),
                timeout=2,
            )
            assert result["__interrupt__"]
            assert not entered.is_set()
            with pytest.raises(BusinessError) as caught:
                await competitor
            assert caught.value.code == "agent_unavailable"
            assert caught.value.status_code == 503
        async with runtime.thread_lock(str(uuid4())):
            pass
    finally:
        if competitor is not None:
            competitor.cancel()
            await asyncio.gather(competitor, return_exceptions=True)
        await runtime.close()


async def test_cancellation_after_server_lock_acquisition_does_not_return_locked_pool_connection(
    pg_urls, monkeypatch
):
    left = AgentCheckpointRuntime(pg_urls[0])
    right = AgentCheckpointRuntime(pg_urls[0])
    await left.open()
    await right.open()
    thread_id = str(uuid4())
    server_locked = asyncio.Event()
    original_getconn = left.pool.getconn

    async def get_paused_connection(*args, **kwargs):
        connection = await original_getconn(*args, **kwargs)
        execute = connection.execute

        async def pause_after_real_execute(query, *query_args, **query_kwargs):
            cursor = await execute(query, *query_args, **query_kwargs)
            if "pg_try_advisory_lock" in query:
                server_locked.set()
                await asyncio.Event().wait()
            return cursor

        monkeypatch.setattr(connection, "execute", pause_after_real_execute)
        return connection

    monkeypatch.setattr(left.pool, "getconn", get_paused_connection)

    async def worker():
        async with left.thread_lock(thread_id):
            pytest.fail("The controlled acquisition should have been cancelled")

    pending = asyncio.create_task(worker())
    try:
        await asyncio.wait_for(server_locked.wait(), 5)
        with pytest.raises(BusinessError) as caught:
            async with right.thread_lock(thread_id):
                pytest.fail("The real PostgreSQL server should hold the first lock")
        assert caught.value.code == "agent_session_busy"
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with right.thread_lock(thread_id):
            pass
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await left.close()
        await right.close()


@pytest.mark.usefixtures("pg_seed")
async def test_repository_scopes_every_role_to_own_sessions_and_tasks(pg_factory):
    async with pg_factory() as session:
        users = list(
            await session.scalars(
                select(User).where(User.role == UserRole.CUSTOMER).order_by(User.id)
            )
        )
    owner = Principal(users[0].id, UserRole.CUSTOMER)
    other = Principal(users[1].id, UserRole.CUSTOMER)
    async with pg_factory() as session, session.begin():
        repository = AgentRepository(session)
        record = await repository.create_session(owner)
        task = await repository.create_task(owner, record, "查询物流")
        session_id, task_id = record.id, task.id

    async with pg_factory() as session, session.begin():
        repository = AgentRepository(session)
        for role in UserRole:
            outsider = Principal(other.user_id, role)
            assert await repository.owned_session(outsider, session_id) is None
            assert await repository.owned_task(outsider, task_id) is None
            assert (await repository.page_sessions(outsider, offset=0, limit=20))[1] == 0
            assert (await repository.page_tasks(outsider, offset=0, limit=20))[1] == 0
        own_record = await repository.owned_session(owner, session_id, lock=True)
        with pytest.raises(BusinessError) as caught:
            await repository.create_task(owner, own_record, "并发的新问题")
        assert caught.value.code == "agent_session_busy"
        own_task = await repository.owned_task(owner, task_id, lock=True)
        own_task.status = "completed"
        await session.flush()
        new_task = await repository.create_task(owner, own_record, "合法的下一轮")
        assert own_record.current_task_id == new_task.id
        assert new_task.id != task_id
        assert (await repository.page_tasks(owner, offset=0, limit=20))[1] == 2


@pytest.mark.usefixtures("pg_seed")
@pytest.mark.parametrize(
    "invalid_fields, expected_constraint",
    [
        ({"elapsed_ms": -1}, "ck_agent_tasks_elapsed_nonnegative"),
        ({"message": "界" * 2001}, "ck_agent_tasks_message_bounded"),
        ({"status": "arbitrary"}, "ck_agent_tasks_status_valid"),
    ],
)
async def test_agent_task_database_constraints(invalid_fields, expected_constraint, pg_factory):
    async with pg_factory() as session:
        user_id = await session.scalar(
            select(User.id).where(User.role == UserRole.CUSTOMER).limit(1)
        )
    actor = Principal(user_id, UserRole.CUSTOMER)
    async with pg_factory() as session, session.begin():
        record = await AgentRepository(session).create_session(actor)
        session_id = record.id
    values = {
        "session_id": session_id,
        "user_id": user_id,
        "task_type": "test",
        "status": "running",
        "message": "约束测试",
        **invalid_fields,
    }
    async with pg_factory() as session:
        session.add(AgentTask(**values))
        try:
            with pytest.raises(IntegrityError) as caught:
                await session.flush()
        finally:
            await session.rollback()
    assert caught.value.orig.diag.constraint_name == expected_constraint


@pytest.mark.usefixtures("pg_seed")
async def test_role_change_hides_prior_staff_history_and_requires_new_session(
    pg_factory, pg_admin_factory
):
    async with pg_factory() as session:
        user = await session.scalar(select(User).where(User.role == UserRole.SUPPORT).limit(1))
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        original = Principal(user.id, user.role)
        # These staff-only facts come from the actual seeded order database.
        historical_facts = {"order_no": order.order_no, "customer_id": order.user_id}
    async with pg_factory() as session, session.begin():
        repository = AgentRepository(session)
        record = await repository.create_session(original)
        task = await repository.create_task(original, record, "查询其他客户订单 10086")
        task.status = "completed"
        task.result = {"facts": historical_facts}
        original_session_id, original_task_id = record.id, task.id
        assert record.role_at_creation == UserRole.SUPPORT

    # Only the migration/test administrator can change user roles. Auth will
    # load the new role from PostgreSQL on subsequent API requests.
    async with pg_admin_factory() as session, session.begin():
        await session.execute(
            update(User).where(User.id == original.user_id).values(role="customer")
        )
    async with pg_factory() as session, session.begin():
        changed_user = await session.get(User, original.user_id)
        changed = Principal(changed_user.id, changed_user.role)
        repository = AgentRepository(session)
        assert await repository.owned_session(changed, original_session_id) is None
        assert await repository.owned_task(changed, original_task_id) is None
        assert (await repository.page_sessions(changed, offset=0, limit=20))[1] == 0
        assert (await repository.page_tasks(changed, offset=0, limit=20))[1] == 0
        # The old history is retained but inaccessible under the new role.
        old_record = await session.get(AgentSession, original_session_id)
        with pytest.raises(BusinessError) as caught:
            await repository.create_task(changed, old_record, "继续旧会话")
        assert caught.value.code == "agent_session_not_found"
        assert caught.value.status_code == 404
        fresh = await repository.create_session(changed)
        new_task = await repository.create_task(changed, fresh, "查询本人订单")
        assert fresh.role_at_creation == UserRole.CUSTOMER
        assert fresh.thread_id != old_record.thread_id
        assert (await repository.page_sessions(changed, offset=0, limit=20))[1] == 1
        assert (await repository.page_tasks(changed, offset=0, limit=20))[1] == 1
        assert await repository.owned_task(changed, new_task.id) is new_task


@pytest.mark.usefixtures("pg_seed")
@pytest.mark.parametrize("field", ["role_at_creation", "user_id", "thread_id"])
async def test_agent_session_identity_and_creation_role_cannot_be_overwritten(field, pg_factory):
    async with pg_factory() as session:
        user_ids = list(
            await session.scalars(
                select(User.id).where(User.role == UserRole.CUSTOMER).order_by(User.id)
            )
        )
    actor = Principal(user_ids[0], UserRole.CUSTOMER)
    async with pg_factory() as session, session.begin():
        record = await AgentRepository(session).create_session(actor)
        identifier = record.id
    changes = {"role_at_creation": "support", "user_id": user_ids[1], "thread_id": str(uuid4())}
    async with pg_factory() as session:
        try:
            with pytest.raises(IntegrityError) as caught:
                await session.execute(
                    update(AgentSession)
                    .where(AgentSession.id == identifier)
                    .values({field: changes[field]})
                )
        finally:
            await session.rollback()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_agent_session_identity_immutable"
