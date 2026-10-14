"""Reproducible human-authored SQL fixtures, compared with independent base-table queries."""

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlglot import exp
from sqlglot.errors import ErrorLevel

from enterprise_agent import __version__
from enterprise_agent.analytics.runtime import AnalyticsRuntime, _json_value
from enterprise_agent.analytics.sql import _ALLOWED_NODES
from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.analytics import MetricId

DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "analytics-sql.json"
_REFERENCE_NODES = _ALLOWED_NODES | {exp.Join, exp.AtTimeZone}
_REFERENCE_TYPES = {
    exp.DataType.Type.DATE,
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.INT,
    exp.DataType.Type.TEXT,
}
_SNAPSHOT_QUERIES = {
    "orders": "SELECT id, status, paid_at, goods_paid_cents, shipping_paid_cents, paid_total_cents "
    "FROM public.orders ORDER BY id LIMIT 10001",
    "order_items": "SELECT id, order_id, product_id, product_name, category, quantity, "
    "paid_amount_cents FROM public.order_items ORDER BY id LIMIT 10001",
    "refund_requests": "SELECT id, order_id, status, amount_cents, completed_at "
    "FROM public.refund_requests ORDER BY id LIMIT 10001",
}


class SQLCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    question: str = Field(min_length=1, max_length=1000)
    query_sql: str = Field(min_length=1, max_length=16000)
    reference_sql: str | None = Field(default=None, min_length=1, max_length=16000)
    metric_ids: list[MetricId] = Field(default_factory=list, max_length=4)
    expected_error_code: Literal["analytics_sql_rejected"] | None = None

    @model_validator(mode="after")
    def distinct_case_types(self) -> "SQLCase":
        positive = self.reference_sql is not None and bool(self.metric_ids)
        if (
            self.expected_error_code is None
            and not positive
            or self.expected_error_code is not None
            and (self.reference_sql or self.metric_ids)
        ):
            raise ValueError(
                "An evaluation case must be either a reference comparison or a rejection"
            )
        return self


class SQLDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1]
    source: Literal["human_authored_sql_fixtures"]
    as_of: Literal["2026-10-12"]
    timezone: Literal["Asia/Shanghai"]
    cases: list[SQLCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_cases(self) -> "SQLDataset":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate evaluation case ids")
        return self


def load_dataset(path: Path) -> SQLDataset:
    if path.stat().st_size > 256 * 1024:
        raise ValueError("Oversized analytics dataset")
    return SQLDataset.model_validate_json(path.read_bytes())


def _reference_sql(sql: str) -> str:
    statements = sqlglot.parse(sql, read="postgres", error_level=ErrorLevel.RAISE)
    if len(statements) != 1 or type(statements[0]) is not exp.Select:
        raise ValueError("Reference must be a single SELECT")
    tree = statements[0]
    tables = list(tree.find_all(exp.Table))
    if not tables:
        raise ValueError("Reference must read independent base tables")
    for table in tables:
        if (
            table.db != "public"
            or table.name not in _SNAPSHOT_QUERIES
            or table.catalog
            or type(table.this) is not exp.Identifier
        ):
            raise ValueError("Reference may only read the three explicit ecommerce base tables")
    for node in tree.walk():
        if type(node) not in _REFERENCE_NODES or node.comments:
            raise ValueError("Unsupported reference construct")
        if type(node) is exp.DataType and (
            node.this not in _REFERENCE_TYPES or node.args.get("nested") or node.args.get("kind")
        ):
            raise ValueError("Unsupported reference cast")
    return tree.sql(dialect="postgres")


async def _prepare_reference(session) -> None:
    # This evaluator never seeds, migrates, resets or changes the demo database.
    await session.execute(text("SET TRANSACTION READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout = 5000"))
    await session.execute(text("SET LOCAL lock_timeout = 1000"))
    await session.execute(text("SET LOCAL search_path = pg_catalog"))
    await session.execute(text("SET LOCAL timezone = 'Asia/Shanghai'"))
    identity = (await session.execute(text("SELECT current_database(), current_user"))).one()
    if tuple(identity) != ("enterprise_agent_test", "ea_local_admin"):
        raise ValueError("References require the explicit test database migration account")


async def _reference_result(factory, sql: str) -> dict:
    normalized = _reference_sql(sql)
    # Fixture references have a separate small fetch bound, never an unbounded fetchall.
    bounded = sqlglot.parse_one(normalized, read="postgres").limit(501).sql(dialect="postgres")
    original = sqlglot.parse_one(normalized, read="postgres")
    if original.args.get("limit"):
        limit = original.args["limit"].expression
        if type(limit) is not exp.Literal or limit.is_string or not limit.this.isdigit():
            raise ValueError("Reference limit must be an integer")
        bounded = original.limit(min(int(limit.this), 501), copy=False).sql(dialect="postgres")
    async with asyncio.timeout(7), factory() as session, session.begin():
        await _prepare_reference(session)
        result = await session.execute(text(bounded))
        columns = list(result.keys())
        rows = result.fetchmany(501)
        if len(rows) > 500:
            raise ValueError("Reference output exceeds the fixture budget")
        return {
            "normalized_sql": bounded,
            "columns": columns,
            "rows": [[_json_value(value) for value in row] for row in rows],
        }


async def _snapshot(factory) -> dict:
    contents = {}
    async with asyncio.timeout(7), factory() as session, session.begin():
        await _prepare_reference(session)
        for name, sql in _SNAPSHOT_QUERIES.items():
            result = await session.execute(text(sql))
            rows = result.fetchmany(10001)
            if len(rows) > 10000:
                raise ValueError("Evaluation snapshot exceeds the disposable fixture budget")
            contents[name] = [
                [str(value) if isinstance(value, UUID) else _json_value(value) for value in row]
                for row in rows
            ]
        database_version = await session.scalar(text("SELECT version()"))
    serialized = json.dumps(contents, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "database": "enterprise_agent_test",
        "reference_role": "ea_local_admin",
        "table_counts": {name: len(rows) for name, rows in contents.items()},
        "data_fingerprint_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "database_version": database_version,
    }


def _write_report(result: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = result["summary"]
    lines = [
        "# Analytics SQL Evaluation",
        "",
        "本报告验证人工编写 SQL 的视图结果与独立基表聚合一致性，以及受限 SQL 拒绝边界。",
        "自然语言问题只用于说明样例；本轮未调用模型，不代表自然语言转 SQL 正确率或线上安全保证。",
        "",
        f"- 固定问题日期：{result['as_of']}，时区 {result['timezone']}。",
        f"- 实际执行时间：{result['executed_at']}。",
        f"- 测试数据库：{result['data']['database']}；表行数：{result['data']['table_counts']}。",
        f"- 数据指纹：`{result['data']['data_fingerprint_sha256']}`。",
        f"- 数据在执行前后保持一致：{result['data_stable']}。",
        f"- 正确查询对照通过：{summary['correct_query_passed']}/{summary['correct_query_total']}。",
        f"- 安全拒绝样例通过：{summary['rejection_passed']}/{summary['rejection_total']}。",
        f"- 总计：{summary['passed']}/{summary['total']}；失败：{summary['failed']}。",
        "- 模型调用：0；Provider Token 消耗：N/A。",
        "",
        "耗时来自本次实际执行，包含本地缓存、连接池和当前机器状态影响，不是性能基准。",
        "",
        "| Case | Kind | Passed | Actual elapsed ms |",
        "| --- | --- | --- | --- |",
    ]
    for case in result["cases"]:
        lines.append(f"| {case['id']} | {case['kind']} | {case['passed']} | {case['elapsed_ms']} |")
    lines.extend(["", "详见 results.json 的实际结果、独立参考结果、规范化 SQL 与失败代码。", ""])
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


async def evaluate(
    runtime: AnalyticsRuntime, reference_factory, dataset_path: Path, output_dir: Path
) -> dict:
    dataset_path, output_dir = Path(dataset_path), Path(output_dir)
    dataset = load_dataset(dataset_path)
    before = await _snapshot(reference_factory)
    cases = []
    for case in dataset.cases:
        started = time.perf_counter()
        record = {
            "id": case.id,
            "question": case.question,
            "metric_ids": case.metric_ids,
            "kind": "rejection" if case.expected_error_code else "reference_comparison",
            "query_sql": case.query_sql,
            "passed": False,
        }
        try:
            if case.reference_sql:
                expected = await _reference_result(reference_factory, case.reference_sql)
                actual = await runtime.execute(case.query_sql)
                record.update(
                    {
                        "expected": expected,
                        "actual": actual.model_dump(mode="json"),
                        "reference_sql": expected["normalized_sql"],
                        "passed": not actual.truncated
                        and actual.columns == expected["columns"]
                        and actual.rows == expected["rows"],
                    }
                )
                if not record["passed"]:
                    record["error_code"] = "analytics_result_mismatch"
            else:
                await runtime.execute(case.query_sql)
                record["error_code"] = "analytics_expected_rejection_missing"
        except BusinessError as exc:
            record["error_code"] = exc.code
            record["passed"] = case.expected_error_code == exc.code
        except Exception as exc:
            record["error_code"] = "analytics_evaluation_case_failed"
            record["exception_type"] = type(exc).__name__
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        cases.append(record)
    after = await _snapshot(reference_factory)
    stable = before["data_fingerprint_sha256"] == after["data_fingerprint_sha256"]
    correct = [case for case in cases if case["kind"] == "reference_comparison"]
    rejection = [case for case in cases if case["kind"] == "rejection"]
    passed = sum(case["passed"] for case in cases)
    result = {
        "schema_version": 1,
        "application_version": __version__,
        "source": dataset.source,
        "as_of": dataset.as_of,
        "timezone": dataset.timezone,
        "executed_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "data": before,
        "data_stable": stable,
        "dependencies": {
            name: version(name) for name in ("sqlglot", "SQLAlchemy", "psycopg", "pydantic")
        },
        "model_calls": 0,
        "provider_token_usage": None,
        "summary": {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "correct_query_total": len(correct),
            "correct_query_passed": sum(c["passed"] for c in correct),
            "rejection_total": len(rejection),
            "rejection_passed": sum(c["passed"] for c in rejection),
            "reference_match_rate": sum(c["passed"] for c in correct) / len(correct)
            if correct
            else None,
            "rejection_rate": sum(c["passed"] for c in rejection) / len(rejection)
            if rejection
            else None,
        },
        "cases": cases,
    }
    _write_report(result, output_dir)
    return result


async def _cli(output_dir: Path, dataset_path: Path) -> int:
    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_enabled=False,
        deepseek_api_key=None,
        agent_enabled=False,
        analytics_enabled=False,
        analytics_database_url=None,
        database_url=None,
        migration_database_url=None,
        redis_url=None,
        mcp_access_token=None,
        rag_enabled=False,
    )
    reader, reference = settings.test_analytics_database_url, settings.test_migration_database_url
    if not reader or not reference:
        raise ValueError("Explicit test analytics and migration database credentials are required")
    for secret, role in ((reader, "ea_test_analytics_reader"), (reference, "ea_local_admin")):
        url = make_url(secret.get_secret_value())
        if (
            url.database != "enterprise_agent_test"
            or url.username != role
            or url.drivername != "postgresql+psycopg"
        ):
            raise ValueError("Evaluation refuses a non-dedicated PostgreSQL test database or role")
    configured = settings.model_copy(
        update={
            "analytics_enabled": True,
            "analytics_database_url": reader,
        }
    )
    runtime = AnalyticsRuntime(configured)
    engine = create_async_engine(
        reference.get_secret_value(),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "connect_timeout": 3,
            "options": "-c statement_timeout=5000 -c search_path=pg_catalog",
        },
    )
    try:
        if not await runtime.is_ready():
            raise ValueError("The dedicated analytics reader has not passed readiness checks")
        result = await evaluate(runtime, async_sessionmaker(engine), dataset_path, output_dir)
        print(
            json.dumps(
                {
                    "output": str(output_dir),
                    "summary": result["summary"],
                    "data_stable": result["data_stable"],
                    "model_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return 0 if not result["summary"]["failed"] and result["data_stable"] else 1
    finally:
        await runtime.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare manual SQL fixtures against real test data"
    )
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "var" / "evaluation" / "sql")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    arguments = parser.parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(_cli(arguments.output, arguments.dataset))
    except Exception as exc:
        print(f"analytics_evaluation_failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
