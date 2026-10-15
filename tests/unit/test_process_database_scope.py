"""A deployment marker cannot widen process tests to other accounts or databases."""

import importlib.util
import sys

import pytest
from sqlalchemy.engine import make_url

from enterprise_agent.config import PROJECT_ROOT


def load_scope():
    specification = importlib.util.spec_from_file_location(
        "isolated_process_database_scope", PROJECT_ROOT / "tests/e2e/database_scope.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_default_process_scope_still_accepts_loopback_test_accounts(host):
    database = make_url(f"postgresql+psycopg://ea_test_app:fixture@{host}/enterprise_agent_test")
    load_scope().require_test_database(database, platform="win32", container_host=None)


@pytest.mark.parametrize(
    ("platform", "marker", "allowed"),
    [
        ("linux", "postgres", True),
        ("linux", None, False),
        ("win32", "postgres", False),
        ("linux", "redis", False),
    ],
)
def test_compose_process_scope_requires_both_linux_and_explicit_fixed_host(
    platform, marker, allowed
):
    database = make_url("postgresql+psycopg://ea_test_app:fixture@postgres/enterprise_agent_test")
    scope = load_scope()
    if allowed:
        scope.require_test_database(database, platform=platform, container_host=marker)
    else:
        with pytest.raises(ValueError, match="dedicated local test scope"):
            scope.require_test_database(database, platform=platform, container_host=marker)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://ea_test_app:fixture@postgres/enterprise_agent",
        "postgresql+psycopg://ea_local_admin:fixture@postgres/enterprise_agent_test",
        "postgresql+psycopg://ea_test_app:fixture@remote.example/enterprise_agent_test",
        "mysql://ea_test_app:fixture@postgres/enterprise_agent_test",
    ],
)
def test_deployment_scope_refuses_production_owner_remote_and_non_postgres_connections(url):
    with pytest.raises(ValueError, match="dedicated local test scope"):
        load_scope().require_test_database(
            make_url(url), platform="linux", container_host="postgres"
        )


def test_process_scope_survives_pytest_environment_isolation_and_is_forwarded_to_child(monkeypatch):
    monkeypatch.setenv("EA_TEST_CONTAINER_HOST", "postgres")
    monkeypatch.setattr(sys, "platform", "linux")
    scope = load_scope()
    monkeypatch.delenv("EA_TEST_CONTAINER_HOST")
    database = make_url("postgresql+psycopg://ea_test_app:fixture@postgres/enterprise_agent_test")
    scope.require_test_database(database)
    assert scope.container_scope_environment() == {"EA_TEST_CONTAINER_HOST": "postgres"}
