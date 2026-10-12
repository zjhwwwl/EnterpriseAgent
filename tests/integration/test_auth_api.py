"""Authenticated HTTP business flows against the dedicated real PostgreSQL database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from enterprise_agent.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    Order,
    OrderStatus,
    RefundRequest,
    SupportTicket,
    User,
    UserRole,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.usefixtures("pg_seed"),
]
DEMO_PASSWORD = "DemoOnly!2026"
NOW = datetime(2026, 10, 12, tzinfo=UTC)


async def login(client: AsyncClient, username="demo_customer"):
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def credentials(client: AsyncClient, username="demo_customer"):
    token = await login(client, username)
    return {"Authorization": f"Bearer {token['access_token']}"}


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"]["code"]
    assert body["error"]["message"]
    assert "details" in body["error"]
    assert body["request_id"] == response.headers["X-Request-ID"]
    if code is not None:
        assert body["error"]["code"] == code


async def preview(client: AsyncClient, headers):
    response = await client.post(
        "/api/v1/orders/10086/refund-preview",
        headers=headers,
        json={"reason": "change_mind", "notes": "模拟退货申请"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_refund(client: AsyncClient, headers, *, key="api-refund-create-01"):
    pending = await preview(client, headers)
    response = await client.post(
        "/api/v1/refunds",
        headers=headers | {"Idempotency-Key": key},
        json={"approval_id": pending["approval_id"], "confirmed": True},
    )
    assert response.status_code == 200, response.text
    return pending, response.json()


async def test_login_and_current_user_omit_passwords_and_private_token_metadata(pg_api_client):
    token = await login(pg_api_client, "DEMO_CUSTOMER")
    assert token["token_type"] == "bearer"
    assert token["expires_in"] == 1800
    assert set(token["user"]) == {"id", "username", "role"}
    assert token["user"]["username"] == "demo_customer"
    assert token["user"]["role"] == "customer"
    response = await pg_api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert response.status_code == 200
    assert response.json() == token["user"]
    assert DEMO_PASSWORD not in response.text
    assert "password_hash" not in response.text


async def test_invalid_credentials_share_one_error_without_echoing_input(pg_api_client):
    for username, password in [
        ("demo_customer", "wrong-password-do-not-echo"),
        ("missing_demo_user", "wrong-password-do-not-echo"),
    ]:
        response = await pg_api_client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert_error(response, 401, "invalid_credentials")
        assert password not in response.text
        assert username not in response.text
        assert "access_token" not in response.text


async def test_protected_endpoints_require_a_valid_bearer_token(pg_api_client):
    for path in ["/api/v1/auth/me", "/api/v1/orders", "/api/v1/orders/10086"]:
        for headers in [
            {},
            {"Authorization": "Bearer invalid"},
            {"Authorization": "Basic ignored"},
        ]:
            response = await pg_api_client.get(path, headers=headers)
            assert_error(response, 401, "unauthenticated")


async def test_login_does_not_accept_identity_or_role_injection(pg_api_client):
    for extra in [{"role": "admin"}, {"user_id": 1}]:
        response = await pg_api_client.post(
            "/api/v1/auth/login",
            json={"username": "demo_customer", "password": DEMO_PASSWORD} | extra,
        )
        assert_error(response, 422)
        assert DEMO_PASSWORD not in response.text


async def test_customer_order_list_detail_items_and_logistics_are_consistent(pg_api_client):
    token = await login(pg_api_client)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    page = await pg_api_client.get("/api/v1/orders?offset=0&limit=100", headers=headers)
    assert page.status_code == 200
    listing = page.json()
    assert listing["offset"] == 0 and listing["limit"] == 100
    assert 0 < len(listing["items"]) <= 100
    assert 0 < listing["total"] < 500
    assert all(item["user_id"] == token["user"]["id"] for item in listing["items"])

    detail = await pg_api_client.get("/api/v1/orders/10086", headers=headers)
    assert detail.status_code == 200
    order = detail.json()
    assert order["user_id"] == token["user"]["id"]
    assert order["status"] == "delivered"
    assert order["goods_paid_cents"] == 12900
    assert order["shipping_paid_cents"] == 800
    assert order["paid_total_cents"] == 13700

    items = await pg_api_client.get("/api/v1/orders/10086/items", headers=headers)
    assert items.status_code == 200
    assert sum(item["paid_amount_cents"] for item in items.json()) == order["goods_paid_cents"]
    assert all(item["quantity"] > 0 for item in items.json())

    logistics = await pg_api_client.get("/api/v1/orders/10086/logistics", headers=headers)
    assert logistics.status_code == 200
    assert logistics.json()["tracking_no"] == "SIM-10086"
    assert logistics.json()["status"] == "delivered"
    assert logistics.json()["events"][-1]["status"] == "delivered"

    eligibility = await pg_api_client.get(
        "/api/v1/orders/10086/refund-eligibility?reason=change_mind", headers=headers
    )
    assert eligibility.status_code == 200
    assessment = eligibility.json()
    assert assessment["eligible"]
    assert assessment["amount_cents"] == 12900
    assert assessment["policy"]["document_id"]
    assert assessment["policy"]["document_title"]
    assert assessment["policy"]["version"] >= 1


async def test_customer_cannot_access_another_customers_order_through_any_read_route(pg_api_client):
    headers = await credentials(pg_api_client)
    for suffix in ["", "/items", "/logistics", "/refund-eligibility"]:
        response = await pg_api_client.get("/api/v1/orders/10087" + suffix, headers=headers)
        assert_error(response, 404, "order_not_found")
        assert "SIM-10087" not in response.text
    response = await pg_api_client.post(
        "/api/v1/orders/10087/refund-preview",
        headers=headers,
        json={"reason": "change_mind"},
    )
    assert_error(response, 404, "order_not_found")


async def test_support_can_read_customer_orders_but_analyst_cannot(pg_api_client):
    support = await credentials(pg_api_client, "demo_support")
    analyst = await credentials(pg_api_client, "demo_analyst")
    for order_no in ["10086", "10087"]:
        response = await pg_api_client.get(f"/api/v1/orders/{order_no}", headers=support)
        assert response.status_code == 200
        response = await pg_api_client.get(f"/api/v1/orders/{order_no}", headers=analyst)
        assert_error(response, 403, "forbidden")
    for path in ["/api/v1/orders", "/api/v1/refunds"]:
        response = await pg_api_client.get(path, headers=analyst)
        assert_error(response, 403, "forbidden")


async def test_invalid_pagination_and_order_identifiers_are_rejected(pg_api_client):
    headers = await credentials(pg_api_client)
    for path in [
        "/api/v1/orders?offset=-1",
        "/api/v1/orders?limit=0",
        "/api/v1/orders?limit=101",
        "/api/v1/orders/not%20an%20order",
        "/api/v1/orders/10086/refund-eligibility?reason=unsupported",
    ]:
        response = await pg_api_client.get(path, headers=headers)
        assert_error(response, 422)


async def test_preview_requires_confirmation_and_does_not_create_a_refund(
    pg_api_client, pg_admin_factory
):
    headers = await credentials(pg_api_client)
    pending = await preview(pg_api_client, headers)
    assert pending["status"] == "pending"
    assert pending["requires_confirmation"] is True
    assert pending["amount_cents"] == 12900
    assert datetime.fromisoformat(pending["expires_at"]) == NOW + timedelta(minutes=30)
    async with pg_admin_factory() as session:
        approval = await session.get(ApprovalRequest, UUID(pending["approval_id"]))
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        count = await session.scalar(
            select(func.count())
            .select_from(RefundRequest)
            .where(RefundRequest.order_id == order.id)
        )
        assert approval.status == ApprovalStatus.PENDING
        assert count == 0


async def test_confirmation_validation_cannot_override_amount_or_identity(
    pg_api_client, pg_admin_factory
):
    headers = await credentials(pg_api_client)
    pending = await preview(pg_api_client, headers)
    base = {"approval_id": pending["approval_id"], "confirmed": True}
    bad_requests = [
        (base, {}),
        ({"approval_id": pending["approval_id"]}, {"Idempotency-Key": "valid-key-01"}),
        (base | {"confirmed": "true"}, {"Idempotency-Key": "valid-key-02"}),
        (base | {"confirmed": 1}, {"Idempotency-Key": "valid-key-03"}),
        (base | {"amount_cents": 1}, {"Idempotency-Key": "valid-key-04"}),
        (base | {"user_id": 1}, {"Idempotency-Key": "valid-key-05"}),
        (base | {"role": "admin"}, {"Idempotency-Key": "valid-key-06"}),
        (base, {"Idempotency-Key": "short"}),
        (base, {"Idempotency-Key": "x" * 129}),
    ]
    for body, extra_headers in bad_requests:
        response = await pg_api_client.post(
            "/api/v1/refunds", headers=headers | extra_headers, json=body
        )
        assert_error(response, 422)
    async with pg_admin_factory() as session:
        approval = await session.get(ApprovalRequest, UUID(pending["approval_id"]))
        assert approval.status == ApprovalStatus.PENDING
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == order.id)
            )
            == 0
        )


async def test_confirmation_rejection_is_durable_and_idempotent(pg_api_client, pg_admin_factory):
    headers = await credentials(pg_api_client)
    pending = await preview(pg_api_client, headers)
    body = {"approval_id": pending["approval_id"], "confirmed": False}
    confirmed_headers = headers | {"Idempotency-Key": "api-refund-rejection-01"}
    first = await pg_api_client.post("/api/v1/refunds", headers=confirmed_headers, json=body)
    assert first.status_code == 200
    assert first.json()["status"] == "rejected"
    assert first.json()["refund"] is None
    replay = await pg_api_client.post("/api/v1/refunds", headers=confirmed_headers, json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    changed = await pg_api_client.post(
        "/api/v1/refunds", headers=confirmed_headers, json=body | {"confirmed": True}
    )
    assert_error(changed, 409, "idempotency_conflict")
    async with pg_admin_factory() as session:
        approval = await session.get(ApprovalRequest, UUID(pending["approval_id"]))
        assert approval.status == ApprovalStatus.REJECTED
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == order.id)
            )
            == 0
        )


async def test_other_customer_cannot_confirm_or_read_a_refund(pg_api_client):
    owner = await credentials(pg_api_client)
    other = await credentials(pg_api_client, "demo_other")
    pending = await preview(pg_api_client, owner)
    denied = await pg_api_client.post(
        "/api/v1/refunds",
        headers=other | {"Idempotency-Key": "foreign-approval-attempt"},
        json={"approval_id": pending["approval_id"], "confirmed": True},
    )
    assert_error(denied, 404, "approval_not_found")

    submitted = await pg_api_client.post(
        "/api/v1/refunds",
        headers=owner | {"Idempotency-Key": "owner-approval-confirm"},
        json={"approval_id": pending["approval_id"], "confirmed": True},
    )
    assert submitted.status_code == 200
    refund_id = submitted.json()["refund"]["id"]
    denied = await pg_api_client.get(f"/api/v1/refunds/{refund_id}", headers=other)
    assert_error(denied, 404, "refund_not_found")
    listing = await pg_api_client.get("/api/v1/refunds", headers=other)
    assert listing.status_code == 200
    assert refund_id not in {item["id"] for item in listing.json()["items"]}


async def test_refund_confirmation_review_and_simulated_completion_have_audited_boundaries(
    pg_api_client, pg_admin_factory
):
    owner = await credentials(pg_api_client)
    support = await credentials(pg_api_client, "demo_support")
    pending, confirmed = await create_refund(
        pg_api_client, owner | {"X-Request-ID": "http-refund-create"}
    )
    assert confirmed["status"] == "consumed"
    assert confirmed["replayed"] is False
    refund = confirmed["refund"]
    refund_id = refund["id"]
    assert refund["status"] == "pending_review"
    assert refund["amount_cents"] == pending["amount_cents"]

    replay = await pg_api_client.post(
        "/api/v1/refunds",
        headers=owner | {"Idempotency-Key": "api-refund-create-01"},
        json={"approval_id": pending["approval_id"], "confirmed": True},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["refund"]["id"] == refund_id

    denied = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/review",
        headers=owner,
        json={"decision": "approve", "note": "不能由客户审核"},
    )
    assert_error(denied, 403, "forbidden")
    denied = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/complete-simulated", headers=owner
    )
    assert_error(denied, 403, "forbidden")
    premature = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/complete-simulated", headers=support
    )
    assert_error(premature, 409, "invalid_refund_transition")

    approved = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/review",
        headers=support | {"X-Request-ID": "http-refund-review"},
        json={"decision": "approve", "note": "模拟客服核实后批准"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    completed = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/complete-simulated",
        headers=support | {"X-Request-ID": "http-refund-complete"},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed_simulated"
    assert completed.json()["completed_at"]
    again = await pg_api_client.post(
        f"/api/v1/refunds/{refund_id}/complete-simulated", headers=support
    )
    assert_error(again, 409, "invalid_refund_transition")

    async with pg_admin_factory() as session:
        ticket = await session.scalar(
            select(SupportTicket).where(SupportTicket.refund_request_id == UUID(refund_id))
        )
        assert ticket.status == "closed"
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        assert order.status == OrderStatus.DELIVERED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == order.id)
            )
            == 1
        )
        logs = list(
            await session.scalars(
                select(AuditLog).where(AuditLog.resource_id == refund_id).order_by(AuditLog.id)
            )
        )
        assert [entry.action for entry in logs] == [
            "refund.create",
            "refund.approve",
            "refund.complete_simulated",
        ]
        assert [entry.request_id for entry in logs] == [
            "http-refund-create",
            "http-refund-review",
            "http-refund-complete",
        ]


@pytest.mark.parametrize("change", ["disabled", "token-version"])
async def test_database_user_changes_invalidate_existing_tokens(
    change, pg_api_client, pg_admin_factory
):
    headers = await credentials(pg_api_client)
    values = (
        {"is_active": False} if change == "disabled" else {"token_version": User.token_version + 1}
    )
    async with pg_admin_factory() as session, session.begin():
        await session.execute(update(User).where(User.username == "demo_customer").values(**values))
    response = await pg_api_client.get("/api/v1/auth/me", headers=headers)
    assert_error(response, 401, "unauthenticated")


async def test_role_claims_cannot_override_database_permissions(
    pg_api_client, pg_api_app, pg_admin_factory
):
    token = await login(pg_api_client)
    secret = pg_api_app.state.settings.jwt_secret.get_secret_value()
    payload = jwt.decode(
        token["access_token"],
        secret,
        algorithms=["HS256"],
        issuer="enterprise-agent",
        audience="enterprise-agent-api",
    )
    payload["role"] = "admin"
    forged_authority = jwt.encode(payload, secret, algorithm="HS256")
    headers = {"Authorization": f"Bearer {forged_authority}"}
    response = await pg_api_client.get("/api/v1/orders/10087", headers=headers)
    assert_error(response, 404, "order_not_found")

    async with pg_admin_factory() as session, session.begin():
        await session.execute(
            update(User).where(User.username == "demo_customer").values(role=UserRole.ANALYST)
        )
    me = await pg_api_client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "analyst"
    response = await pg_api_client.get("/api/v1/orders/10086", headers=headers)
    assert_error(response, 403, "forbidden")


async def test_expired_confirmation_is_persisted_and_cannot_create_a_refund(
    pg_api_client, pg_api_app, pg_admin_factory
):
    headers = await credentials(pg_api_client)
    pending = await preview(pg_api_client, headers)
    pg_api_app.state.clock = lambda: NOW + timedelta(minutes=31)
    response = await pg_api_client.post(
        "/api/v1/refunds",
        headers=headers | {"Idempotency-Key": "expired-approval-confirm"},
        json={"approval_id": pending["approval_id"], "confirmed": True},
    )
    assert_error(response, 409, "approval_expired")
    async with pg_admin_factory() as session:
        approval = await session.get(ApprovalRequest, UUID(pending["approval_id"]))
        assert approval.status == ApprovalStatus.EXPIRED
        order = await session.scalar(select(Order).where(Order.order_no == "10086"))
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.order_id == order.id)
            )
            == 0
        )


async def test_real_redis_login_limit_blocks_the_fourth_attempt(pg_rate_limited_client):
    for _ in range(3):
        response = await pg_rate_limited_client.post(
            "/api/v1/auth/login",
            json={"username": "demo_customer", "password": "wrong-password-never-echo"},
        )
        assert_error(response, 401, "invalid_credentials")
    response = await pg_rate_limited_client.post(
        "/api/v1/auth/login",
        json={"username": "demo_customer", "password": DEMO_PASSWORD},
    )
    assert_error(response, 429, "login_rate_limited")
    assert DEMO_PASSWORD not in response.text
    assert "access_token" not in response.text
