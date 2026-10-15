"""Bounded SQLAlchemy pools; the API never creates or migrates tables."""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

EXPECTED_SCHEMA_REVISION = "0005_analytics"
logger = logging.getLogger("enterprise_agent.database")


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            hide_parameters=True,
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000"},
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.verified = False
        self.readiness_checks = {}

    async def is_ready(self, *, timeout_seconds: float = 3) -> bool:
        self.readiness_checks = {
            "connection_opened": False,
            "query_completed": False,
            "role_safe": None,
            "schema_current": None,
            "privileges_bounded": None,
            "required_grants": None,
            "timed_out": False,
        }
        try:
            async with asyncio.timeout(timeout_seconds), self.engine.connect() as connection:
                self.readiness_checks["connection_opened"] = True
                unsafe_role = await connection.scalar(
                    text(
                        "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
                self.readiness_checks["role_safe"] = unsafe_role is False
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                self.readiness_checks["schema_current"] = revision == EXPECTED_SCHEMA_REVISION
                excessive_grants = await connection.scalar(
                    text(
                        "SELECT has_schema_privilege(current_user, 'public', 'CREATE') "
                        "OR has_column_privilege(current_user, 'orders', 'status', 'UPDATE') "
                        "OR has_table_privilege(current_user, 'audit_logs', 'UPDATE,DELETE') "
                        "OR has_any_column_privilege(current_user, 'audit_logs', 'UPDATE') "
                        "OR EXISTS (SELECT 1 FROM pg_database WHERE datname = current_database() "
                        "AND pg_get_userbyid(datdba) = current_user) "
                        "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                        "AND pg_get_userbyid(c.relowner) = current_user)"
                    )
                )
                self.readiness_checks["privileges_bounded"] = excessive_grants is False
                required_grants = await connection.scalar(
                    text("""
                    SELECT
                      (SELECT bool_and(has_table_privilege(current_user, name, 'SELECT'))
                       FROM unnest(ARRAY['users', 'orders', 'order_items', 'logistics',
                         'refund_requests', 'support_tickets', 'refund_policy_versions',
                         'knowledge_documents', 'knowledge_chunks', 'approval_requests',
                         'idempotency_records',
                         'audit_logs']) AS t(name))
                      AND (SELECT bool_and(has_table_privilege(current_user, name, privilege))
                       FROM unnest(ARRAY['approval_requests', 'refund_requests',
                         'support_tickets', 'idempotency_records']) AS t(name)
                       CROSS JOIN unnest(ARRAY['INSERT', 'UPDATE']) AS p(privilege))
                      AND has_table_privilege(current_user, 'audit_logs', 'INSERT')
                      AND has_table_privilege(current_user, 'knowledge_documents', 'INSERT')
                      AND has_table_privilege(current_user, 'knowledge_documents', 'UPDATE')
                      AND has_table_privilege(current_user, 'knowledge_chunks', 'INSERT')
                      AND has_table_privilege(current_user, 'knowledge_chunks', 'DELETE')
                      AND (SELECT bool_and(has_table_privilege(current_user, name, privilege))
                       FROM unnest(ARRAY['agent_sessions', 'agent_tasks']) AS t(name)
                       CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE']) AS p(privilege))
                      AND has_column_privilege(current_user, 'orders', 'id', 'UPDATE')
                      AND has_sequence_privilege(current_user,
                        pg_get_serial_sequence('audit_logs', 'id'), 'USAGE')
                """)
                )
                self.readiness_checks["required_grants"] = required_grants is True
                self.readiness_checks["query_completed"] = True
                self.verified = (
                    unsafe_role is False
                    and excessive_grants is False
                    and required_grants is True
                    and revision == EXPECTED_SCHEMA_REVISION
                )
        except Exception as exc:
            self.verified = False
            self.readiness_checks["timed_out"] = isinstance(exc, TimeoutError)
            logger.warning(
                "Database readiness check failed",
                extra={"event": "database_not_ready", "exception_type": type(exc).__name__},
            )
        return self.verified

    async def close(self) -> None:
        self.verified = False
        await self.engine.dispose()
