"""Application health only; Phase 1 does not probe external services."""

from fastapi import APIRouter, Request, Response

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
    summary="Phase 1 应用生命周期就绪检查",
)
async def readiness(request: Request, response: Response) -> HealthResponse:
    ready = request.app.state.ready
    if not ready:
        response.status_code = 503
    return HealthResponse(
        status="ok" if ready else "not_ready",
        service="EnterpriseAgent",
        version=__version__,
        phase=DEVELOPMENT_PHASE,
        checks={"application": "ok" if ready else "not_ready"},
    )
