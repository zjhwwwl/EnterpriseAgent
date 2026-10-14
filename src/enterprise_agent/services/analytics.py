"""Authorized aggregate queries and reports composed only from executed results."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from enterprise_agent.analytics.catalog import METRIC_DEFINITIONS, SCHEMA, VIEWS
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import UserRole
from enterprise_agent.schemas.analytics import QueryResult

SHANGHAI = ZoneInfo("Asia/Shanghai")


def authorize_analytics(actor) -> None:
    if actor.role not in (UserRole.ANALYST, UserRole.ADMIN):
        raise BusinessError("analytics_forbidden", "仅运营分析员与管理员可查询运营数据。", 403)


def _numeric(value) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _chart(result: QueryResult) -> dict | None:
    if not result.rows or not result.columns:
        return None
    dimension = next(
        (
            index
            for index, column in enumerate(result.columns)
            if column.endswith("_id")
            or any(
                value is not None and not _numeric(value)
                for value in (row[index] for row in result.rows)
            )
        ),
        None,
    )
    series = [
        {
            "name": result.columns[index],
            "column_index": index,
            "data": [row[index] for row in result.rows],
        }
        for index in range(len(result.columns))
        if index != dimension
        and not result.columns[index].endswith("_id")
        and all(_numeric(row[index]) for row in result.rows)
        and any(row[index] is not None for row in result.rows)
    ]
    if not series:
        return None
    labels = (
        [str(row[dimension]) if row[dimension] is not None else "空值" for row in result.rows]
        if dimension is not None
        else [str(index + 1) for index in range(result.row_count)]
    )
    try:
        dates = [datetime.strptime(label, "%Y-%m-%d") for label in labels]
        time_series = dimension is not None and all(
            date.strftime("%Y-%m-%d") == label for date, label in zip(dates, labels, strict=True)
        )
    except ValueError:
        time_series = False
    return {
        "type": "line" if time_series else "bar",
        "labels": labels,
        "series": series,
        "source": "query_rows",
    }


def _cell(value) -> str:
    if value is None:
        return "NULL"
    return (
        str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    )


class AnalyticsService:
    def __init__(self, runtime, *, clock=lambda: datetime.now(UTC)):
        self.runtime = runtime
        self.clock = clock

    def catalog(self, actor) -> dict:
        authorize_analytics(actor)
        current = self.clock()
        if current.tzinfo is None:
            raise BusinessError("analytics_clock_invalid", "运营日期配置不可用。", 503)
        today = current.astimezone(SHANGHAI).date()
        tomorrow = today + timedelta(days=1)
        return {
            "schema": SCHEMA,
            "views": deepcopy(VIEWS),
            "metric_definitions": dict(METRIC_DEFINITIONS),
            "business_timezone": "Asia/Shanghai",
            "as_of_utc": current.astimezone(UTC).isoformat(),
            "today": today.isoformat(),
            "date_windows": {
                "last_seven_days_including_today": {
                    "start_inclusive": (today - timedelta(days=6)).isoformat(),
                    "end_exclusive": tomorrow.isoformat(),
                },
                "this_month_to_date": {
                    "start_inclusive": today.replace(day=1).isoformat(),
                    "end_exclusive": tomorrow.isoformat(),
                },
                "previous_complete_week": {
                    "start_inclusive": (today - timedelta(days=14)).isoformat(),
                    "end_exclusive": (today - timedelta(days=7)).isoformat(),
                },
                "latest_complete_week": {
                    "start_inclusive": (today - timedelta(days=7)).isoformat(),
                    "end_exclusive": today.isoformat(),
                },
            },
            "notes": [
                "时间窗口采用左闭右开；今天的数据截至实际查询时间，非完整自然日。",
                "退款状态为模拟完成；查询时可能变化，毛销售额不扣除退款。",
                "商品名称和类目来自订单明细快照，跨历史名称可出现不同分组。",
            ],
        }

    async def execute(self, actor, sql: str) -> QueryResult:
        authorize_analytics(actor)
        if self.runtime is None or not self.runtime.verified:
            raise BusinessError("analytics_unavailable", "只读运营数据库尚未就绪。", 503)
        return await self.runtime.execute(sql)

    def render(self, result: QueryResult, metric_ids: list[str]) -> dict[str, Any]:
        # Revalidate persisted/external values before indexing rows for display.
        result = QueryResult.model_validate(result)
        if not metric_ids or any(metric not in METRIC_DEFINITIONS for metric in metric_ids):
            raise BusinessError("analytics_metric_invalid", "分析指标口径不受支持。", 422)
        metrics = list(dict.fromkeys(metric_ids))
        definitions = {metric: METRIC_DEFINITIONS[metric] for metric in metrics}
        lines = ["运营查询结果", "", f"实际返回 {result.row_count} 行。"]
        if result.truncated:
            lines.append("结果达到行数上限，已截断；不能据此计算未返回部分的总量。")
        lines.extend(["", "指标口径：", *[f"- {value}" for value in definitions.values()], ""])
        if result.rows:
            lines.append("| " + " | ".join(_cell(column) for column in result.columns) + " |")
            lines.append("| " + " | ".join("---" for _ in result.columns) + " |")
            lines.extend(
                "| " + " | ".join(_cell(value) for value in row) + " |" for row in result.rows[:20]
            )
            if result.row_count > 20:
                lines.append("正文仅展示前 20 行；完整受限结果见 query.rows。")
        else:
            lines.append("所选条件没有结果；不将缺失数据推断为业务变化。")
        lines.extend(
            [
                "",
                "金额原始指标以人民币分计，派生比例与范围以执行 SQL 为准。",
                "查询结果只反映记录，不能据此推断业务变化原因。",
                "",
                "执行 SQL：",
                "```sql",
                result.normalized_sql,
                "```",
            ]
        )
        return {
            "answer": "\n".join(lines),
            "query": result.model_dump(mode="json"),
            "chart": _chart(result),
            "metric_ids": metrics,
            "metric_definitions": definitions,
        }
