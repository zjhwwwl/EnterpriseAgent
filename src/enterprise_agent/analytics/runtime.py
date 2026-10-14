"""A dedicated reader pool with database privilege verification and bounded queries."""

import asyncio
import logging
import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.analytics.catalog import SCHEMA_MARKER, VIEWS
from enterprise_agent.analytics.sql import validate_sql
from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.schemas.analytics import AnalyticsScalar, QueryResult

logger = logging.getLogger("enterprise_agent.analytics")
_MAX_PAYLOAD_BYTES = 100 * 1024

_READINESS_SQL = """
SELECT
    current_user AS role_name,
    current_database() AS database_name,
    current_setting('default_transaction_read_only') AS read_only,
    current_setting('transaction_read_only') AS transaction_read_only,
    (SELECT obj_description(oid, 'pg_namespace')
       FROM pg_namespace WHERE nspname = 'ea_analytics') AS marker,
    EXISTS (SELECT 1 FROM pg_roles
       WHERE rolname = current_user AND (rolsuper OR rolcreatedb OR rolcreaterole
          OR rolbypassrls OR rolinherit OR NOT rolcanlogin)) AS unsafe_role,
    EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON m.member = r.oid
       WHERE r.rolname = current_user) AS memberships,
    has_database_privilege(current_user, current_database(), 'CREATE,TEMP') AS database_ddl,
    EXISTS (SELECT 1 FROM pg_database WHERE pg_get_userbyid(datdba) = current_user)
       OR EXISTS (SELECT 1 FROM pg_namespace WHERE pg_get_userbyid(nspowner) = current_user)
       OR EXISTS (SELECT 1 FROM pg_class WHERE pg_get_userbyid(relowner) = current_user)
       OR EXISTS (SELECT 1 FROM pg_proc WHERE pg_get_userbyid(proowner) = current_user)
       AS owns_objects,
    EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_'
       AND n.nspname <> 'information_schema'
       AND has_schema_privilege(current_user, n.oid, 'CREATE')) AS schema_ddl,
    EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
       AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
       AND NOT (n.nspname = 'ea_analytics' AND c.relname IN
          ('daily_orders', 'daily_product_sales') AND c.relkind = 'v')
       AND (has_table_privilege(current_user, c.oid,
           'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          OR has_any_column_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')))
       AS base_access,
    EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind = 'S'
       AND has_sequence_privilege(current_user, c.oid, 'SELECT,USAGE,UPDATE')) AS sequence_access,
    has_schema_privilege(current_user, 'ea_analytics', 'USAGE') AS schema_usage,
    (SELECT count(*) = 2 AND bool_and(
          has_table_privilege(current_user, c.oid, 'SELECT')
          AND NOT has_table_privilege(current_user, c.oid,
              'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          AND NOT has_any_column_privilege(current_user, c.oid, 'INSERT,UPDATE,REFERENCES'))
       FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
       WHERE n.nspname = 'ea_analytics' AND c.relkind = 'v'
          AND c.relname IN ('daily_orders', 'daily_product_sales')) AS view_grants
"""


def _json_value(value: object) -> AnalyticsScalar:
    if value is None or type(value) in {int, bool}:
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite analytics value")
        result = format(value, "f")
    elif type(value) is str:
        result = value
    elif isinstance(value, (datetime, date)):
        result = value.isoformat()
    else:
        # Unknown database types never get converted using repr or lossy float.
        raise ValueError("Unsupported analytics value")
    if len(result) > 512:
        raise ValueError("Oversized analytics cell")
    return result


class AnalyticsRuntime:
    def __init__(self, settings: Settings) -> None:
        if not settings.analytics_database_url:
            raise BusinessError("analytics_unavailable", "只读分析数据库未配置。", 503)
        self.settings = settings
        self.engine = create_async_engine(
            settings.analytics_database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=2,
            pool_timeout=3,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 3,
                "options": "-c statement_timeout=5000 -c search_path=pg_catalog",
            },
        )
        self.verified = False

    async def is_ready(self) -> bool:
        self.verified = False
        try:
            async with asyncio.timeout(3), self.engine.connect() as connection:
                row = (await connection.execute(text(_READINESS_SQL))).mappings().one()
                is_test = self.settings.app_env == "test"
                self.verified = (
                    row["role_name"]
                    == ("ea_test_analytics_reader" if is_test else "ea_analytics_reader")
                    and row["database_name"]
                    == ("enterprise_agent_test" if is_test else "enterprise_agent")
                    and row["read_only"] == "on"
                    and row["transaction_read_only"] == "on"
                    and row["marker"] == SCHEMA_MARKER
                    and all(
                        row[name] is False
                        for name in (
                            "unsafe_role",
                            "memberships",
                            "database_ddl",
                            "owns_objects",
                            "schema_ddl",
                            "base_access",
                            "sequence_access",
                        )
                    )
                    and row["schema_usage"] is True
                    and row["view_grants"] is True
                )
                if self.verified:
                    for view_name, columns in VIEWS.items():
                        actual = (
                            await connection.execute(
                                text(
                                    "SELECT column_name, data_type FROM information_schema.columns "
                                    "WHERE table_schema = 'ea_analytics' AND table_name = :name "
                                    "ORDER BY ordinal_position"
                                ),
                                {"name": view_name},
                            )
                        ).all()
                        expected_types = {
                            "DATE": "date",
                            "BIGINT": "bigint",
                            "NUMERIC": "numeric",
                            "TEXT": "text",
                        }
                        expected = [(name, expected_types[kind]) for name, kind in columns.items()]
                        if list(actual) != expected:
                            self.verified = False
                            break
        except Exception as exc:
            self.verified = False
            logger.warning(
                "Analytics readiness check failed",
                extra={
                    "event": "analytics_not_ready",
                    "exception_type": type(exc).__name__,
                },
            )
        return self.verified

    async def execute(self, sql: str) -> QueryResult:
        query = validate_sql(
            sql,
            max_rows=self.settings.analytics_max_rows,
            max_sql_chars=self.settings.analytics_max_sql_chars,
        )
        if not self.verified:
            raise BusinessError("analytics_unavailable", "只读分析数据库尚未通过权限检查。", 503)
        started = time.perf_counter()
        timeout_ms = self.settings.analytics_statement_timeout_ms
        try:
            async with asyncio.timeout(timeout_ms / 1000 + 2), self.engine.begin() as connection:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await connection.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                await connection.execute(text("SET LOCAL lock_timeout = 1000"))
                await connection.execute(text("SET LOCAL timezone = 'Asia/Shanghai'"))
                await connection.execute(text("SET LOCAL search_path = pg_catalog"))
                result = await connection.execute(text(query.normalized_sql))
                columns = list(result.keys())
                fetched = result.fetchmany(self.settings.analytics_max_rows + 1)
                if len(columns) > 20:
                    raise ValueError("Oversized analytics column count")
                rows = [
                    [_json_value(value) for value in row]
                    for row in fetched[: self.settings.analytics_max_rows]
                ]
                response = QueryResult(
                    normalized_sql=query.normalized_sql,
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    truncated=len(fetched) > self.settings.analytics_max_rows,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                )
                if len(response.model_dump_json().encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                    raise ValueError("Oversized analytics payload")
            logger.info(
                "Analytics query completed",
                extra={
                    "event": "analytics_query_completed",
                    "duration_ms": response.elapsed_ms,
                },
            )
            return response
        except (TimeoutError, SQLAlchemyError) as exc:
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if isinstance(exc, TimeoutError) or sqlstate in {"57014", "55P03"}:
                raise BusinessError(
                    "analytics_query_timeout", "分析查询超时，请缩小查询范围。", 504
                ) from None
            logger.warning(
                "Analytics query failed",
                extra={
                    "event": "analytics_query_failed",
                    "exception_type": type(exc).__name__,
                },
            )
            raise BusinessError("analytics_query_failed", "分析查询执行失败。", 503) from None
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Analytics result rejected",
                extra={
                    "event": "analytics_result_rejected",
                    "exception_type": type(exc).__name__,
                },
            )
            raise BusinessError(
                "analytics_result_too_large", "分析结果超出安全展示范围。", 422
            ) from None

    async def close(self) -> None:
        self.verified = False
        await self.engine.dispose()
