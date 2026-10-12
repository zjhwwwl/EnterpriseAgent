"""Policy boundaries are checked without an LLM or a database."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from enterprise_agent.models import OrderStatus
from enterprise_agent.schemas.business import (
    RefundConfirmInput,
    RefundPrepareInput,
    RefundReviewInput,
)
from enterprise_agent.services.refunds import canonical_digest, evaluate_refund_rule

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def decision(**overrides):
    facts = {
        "status": OrderStatus.DELIVERED,
        "paid_at": NOW - timedelta(days=9),
        "delivered_at": NOW - timedelta(days=2),
        "goods_paid_cents": 12900,
        "shipping_paid_cents": 800,
        "categories": ["clothing"],
        "reason": "change_mind",
        "now": NOW,
        "refund_window_days": 7,
        "refund_shipping": False,
        "allow_before_delivery": True,
        "excluded_categories": ["food"],
    }
    return evaluate_refund_rule(**(facts | overrides))


def test_window_is_based_on_delivery_and_includes_the_exact_boundary():
    boundary = NOW - timedelta(days=7)
    assert decision(delivered_at=boundary).eligible
    assert not decision(delivered_at=boundary - timedelta(microseconds=1)).eligible
    assert (
        decision(delivered_at=boundary - timedelta(microseconds=1)).code == "refund_window_expired"
    )


def test_policy_amount_preserves_exact_integer_cents():
    assert decision().amount_cents == 12900
    assert decision(refund_shipping=True).amount_cents == 13700


@pytest.mark.parametrize("status", [OrderStatus.UNPAID, OrderStatus.CANCELLED])
def test_unpaid_and_cancelled_orders_are_not_refundable(status):
    assert decision(status=status).code == "order_not_refundable"


def test_missing_payment_is_not_eligible():
    assert decision(paid_at=None).code == "order_not_refundable"


def test_food_requires_quality_issue_even_in_a_mixed_order():
    assert decision(categories=["clothing", "food"]).code == "category_restricted"
    assert decision(categories=["clothing", "food"], reason="quality_issue").eligible


def test_before_delivery_uses_the_selected_policy():
    before = {"status": OrderStatus.SHIPPED, "delivered_at": None, "reason": "not_received"}
    assert decision(**before).eligible
    assert decision(**before, allow_before_delivery=False).code == "delivery_required"


def test_delivery_claim_and_bad_order_times_require_resolution():
    assert decision(reason="not_received").code == "already_delivered"
    assert decision(delivered_at=None).code == "invalid_order_facts"
    assert decision(paid_at=NOW + timedelta(seconds=1)).code == "invalid_order_facts"
    assert decision(delivered_at=NOW + timedelta(seconds=1)).code == "invalid_order_facts"


def test_delivery_cannot_precede_payment_even_inside_the_refund_window():
    result = decision(paid_at=NOW - timedelta(hours=1), delivered_at=NOW - timedelta(days=2))
    assert not result.eligible and result.code == "invalid_order_facts"


def test_utc_and_local_timezone_represent_the_same_boundary():
    local = timezone(timedelta(hours=8))
    boundary = (NOW - timedelta(days=7)).astimezone(local)
    assert decision(delivered_at=boundary).eligible
    with pytest.raises(ValueError, match="timezone-aware"):
        decision(now=NOW.replace(tzinfo=None))


def test_unknown_reason_and_zero_refundable_money_are_rejected():
    assert decision(reason="arbitrary").code == "invalid_reason"
    assert decision(goods_paid_cents=0).code == "no_refundable_amount"


def test_approval_digest_is_canonical_and_detects_changed_amount():
    assert canonical_digest({"reason": "质量问题", "amount": 100}) == canonical_digest(
        {"amount": 100, "reason": "质量问题"}
    )
    assert canonical_digest({"amount": 100}) != canonical_digest({"amount": 101})


@pytest.mark.parametrize(
    "payload",
    [
        {"reason": "unsupported"},
        {"reason": "change_mind", "amount_cents": 1},
        {"reason": "quality_issue", "notes": "x" * 501},
    ],
)
def test_prepare_input_rejects_invalid_or_injected_fields(payload):
    with pytest.raises(ValidationError):
        RefundPrepareInput.model_validate(payload)


def test_confirmation_requires_a_real_boolean_and_does_not_accept_amount():
    with pytest.raises(ValidationError):
        RefundConfirmInput.model_validate(
            {"approval_id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "confirmed": "true"}
        )
    with pytest.raises(ValidationError):
        RefundConfirmInput.model_validate(
            {"approval_id": "ffffffff-ffff-ffff-ffff-ffffffffffff", "confirmed": True, "amount": 1}
        )


def test_review_input_requires_a_decision_and_a_note():
    with pytest.raises(ValidationError):
        RefundReviewInput(decision="completed_simulated", note="unsupported")
    with pytest.raises(ValidationError):
        RefundReviewInput(decision="approve", note="")
