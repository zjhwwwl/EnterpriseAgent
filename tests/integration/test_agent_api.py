"""Actual authorization, empty-body schemas and checkpoint APIs without a chat key."""

import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
BASE = "/api/v1/agent"


async def login(client, username):
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "DemoOnly!2026"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


async def test_session_history_is_owned_and_missing_model_does_not_fake_a_task(pg_api_client):
    owner = await login(pg_api_client, "demo_customer")
    other = await login(pg_api_client, "demo_other")
    assert (await pg_api_client.get(BASE + "/sessions")).status_code == 401
    response = await pg_api_client.post(BASE + "/sessions", headers=owner)
    assert response.status_code == 201, response.text
    session = response.json()
    assert "thread_id" not in session and "user_id" not in session
    session_id = session["id"]
    assert (
        await pg_api_client.get(BASE + f"/sessions/{session_id}", headers=other)
    ).status_code == 404
    refused = await pg_api_client.post(
        BASE + f"/sessions/{session_id}/messages",
        headers=owner,
        json={"message": "查询订单10086的物流"},
    )
    assert (
        refused.status_code == 503
        and refused.json()["error"]["code"] == "agent_model_not_configured"
    )
    assert (await pg_api_client.get(BASE + "/tasks", headers=owner)).json()["total"] == 0
    assert (await pg_api_client.get(BASE + "/sessions", headers=other)).json()["total"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"thread_id": "other-thread"},
        {"role_at_creation": "admin"},
        {"user_id": 999},
        {"configurable": {"thread_id": "x"}},
    ],
)
async def test_clients_cannot_control_session_identity(pg_api_client, extra):
    owner = await login(pg_api_client, "demo_customer")
    response = await pg_api_client.post(BASE + "/sessions", headers=owner, json=extra)
    assert response.status_code == 422
    assert (await pg_api_client.get(BASE + "/sessions", headers=owner)).json()["total"] == 0


async def test_message_parameters_and_utf8_are_checked_before_task_creation(pg_api_client):
    owner = await login(pg_api_client, "demo_customer")
    session_id = (await pg_api_client.post(BASE + "/sessions", headers=owner)).json()["id"]
    for payload in (
        {"message": "查询订单", "user_id": 999},
        {"message": " "},
        {"message": "x" * 2001},
        {"message": "订单\ud800"},
    ):
        response = await pg_api_client.post(
            BASE + f"/sessions/{session_id}/messages",
            headers={**owner, "Content-Type": "application/json"},
            content=json.dumps(payload, ensure_ascii=True).encode(),
        )
        assert response.status_code == 422


async def test_oversized_agent_body_is_rejected_with_cors_before_business(pg_api_client):
    response = await pg_api_client.post(
        BASE + "/sessions",
        headers={"Origin": "http://localhost:5173", "Content-Type": "application/json"},
        content=b" " * (16 * 1024 + 1),
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "agent_request_too_large"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
