"""Typed ecommerce inputs and results; monetary values are integer cents."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent.models import ApprovalStatus, OrderStatus, RefundStatus

RefundReason = Literal["change_mind", "quality_issue", "not_received"]


class BusinessInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RefundPrepareInput(BusinessInput):
    reason: RefundReason
    notes: str = Field(default="", max_length=500)


class RefundConfirmInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: UUID
    confirmed: bool = Field(strict=True)


class RefundReviewInput(BusinessInput):
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=1, max_length=500)


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    user_id: int
    status: OrderStatus
    goods_paid_cents: int
    shipping_paid_cents: int
    paid_total_cents: int
    created_at: datetime
    paid_at: datetime | None
    delivered_at: datetime | None


class OrderPage(BaseModel):
    items: list[OrderResponse]
    total: int
    offset: int
    limit: int


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    product_name: str
    category: str
    quantity: int
    unit_price_cents: int
    paid_amount_cents: int


class LogisticsResponse(BaseModel):
    order_no: str
    tracking_no: str
    status: str
    events: list[dict[str, Any]]


class PolicyResponse(BaseModel):
    id: int
    policy_code: str
    version: int
    document_id: UUID
    document_title: str
    document_version: int
    valid_from: datetime
    valid_until: datetime | None
    refund_window_days: int
    refund_shipping: bool
    allow_before_delivery: bool
    excluded_categories: list[str]


class EligibilityResponse(BaseModel):
    order_no: str
    eligible: bool
    code: str
    message: str
    amount_cents: int
    policy: PolicyResponse | None


class ApprovalResponse(BaseModel):
    approval_id: UUID
    order_no: str
    status: ApprovalStatus
    reason: RefundReason
    notes: str
    amount_cents: int
    expires_at: datetime
    policy: PolicyResponse
    requires_confirmation: bool = True


class RefundResponse(BaseModel):
    id: UUID
    order_no: str
    applicant_id: int
    amount_cents: int
    reason: str
    notes: str
    status: RefundStatus
    policy_version_id: int
    policy_snapshot: dict[str, Any]
    created_at: datetime
    reviewer_id: int | None
    reviewed_at: datetime | None
    review_note: str | None
    completed_at: datetime | None


class ConfirmResponse(BaseModel):
    approval_id: UUID
    status: ApprovalStatus
    refund: RefundResponse | None
    replayed: bool = False


class RefundPage(BaseModel):
    items: list[RefundResponse]
    total: int
    offset: int
    limit: int
