"""Explicit confirmation creates an application, never a real payment refund."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from enterprise_agent.api.dependencies import CurrentUser, Refunds
from enterprise_agent.schemas.business import (
    ConfirmResponse,
    RefundConfirmInput,
    RefundPage,
    RefundResponse,
    RefundReviewInput,
)

router = APIRouter(prefix="/api/v1/refunds", tags=["refunds"])


@router.get("", response_model=RefundPage)
async def list_refunds(
    actor: CurrentUser,
    service: Refunds,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RefundPage:
    return await service.list_refunds(actor, offset=offset, limit=limit)


@router.post("", response_model=ConfirmResponse)
async def confirm_refund(
    payload: RefundConfirmInput,
    actor: CurrentUser,
    service: Refunds,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> ConfirmResponse:
    return await service.confirm_refund(
        actor,
        payload.approval_id,
        idempotency_key=idempotency_key,
        confirmed=payload.confirmed,
    )


@router.get("/{refund_id}", response_model=RefundResponse)
async def refund_detail(refund_id: UUID, actor: CurrentUser, service: Refunds) -> RefundResponse:
    return await service.get_refund(actor, refund_id)


@router.post("/{refund_id}/review", response_model=RefundResponse)
async def review_refund(
    refund_id: UUID,
    payload: RefundReviewInput,
    actor: CurrentUser,
    service: Refunds,
) -> RefundResponse:
    return await service.review_refund(
        actor,
        refund_id,
        decision=payload.decision,
        note=payload.note,
    )


@router.post("/{refund_id}/complete-simulated", response_model=RefundResponse)
async def complete_simulated(
    refund_id: UUID, actor: CurrentUser, service: Refunds
) -> RefundResponse:
    return await service.complete_simulated_refund(actor, refund_id)
