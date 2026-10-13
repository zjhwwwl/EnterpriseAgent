"""Official MCP requests with real SQL identity, Services and scoped RAG.

The in-memory transport replaces only OS pipes. The SDK performs initialization,
discovery and tools/call; neither authentication nor business data is mocked.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import SecretStr
from sqlalchemy import func, select, update

from enterprise_agent.mcp.server import TOOL_NAMES, create_server
from enterprise_agent.models import (
    ApprovalRequest,
    RefundRequest,
    SupportTicket,
    User,
    UserRole,
)
from enterprise_agent.security import Principal, create_access_token
from enterprise_agent.services.auth import AuthService
from enterprise_agent.tools import BusinessTools

pytestmark = [pytest.mark.integration, pytest.mark.postgres]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def mcp_users(pg_factory, pg_seed):
    async with pg_factory() as session:
        users = list(await session.scalars(select(User)))
    return {user.username: user for user in users}


@pytest.fixture
def mcp_settings(rag_settings, pg_urls):
    return rag_settings.model_copy(
        update={
            "database_url": SecretStr(pg_urls[0]),
            "jwt_secret": SecretStr(uuid4().hex + uuid4().hex),
            "llm_enabled": False,
            "agent_enabled": False,
            "mcp_request_timeout_seconds": 45,
        }
    )


def owned_server(factory, settings, tools, user):
    token = create_access_token(user.id, user.token_version, settings)
    configured = settings.model_copy(update={"mcp_access_token": SecretStr(token)})
    return create_server(
        tools=tools,
        auth=AuthService(factory, configured),
        settings=configured,
    )


def data_of(result):
    assert result.isError is False
    assert result.structuredContent is not None
    return result.structuredContent["data"]


def assert_error(result, code, status):
    assert result.isError is True
    assert result.structuredContent is not None
    error = result.structuredContent["error"]
    assert error["code"] == code and error["status_code"] == status
    assert isinstance(error["message"], str) and error["message"]


async def business_write_counts(factory):
    async with factory() as session:
        return {
            model.__tablename__: await session.scalar(select(func.count(model.id)))
            for model in (RefundRequest, SupportTicket, ApprovalRequest)
        }


async def test_sdk_discovery_reads_sql_facts_and_denies_identity_arguments(
    pg_factory, mcp_settings, mcp_users
):
    user = mcp_users["demo_customer"]
    actor = Principal(user.id, user.role)
    tools = BusinessTools(pg_factory, mcp_settings, clock=lambda: NOW)
    server = owned_server(pg_factory, mcp_settings, tools, user)
    before = await business_write_counts(pg_factory)
    async with create_connected_server_and_client_session(server) as client:
        manifest = await client.list_tools()
        assert {tool.name for tool in manifest.tools} == set(TOOL_NAMES)
        for tool in manifest.tools:
            assert tool.inputSchema["additionalProperties"] is False
            assert "order_no" in tool.inputSchema["required"]
            assert not {"user_id", "role", "actor", "confirmed"} & set(
                tool.inputSchema["properties"]
            )
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
        for exposed, internal in (
            ("query_order", "get_order"),
            ("query_logistics", "get_logistics"),
        ):
            result = await client.call_tool(exposed, {"order_no": "10086"})
            assert data_of(result) == await tools.execute(actor, internal, {"order_no": "10086"})
            denied = await client.call_tool(exposed, {"order_no": "10087"})
            assert_error(denied, "order_not_found", 404)
        for arguments in (
            {"order_no": 10086},
            {"order_no": "１００８６"},
            {"order_no": "10086", "role": "admin"},
            {"order_no": "10087", "user_id": mcp_users["demo_other"].id},
        ):
            assert_error(
                await client.call_tool("query_order", arguments), "invalid_tool_arguments", 422
            )
        for name in ("create_refund_request", "query_nonexistent"):
            assert_error(await client.call_tool(name, {"order_no": "10086"}), "unknown_tool", 422)
    assert await business_write_counts(pg_factory) == before


async def test_same_connected_session_reloads_current_role_active_flag_and_token_version(
    pg_factory, pg_admin_factory, mcp_settings, mcp_users
):
    user = mcp_users["demo_customer"]
    tools = BusinessTools(pg_factory, mcp_settings, clock=lambda: NOW)
    server = owned_server(pg_factory, mcp_settings, tools, user)

    async def change(**values):
        async with pg_admin_factory() as session, session.begin():
            await session.execute(update(User).where(User.id == user.id).values(**values))

    try:
        async with create_connected_server_and_client_session(server) as client:
            assert_error(
                await client.call_tool("query_order", {"order_no": "10087"}),
                "order_not_found",
                404,
            )
            await change(role=UserRole.SUPPORT)
            assert (
                data_of(await client.call_tool("query_order", {"order_no": "10087"}))["order_no"]
                == "10087"
            )
            await change(role=UserRole.ANALYST)
            assert_error(
                await client.call_tool("query_order", {"order_no": "10086"}), "forbidden", 403
            )
            await change(role=user.role, is_active=False)
            assert_error(
                await client.call_tool("query_order", {"order_no": "10086"}),
                "unauthenticated",
                401,
            )
            await change(is_active=True, token_version=user.token_version + 1)
            assert_error(
                await client.call_tool("query_order", {"order_no": "10086"}),
                "unauthenticated",
                401,
            )
    finally:
        await change(role=user.role, is_active=user.is_active, token_version=user.token_version)


@pytest.mark.rag
async def test_policy_tool_uses_real_index_and_business_permissions(
    pg_factory, mcp_settings, mcp_users, rag_runtime
):
    tools = BusinessTools(pg_factory, mcp_settings, rag_runtime, clock=lambda: NOW)
    customer = mcp_users["demo_customer"]
    admin = mcp_users["demo_admin"]
    actor = Principal(customer.id, customer.role)
    eligibility = await tools.refunds.get_eligibility(actor, "10086")
    document_id = eligibility.policy.document_id
    await tools.knowledge.reindex(Principal(admin.id, admin.role), document_id)
    before = await business_write_counts(pg_factory)

    customer_server = owned_server(pg_factory, mcp_settings, tools, customer)
    async with create_connected_server_and_client_session(customer_server) as client:
        public = data_of(await client.call_tool("query_refund_policy", {"order_no": "10086"}))
        assert public["eligibility"] == eligibility.model_dump(mode="json")
        assert public["knowledge"]["status"] == "evidence_found"
        assert public["knowledge"]["citations"]
        for citation in public["knowledge"]["citations"]:
            assert UUID(citation["document_id"]) == document_id
            current = await tools.knowledge.citation(actor, UUID(citation["chunk_id"]))
            assert current.text == citation["text"]
        assert public["knowledge"]["llm_calls"] == 0
    support_server = owned_server(pg_factory, mcp_settings, tools, mcp_users["demo_support"])
    async with create_connected_server_and_client_session(support_server) as client:
        staff = data_of(await client.call_tool("query_refund_policy", {"order_no": "10086"}))
        assert staff["eligibility"] == public["eligibility"]
        assert staff["knowledge"]["citations"]
        assert staff["knowledge"]["llm_calls"] == 0
    for username, code, status in (
        ("demo_analyst", "forbidden", 403),
        ("demo_other", "order_not_found", 404),
    ):
        server = owned_server(pg_factory, mcp_settings, tools, mcp_users[username])
        async with create_connected_server_and_client_session(server) as client:
            assert_error(
                await client.call_tool("query_refund_policy", {"order_no": "10086"}), code, status
            )
    assert await business_write_counts(pg_factory) == before
