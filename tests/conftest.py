"""Test isolation shared by the Phase 1 API and configuration checks."""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings, get_settings


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
