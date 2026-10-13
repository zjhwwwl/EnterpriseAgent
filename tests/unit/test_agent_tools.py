"""Tool contracts with controlled Services; no claims about model or database quality."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from enterprise_agent.config import Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import ApprovalStatus, OrderStatus, RefundStatus, UserRole
from enterprise_agent.schemas.business import (
    ApprovalResponse,
    ConfirmResponse,
    EligibilityResponse,
    OrderResponse,
    PolicyResponse,
    RefundPage,
    RefundResponse,
)
from enterprise_agent.schemas.knowledge import SearchInput, SearchResponse
from enterprise_agent.security import Principal
from enterprise_agent.tools import BusinessTools, openai_tools

NOW = datetime(2026, 10, 13, tzinfo=UTC)
ACTOR = Principal(user_id=42, role=UserRole.CUSTOMER)


@pytest.fixture
def toolset():
    return BusinessTools(None, Settings(_env_file=None), clock=lambda: NOW)


def policy():
    return PolicyResponse(
        id=8,
        policy_code="STANDARD_RETURN",
        version=2,
        document_id=uuid4(),
        document_title="退款规则",
        document_version=1,
        valid_from=NOW - timedelta(days=30),
        valid_until=None,
        refund_window_days=7,
        refund_shipping=False,
        allow_before_delivery=True,
        excluded_categories=["food"],
    )


def order():
    return OrderResponse(
        id=6,
        order_no="10086",
        user_id=ACTOR.user_id,
        status=OrderStatus.DELIVERED,
        goods_paid_cents=10900,
        shipping_paid_cents=800,
        paid_total_cents=11700,
        created_at=NOW - timedelta(days=8),
        paid_at=NOW - timedelta(days=8),
        delivered_at=NOW - timedelta(days=2),
    )


def refund(order_no="10086"):
    return RefundResponse(
        id=uuid4(),
        order_no=order_no,
        applicant_id=ACTOR.user_id,
        amount_cents=10900,
        reason="quality_issue",
        notes="包装有破损",
        status=RefundStatus.PENDING_REVIEW,
        policy_version_id=8,
        policy_snapshot={},
        created_at=NOW,
        reviewer_id=None,
        reviewed_at=None,
        review_note=None,
        completed_at=None,
    )


def approval():
    return ApprovalResponse(
        approval_id=uuid4(),
        order_no="10086",
        status=ApprovalStatus.PENDING,
        reason="quality_issue",
        notes="包装有破损",
        amount_cents=10900,
        expires_at=NOW + timedelta(minutes=30),
        policy=policy(),
    )


def test_model_tool_schemas_have_no_trusted_confirmation_or_identity_fields():
    tools = openai_tools()
    assert {tool["function"]["name"] for tool in tools} == {
        "get_order",
        "get_order_items",
        "get_logistics",
        "get_refund_policy",
        "create_refund_request",
        "get_refund_status",
        "search_knowledge",
    }
    forbidden = {
        "user_id",
        "actor_id",
        "role",
        "approval_id",
        "confirmed",
        "idempotency_key",
        "amount_cents",
        "task_id",
    }
    for tool in tools:
        assert tool["type"] == "function"
        schema = tool["function"]["parameters"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert not forbidden.intersection(schema["properties"])
    assert BusinessTools.openai_tools() == tools


@pytest.mark.parametrize("name", ["confirm_refund", "delete_order", "unknown", 12, None])
async def test_unknown_tools_cannot_reach_business_services(toolset, monkeypatch, name):
    read = AsyncMock()
    write = AsyncMock()
    monkeypatch.setattr(toolset.orders, "get_order", read)
    monkeypatch.setattr(toolset.refunds, "confirm_refund", write)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, name, {"private": "sensitive-input"})
    assert caught.value.code == "unknown_tool" and caught.value.status_code == 422
    assert "sensitive-input" not in str(caught.value)
    read.assert_not_awaited()
    write.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        [],
        '{"order_no":"10086"}',
        {},
        {"order_no": 10086},
        {"order_no": True},
        {"order_no": "1008"},
        {"order_no": "1" * 31},
        {"order_no": "１００８６"},
        {"order_no": "10086\n"},
        {"order_no": "10086' OR 1=1"},
        {"order_no": "10086", "user_id": 99},
    ],
)
async def test_bad_order_arguments_fail_before_database_access(toolset, monkeypatch, arguments):
    read = AsyncMock()
    monkeypatch.setattr(toolset.orders, "get_order", read)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_order", arguments)
    assert caught.value.code == "invalid_tool_arguments" and caught.value.status_code == 422
    assert "10086'" not in caught.value.message
    read.assert_not_awaited()


@pytest.mark.parametrize(
    "extra",
    ["user_id", "role", "approval_id", "confirmed", "idempotency_key", "amount_cents", "task_id"],
)
async def test_model_cannot_inject_confirmation_or_identity(toolset, monkeypatch, extra):
    prepare = AsyncMock()
    confirm = AsyncMock()
    monkeypatch.setattr(toolset.refunds, "prepare_refund", prepare)
    monkeypatch.setattr(toolset.refunds, "confirm_refund", confirm)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(
            ACTOR,
            "create_refund_request",
            {"order_no": "10086", "reason": "quality_issue", extra: "private-value"},
        )
    assert caught.value.code == "invalid_tool_arguments"
    assert "private-value" not in str(caught.value)
    prepare.assert_not_awaited()
    confirm.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [
        {"order_no": "10086"},
        {"order_no": "10086", "reason": "refund_everything"},
        {"order_no": "10086", "reason": True},
        {"order_no": "10086", "reason": "quality_issue", "notes": "x" * 501},
        {"order_no": "10086", "reason": "quality_issue", "notes": 1},
        {"order_no": "10086", "reason": "quality_issue", "comment": "use the public field"},
    ],
)
async def test_refund_reason_and_notes_are_strictly_validated(toolset, monkeypatch, arguments):
    prepare = AsyncMock()
    monkeypatch.setattr(toolset.refunds, "prepare_refund", prepare)
    with pytest.raises(BusinessError) as caught:
        await toolset.prepare_refund(ACTOR, arguments)
    assert caught.value.code == "invalid_tool_arguments"
    prepare.assert_not_awaited()


async def test_order_results_use_authenticated_actor_and_exact_cents(toolset, monkeypatch):
    read = AsyncMock(return_value=order())
    monkeypatch.setattr(toolset.orders, "get_order", read)
    result = await toolset.execute(ACTOR, "get_order", {"order_no": "10086"})
    read.assert_awaited_once_with(ACTOR, "10086")
    assert result["paid_total_cents"] == 11700
    assert result["status"] == "delivered"
    assert result["delivered_at"] == (NOW - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    assert toolset.orders.clock() == toolset.refunds.clock() == NOW


async def test_order_access_denial_is_preserved_without_replacing_actor(toolset, monkeypatch):
    error = BusinessError("order_not_found", "订单不存在", 404)
    read = AsyncMock(side_effect=error)
    monkeypatch.setattr(toolset.orders, "get_order", read)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_order", {"order_no": "10087"})
    assert caught.value is error
    read.assert_awaited_once_with(ACTOR, "10087")


async def test_refund_tool_prepares_only_and_never_executes_confirmation(toolset, monkeypatch):
    prepared = approval()
    prepare = AsyncMock(return_value=prepared)
    confirm = AsyncMock()
    monkeypatch.setattr(toolset.refunds, "prepare_refund", prepare)
    monkeypatch.setattr(toolset.refunds, "confirm_refund", confirm)
    result = await toolset.execute(
        ACTOR,
        "create_refund_request",
        {"order_no": "10086", "reason": "quality_issue", "notes": "包装有破损"},
    )
    prepare.assert_awaited_once_with(
        ACTOR, "10086", reason="quality_issue", notes="包装有破损", task_id=None
    )
    confirm.assert_not_awaited()
    assert result["requires_confirmation"] is True
    assert result["status"] == "pending"
    assert UUID(result["approval_id"]) == prepared.approval_id


@pytest.mark.parametrize("confirmed", [True, False])
async def test_trusted_human_path_reuses_transactional_confirmation(
    toolset, monkeypatch, confirmed
):
    prepared = approval()
    expected = ConfirmResponse(
        approval_id=prepared.approval_id, status=ApprovalStatus.CONSUMED, refund=refund()
    )
    confirm = AsyncMock(return_value=expected)
    monkeypatch.setattr(toolset.refunds, "confirm_refund", confirm)
    result = await toolset.execute_confirmed_refund(
        ACTOR, prepared.approval_id, "request-key-123", confirmed=confirmed
    )
    assert result is expected
    confirm.assert_awaited_once_with(
        ACTOR, prepared.approval_id, idempotency_key="request-key-123", confirmed=confirmed
    )


async def test_prepare_binds_trusted_task_identifier_outside_model_arguments(toolset, monkeypatch):
    prepared = approval()
    prepare = AsyncMock(return_value=prepared)
    monkeypatch.setattr(toolset.refunds, "prepare_refund", prepare)
    task_id = uuid4()
    result = await toolset.prepare_refund(
        ACTOR,
        {"order_no": "10086", "reason": "quality_issue", "notes": "包装有破损"},
        task_id=task_id,
    )
    assert result is prepared
    prepare.assert_awaited_once_with(
        ACTOR, "10086", reason="quality_issue", notes="包装有破损", task_id=task_id
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"refund_id": "broken"},
        {"refund_id": "x" * 36},
        {"refund_id": 12345},
        {"refund_id": str(uuid4()), "order_no": "10086"},
        {"refund_id": None, "order_no": None},
        {"order_no": "10086", "user_id": 99},
    ],
)
async def test_refund_status_requires_exactly_one_safe_reference(toolset, arguments):
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_refund_status", arguments)
    assert caught.value.code == "invalid_tool_arguments"


async def test_refund_identifier_is_canonicalized_and_service_checks_owner(toolset, monkeypatch):
    expected = refund()
    read = AsyncMock(return_value=expected)
    monkeypatch.setattr(toolset.refunds, "get_refund", read)
    result = await toolset.execute(
        ACTOR, "get_refund_status", {"refund_id": str(expected.id).upper()}
    )
    read.assert_awaited_once_with(ACTOR, expected.id)
    assert result["id"] == str(expected.id)


async def test_order_status_lookup_checks_visibility_before_listing_refunds(toolset, monkeypatch):
    denial = BusinessError("order_not_found", "订单不存在", 404)
    monkeypatch.setattr(toolset.orders, "get_order", AsyncMock(side_effect=denial))
    listing = AsyncMock()
    monkeypatch.setattr(toolset.refunds, "list_refunds", listing)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_refund_status", {"order_no": "10087"})
    assert caught.value is denial
    listing.assert_not_awaited()


async def test_latest_visible_refund_is_found_across_bounded_pages(toolset, monkeypatch):
    monkeypatch.setattr(toolset.orders, "get_order", AsyncMock(return_value=order()))
    latest = refund()
    old = refund()
    listing = AsyncMock(
        side_effect=[
            RefundPage(items=[refund("20000") for _ in range(100)], total=102, offset=0, limit=100),
            RefundPage(items=[latest, old], total=102, offset=100, limit=100),
        ]
    )
    monkeypatch.setattr(toolset.refunds, "list_refunds", listing)
    result = await toolset.execute(ACTOR, "get_refund_status", {"order_no": "10086"})
    assert result["id"] == str(latest.id)
    assert listing.await_args_list[1].kwargs == {"offset": 100, "limit": 100}


async def test_large_refund_scan_fails_explicitly_instead_of_missing_data(toolset, monkeypatch):
    monkeypatch.setattr(toolset.orders, "get_order", AsyncMock(return_value=order()))
    listing = AsyncMock(return_value=RefundPage(items=[], total=3000, offset=0, limit=100))
    monkeypatch.setattr(toolset.refunds, "list_refunds", listing)
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_refund_status", {"order_no": "10086"})
    assert caught.value.code == "refund_lookup_capacity_exceeded"
    assert listing.await_count == 20


async def test_policy_retrieval_is_scoped_to_database_selected_policy_document(
    toolset, monkeypatch
):
    selected = policy()
    eligibility = EligibilityResponse(
        order_no="10086",
        eligible=True,
        code="eligible",
        message="待审核",
        amount_cents=10900,
        policy=selected,
    )
    monkeypatch.setattr(toolset.refunds, "get_eligibility", AsyncMock(return_value=eligibility))
    toolset.rag_runtime = object()
    evidence = SearchResponse(
        status="insufficient_evidence",
        message="证据不足",
        mode="hybrid_rerank",
        citations=[],
        elapsed_ms=2.0,
        stage_ms={},
        model_fingerprint="controlled-service-test",
    )
    search = AsyncMock(return_value=evidence)
    monkeypatch.setattr(toolset.knowledge, "search", search)
    result = await toolset.execute(
        ACTOR, "get_refund_policy", {"order_no": "10086", "reason": "quality_issue"}
    )
    assert search.await_args.args[0] is ACTOR
    assert isinstance(search.await_args.args[1], SearchInput)
    assert search.await_args.kwargs == {"document_ids": [selected.document_id]}
    assert result["eligibility"]["amount_cents"] == 10900
    assert result["knowledge"]["status"] == "insufficient_evidence"


async def test_rag_failure_is_explicit_and_not_silently_replaced_with_policy_text(
    toolset, monkeypatch
):
    eligibility = EligibilityResponse(
        order_no="10086",
        eligible=True,
        code="eligible",
        message="待审核",
        amount_cents=10900,
        policy=policy(),
    )
    monkeypatch.setattr(toolset.refunds, "get_eligibility", AsyncMock(return_value=eligibility))
    toolset.rag_runtime = object()
    monkeypatch.setattr(
        toolset.knowledge,
        "search",
        AsyncMock(side_effect=BusinessError("rag_models_unavailable", "本地模型未准备", 503)),
    )
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_refund_policy", {"order_no": "10086"})
    assert caught.value.code == "rag_models_unavailable"
    assert caught.value.status_code == 503


async def test_policy_requires_enabled_knowledge_runtime(toolset, monkeypatch):
    eligibility = EligibilityResponse(
        order_no="10086",
        eligible=True,
        code="eligible",
        message="待审核",
        amount_cents=10900,
        policy=policy(),
    )
    monkeypatch.setattr(toolset.refunds, "get_eligibility", AsyncMock(return_value=eligibility))
    with pytest.raises(BusinessError) as caught:
        await toolset.execute(ACTOR, "get_refund_policy", {"order_no": "10086"})
    assert caught.value.code == "rag_not_enabled"
    assert caught.value.status_code == 503


async def test_unpaid_order_does_not_retrieve_unrelated_policy(toolset, monkeypatch):
    eligibility = EligibilityResponse(
        order_no="10086",
        eligible=False,
        code="order_not_refundable",
        message="未付款",
        amount_cents=0,
        policy=None,
    )
    monkeypatch.setattr(toolset.refunds, "get_eligibility", AsyncMock(return_value=eligibility))
    search = AsyncMock()
    monkeypatch.setattr(toolset.knowledge, "search", search)
    result = await toolset.execute(ACTOR, "get_refund_policy", {"order_no": "10086"})
    assert result["eligibility"]["eligible"] is False
    assert result["knowledge"] is None
    search.assert_not_awaited()
