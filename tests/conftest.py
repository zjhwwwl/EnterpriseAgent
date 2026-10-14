"""Isolated API tests and real PostgreSQL tests against a dedicated database."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_agent.application import create_app
from enterprise_agent.config import PROJECT_ROOT, Settings, get_settings
from enterprise_agent.models import Base
from enterprise_agent.seed import seed_demo

# Capture explicitly exported credentials before the autouse isolation fixture.
_PG_ENV = {
    name: os.environ.get(name)
    for name in (
        "EA_TEST_DATABASE_URL",
        "EA_TEST_MIGRATION_DATABASE_URL",
        "EA_REDIS_URL",
        "EA_TEST_ANALYTICS_DATABASE_URL",
    )
}
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_addoption(parser):
    parser.addoption(
        "--require-postgres", action="store_true", help="Fail rather than skip PostgreSQL tests"
    )
    parser.addoption(
        "--require-rag", action="store_true", help="Fail rather than skip real RAG dependencies"
    )


@pytest.fixture(scope="session")
def rag_models(request):
    from enterprise_agent.rag.backends import ModelRuntime

    settings = Settings(
        _env_file=None, app_env="test", rag_model_dir=PROJECT_ROOT / "var" / "models"
    )
    models = ModelRuntime(settings)
    if not models.ready():
        if request.config.getoption("--require-rag"):
            pytest.fail("Prepared, verified local RAG models are required")
        pytest.skip("RAG models are not prepared")
    return models


@pytest.fixture
def rag_settings(tmp_path):
    return Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path,
        rag_enabled=True,
        rag_model_dir=PROJECT_ROOT / "var" / "models",
        rag_collection_prefix="ea_test_" + uuid4().hex,
    )


@pytest_asyncio.fixture
async def rag_runtime(rag_models, rag_settings, request):
    from enterprise_agent.rag.runtime import RagRuntime

    runtime = RagRuntime(rag_settings, models=rag_models)
    if not await runtime.call(runtime.store.ready):
        if request.config.getoption("--require-rag"):
            pytest.fail("Real Chroma HTTP server is required")
        pytest.skip("Chroma HTTP server is not available")
    await runtime.call(lambda: runtime.store.collection)
    try:
        yield runtime
    finally:
        await asyncio.to_thread(runtime.store.client.delete_collection, runtime.store.name)
        await runtime.close()


@pytest.fixture
def pg_rag_service(pg_factory, rag_settings, rag_runtime):
    from enterprise_agent.services.knowledge import KnowledgeService

    return KnowledgeService(pg_factory, rag_settings, rag_runtime)


@pytest.fixture
def rag_api_app(pg_urls, rag_settings, rag_runtime):
    configured = rag_settings.model_copy(
        update={
            "database_url": SecretStr(pg_urls[0]),
            "jwt_secret": SecretStr(uuid4().hex + uuid4().hex),
        }
    )
    app = create_app(configured, configure_logs=False, rag_runtime=rag_runtime)
    return app


@pytest_asyncio.fixture
async def rag_api_client(rag_api_app, pg_seed):
    async with rag_api_app.router.lifespan_context(rag_api_app):
        assert rag_api_app.state.database.verified
        async with AsyncClient(
            transport=ASGITransport(app=rag_api_app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as client:
            yield client


@pytest.fixture(scope="session")
def pg_urls(request):
    urls = (_PG_ENV["EA_TEST_DATABASE_URL"], _PG_ENV["EA_TEST_MIGRATION_DATABASE_URL"])
    if not all(urls):
        if request.config.getoption("--require-postgres"):
            pytest.fail("Explicit dedicated PostgreSQL test credentials are required")
        pytest.skip("Dedicated PostgreSQL test credentials are not exported")
    for raw in urls:
        parsed = make_url(raw)
        if parsed.database != "enterprise_agent_test" or parsed.get_backend_name() != "postgresql":
            pytest.fail("Tests refuse any database other than enterprise_agent_test")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("EA_")}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "EA_APP_ENV": "test",
            "EA_DATABASE_URL": urls[0],
            "EA_MIGRATION_DATABASE_URL": urls[1],
            "EA_JWT_SECRET": uuid4().hex + uuid4().hex,
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if result.returncode:
        pytest.fail("Test database migration failed; verify infrastructure and credentials")
    return urls


@pytest_asyncio.fixture
async def pg_factory(pg_urls):
    engine = create_async_engine(
        pg_urls[0],
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"options": "-c statement_timeout=10000"},
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_admin_factory(pg_urls):
    engine = create_async_engine(pg_urls[1], poolclass=NullPool, hide_parameters=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def analytics_urls(pg_urls, request):
    raw = _PG_ENV["EA_TEST_ANALYTICS_DATABASE_URL"]
    if not raw:
        if request.config.getoption("--require-postgres"):
            pytest.fail("Explicit dedicated analytics reader credentials are required")
        pytest.skip("Dedicated analytics reader credentials are not exported")
    parsed = make_url(raw)
    if parsed.database != "enterprise_agent_test" or parsed.username != "ea_test_analytics_reader":
        pytest.fail("Analytics tests refuse a non-dedicated reader or database")
    return raw, pg_urls[1]


@pytest.fixture
def analytics_settings(analytics_urls):
    return Settings(
        _env_file=None,
        app_env="test",
        analytics_enabled=True,
        analytics_database_url=analytics_urls[0],
    )


@pytest_asyncio.fixture
async def analytics_runtime(analytics_settings):
    from enterprise_agent.analytics.runtime import AnalyticsRuntime

    runtime = AnalyticsRuntime(analytics_settings)
    try:
        assert await runtime.is_ready(), (
            "A dedicated, migrated read-only analytics role is required"
        )
        yield runtime
    finally:
        await runtime.close()


@pytest_asyncio.fixture
async def pg_seed(pg_admin_factory):
    async with pg_admin_factory() as session, session.begin():
        # This reset is restricted by pg_urls to our disposable test database.
        tables = ", ".join('"' + name + '"' for name in Base.metadata.tables)
        await session.execute(text("TRUNCATE " + tables + " RESTART IDENTITY CASCADE"))
    return await seed_demo(
        pg_admin_factory,
        as_of=datetime(2026, 10, 12, tzinfo=UTC),
        seed=42,
        orders_count=500,
        demo_password="DemoOnly!2026",
    )


@pytest.fixture
def pg_api_app(pg_urls, tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_url=pg_urls[0],
            jwt_secret=uuid4().hex + uuid4().hex,
            data_dir=tmp_path,
        ),
        configure_logs=False,
    )
    app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=UTC)
    return app


@pytest_asyncio.fixture
async def pg_api_client(pg_api_app, pg_seed):
    async with pg_api_app.router.lifespan_context(pg_api_app):
        assert pg_api_app.state.database.verified, "Migrated ordinary API account must be ready"
        async with AsyncClient(
            transport=ASGITransport(app=pg_api_app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as http:
            yield http


@pytest_asyncio.fixture
async def pg_rate_limited_client(pg_urls, pg_seed, request, tmp_path):
    raw = _PG_ENV["EA_REDIS_URL"]
    if not raw:
        if request.config.getoption("--require-postgres"):
            pytest.fail("Real Redis URL is required for the login limiter test")
        pytest.skip("Redis URL not exported")
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_url=pg_urls[0],
            redis_url=raw,
            jwt_secret=uuid4().hex + uuid4().hex,
            data_dir=tmp_path,
            login_max_attempts=3,
        ),
        configure_logs=False,
    )
    async with app.router.lifespan_context(app):
        assert await app.state.redis.ping(), "Real Redis must be available"
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
        ) as http:
            yield http


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Do not let a developer's exported credentials affect test outcomes."""
    for name in tuple(os.environ):
        if name.startswith("EA_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, app_env="test", data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    return create_app(settings, configure_logs=False)


@pytest_asyncio.fixture
async def client(app):
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http
