"""Real PostgreSQL boundaries for knowledge publication and policy sources."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError

from enterprise_agent.models import KnowledgeChunk, KnowledgeDocument, RefundPolicyVersion

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.usefixtures("pg_seed"),
]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


async def policy_document_id(factory) -> UUID:
    async with factory() as session:
        document_id = await session.scalar(
            select(RefundPolicyVersion.document_id).order_by(RefundPolicyVersion.id).limit(1)
        )
    assert document_id is not None
    return document_id


async def create_document(factory, *, visibility="public", deleted=False) -> UUID:
    document_id = uuid4()
    async with factory() as session, session.begin():
        session.add(
            KnowledgeDocument(
                id=document_id,
                title="数据库边界测试文档",
                file_type="txt",
                file_path=f"var/test-knowledge/{document_id}.txt",
                checksum="a" * 64,
                visibility=visibility,
                status="deleted" if deleted else "uploaded",
                deleted_at=NOW if deleted else None,
            )
        )
    return document_id


def source_policy(document_id: UUID) -> RefundPolicyVersion:
    # A distinct policy code leaves the executable STANDARD_RETURN rules alone.
    return RefundPolicyVersion(
        policy_code=f"KNOWLEDGE_TEST_{uuid4().hex}",
        version=1,
        document_id=document_id,
        valid_from=NOW,
        refund_window_days=7,
        refund_shipping=False,
        allow_before_delivery=True,
        excluded_categories=[],
    )


async def expect_rejection(factory, statement, sqlstate, *, constraint=None):
    async with factory() as session:
        try:
            with pytest.raises(DBAPIError) as caught:
                await session.execute(statement)
        finally:
            # An unexpectedly accepted statement must not persist test damage.
            await session.rollback()
    assert caught.value.orig.sqlstate == sqlstate
    if constraint is not None:
        assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "changes",
    [
        {"id": uuid4()},
        {"title": "被替换的政策来源"},
        {"file_type": "txt"},
        {"file_path": "var/replaced-policy.txt"},
        {"checksum": "b" * 64},
        {"version": 2},
        {"visibility": "internal"},
        {"deleted_at": NOW},
    ],
    ids=["id", "title", "type", "path", "checksum", "version", "visibility", "soft-delete"],
)
async def test_policy_source_fields_cannot_be_changed(changes, pg_factory):
    document_id = await policy_document_id(pg_factory)
    await expect_rejection(
        pg_factory,
        update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(**changes),
        "23514",
        constraint="ck_knowledge_policy_source_immutable",
    )


async def test_policy_source_cannot_be_physically_deleted_by_owner(pg_admin_factory):
    document_id = await policy_document_id(pg_admin_factory)
    await expect_rejection(
        pg_admin_factory,
        delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id),
        "23514",
        constraint="ck_knowledge_policy_source_immutable",
    )


async def test_policy_source_allows_indexing_metadata_updates(pg_factory):
    document_id = await policy_document_id(pg_factory)
    job_id = uuid4()
    partition_id = uuid4()
    async with pg_factory() as session, session.begin():
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(
                status="indexing",
                index_attempt_generation=1,
                indexing_job_id=job_id,
                index_started_at=NOW,
                index_error_code=None,
            )
        )
        # This is a permission/invariant fixture, not evidence of vector execution.
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(
                status="indexed",
                index_generation=1,
                active_index_id=partition_id,
                indexing_job_id=None,
                index_started_at=None,
                index_fingerprint="database-test-only",
                index_error_code=None,
                indexed_at=NOW,
            )
        )
    async with pg_factory() as session:
        document = await session.get(KnowledgeDocument, document_id)
        assert document.index_generation == document.index_attempt_generation == 1
        assert document.active_index_id == partition_id
        assert document.indexing_job_id is None
        assert document.index_started_at is None
        assert document.indexed_at == NOW
        assert document.index_fingerprint == "database-test-only"
        assert document.deleted_at is None
        assert document.visibility == "public"


@pytest.mark.parametrize(
    "changes",
    [
        {"index_generation": 1, "index_attempt_generation": 0},
        {"index_attempt_generation": -1},
    ],
    ids=["attempt-before-active", "negative-attempt"],
)
async def test_attempt_generation_cannot_precede_active(changes, pg_factory):
    document_id = await policy_document_id(pg_factory)
    await expect_rejection(
        pg_factory,
        update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(**changes),
        "23514",
        constraint="ck_knowledge_documents_index_attempt_generation_ordered",
    )


@pytest.mark.parametrize("source_state", ["deleted", "internal", "missing"])
async def test_new_policy_rejects_unavailable_sources(source_state, pg_factory, pg_admin_factory):
    document_id = (
        uuid4()
        if source_state == "missing"
        else await create_document(
            pg_factory,
            visibility="internal" if source_state == "internal" else "public",
            deleted=source_state == "deleted",
        )
    )
    async with pg_admin_factory() as session:
        try:
            with pytest.raises(DBAPIError) as caught:
                session.add(source_policy(document_id))
                await session.flush()
        finally:
            await session.rollback()
    assert caught.value.orig.sqlstate == "23514"
    assert caught.value.orig.diag.constraint_name == "ck_refund_policy_source_available"


async def test_new_policy_accepts_public_undeleted_source(pg_factory, pg_admin_factory):
    document_id = await create_document(pg_factory)
    policy = source_policy(document_id)
    async with pg_admin_factory() as session, session.begin():
        session.add(policy)
        await session.flush()
        policy_id = policy.id
    async with pg_factory() as session:
        assert (
            await session.scalar(
                select(RefundPolicyVersion.document_id).where(RefundPolicyVersion.id == policy_id)
            )
            == document_id
        )
    await expect_rejection(
        pg_factory,
        update(KnowledgeDocument).where(KnowledgeDocument.id == document_id).values(deleted_at=NOW),
        "23514",
        constraint="ck_knowledge_policy_source_immutable",
    )


async def test_policy_insert_holds_source_lock_until_transaction_ends(pg_factory, pg_admin_factory):
    document_id = await create_document(pg_factory)
    async with pg_admin_factory() as publisher:
        try:
            publisher.add(source_policy(document_id))
            await publisher.flush()
            # NOWAIT proves the source lock without scheduling sleeps or timeouts.
            await expect_rejection(
                pg_factory,
                select(KnowledgeDocument.id)
                .where(KnowledgeDocument.id == document_id)
                .with_for_update(nowait=True),
                "55P03",
            )
        finally:
            await publisher.rollback()
    async with pg_factory() as session, session.begin():
        assert (
            await session.scalar(
                select(KnowledgeDocument.id)
                .where(KnowledgeDocument.id == document_id)
                .with_for_update(nowait=True)
            )
            == document_id
        )


async def test_policy_publication_waits_for_concurrent_source_update(pg_factory, pg_admin_factory):
    document_id = await create_document(pg_factory)
    async with pg_factory() as editor:
        try:
            await editor.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.id == document_id)
                .values(deleted_at=NOW)
            )
            async with pg_admin_factory() as publisher:
                try:
                    await publisher.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError) as caught:
                        publisher.add(source_policy(document_id))
                        await publisher.flush()
                finally:
                    await publisher.rollback()
            assert caught.value.orig.sqlstate == "55P03"
        finally:
            await editor.rollback()


async def test_knowledge_grants_allow_publication_without_document_delete_or_chunk_update(
    pg_factory,
):
    async with pg_factory() as session:
        for table, expected in {
            "knowledge_documents": {"SELECT", "INSERT", "UPDATE"},
            "knowledge_chunks": {"SELECT", "INSERT", "DELETE"},
        }.items():
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                allowed = await session.scalar(
                    text("SELECT has_table_privilege(current_user, :table_name, :privilege)"),
                    {"table_name": table, "privilege": privilege},
                )
                assert allowed is (privilege in expected)
        assert not await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = 'public' "
                "AND c.relname IN ('knowledge_documents', 'knowledge_chunks') "
                "AND pg_get_userbyid(c.relowner) = current_user)"
            )
        )
        assert not await session.scalar(
            text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
        )
        assert not await session.scalar(
            text("SELECT has_column_privilege(current_user, 'orders', 'status', 'UPDATE')")
        )
        for privilege in ("UPDATE", "DELETE"):
            assert not await session.scalar(
                text("SELECT has_table_privilege(current_user, 'audit_logs', :privilege)"),
                {"privilege": privilege},
            )
        assert not await session.scalar(
            text("SELECT has_any_column_privilege(current_user, 'audit_logs', 'UPDATE')")
        )


async def test_knowledge_account_can_insert_and_remove_chunks_and_soft_delete(pg_factory):
    document_id = await create_document(pg_factory)
    chunk_id = uuid4()
    async with pg_factory() as session, session.begin():
        session.add(
            KnowledgeChunk(
                id=chunk_id,
                document_id=document_id,
                index_generation=1,
                chunk_index=0,
                text="用于数据库权限测试的片段，不代表模型输出。",
                source_location={"line_start": 1, "line_end": 1},
            )
        )
    async with pg_factory() as session, session.begin():
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.id == chunk_id))
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(status="deleted", deleted_at=NOW)
        )
    async with pg_factory() as session:
        assert await session.get(KnowledgeChunk, chunk_id) is None
        assert (await session.get(KnowledgeDocument, document_id)).deleted_at == NOW
    await expect_rejection(
        pg_factory,
        delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id),
        "42501",
    )
