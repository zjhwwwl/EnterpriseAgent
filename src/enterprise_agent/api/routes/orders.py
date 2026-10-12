"""Order HTTP adapter; ownership and policy checks remain in Services."""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from enterprise_agent.api.dependencies import CurrentUser, Orders, Refunds
from enterprise_agent.schemas.business import (
    ApprovalResponse,
    EligibilityResponse,
    LogisticsResponse,
    OrderItemResponse,
    OrderPage,
    OrderResponse,
    RefundPrepareInput,
    RefundReason,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])
OrderNumber = Annotated[str, Path(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]


@router.get("", response_model=OrderPage)
async def list_orders(
    actor: CurrentUser,
    service: Orders,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderPage:
    return await service.list_orders(actor, offset=offset, limit=limit)


@router.get("/{order_no}", response_model=OrderResponse)
async def order_detail(order_no: OrderNumber, actor: CurrentUser, service: Orders) -> OrderResponse:
    return await service.get_order(actor, order_no)


@router.get("/{order_no}/items", response_model=list[OrderItemResponse])
async def order_items(order_no: OrderNumber, actor: CurrentUser, service: Orders):
    return await service.get_order_items(actor, order_no)


@router.get("/{order_no}/logistics", response_model=LogisticsResponse)
async def logistics(
    order_no: OrderNumber, actor: CurrentUser, service: Orders
) -> LogisticsResponse:
    return await service.get_logistics(actor, order_no)


@router.get("/{order_no}/refund-eligibility", response_model=EligibilityResponse)
async def refund_eligibility(
    order_no: OrderNumber,
    actor: CurrentUser,
    service: Refunds,
    reason: RefundReason = "change_mind",
) -> EligibilityResponse:
    return await service.get_eligibility(actor, order_no, reason=reason)


@router.post("/{order_no}/refund-preview", response_model=ApprovalResponse, status_code=201)
async def prepare_refund(
    order_no: OrderNumber,
    payload: RefundPrepareInput,
    actor: CurrentUser,
    service: Refunds,
) -> ApprovalResponse:
    return await service.prepare_refund(actor, order_no, reason=payload.reason, notes=payload.notes)
