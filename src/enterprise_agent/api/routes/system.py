"""Liveness and configured dependency readiness; never disclose credentials."""

import asyncio

from fastapi import APIRouter, Request, Response
from redis.exceptions import RedisError

from enterprise_agent import DEVELOPMENT_PHASE, __version__
from enterprise_agent.schemas.system import HealthResponse, ServiceInfo

router = APIRouter(tags=["system"])


@router.get("/", response_model=ServiceInfo, summary="服务信息")
async def service_info(request: Request) -> ServiceInfo:
    return ServiceInfo(
        name="EnterpriseAgent",
        version=__version__,
        phase=DEVELOPMENT_PHASE,
        docs_url=request.app.docs_url,
        health_url="/health/ready",
    )


@router.get("/health/live", response_model=HealthResponse, summary="进程存活检查")
async def liveness() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="EnterpriseAgent",
        version=__version__,
        phase=DEVELOPMENT_PHASE,
        checks={"application": "ok"},
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
    summary="业务数据库、迁移版本及已配置 Redis 的就绪检查",
)
async def readiness(request: Request, response: Response) -> HealthResponse:
    checks = {"application": "ok" if request.app.state.ready else "not_ready"}
    database = request.app.state.database
    checks["database"] = (
        "ok"
        if request.app.state.ready and database is not None and await database.is_ready()
        else "not_ready"
    )
    redis = request.app.state.redis
    if redis is not None:
        try:
            checks["redis"] = "ok" if await redis.ping() else "not_ready"
        except RedisError:
            checks["redis"] = "not_ready"
    rag = request.app.state.rag
    if rag is not None:
        try:
            async with asyncio.timeout(3):
                checks["knowledge"] = "ok" if await rag.ready() else "not_ready"
        except Exception:
            checks["knowledge"] = "not_ready"
    checkpoint = request.app.state.agent_checkpoint
    if checkpoint is not None:
        checks["agent_checkpoint"] = "ok" if await checkpoint.is_ready() else "not_ready"
    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = 503
    return HealthResponse(
        status="ok" if ready else "not_ready",
        service="EnterpriseAgent",
        version=__version__,
        phase=DEVELOPMENT_PHASE,
        checks=checks,
    )
