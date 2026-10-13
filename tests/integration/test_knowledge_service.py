"""Real PostgreSQL, Chroma and local models; controls inject storage failures only."""

import asyncio
import hashlib
from io import BytesIO
from threading import Event
from uuid import UUID

import pytest
import pytest_asyncio
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    AuditLog,
    KnowledgeChunk,
    KnowledgeDocument,
    RefundPolicyVersion,
    User,
)
from enterprise_agent.schemas.knowledge import SearchInput
from enterprise_agent.security import Principal
from enterprise_agent.services.knowledge import KnowledgeService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.rag,
    pytest.mark.usefixtures("pg_seed"),
]

BASE = "/api/v1/knowledge"


async def settle_runtime_work(runtime):
    """Wait for controlled late threads without closing the reusable test store."""
    await asyncio.gather(*tuple(runtime._tasks), return_exceptions=True)


PUBLIC_TEXT = (
    "退款与退货规则\n"
    "商品签收后七天内可以申请退货退款。退货商品应保持完好，用户需要保留订单凭证。\n"
    "标准退款仅退优惠后商品实付金额，原始运费不退。退款申请需要客服审核。"
)
PUBLIC_QUERY = "商品签收后七天内可以申请退货退款吗？"
INTERNAL_TEXT = (
    "内部客服补贴规则\n"
    "内部客服专用补贴码是海棠内部1888。补贴需要内部主管审核，不属于客户优惠券。\n"
    "这份模拟内部文件只允许客服和管理员查看。"
)
USERNAMES = ("demo_customer", "demo_other", "demo_support", "demo_analyst", "demo_admin")


async def test_off_topic_rerank_cannot_override_vector_evidence_floor(
    pg_rag_service, pg_factory, knowledge_actors
):
    """A real-model false positive seen in the first evaluation must be refused."""
    admin = knowledge_actors["demo_admin"]
    customer = knowledge_actors["demo_customer"]
    async with pg_factory() as session:
        documents = list(await session.scalars(select(KnowledgeDocument)))
    for document in documents:
        await pg_rag_service.reindex(admin, document.id)

    payload = SearchInput(query="给儿童接种疫苗的具体剂量是多少？", mode="hybrid_rerank")
    raw = await pg_rag_service.search(customer, payload, apply_gate=False)
    assert raw.citations
    # Pinned CPU models produce a misleading reranker score on this unrelated corpus.
    assert any(
        citation.rerank_score >= pg_rag_service.settings.rag_min_rerank_score
        and citation.vector_similarity < pg_rag_service.settings.rag_min_vector_similarity
        for citation in raw.citations
    )
    filtered = await pg_rag_service.search(customer, payload)
    assert raw.status == filtered.status == "insufficient_evidence"
    assert filtered.citations == []

    relevant = await pg_rag_service.search(customer, SearchInput(query=PUBLIC_QUERY))
    assert relevant.status == "evidence_found"
    assert relevant.citations


@pytest_asyncio.fixture
async def knowledge_actors(pg_factory):
    async with pg_factory() as session:
        users = list(await session.scalars(select(User).where(User.username.in_(USERNAMES))))
    assert len(users) == len(USERNAMES)
    return {user.username: Principal(user_id=user.id, role=user.role) for user in users}


@pytest_asyncio.fixture
async def knowledge_headers(rag_api_client):
    headers = {}
    for username in USERNAMES:
        response = await rag_api_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "DemoOnly!2026"},
        )
        assert response.status_code == 200, response.text
        headers[username] = {"Authorization": "Bearer " + response.json()["access_token"]}
    return headers


def format_document(file_type):
    if file_type == "pdf":
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 40 700 Td "
            b"(Enterprise refund policy: return within seven days after delivery.) Tj ET"
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = BytesIO()
        writer.write(buffer)
        return buffer.getvalue(), "application/pdf", "return within seven days", "page"
    if file_type == "docx":
        document = Document()
        document.add_heading("退款与退货规则", level=1)
        document.add_paragraph(PUBLIC_TEXT)
        buffer = BytesIO()
        document.save(buffer)
        return (
            buffer.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "七天",
            "paragraph",
        )
    return PUBLIC_TEXT.encode("utf-8"), "text/plain", "七天", "line_start"


async def upload_http(
    client, headers, *, text=PUBLIC_TEXT, visibility="public", title="模拟售后政策"
):
    response = await client.post(
        BASE + "/documents",
        headers=headers,
        data={"title": title, "visibility": visibility},
        files={"file": ("模拟政策.md", text.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def snapshot(pg_factory, document_id):
    identifier = document_id if isinstance(document_id, UUID) else UUID(document_id)
    async with pg_factory() as session:
        document = await session.get(KnowledgeDocument, identifier)
        chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == identifier)
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
    assert document is not None
    return document, chunks


async def upload_service(service, actor, *, expected_status="indexed"):
    document = await service.upload(
        actor,
        filename="模拟售后政策.md",
        content=PUBLIC_TEXT.encode("utf-8"),
        title="模拟售后政策",
        visibility="public",
    )
    assert document.status == expected_status
    return document


def storage_failure(*_args):
    raise BusinessError("vector_store_unavailable", "测试注入的向量服务故障", 503)


def pause_upsert(runtime, monkeypatch):
    entered, release = Event(), Event()
    original = runtime.store.upsert

    def paused(*args):
        entered.set()
        if not release.wait(15):
            raise BusinessError("test_barrier_timeout", "测试同步屏障未释放", 503)
        return original(*args)

    monkeypatch.setattr(runtime.store, "upsert", paused)
    return entered, release


async def wait_event(event):
    async with asyncio.timeout(10):
        while not event.is_set():
            await asyncio.sleep(0.01)


async def wait_deleted(pg_factory, document_id):
    async with asyncio.timeout(5):
        while True:
            document, _ = await snapshot(pg_factory, document_id)
            if document.deleted_at is not None:
                return document
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("file_type", ["pdf", "docx", "txt", "md"])
async def test_four_real_formats_publish_vectors_and_traceable_citations(
    file_type,
    rag_api_client,
    knowledge_headers,
    pg_factory,
    rag_runtime,
):
    content, media_type, expected_text, location_key = format_document(file_type)
    response = await rag_api_client.post(
        BASE + "/documents",
        headers=knowledge_headers["demo_admin"],
        data={"title": f"四格式演示 {file_type}", "visibility": "public"},
        files={"file": (f"模拟文档.{file_type}", content, media_type)},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "indexed"
    assert result["file_type"] == ("markdown" if file_type == "md" else file_type)
    assert result["checksum"] == hashlib.sha256(content).hexdigest()
    assert result["index_generation"] == result["index_attempt_generation"] == 1
    assert result["chunk_count"] > 0
    assert "file_path" not in result

    document, chunks = await snapshot(pg_factory, result["id"])
    assert len(chunks) == result["chunk_count"]
    assert expected_text in "\n".join(chunk.text for chunk in chunks)
    for chunk in chunks:
        assert chunk.index_generation == document.index_generation
        assert chunk.source_location[location_key] >= 1
        citation = await rag_api_client.get(
            BASE + f"/citations/{chunk.id}",
            headers=knowledge_headers["demo_customer"],
        )
        assert citation.status_code == 200, citation.text
        assert citation.json()["text"] == chunk.text
        assert citation.json()["document_id"] == result["id"]
        assert citation.json()["source_location"] == chunk.source_location

    vector = await rag_runtime.call(rag_runtime.models.embed_query, expected_text)
    candidates = await rag_runtime.call(
        rag_runtime.store.query,
        vector,
        [str(document.active_index_id)],
        min(10, len(chunks)),
    )
    assert {identifier for identifier, _ in candidates}.intersection(
        str(chunk.id) for chunk in chunks
    ), "Published chunks must be present in the actual Chroma HTTP index"
    download = await rag_api_client.get(
        BASE + f"/documents/{result['id']}/download",
        headers=knowledge_headers["demo_customer"],
    )
    assert download.status_code == 200
    assert download.content == content


async def test_internal_evidence_permissions_apply_to_every_read_path(
    rag_api_client,
    knowledge_headers,
    pg_factory,
):
    public = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    internal = await upload_http(
        rag_api_client,
        knowledge_headers["demo_admin"],
        text=INTERNAL_TEXT,
        visibility="internal",
        title="模拟内部补贴",
    )
    assert public["status"] == internal["status"] == "indexed"
    _, internal_chunks = await snapshot(pg_factory, internal["id"])
    internal_citation = internal_chunks[0].id

    totals = {}
    for username, allowed in (
        ("demo_customer", False),
        ("demo_analyst", False),
        ("demo_support", True),
        ("demo_admin", True),
    ):
        headers = knowledge_headers[username]
        listed = await rag_api_client.get(BASE + "/documents?limit=100", headers=headers)
        assert listed.status_code == 200
        ids = {item["id"] for item in listed.json()["items"]}
        totals[username] = listed.json()["total"]
        assert public["id"] in ids
        assert (internal["id"] in ids) is allowed
        assert all("file_path" not in item for item in listed.json()["items"])
        for route in (
            f"/documents/{internal['id']}",
            f"/documents/{internal['id']}/download",
            f"/citations/{internal_citation}",
        ):
            read = await rag_api_client.get(BASE + route, headers=headers)
            assert read.status_code == (200 if allowed else 404), read.text
        search = await rag_api_client.post(
            BASE + "/search",
            headers=headers,
            json={"query": "内部客服专用补贴码海棠内部1888", "mode": "hybrid_rerank"},
        )
        assert search.status_code == 200, search.text
        citations = search.json()["citations"]
        if allowed:
            assert internal["id"] in {citation["document_id"] for citation in citations}
        else:
            assert internal["id"] not in {citation["document_id"] for citation in citations}
            assert "海棠内部1888" not in str(citations)
    assert totals["demo_customer"] == totals["demo_analyst"]
    assert totals["demo_support"] == totals["demo_admin"] == totals["demo_customer"] + 1


async def test_non_admin_writes_cannot_change_document_or_index(
    rag_api_client,
    knowledge_headers,
    pg_factory,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    for username in USERNAMES:
        if username == "demo_admin":
            continue
        headers = knowledge_headers[username]
        denied_upload = await rag_api_client.post(
            BASE + "/documents",
            headers=headers,
            data={"title": "不应写入的文档", "visibility": "public"},
            files={"file": ("denied.txt", b"should not be stored", "text/plain")},
        )
        assert denied_upload.status_code == 403
        reindex = await rag_api_client.post(
            BASE + f"/documents/{document['id']}/reindex",
            headers=headers,
        )
        deletion = await rag_api_client.delete(
            BASE + f"/documents/{document['id']}",
            headers=headers,
        )
        assert reindex.status_code == deletion.status_code == 403
    stored, chunks = await snapshot(pg_factory, document["id"])
    assert stored.index_generation == stored.index_attempt_generation == 1
    assert stored.deleted_at is None and chunks
    async with pg_factory() as session:
        assert not await session.scalar(
            select(KnowledgeDocument.id).where(KnowledgeDocument.title == "不应写入的文档")
        )


async def test_hybrid_rerank_response_uses_real_evidence_and_no_chat_model(
    rag_api_client,
    knowledge_headers,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    response = await rag_api_client.post(
        BASE + "/search",
        headers=knowledge_headers["demo_customer"],
        json={"query": PUBLIC_QUERY, "mode": "hybrid_rerank", "top_k": 3},
    )
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["status"] == "evidence_found"
    assert answer["answer_mode"] == "evidence_only" and answer["llm_calls"] == 0
    assert answer["citations"]
    assert {citation["document_id"] for citation in answer["citations"]} == {document["id"]}
    assert {"vector_ms", "bm25_rrf_ms", "rerank_ms"}.issubset(answer["stage_ms"])
    assert all(citation["rerank_score"] is not None for citation in answer["citations"])
    for citation in answer["citations"]:
        resolved = await rag_api_client.get(
            citation["citation_url"],
            headers=knowledge_headers["demo_customer"],
        )
        assert resolved.status_code == 200
        assert resolved.json()["text"] == citation["text"]


async def test_reindex_retires_old_citations_and_publishes_new_generation(
    rag_api_client,
    knowledge_headers,
    pg_factory,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    previous, old_chunks = await snapshot(pg_factory, document["id"])
    response = await rag_api_client.post(
        BASE + f"/documents/{document['id']}/reindex",
        headers=knowledge_headers["demo_admin"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["index_generation"] == 2
    current, chunks = await snapshot(pg_factory, document["id"])
    assert current.active_index_id != previous.active_index_id
    assert current.index_generation == current.index_attempt_generation == 2
    assert {chunk.id for chunk in old_chunks}.isdisjoint(chunk.id for chunk in chunks)
    for chunk in old_chunks:
        assert (
            await rag_api_client.get(
                BASE + f"/citations/{chunk.id}",
                headers=knowledge_headers["demo_customer"],
            )
        ).status_code == 404
    for chunk in chunks:
        assert (
            await rag_api_client.get(
                BASE + f"/citations/{chunk.id}",
                headers=knowledge_headers["demo_customer"],
            )
        ).status_code == 200
    searched = await rag_api_client.post(
        BASE + "/search",
        headers=knowledge_headers["demo_customer"],
        json={"query": PUBLIC_QUERY},
    )
    assert searched.status_code == 200
    assert searched.json()["citations"]
    assert {citation["index_generation"] for citation in searched.json()["citations"]} == {2}


@pytest.mark.parametrize(
    "cleanup_failure", [False, True], ids=["real-cleanup", "control-cleanup-failure"]
)
async def test_deleted_evidence_stays_invisible_even_if_vector_cleanup_fails(
    cleanup_failure,
    rag_api_client,
    knowledge_headers,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    _, chunks = await snapshot(pg_factory, document["id"])
    if cleanup_failure:
        monkeypatch.setattr(rag_runtime.store, "delete_document", storage_failure)
    response = await rag_api_client.delete(
        BASE + f"/documents/{document['id']}",
        headers=knowledge_headers["demo_admin"],
    )
    assert response.status_code == 204, response.text
    stored, remaining_chunks = await snapshot(pg_factory, document["id"])
    assert stored.deleted_at is not None and stored.status == "deleted"
    assert stored.active_index_id is None and stored.indexing_job_id is None
    assert remaining_chunks == []
    for route in (
        f"/documents/{document['id']}",
        f"/documents/{document['id']}/download",
        f"/citations/{chunks[0].id}",
    ):
        assert (
            await rag_api_client.get(
                BASE + route,
                headers=knowledge_headers["demo_customer"],
            )
        ).status_code == 404
    searched = await rag_api_client.post(
        BASE + "/search",
        headers=knowledge_headers["demo_customer"],
        json={"query": PUBLIC_QUERY},
    )
    assert searched.status_code == 200
    assert searched.json()["status"] == "insufficient_evidence"
    assert searched.json()["citations"] == []
    raw = await rag_runtime.call(
        lambda: rag_runtime.store.collection.get(
            where={"document_id": document["id"]},
            include=["metadatas"],
        )
    )
    assert bool(raw["ids"]) is cleanup_failure
    async with pg_factory() as session:
        actions = set(
            await session.scalars(
                select(AuditLog.action).where(AuditLog.resource_id == document["id"])
            )
        )
    assert {"knowledge.upload", "knowledge.index_published", "knowledge.delete"} <= actions


async def test_policy_source_is_protected_and_uploads_do_not_publish_refund_rules(
    rag_api_client,
    knowledge_headers,
    pg_factory,
):
    customer = knowledge_headers["demo_customer"]
    admin = knowledge_headers["demo_admin"]
    endpoint = "/api/v1/orders/10086/refund-eligibility"
    before = await rag_api_client.get(endpoint, headers=customer)
    assert before.status_code == 200
    policy_id = before.json()["policy"]["document_id"]
    async with pg_factory() as session:
        policy_versions = list(await session.scalars(select(RefundPolicyVersion.id)))
    indexed = await rag_api_client.post(BASE + f"/documents/{policy_id}/reindex", headers=admin)
    assert indexed.status_code == 200, indexed.text
    deletion = await rag_api_client.delete(BASE + f"/documents/{policy_id}", headers=admin)
    assert deletion.status_code == 409
    assert deletion.json()["error"]["code"] == "policy_source_protected"
    uploaded = await upload_http(
        rag_api_client,
        admin,
        text="这是一份普通模拟知识文档，声明九十天退款。它不是已发布的可执行退款规则。",
        title="普通文档不发布退款规则",
    )
    assert uploaded["status"] == "indexed"
    after = await rag_api_client.get(endpoint, headers=customer)
    assert after.status_code == 200
    assert after.json() == before.json()
    async with pg_factory() as session:
        assert list(await session.scalars(select(RefundPolicyVersion.id))) == policy_versions


async def test_changed_original_cannot_be_silently_reindexed(
    rag_api_client,
    rag_api_app,
    knowledge_headers,
    pg_factory,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    before, _ = await snapshot(pg_factory, document["id"])
    path = rag_api_app.state.settings.data_dir / before.file_path
    original = path.read_bytes()
    try:
        path.write_bytes(b"changed original")
        response = await rag_api_client.post(
            BASE + f"/documents/{document['id']}/reindex",
            headers=knowledge_headers["demo_admin"],
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "document_source_changed"
        after, _ = await snapshot(pg_factory, document["id"])
        assert after.index_generation == before.index_generation
        assert after.active_index_id == before.active_index_id
        assert after.index_error_code == "document_source_changed"
    finally:
        path.write_bytes(original)


async def test_control_cancel_after_upload_commit_preserves_registered_original(
    pg_rag_service, knowledge_actors, pg_factory, rag_runtime
):
    closed, release, settled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class CloseAfterCommit(AsyncSession):
        async def close(self):
            await super().close()
            closed.set()
            try:
                await release.wait()
            finally:
                settled.set()

    sessions = async_sessionmaker(
        bind=pg_factory.kw["bind"], class_=CloseAfterCommit, expire_on_commit=False
    )
    controlled = KnowledgeService(sessions, pg_rag_service.settings, rag_runtime)
    admin = knowledge_actors["demo_admin"]
    task = asyncio.create_task(
        controlled.upload(
            admin,
            filename="committed-original.md",
            content=PUBLIC_TEXT.encode("utf-8"),
            title="取消请求后原件保留",
            visibility="public",
        )
    )
    try:
        await asyncio.wait_for(closed.wait(), timeout=10)
        # This is a real committed row, observed from a separate ordinary session.
        async with pg_factory() as session:
            document = await session.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.title == "取消请求后原件保留")
            )
        assert document is not None and document.status == "uploaded"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        path = pg_rag_service.settings.data_dir / document.file_path
        assert path.read_bytes() == PUBLIC_TEXT.encode("utf-8")
        downloaded, _, _ = await pg_rag_service.download(admin, document.id)
        assert downloaded == PUBLIC_TEXT.encode("utf-8")
        assert (await pg_rag_service.reindex(admin, document.id)).status == "indexed"
    finally:
        release.set()
        await asyncio.wait_for(settled.wait(), timeout=5)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_control_models_disappear_after_upload_records_failure_and_retry(
    pg_rag_service, knowledge_actors, pg_factory, rag_runtime, monkeypatch
):
    original_ready = rag_runtime.models.ready
    checks = 0

    def lose_models_after_accepting_upload():
        nonlocal checks
        checks += 1
        return original_ready() if checks == 1 else False

    admin = knowledge_actors["demo_admin"]
    with monkeypatch.context() as injected:
        injected.setattr(rag_runtime.models, "ready", lose_models_after_accepting_upload)
        document = await upload_service(pg_rag_service, admin, expected_status="failed")
    assert document.status == "failed"
    assert document.index_error_code == "rag_models_unavailable"
    stored, chunks = await snapshot(pg_factory, document.id)
    assert stored.index_attempt_generation == 1 and stored.index_generation == 0
    assert stored.indexing_job_id is None and not chunks
    async with pg_factory() as session:
        failure = await session.scalar(
            select(AuditLog).where(
                AuditLog.resource_id == str(document.id),
                AuditLog.action == "knowledge.index_failed",
            )
        )
    assert failure.details["error_code"] == "rag_models_unavailable"
    assert (await pg_rag_service.reindex(admin, document.id)).index_generation == 2


async def test_control_failed_initial_index_keeps_original_and_can_retry(
    rag_api_client,
    knowledge_headers,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    with monkeypatch.context() as injected:
        injected.setattr(rag_runtime.store, "upsert", storage_failure)
        document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    assert document["status"] == "failed"
    assert document["index_error_code"] == "vector_store_unavailable"
    stored, chunks = await snapshot(pg_factory, document["id"])
    assert stored.index_generation == 0 and stored.index_attempt_generation == 1
    assert stored.active_index_id is None and stored.indexing_job_id is None and chunks == []
    download = await rag_api_client.get(
        BASE + f"/documents/{document['id']}/download",
        headers=knowledge_headers["demo_admin"],
    )
    assert download.status_code == 200 and download.content == PUBLIC_TEXT.encode("utf-8")
    retry = await rag_api_client.post(
        BASE + f"/documents/{document['id']}/reindex",
        headers=knowledge_headers["demo_admin"],
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "indexed" and retry.json()["index_generation"] == 2


async def test_control_failed_reindex_preserves_published_evidence(
    rag_api_client,
    knowledge_headers,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    document = await upload_http(rag_api_client, knowledge_headers["demo_admin"])
    before, old_chunks = await snapshot(pg_factory, document["id"])
    with monkeypatch.context() as injected:
        injected.setattr(rag_runtime.store, "upsert", storage_failure)
        response = await rag_api_client.post(
            BASE + f"/documents/{document['id']}/reindex",
            headers=knowledge_headers["demo_admin"],
        )
    assert response.status_code == 503
    after, chunks = await snapshot(pg_factory, document["id"])
    assert after.status == "indexed" and after.index_error_code == "vector_store_unavailable"
    assert after.index_generation == before.index_generation == 1
    assert after.active_index_id == before.active_index_id
    assert {chunk.id for chunk in chunks} == {chunk.id for chunk in old_chunks}
    searched = await rag_api_client.post(
        BASE + "/search",
        headers=knowledge_headers["demo_customer"],
        json={"query": PUBLIC_QUERY},
    )
    assert searched.status_code == 200 and searched.json()["citations"]
    assert {citation["index_generation"] for citation in searched.json()["citations"]} == {1}


async def test_control_active_citation_survives_indexing_and_second_job_is_rejected(
    pg_rag_service,
    knowledge_actors,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    admin = knowledge_actors["demo_admin"]
    document = await upload_service(pg_rag_service, admin)
    _, chunks = await snapshot(pg_factory, document.id)
    entered, release = pause_upsert(rag_runtime, monkeypatch)
    task = asyncio.create_task(pg_rag_service.reindex(admin, document.id))
    try:
        await wait_event(entered)
        pending = await pg_rag_service.get_document(admin, document.id)
        assert pending.status == "indexing" and pending.index_generation == 1
        citation = await pg_rag_service.citation(knowledge_actors["demo_customer"], chunks[0].id)
        assert citation.index_generation == 1 and citation.text == chunks[0].text
        with pytest.raises(BusinessError) as rejected:
            await pg_rag_service.reindex(admin, document.id)
        assert rejected.value.code == "index_in_progress"
        release.set()
        indexed = await task
        assert indexed.index_generation == 2
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await settle_runtime_work(rag_runtime)


async def test_control_delete_during_upsert_cannot_publish_late_evidence(
    pg_rag_service,
    knowledge_actors,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    admin = knowledge_actors["demo_admin"]
    document = await upload_service(pg_rag_service, admin)
    entered, release = pause_upsert(rag_runtime, monkeypatch)
    index_task = asyncio.create_task(pg_rag_service.reindex(admin, document.id))
    deletion_task = None
    try:
        await wait_event(entered)
        deletion_task = asyncio.create_task(pg_rag_service.delete_document(admin, document.id))
        await wait_deleted(pg_factory, document.id)
        release.set()
        with pytest.raises(BusinessError) as rejected:
            await index_task
        assert rejected.value.code in {"document_not_found", "index_attempt_superseded"}
        await deletion_task
        stored, chunks = await snapshot(pg_factory, document.id)
        assert stored.deleted_at is not None and stored.active_index_id is None and chunks == []
        searched = await pg_rag_service.search(
            knowledge_actors["demo_customer"],
            SearchInput(query=PUBLIC_QUERY),
        )
        assert searched.status == "insufficient_evidence" and searched.citations == []
    finally:
        release.set()
        tasks = [index_task] + ([deletion_task] if deletion_task is not None else [])
        await asyncio.gather(*tasks, return_exceptions=True)
        await settle_runtime_work(rag_runtime)


async def test_control_timeout_and_late_vectors_do_not_replace_active_generation(
    pg_rag_service,
    knowledge_actors,
    pg_factory,
    rag_runtime,
    monkeypatch,
):
    admin = knowledge_actors["demo_admin"]
    document = await upload_service(pg_rag_service, admin)
    before, _ = await snapshot(pg_factory, document.id)
    monkeypatch.setattr(pg_rag_service.settings, "rag_timeout_seconds", 5)
    monkeypatch.setattr(rag_runtime.settings, "rag_timeout_seconds", 5)
    entered, release = pause_upsert(rag_runtime, monkeypatch)
    task = asyncio.create_task(pg_rag_service.reindex(admin, document.id))
    try:
        await wait_event(entered)
        with pytest.raises(BusinessError) as timed_out:
            await asyncio.wait_for(asyncio.shield(task), timeout=8)
        assert timed_out.value.code == "rag_timeout"
        failed, _ = await snapshot(pg_factory, document.id)
        assert failed.indexing_job_id is None and failed.index_error_code == "rag_timeout"
        assert failed.active_index_id == before.active_index_id
        release.set()
        await settle_runtime_work(rag_runtime)
        after, _ = await snapshot(pg_factory, document.id)
        assert after.index_generation == 1 and after.active_index_id == before.active_index_id
        searched = await pg_rag_service.search(
            knowledge_actors["demo_customer"],
            SearchInput(query=PUBLIC_QUERY),
        )
        assert searched.citations
        assert {citation.index_generation for citation in searched.citations} == {1}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await settle_runtime_work(rag_runtime)
