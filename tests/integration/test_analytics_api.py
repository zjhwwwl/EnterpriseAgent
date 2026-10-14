"""Real authentication, reader queries, schema and error handling over the API."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings
from enterprise_agent.models import Order

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
SQL = "SELECT SUM(gross_revenue_cents) AS sales_cents FROM ea_analytics.daily_orders"


@pytest_asyncio.fixture
async def analytics_http(pg_urls, analytics_urls, analytics_runtime, pg_seed):
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=pg_urls[0],
        jwt_secret=uuid4().hex * 2,
        analytics_enabled=True,
        analytics_database_url=analytics_urls[0],
        agent_enabled=False,
    )
    app = create_app(settings, configure_logs=False, analytics_runtime=analytics_runtime)
    app.state.clock = lambda: datetime(2026, 10, 12, tzinfo=UTC)
    async with app.router.lifespan_context(app):
        assert app.state.database.verified and app.state.analytics.verified
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as http:
            yield http


async def headers(http, username="demo_analyst"):
    response = await http.post(
        "/api/v1/auth/login", json={"username": username, "password": "DemoOnly!2026"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def test_catalog_and_executed_report_have_real_values_and_chart(analytics_http, pg_factory):
    auth = await headers(analytics_http)
    metadata = await analytics_http.get("/api/v1/analytics/catalog", headers=auth)
    assert metadata.status_code == 200
    assert set(metadata.json()["views"]) == {"daily_orders", "daily_product_sales"}
    assert metadata.json()["today"] == "2026-10-12"
    response = await analytics_http.post(
        "/api/v1/analytics/query", headers=auth, json={"sql": SQL, "metric_ids": ["paid_sales"]}
    )
    assert response.status_code == 200
    report = response.json()
    async with pg_factory() as session:
        expected = await session.scalar(
            select(func.sum(Order.paid_total_cents)).where(
                Order.paid_at.is_not(None), Order.status.in_(["paid", "shipped", "delivered"])
            )
        )
    assert report["query"]["rows"] == [[str(expected)]]
    assert report["chart"]["series"][0]["data"] == [str(expected)]
    assert str(expected) in report["answer"] and "不扣退款" in report["answer"]
    assert report["query"]["truncated"] is False


@pytest.mark.parametrize("username", ["demo_customer", "demo_support"])
async def test_non_operator_roles_are_denied_catalog_and_query(analytics_http, username):
    auth = await headers(analytics_http, username)
    for path in ("catalog", "query"):
        response = (
            await analytics_http.get("/api/v1/analytics/catalog", headers=auth)
            if path == "catalog"
            else await analytics_http.post(
                "/api/v1/analytics/query",
                headers=auth,
                json={"sql": SQL, "metric_ids": ["paid_sales"]},
            )
        )
        assert response.status_code == 403


async def test_admin_can_query_and_unauthenticated_user_cannot(analytics_http):
    assert (await analytics_http.get("/api/v1/analytics/catalog")).status_code == 401
    auth = await headers(analytics_http, "demo_admin")
    assert (
        await analytics_http.post(
            "/api/v1/analytics/query", headers=auth, json={"sql": SQL, "metric_ids": ["paid_sales"]}
        )
    ).status_code == 200


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM public.orders",
        "SELECT password_hash FROM public.users",
        "SELECT pg_sleep(1) AS x FROM ea_analytics.daily_orders",
    ],
)
async def test_unsafe_sql_is_a_safe_api_error(analytics_http, sql):
    response = await analytics_http.post(
        "/api/v1/analytics/query",
        headers=await headers(analytics_http),
        json={"sql": sql, "metric_ids": ["paid_sales"]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "analytics_sql_rejected"
    assert sql not in response.text


@pytest.mark.parametrize("extra", [{"actor_id": 1}, {"role": "admin"}, {"confirmed": True}])
async def test_parameters_cannot_choose_identity(analytics_http, extra):
    response = await analytics_http.post(
        "/api/v1/analytics/query",
        headers=await headers(analytics_http),
        json={"sql": SQL, "metric_ids": ["paid_sales"], **extra},
    )
    assert response.status_code == 422


async def test_readiness_includes_reader_and_openapi_describes_report(analytics_http):
    response = await analytics_http.get("/health/ready")
    assert response.status_code == 200 and response.json()["checks"]["analytics"] == "ok"
    specification = (await analytics_http.get("/openapi.json")).json()
    assert "AnalyticsReport" in specification["components"]["schemas"]


async def test_body_limit_precedes_large_parameter_validation(analytics_http):
    response = await analytics_http.post(
        "/api/v1/analytics/query",
        headers=await headers(analytics_http),
        json={"sql": "x" * 17000, "metric_ids": ["paid_sales"]},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "analytics_request_too_large"
