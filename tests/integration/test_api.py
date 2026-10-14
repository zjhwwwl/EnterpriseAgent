"""Exercise the real ASGI application without external services or a model call."""

import asyncio
import re

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from enterprise_agent.application import create_app
from enterprise_agent.config import Settings
from enterprise_agent.request_context import get_request_id


class ValidationBody(BaseModel):
    quantity: int = Field(gt=0)
    password: str


class ContextBody(BaseModel):
    request_id: str


def assert_error_response(response, code):
    payload = response.json()
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["message"], str)
    assert "details" in payload["error"]
    assert payload["request_id"] == response.headers["X-Request-ID"]
    return payload


@pytest.mark.asyncio
async def test_service_information_and_liveness(client):
    info = await client.get("/")
    assert info.status_code == 200
    assert info.json() == {
        "name": "EnterpriseAgent",
        "version": "0.7.0",
        "phase": 7,
        "docs_url": "/docs",
        "health_url": "/health/ready",
    }

    live = await client.get("/health/live")
    assert live.status_code == 200
    assert live.json() == {
        "status": "ok",
        "service": "EnterpriseAgent",
        "version": "0.7.0",
        "phase": 7,
        "checks": {"application": "ok"},
    }


@pytest.mark.asyncio
async def test_readiness_follows_lifespan_and_does_not_claim_database_health(app):
    assert app.state.ready is False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        before = await http.get("/health/ready")
        assert before.status_code == 503
        assert before.json() == {
            "status": "not_ready",
            "service": "EnterpriseAgent",
            "version": "0.7.0",
            "phase": 7,
            "checks": {"application": "not_ready", "database": "not_ready"},
        }
        async with app.router.lifespan_context(app):
            assert app.state.ready is True
            ready = await http.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"] == {"application": "ok", "database": "not_ready"}
        after = await http.get("/health/ready")
        assert after.status_code == 503
    assert app.state.ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/not-a-route", 404, "NOT_FOUND"),
        ("POST", "/health/live", 405, "METHOD_NOT_ALLOWED"),
    ],
)
async def test_http_errors_share_a_request_correlated_envelope(client, method, path, status, code):
    response = await client.request(method, path, headers={"X-Request-ID": "error-123"})
    assert response.status_code == status
    assert_error_response(response, code)
    assert response.headers["X-Request-ID"] == "error-123"
    if status == 405:
        assert "GET" in response.headers["Allow"]


@pytest.mark.asyncio
async def test_validation_response_never_echoes_sensitive_input(app, client):
    @app.post("/_test/validate")
    async def validate_payload(payload: ValidationBody):
        return {"quantity": payload.quantity}

    response = await client.post(
        "/_test/validate",
        json={"quantity": "fake-secret-invalid-quantity", "password": "fake-password"},
    )

    assert response.status_code == 422
    payload = assert_error_response(response, "REQUEST_VALIDATION_ERROR")
    assert payload["error"]["details"]
    for detail in payload["error"]["details"]:
        assert set(detail) == {"loc", "type"}
    assert "fake-secret-invalid-quantity" not in response.text
    assert "fake-password" not in response.text


@pytest.mark.asyncio
async def test_unhandled_exception_response_is_generic_and_keeps_request_id(app, client):
    @app.get("/_test/failure")
    async def fail():
        raise RuntimeError("fake-private-database-password")

    response = await client.get("/_test/failure", headers={"X-Request-ID": "failure-123"})

    assert response.status_code == 500
    assert_error_response(response, "INTERNAL_SERVER_ERROR")
    assert response.headers["X-Request-ID"] == "failure-123"
    assert "fake-private-database-password" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("origin", ["http://localhost:5173", "https://evil.example.com"])
async def test_safe_500_response_keeps_the_explicit_cors_policy(app, client, origin):
    @app.get("/_test/cors-failure")
    async def fail():
        raise RuntimeError("fake-private-password")

    response = await client.get("/_test/cors-failure", headers={"Origin": origin})
    assert response.status_code == 500
    assert_error_response(response, "INTERNAL_SERVER_ERROR")
    if origin == "http://localhost:5173":
        assert response.headers["Access-Control-Allow-Origin"] == origin
        assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]
    else:
        assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", ["a", "valid_ID-1.2", "a" * 64])
async def test_valid_request_id_is_preserved(client, request_id):
    response = await client.get("/health/live", headers={"X-Request-ID": request_id})
    assert response.headers["X-Request-ID"] == request_id


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", ["", ".bad", "a b", "a@b", "a" * 65, b"\xe9"])
async def test_invalid_request_id_is_replaced_with_server_generated_id(client, request_id):
    response = await client.get("/health/live", headers={"X-Request-ID": request_id})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_query_and_body_cannot_override_request_context(app, client):
    @app.post("/_test/context")
    async def show_context(payload: ContextBody):
        return {"request_id": get_request_id()}

    response = await client.post(
        "/_test/context?request_id=query-impostor",
        headers={"X-Request-ID": "trusted-header"},
        json={"request_id": "body-impostor"},
    )

    assert response.json()["request_id"] == "trusted-header"
    assert response.headers["X-Request-ID"] == "trusted-header"


@pytest.mark.asyncio
async def test_concurrent_requests_keep_separate_contexts(app, client):
    @app.get("/_test/concurrent-context")
    async def show_context():
        before = get_request_id()
        await asyncio.sleep(0.01)
        return {"before": before, "after": get_request_id()}

    outside_context = get_request_id()
    request_ids = [f"concurrent-{index}" for index in range(20)]
    responses = await asyncio.gather(
        *(
            client.get("/_test/concurrent-context", headers={"X-Request-ID": request_id})
            for request_id in request_ids
        )
    )

    for request_id, response in zip(request_ids, responses, strict=True):
        assert response.json() == {"before": request_id, "after": request_id}
        assert response.headers["X-Request-ID"] == request_id
    assert get_request_id() == outside_context


@pytest.mark.asyncio
async def test_cors_only_accepts_configured_origin(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        data_dir=tmp_path,
        cors_origins=["http://localhost:5173"],
    )
    app = create_app(settings, configure_logs=False)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        allowed = await http.options(
            "/health/live",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = await http.options(
            "/health/live",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        direct = await http.get("/health/live", headers={"Origin": "https://evil.example.com"})

    assert allowed.status_code == 200
    assert allowed.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert denied.status_code == 400
    assert "Access-Control-Allow-Origin" not in denied.headers
    assert "Access-Control-Allow-Origin" not in direct.headers


@pytest.mark.asyncio
async def test_documentation_can_be_disabled_per_application(tmp_path):
    disabled = create_app(
        Settings(_env_file=None, app_env="test", data_dir=tmp_path, docs_enabled=False),
        configure_logs=False,
    )
    enabled = create_app(
        Settings(_env_file=None, app_env="test", data_dir=tmp_path, docs_enabled=True),
        configure_logs=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=disabled), base_url="http://testserver"
    ) as http:
        for path in ("/docs", "/openapi.json"):
            response = await http.get(path)
            assert response.status_code == 404
            assert_error_response(response, "NOT_FOUND")
        assert (await http.get("/")).json()["docs_url"] is None

    async with AsyncClient(
        transport=ASGITransport(app=enabled), base_url="http://testserver"
    ) as http:
        assert (await http.get("/docs")).status_code == 200
        assert (await http.get("/openapi.json")).status_code == 200

    assert disabled.state.ready is False
    assert enabled.state.ready is False
