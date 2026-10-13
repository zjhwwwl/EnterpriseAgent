"""Verify readiness against migrated PostgreSQL and reject an owner configuration."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.mark.parametrize(
    "permission",
    [
        "SELECT ON users",
        "INSERT ON audit_logs",
        "UPDATE ON approval_requests",
        "UPDATE(id) ON orders",
    ],
)
async def test_missing_required_grants_are_not_reported_ready(
    permission,
    pg_api_app,
    pg_admin_factory,
):
    # Alter only the dedicated test role and restore its grant even on failure.
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text(f"REVOKE {permission} FROM ea_test_app"))
    try:
        async with pg_api_app.router.lifespan_context(pg_api_app):
            assert pg_api_app.state.database.verified is False
            async with AsyncClient(
                transport=ASGITransport(app=pg_api_app), base_url="http://testserver"
            ) as http:
                assert (await http.get("/health/ready")).status_code == 503
    finally:
        async with pg_admin_factory() as session, session.begin():
            await session.execute(text(f"GRANT {permission} TO ea_test_app"))


async def test_ready_api_checks_real_database_and_returns_no_credentials(pg_api_client):
    response = await pg_api_client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {
        "application": "ok",
        "database": "ok",
        "agent_checkpoint": "ok",
    }
    assert "password" not in response.text and "postgresql" not in response.text


@pytest.mark.parametrize(
    "operation, permission, failed_check",
    [
        ("REVOKE", "SELECT ON knowledge_chunks", "database"),
        ("GRANT", "INSERT(v) ON ea_checkpoint.checkpoint_migrations", "agent_checkpoint"),
        ("GRANT", "UPDATE(v) ON ea_checkpoint.checkpoint_migrations", "agent_checkpoint"),
    ],
    ids=["missing-chunk-read", "migration-column-insert", "migration-column-update"],
)
async def test_readiness_rejects_missing_chunk_reads_and_column_level_migration_writes(
    operation, permission, failed_check, pg_api_app, pg_factory, pg_admin_factory
):
    destination = "FROM" if operation == "REVOKE" else "TO"
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text(f"{operation} {permission} {destination} ea_test_app"))
    try:
        async with pg_factory() as session:
            if operation == "REVOKE":
                assert (
                    await session.scalar(
                        text(
                            "SELECT has_table_privilege(current_user, 'knowledge_chunks', 'SELECT')"
                        )
                    )
                    is False
                )
            else:
                privilege = permission.split("(", 1)[0]
                assert (
                    await session.scalar(
                        text(
                            "SELECT has_column_privilege(current_user, "
                            "'ea_checkpoint.checkpoint_migrations', 'v', :privilege)"
                        ),
                        {"privilege": privilege},
                    )
                    is True
                )
                assert (
                    await session.scalar(
                        text(
                            "SELECT has_table_privilege(current_user, "
                            "'ea_checkpoint.checkpoint_migrations', :privilege)"
                        ),
                        {"privilege": privilege},
                    )
                    is False
                )
        async with (
            pg_api_app.router.lifespan_context(pg_api_app),
            AsyncClient(
                transport=ASGITransport(app=pg_api_app), base_url="http://testserver"
            ) as http,
        ):
            response = await http.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["checks"][failed_check] == "not_ready"
            assert "password" not in response.text and "postgresql" not in response.text
    finally:
        inverse = "GRANT" if operation == "REVOKE" else "REVOKE"
        destination = "TO" if inverse == "GRANT" else "FROM"
        async with pg_admin_factory() as session, session.begin():
            await session.execute(text(f"{inverse} {permission} {destination} ea_test_app"))


async def test_api_refuses_migration_owner_even_with_the_correct_schema(pg_urls, tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            data_dir=tmp_path,
            database_url=pg_urls[1],
            jwt_secret=uuid4().hex + uuid4().hex,
        ),
        configure_logs=False,
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        assert app.state.database.verified is False
        assert (await http.get("/health/live")).status_code == 200
        response = await http.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] == "not_ready"
        response = await http.post(
            "/api/v1/auth/login",
            json={
                "username": "demo_customer",
                "password": "DemoOnly!2026",
            },
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"


async def test_audit_column_update_grant_is_also_rejected(pg_api_app, pg_admin_factory):
    async with pg_admin_factory() as session, session.begin():
        await session.execute(text("GRANT UPDATE(action) ON audit_logs TO ea_test_app"))
    try:
        async with pg_api_app.router.lifespan_context(pg_api_app):
            assert pg_api_app.state.database.verified is False
    finally:
        async with pg_admin_factory() as session, session.begin():
            await session.execute(text("REVOKE UPDATE(action) ON audit_logs FROM ea_test_app"))
