"""Dedicated database boundary for local and explicitly scoped Linux process tests."""

import os
import sys

from sqlalchemy.engine import URL

# Pytest's environment-isolation fixture clears EA_* after test collection.
# Capture this explicit deployment scope before that fixture starts.
_CONTAINER_HOST = os.environ.get("EA_TEST_CONTAINER_HOST")
_PLATFORM = sys.platform


def require_test_database(
    database: URL, *, platform: str = _PLATFORM, container_host: str | None = _CONTAINER_HOST
) -> None:
    local_host = database.host in {"127.0.0.1", "localhost"}
    scoped_container_host = (
        platform == "linux" and container_host == "postgres" and database.host == "postgres"
    )
    if (
        database.get_backend_name() != "postgresql"
        or database.database != "enterprise_agent_test"
        or database.username != "ea_test_app"
        or not (local_host or scoped_container_host)
    ):
        raise ValueError(
            "Process tests require the ordinary account in a dedicated local test scope"
        )


def container_scope_environment() -> dict[str, str]:
    if _PLATFORM == "linux" and _CONTAINER_HOST == "postgres":
        return {"EA_TEST_CONTAINER_HOST": "postgres"}
    return {}
