"""Bounded plans and exact JSON values returned by the analytics database."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MetricId = Literal["paid_sales", "refund_rate", "order_count", "product_sales"]
AnalyticsScalar = str | int | bool | None


class SQLPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    sql: str = Field(min_length=1, max_length=8000)
    metric_ids: list[MetricId] = Field(min_length=1, max_length=4)
    label: str = Field(default="", max_length=120)


class QueryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_sql: str
    columns: list[str] = Field(min_length=1, max_length=20)
    rows: list[list[AnalyticsScalar]] = Field(max_length=500)
    row_count: int = Field(ge=0, le=500)
    truncated: bool
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def consistent_rows(self) -> "QueryResult":
        if (
            self.row_count != len(self.rows)
            or any(len(row) != len(self.columns) for row in self.rows)
            or len(set(self.columns)) != len(self.columns)
            or any(not name or len(name) > 63 for name in self.columns)
        ):
            raise ValueError("Inconsistent analytics row shape")
        return self
