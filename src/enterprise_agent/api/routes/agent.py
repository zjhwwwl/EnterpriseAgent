"""Authenticated session/task APIs; clients cannot select graph thread/config."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Query, Request

from enterprise_agent.api.dependencies import CurrentUser, get_database
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.agent import (
    EmptyInput,
    MessageInput,
    ResumeInput,
    SessionPage,
    SessionResponse,
    TaskPage,
    TaskResponse,
)
from enterprise_agent.services.agent import AgentService

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def service(request: Request):
    database = get_database(request)
    checkpoint = request.app.state.agent_checkpoint
    if checkpoint is None:
        raise BusinessError("agent_unavailable", "Agent 持久化未启用。", 503)
    return AgentService(
        database.session_factory,
        request.app.state.settings,
        checkpoint,
        request.app.state.agent_model,
        request.app.state.rag,
        request.app.state.clock,
        analytics_runtime=request.app.state.analytics,
    )


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    request: Request,
    actor: CurrentUser,
    payload: Annotated[EmptyInput, Body(default_factory=EmptyInput)],
):
    return await service(request).create_session(actor)


@router.get("/sessions", response_model=SessionPage)
async def sessions(
    request: Request,
    actor: CurrentUser,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return await service(request).list_sessions(actor, offset=offset, limit=limit)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, request: Request, actor: CurrentUser):
    return await service(request).get_session(actor, session_id)


@router.post("/sessions/{session_id}/messages", response_model=TaskResponse)
async def send_message(
    session_id: UUID, payload: MessageInput, request: Request, actor: CurrentUser
):
    return await service(request).send(actor, session_id, payload.message)


@router.get("/tasks", response_model=TaskPage)
async def tasks(
    request: Request,
    actor: CurrentUser,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    session_id: UUID | None = None,
):
    return await service(request).list_tasks(
        actor, offset=offset, limit=limit, session_id=session_id
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, request: Request, actor: CurrentUser):
    return await service(request).get_task(actor, task_id)


@router.post("/tasks/{task_id}/resume", response_model=TaskResponse)
async def resume_task(task_id: UUID, payload: ResumeInput, request: Request, actor: CurrentUser):
    return await service(request).resume(actor, task_id, payload)


@router.post("/tasks/{task_id}/recover", response_model=TaskResponse)
async def recover_task(
    task_id: UUID,
    request: Request,
    actor: CurrentUser,
    payload: Annotated[EmptyInput, Body(default_factory=EmptyInput)],
):
    return await service(request).recover(actor, task_id)
