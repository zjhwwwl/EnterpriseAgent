"""Real business-tool execution on PostgreSQL, with real local RAG when marked.

Controlled provider responses elsewhere test workflow edges. These tests call
the actual business Services, ordinary database account and local model/store.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditLog,
    RefundRequest,
    RefundStatus,
    SupportTicket,
    User,
)
from enterprise_agent.security import Principal
from enterprise_agent.tools import BusinessTools

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def tool_actors(pg_factory, pg_seed):
    async with pg_factory() as session:
        users = await session.scalars(select(User))
        return {user.username: Principal(user.id, user.role) for user in users}


@pytest.fixture
def db_tools(pg_factory, rag_settings):
    return BusinessTools(pg_factory, rag_settings, clock=lambda: NOW)


async def test_tool_reads_real_facts_and_preserves_permission_boundaries(db_tools, tool_actors):
    actor = tool_actors["demo_customer"]
    result = await db_tools.execute(actor, "get_order", {"order_no": "10086"})
    items = await db_tools.execute(actor, "get_order_items", {"order_no": "10086"})
    logistics = await db_tools.execute(actor, "get_logistics", {"order_no": "10086"})
    assert result["paid_total_cents"] == 13700
    assert sum(item["paid_amount_cents"] for item in items) == result["goods_paid_cents"] == 12900
    assert logistics["tracking_no"] == "SIM-10086" and logistics["events"]
    for name in ("get_order", "get_order_items", "get_logistics", "get_refund_status"):
        with pytest.raises(BusinessError) as caught:
            await db_tools.execute(actor, name, {"order_no": "10087"})
        assert caught.value.code == "order_not_found" and caught.value.status_code == 404
    with pytest.raises(BusinessError) as caught:
        await db_tools.execute(tool_actors["demo_analyst"], "get_order", {"order_no": "10086"})
    assert caught.value.code == "forbidden"
    latest = await db_tools.execute(actor, "get_refund_status", {"order_no": "10090"})
    direct = await db_tools.execute(actor, "get_refund_status", {"refund_id": latest["id"]})
    assert direct == latest and direct["status"] == "completed_simulated"
    with pytest.raises(BusinessError) as caught:
        await db_tools.execute(
            tool_actors["demo_other"], "get_refund_status", {"refund_id": latest["id"]}
        )
    assert caught.value.code == "refund_not_found"


@pytest.mark.parametrize("confirmed", [True, False])
async def test_real_confirmation_is_audited_idempotent_and_order_status_is_unchanged(
    db_tools, tool_actors, pg_factory, confirmed
):
    actor = tool_actors["demo_customer"]
    before = await db_tools.execute(actor, "get_order", {"order_no": "10086"})
    prepared = await db_tools.execute(
        actor,
        "create_refund_request",
        {"order_no": "10086", "reason": "change_mind", "notes": "模拟客户申请"},
    )
    approval_id = UUID(prepared["approval_id"])
    assert prepared["requires_confirmation"] is True and prepared["amount_cents"] == 12900
    async with pg_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval.status == ApprovalStatus.PENDING
        assert (
            await session.scalar(
                select(func.count(RefundRequest.id)).where(RefundRequest.order_id == before["id"])
            )
            == 0
        )
    result = await db_tools.execute_confirmed_refund(
        actor, approval_id, "tool-confirmation-test-0001", confirmed=confirmed
    )
    replay = await db_tools.execute_confirmed_refund(
        actor, approval_id, "tool-confirmation-test-0001", confirmed=confirmed
    )
    assert replay.replayed is True
    after = await db_tools.execute(actor, "get_order", {"order_no": "10086"})
    assert after["status"] == before["status"]
    async with pg_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        stored = await session.scalar(
            select(RefundRequest).where(RefundRequest.order_id == before["id"])
        )
        assert (
            await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.action == "refund.prepare", AuditLog.resource_id == str(approval_id)
                )
            )
            == 1
        )
        if confirmed:
            assert approval.status == ApprovalStatus.CONSUMED
            assert stored.id == result.refund.id == replay.refund.id
            assert stored.status == RefundStatus.PENDING_REVIEW
            assert (
                await session.scalar(
                    select(func.count(SupportTicket.id)).where(
                        SupportTicket.refund_request_id == stored.id
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "refund.create", AuditLog.resource_id == str(stored.id)
                    )
                )
                == 1
            )
        else:
            assert approval.status == ApprovalStatus.REJECTED
            assert stored is None and result.refund is replay.refund is None
            assert (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(
                        AuditLog.action == "refund.confirm_rejected",
                        AuditLog.resource_id == str(approval_id),
                    )
                )
                == 1
            )


@pytest.mark.rag
async def test_policy_tool_combines_database_decision_with_real_scoped_citations(
    pg_factory, rag_settings, rag_runtime, pg_rag_service, tool_actors
):
    tools = BusinessTools(pg_factory, rag_settings, rag_runtime, clock=lambda: NOW)
    actor = tool_actors["demo_customer"]
    decision = await tools.refunds.get_eligibility(actor, "10086")
    document_id = decision.policy.document_id
    await pg_rag_service.reindex(tool_actors["demo_admin"], document_id)
    result = await tools.execute(actor, "get_refund_policy", {"order_no": "10086"})
    assert result["eligibility"]["eligible"] is True
    assert result["eligibility"]["amount_cents"] == 12900
    assert result["knowledge"]["status"] == "evidence_found"
    assert result["knowledge"]["citations"]
    for citation in result["knowledge"]["citations"]:
        assert UUID(citation["document_id"]) == document_id
        current = await pg_rag_service.citation(actor, UUID(citation["chunk_id"]))
        assert current.text == citation["text"]
    assert result["knowledge"]["llm_calls"] == 0
