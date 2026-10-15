"""Database probes keep strict authorization and publish bounded state codes."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from enterprise_agent.database import EXPECTED_SCHEMA_REVISION, Database

PRIVATE_DSN = "postgresql+psycopg://private-user:private-password@private-host/private-db"
HEALTHY_RESULTS = [False, EXPECTED_SCHEMA_REVISION, False, True]


def make_probe(results=None, *, connection_error=None):
    connection = SimpleNamespace(scalar=AsyncMock(side_effect=results or HEALTHY_RESULTS))

    @asynccontextmanager
    async def connect():
        if connection_error is not None:
            raise connection_error
        yield connection

    database = Database.__new__(Database)
    database.engine = SimpleNamespace(connect=connect)
    database.verified = False
    database.readiness_checks = {"stale": True}
    return database, connection


@pytest.mark.asyncio
async def test_safe_role_current_schema_and_bounded_required_grants_are_ready():
    database, connection = make_probe()
    assert await database.is_ready() is True
    assert database.verified is True
    assert connection.scalar.await_count == 4
    assert database.readiness_checks == {
        "connection_opened": True,
        "query_completed": True,
        "role_safe": True,
        "schema_current": True,
        "privileges_bounded": True,
        "required_grants": True,
        "timed_out": False,
    }


@pytest.mark.parametrize(
    ("results", "failed_check"),
    [
        ([True, EXPECTED_SCHEMA_REVISION, False, True], "role_safe"),
        ([None, EXPECTED_SCHEMA_REVISION, False, True], "role_safe"),
        ([False, "old-schema-revision", False, True], "schema_current"),
        ([False, EXPECTED_SCHEMA_REVISION, True, True], "privileges_bounded"),
        ([False, EXPECTED_SCHEMA_REVISION, None, True], "privileges_bounded"),
        ([False, EXPECTED_SCHEMA_REVISION, False, False], "required_grants"),
        ([False, EXPECTED_SCHEMA_REVISION, False, None], "required_grants"),
        ([0, EXPECTED_SCHEMA_REVISION, False, True], "role_safe"),
        ([False, EXPECTED_SCHEMA_REVISION, 0, True], "privileges_bounded"),
        ([False, EXPECTED_SCHEMA_REVISION, False, 1], "required_grants"),
    ],
)
@pytest.mark.asyncio
async def test_security_or_schema_failure_stays_unready_despite_completed_query(
    results, failed_check
):
    database, connection = make_probe(results)
    assert await database.is_ready() is False
    assert database.verified is False
    assert database.readiness_checks[failed_check] is False
    assert database.readiness_checks["query_completed"] is True
    assert database.readiness_checks["timed_out"] is False
    assert connection.scalar.await_count == 4


@pytest.mark.asyncio
async def test_query_timeout_marks_failure_without_disclosing_driver_text(caplog):
    database, _ = make_probe([TimeoutError(PRIVATE_DSN)])
    database.verified = True
    assert await database.is_ready() is False
    assert database.verified is False
    assert database.readiness_checks == {
        "connection_opened": True,
        "query_completed": False,
        "role_safe": None,
        "schema_current": None,
        "privileges_bounded": None,
        "required_grants": None,
        "timed_out": True,
    }
    assert PRIVATE_DSN not in json.dumps(database.readiness_checks)
    assert PRIVATE_DSN not in caplog.text


@pytest.mark.asyncio
async def test_real_async_deadline_cancels_stalled_probe():
    database, connection = make_probe()

    async def never_returns(*args, **kwargs):
        await asyncio.Event().wait()

    connection.scalar = never_returns
    assert await database.is_ready(timeout_seconds=0.01) is False
    assert database.readiness_checks["connection_opened"] is True
    assert database.readiness_checks["query_completed"] is False
    assert database.readiness_checks["timed_out"] is True


@pytest.mark.parametrize("error", [OSError(PRIVATE_DSN), TimeoutError(PRIVATE_DSN)])
@pytest.mark.asyncio
async def test_connection_failure_leaves_queries_unexecuted_and_resets_cached_success(
    error, caplog
):
    database, connection = make_probe(connection_error=error)
    database.verified = True
    assert await database.is_ready() is False
    assert database.verified is False
    assert database.readiness_checks["connection_opened"] is False
    assert database.readiness_checks["query_completed"] is False
    assert database.readiness_checks["role_safe"] is None
    assert database.readiness_checks["timed_out"] is isinstance(error, TimeoutError)
    connection.scalar.assert_not_awaited()
    assert PRIVATE_DSN not in caplog.text


@pytest.mark.asyncio
async def test_partial_query_error_preserves_completed_checks_only(caplog):
    database, connection = make_probe([False, RuntimeError(PRIVATE_DSN)])
    assert await database.is_ready() is False
    assert database.readiness_checks["role_safe"] is True
    assert database.readiness_checks["schema_current"] is None
    assert database.readiness_checks["required_grants"] is None
    assert database.readiness_checks["query_completed"] is False
    assert database.readiness_checks["timed_out"] is False
    assert connection.scalar.await_count == 2
    assert PRIVATE_DSN not in caplog.text
