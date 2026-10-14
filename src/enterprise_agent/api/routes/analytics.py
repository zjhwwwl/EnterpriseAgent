"""Staff-only catalog and SQL queries through the same Agent analytics Service."""

from fastapi import APIRouter, Request

from enterprise_agent.api.dependencies import CurrentUser
from enterprise_agent.schemas.analytics import SQLPlan
from enterprise_agent.schemas.analytics_report import AnalyticsReport
from enterprise_agent.services.analytics import AnalyticsService

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


def service(request: Request) -> AnalyticsService:
    return AnalyticsService(request.app.state.analytics, clock=request.app.state.clock)


@router.get("/catalog")
async def catalog(request: Request, actor: CurrentUser):
    return service(request).catalog(actor)


@router.post("/query", response_model=AnalyticsReport)
async def query(payload: SQLPlan, request: Request, actor: CurrentUser):
    analytics = service(request)
    result = await analytics.execute(actor, payload.sql)
    return analytics.render(result, payload.metric_ids)
