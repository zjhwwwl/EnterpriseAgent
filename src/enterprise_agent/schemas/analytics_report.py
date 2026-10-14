"""API output contains executed rows and deterministic report/chart data."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from enterprise_agent.schemas.analytics import MetricId, QueryResult


class AnalyticsReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    query: QueryResult
    chart: dict[str, Any] | None
    metric_ids: list[MetricId]
    metric_definitions: dict[str, str]
