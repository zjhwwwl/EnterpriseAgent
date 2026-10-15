"""Immutable originals, fenced index publication and authorized evidence retrieval."""

import asyncio
import hashlib
import logging
import math
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4, uuid5

from sqlalchemy import delete, select

from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.domain_errors import BusinessError
from enterprise_agent.models import (
    AuditLog,
    KnowledgeChunk,
    KnowledgeDocument,
    RefundPolicyVersion,
    UserRole,
)
from enterprise_agent.rag.chunking import chunk_sections
from enterprise_agent.rag.parsing import parse_document
from enterprise_agent.rag.ranking import bm25_rank, reciprocal_rank_fusion
from enterprise_agent.repositories.knowledge import KnowledgeRepository
from enterprise_agent.request_context import get_request_id
from enterprise_agent.schemas.knowledge import (
    Citation,
    DocumentContent,
    DocumentPage,
    DocumentResponse,
    SearchInput,
    SearchResponse,
)
from enterprise_agent.security import Principal
from enterprise_agent.services.orders import validate_pagination

logger = logging.getLogger("enterprise_agent.knowledge")
_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "markdown",
    ".markdown": "markdown",
}


def require_admin(actor: Principal):
    if actor.role != UserRole.ADMIN:
        raise BusinessError("forbidden", "只有管理员可以维护知识文档", 403)


def audit(session, actor, action, document_id, details):
    session.add(
        AuditLog(
            actor_id=actor.user_id,
            action=action,
            resource_type="knowledge_document",
            resource_id=str(document_id),
            request_id=get_request_id(),
            details=details,
        )
    )


class KnowledgeService:
    def __init__(self, sessions, settings: Settings, runtime=None):
        self.sessions = sessions
        self.settings = settings
        self.runtime = runtime

    def _runtime(self):
        if self.runtime is None:
            raise BusinessError("rag_not_enabled", "知识检索尚未启用，请先准备本地模型和索引", 503)
        return self.runtime

    @staticmethod
    async def _document(repository, actor, document_id, *, lock=False):
        document = await repository.document(actor, document_id, lock=lock)
        if document is None:
            raise BusinessError("document_not_found", "文档不存在", 404)
        return document

    async def list_documents(self, actor, *, offset=0, limit=20):
        validate_pagination(offset, limit)
        async with self.sessions() as session:
            repository = KnowledgeRepository(session)
            documents, total = await repository.page(actor, offset, limit)
            items = []
            for document in documents:
                item = DocumentResponse.model_validate(document)
                item.chunk_count = await repository.chunk_count(document)
                items.append(item)
            return DocumentPage(items=items, total=total, offset=offset, limit=limit)

    async def get_document(self, actor, document_id):
        async with self.sessions() as session:
            repository = KnowledgeRepository(session)
            document = await self._document(repository, actor, document_id)
            response = DocumentResponse.model_validate(document)
            response.chunk_count = await repository.chunk_count(document)
            return response

    def _source_path(self, document):
        relative = Path(document.file_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise BusinessError("document_source_unavailable", "文档原件路径无效", 409)
        if (
            len(relative.parts) == 3
            and relative.parts[:2] == ("data", "knowledge")
            and relative.suffix == ".md"
            and document.file_type == "markdown"
        ):
            # A checked, immutable copy preserves a published seed version when
            # a later checkout contains a different revision of the same file.
            if not isinstance(document.checksum, str) or not re.fullmatch(
                r"[0-9a-f]{64}", document.checksum
            ):
                raise BusinessError("document_source_unavailable", "文档原件校验信息无效", 409)
            archive_root = self.settings.data_dir / "knowledge-source-archive"
            archive = archive_root / (document.checksum + ".blob")
            resolved_root = archive_root.resolve()
            if not resolved_root.is_relative_to(self.settings.data_dir.resolve()):
                raise BusinessError("document_source_unavailable", "文档原件不可访问", 409)
            if archive.exists() or archive.is_symlink():
                archived_path = archive.resolve()
                if not archived_path.is_relative_to(resolved_root):
                    raise BusinessError("document_source_unavailable", "文档原件不可访问", 409)
                return archived_path
        base = (
            PROJECT_ROOT if relative.parts[:2] == ("data", "knowledge") else self.settings.data_dir
        )
        path = (base / relative).resolve()
        if not path.is_relative_to(base.resolve()):
            raise BusinessError("document_source_unavailable", "文档原件不可访问", 409)
        return path

    def _read_source(self, document):
        try:
            path = self._source_path(document)
            if path.stat().st_size > self.settings.rag_upload_max_bytes:
                raise BusinessError("document_too_large", "文档原件超过大小限制", 413)
            content = path.read_bytes()
        except OSError:
            raise BusinessError("document_source_unavailable", "文档原件不可访问", 409) from None
        if hashlib.sha256(content).hexdigest() != document.checksum:
            raise BusinessError(
                "document_source_changed", "文档原件与登记版本不一致，请重新上传新文档", 409
            )
        return content

    async def download(self, actor, document_id):
        async with self.sessions() as session:
            document = await self._document(KnowledgeRepository(session), actor, document_id)
            content = await asyncio.to_thread(self._read_source, document)
            extension = ".md" if document.file_type == "markdown" else "." + document.file_type
            return content, str(document.id) + extension, document.file_type

    def _preview_source(self, document):
        # Parse the immutable original, not overlapping retrieval chunks. This
        # remains available when local models or the document index are absent.
        sections = parse_document(self._read_source(document), document.file_type)
        return "\n\n".join(section.text for section in sections)

    async def document_content(self, actor, document_id):
        async with self.sessions() as session:
            document = await self._document(KnowledgeRepository(session), actor, document_id)
            try:
                async with asyncio.timeout(self.settings.rag_timeout_seconds):
                    content = await asyncio.to_thread(self._preview_source, document)
            except TimeoutError:
                raise BusinessError(
                    "document_preview_timeout", "文档内容读取超时，请稍后重试", 504
                ) from None
            return DocumentContent(
                document_id=document.id,
                title=document.title,
                file_type=document.file_type,
                version=document.version,
                content=content,
            )

    async def upload(self, actor, *, filename: str, content: bytes, title: str, visibility: str):
        require_admin(actor)
        runtime = self._runtime()
        if not await runtime.call(runtime.models.ready):
            raise BusinessError(
                "rag_models_unavailable", "本地模型尚未准备，请先运行模型准备脚本", 503
            )
        if not filename or any(char in filename for char in ("/", "\\", "\x00")):
            raise BusinessError("invalid_filename", "上传文件名必须为普通文件名", 422)
        extension = Path(filename).suffix.lower()
        if extension not in _FORMATS:
            raise BusinessError(
                "unsupported_document_type", "仅支持 PDF、DOCX、TXT 和 Markdown", 422
            )
        if visibility not in ("public", "internal") or not title.strip() or len(title) > 200:
            raise BusinessError("invalid_document_metadata", "文档标题或可见性不合法", 422)
        if len(content) > self.settings.rag_upload_max_bytes:
            raise BusinessError("document_too_large", "上传文档超过大小限制", 413)
        # Reject malformed input before persisting a document or creating an index attempt.
        await runtime.call(parse_document, content, _FORMATS[extension])
        document_id = uuid4()
        relative = Path("knowledge") / "uploads" / (str(document_id) + extension)
        path = (self.settings.data_dir / relative).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:
                handle.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        # Once persistence begins, commit may succeed even if its acknowledgement
        # or session shutdown is cancelled. Preserve the immutable original in
        # that uncertain case; an unregistered file is safer than a dangling row.
        async with self.sessions() as session, session.begin():
            session.add(
                KnowledgeDocument(
                    id=document_id,
                    title=title.strip(),
                    file_type=_FORMATS[extension],
                    file_path=relative.as_posix(),
                    checksum=hashlib.sha256(content).hexdigest(),
                    version=1,
                    visibility=visibility,
                    status="uploaded",
                    index_generation=0,
                    index_attempt_generation=0,
                )
            )
            audit(
                session,
                actor,
                "knowledge.upload",
                document_id,
                {
                    "file_type": _FORMATS[extension],
                    "visibility": visibility,
                    "bytes": len(content),
                },
            )
        try:
            return await self.reindex(actor, document_id)
        except BusinessError:
            # The accepted original remains available for a visible failed-index retry.
            return await self.get_document(actor, document_id)

    async def _index_failed(self, actor, document_id, job_id, code):
        async with self.sessions() as session, session.begin():
            document = await session.scalar(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id == document_id)
                .with_for_update()
            )
            if (
                document is not None
                and document.deleted_at is None
                and document.indexing_job_id == job_id
            ):
                document.status = "indexed" if document.index_generation else "failed"
                document.indexing_job_id = None
                document.index_error_code = code
                audit(
                    session,
                    actor,
                    "knowledge.index_failed",
                    document_id,
                    {"attempt": document.index_attempt_generation, "error_code": code},
                )

    async def reindex(self, actor, document_id):
        require_admin(actor)
        runtime = self._runtime()
        lease = timedelta(seconds=self.settings.rag_timeout_seconds + 30)
        # Reject a known active attempt before queueing behind its CPU/store work.
        # The locked transaction below repeats this check before reserving a job.
        async with self.sessions() as session:
            pending = await self._document(KnowledgeRepository(session), actor, document_id)
            if (
                pending.indexing_job_id is not None
                and pending.index_started_at is not None
                and pending.index_started_at + lease > datetime.now(UTC)
            ):
                raise BusinessError("index_in_progress", "文档正在索引，请稍后重试", 409)
        job_id = uuid4()
        async with self.sessions() as session, session.begin():
            document = await self._document(
                KnowledgeRepository(session), actor, document_id, lock=True
            )
            now = datetime.now(UTC)
            if (
                document.indexing_job_id is not None
                and document.index_started_at is not None
                and document.index_started_at + lease > now
            ):
                raise BusinessError("index_in_progress", "文档正在索引，请稍后重试", 409)
            document.index_attempt_generation += 1
            attempt = document.index_attempt_generation
            checksum = document.checksum
            document.indexing_job_id = job_id
            document.index_started_at = now
            document.status = "indexing"
            document.index_error_code = None
            previous_index = document.active_index_id
            audit(session, actor, "knowledge.index_started", document_id, {"attempt": attempt})
        try:
            async with asyncio.timeout(self.settings.rag_timeout_seconds):
                if not await runtime.call(runtime.models.ready):
                    raise BusinessError("rag_models_unavailable", "本地模型尚未准备", 503)
                content = await asyncio.to_thread(self._read_source, document)
                sections = await runtime.call(parse_document, content, document.file_type)
                chunks = await runtime.call(
                    partial(
                        chunk_sections,
                        chunk_size=self.settings.rag_chunk_size,
                        chunk_overlap=self.settings.rag_chunk_overlap,
                    ),
                    sections,
                )
                if not chunks or len(chunks) > self.settings.rag_max_chunks:
                    raise BusinessError("document_chunk_limit", "文档文本过多或没有可索引内容", 422)
                ids = [str(uuid5(job_id, str(index))) for index in range(len(chunks))]
                texts = [chunk.text for chunk in chunks]
                vectors = await runtime.call(runtime.models.embed_documents, texts)
                metadata = [
                    {
                        "document_id": str(document_id),
                        "index_id": str(job_id),
                        "generation": attempt,
                    }
                    for _ in chunks
                ]
                await runtime.call(runtime.store.upsert, ids, texts, vectors, metadata)
                async with self.sessions() as session, session.begin():
                    current = await self._document(
                        KnowledgeRepository(session), actor, document_id, lock=True
                    )
                    if (
                        current.indexing_job_id != job_id
                        or current.index_attempt_generation != attempt
                        or current.checksum != checksum
                    ):
                        raise BusinessError(
                            "index_attempt_superseded", "索引尝试已失效，请查看最新文档状态", 409
                        )
                    await session.execute(
                        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
                    )
                    session.add_all(
                        [
                            KnowledgeChunk(
                                id=UUID(identifier),
                                document_id=document_id,
                                index_generation=attempt,
                                chunk_index=index,
                                text=chunk.text,
                                source_location=chunk.source_location,
                            )
                            for index, (identifier, chunk) in enumerate(
                                zip(ids, chunks, strict=True)
                            )
                        ]
                    )
                    current.index_generation = attempt
                    current.active_index_id = job_id
                    current.index_fingerprint = runtime.fingerprint
                    current.indexing_job_id = None
                    current.index_error_code = None
                    current.indexed_at = datetime.now(UTC)
                    current.status = "indexed"
                    audit(
                        session,
                        actor,
                        "knowledge.index_published",
                        document_id,
                        {
                            "generation": attempt,
                            "chunks": len(chunks),
                            "fingerprint": runtime.fingerprint,
                        },
                    )
        except BaseException as exc:
            code = (
                "rag_timeout"
                if isinstance(exc, TimeoutError)
                else exc.code
                if isinstance(exc, BusinessError)
                else "index_failed"
            )
            await asyncio.shield(self._index_failed(actor, document_id, job_id, code))
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(exc, BusinessError):
                raise
            raise BusinessError(code, "索引未发布，请查看文档状态并稍后重试", 503) from None
        if previous_index is not None:
            try:
                await runtime.call(runtime.store.delete_index, str(previous_index))
            except Exception:
                logger.warning(
                    "Old vector cleanup deferred", extra={"event": "knowledge_cleanup_deferred"}
                )
        return await self.get_document(actor, document_id)

    async def delete_document(self, actor, document_id):
        require_admin(actor)
        async with self.sessions() as session, session.begin():
            document = await self._document(
                KnowledgeRepository(session), actor, document_id, lock=True
            )
            policy_id = await session.scalar(
                select(RefundPolicyVersion.id)
                .where(RefundPolicyVersion.document_id == document_id)
                .limit(1)
            )
            if policy_id is not None:
                raise BusinessError(
                    "policy_source_protected", "文档是已发布退款政策的来源，不能删除", 409
                )
            document.deleted_at = datetime.now(UTC)
            document.status = "deleted"
            document.indexing_job_id = None
            document.active_index_id = None
            await session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            )
            audit(
                session,
                actor,
                "knowledge.delete",
                document_id,
                {"generation": document.index_generation},
            )
        if self.runtime is not None:
            try:
                await self.runtime.call(self.runtime.store.delete_document, str(document_id))
            except Exception:
                logger.warning(
                    "Deleted vector cleanup deferred", extra={"event": "knowledge_cleanup_deferred"}
                )

    @staticmethod
    def _citation(chunk, document, **scores):
        return Citation(
            document_id=document.id,
            document_title=document.title,
            document_version=document.version,
            index_generation=chunk.index_generation,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            source_location=chunk.source_location,
            citation_url=f"/api/v1/knowledge/citations/{chunk.id}",
            **scores,
        )

    async def citation(self, actor, chunk_id):
        async with self.sessions() as session:
            rows = await KnowledgeRepository(session).active_chunks(actor, ids=[chunk_id])
            if not rows:
                raise BusinessError("citation_not_found", "引用不存在、已失效或不可访问", 404)
            return self._citation(*rows[0])

    async def search(self, actor, payload: SearchInput, *, apply_gate=True, document_ids=None):
        runtime = self._runtime()
        if not await runtime.call(runtime.models.ready):
            raise BusinessError("rag_models_unavailable", "本地模型尚未准备", 503)
        started = perf_counter()
        stages = {}
        async with self.sessions() as session:
            rows = await KnowledgeRepository(session).active_chunks(
                actor, fingerprint=runtime.fingerprint, document_ids=document_ids
            )
        if len(rows) > 5000:
            raise BusinessError("knowledge_capacity_exceeded", "当前检索语料超过本地演示容量", 503)
        texts = {str(chunk.id): chunk.text for chunk, _ in rows}
        indexes = list(dict.fromkeys(str(document.active_index_id) for _, document in rows))
        if not texts:
            return SearchResponse(
                status="insufficient_evidence",
                message="没有可访问且已完成索引的企业资料，无法确认该政策",
                mode=payload.mode,
                citations=[],
                elapsed_ms=round((perf_counter() - started) * 1000, 3),
                stage_ms={},
                model_fingerprint=runtime.fingerprint,
            )
        tick = perf_counter()
        vector = await runtime.call(runtime.models.embed_query, payload.query)
        vector_candidates = await runtime.call(runtime.store.query, vector, indexes, 30)
        vector_scores = {
            identifier: 1.0 - float(distance)
            for identifier, distance in vector_candidates
            if identifier in texts and math.isfinite(float(distance))
        }
        stages["vector_ms"] = round((perf_counter() - tick) * 1000, 3)
        lexical = []
        ranked = list(vector_scores)
        if payload.mode != "vector":
            tick = perf_counter()
            lexical = await runtime.call(bm25_rank, payload.query, texts, 30)
            ranked = [
                identifier
                for identifier, _ in reciprocal_rank_fusion(
                    ranked, [identifier for identifier, _ in lexical]
                )
            ][:30]
            stages["bm25_rrf_ms"] = round((perf_counter() - tick) * 1000, 3)
        rerank_scores = {}
        if payload.mode == "hybrid_rerank" and ranked:
            tick = perf_counter()
            raw_scores = await runtime.call(
                runtime.models.rerank, payload.query, [texts[identifier] for identifier in ranked]
            )
            if len(raw_scores) != len(ranked):
                raise BusinessError("reranker_failed", "重排结果不完整", 503)
            rerank_scores = dict(zip(ranked, raw_scores, strict=True))
            ranked.sort(key=lambda identifier: (-rerank_scores[identifier], identifier))
            stages["rerank_ms"] = round((perf_counter() - tick) * 1000, 3)
        top_ids = ranked[: payload.top_k]
        allowed = [
            identifier
            for identifier in top_ids
            if vector_scores.get(identifier, 0) >= self.settings.rag_min_vector_similarity
            and (
                payload.mode != "hybrid_rerank"
                or rerank_scores.get(identifier, 0) >= self.settings.rag_min_rerank_score
            )
        ]
        selected_ids = allowed if apply_gate else top_ids
        async with self.sessions() as session:
            # Revalidate after external work: deleted or reindexed evidence cannot be returned.
            final_rows = await KnowledgeRepository(session).active_chunks(
                actor,
                fingerprint=runtime.fingerprint,
                ids=[UUID(identifier) for identifier in selected_ids],
                document_ids=document_ids,
            )
        final = {str(chunk.id): (chunk, document) for chunk, document in final_rows}
        lexical_scores = dict(lexical)
        citations = [
            self._citation(
                *final[identifier],
                vector_similarity=vector_scores.get(identifier),
                bm25_score=lexical_scores.get(identifier),
                rerank_score=rerank_scores.get(identifier),
            )
            for identifier in selected_ids
            if identifier in final
        ]
        found = any(identifier in final for identifier in allowed)
        elapsed = round((perf_counter() - started) * 1000, 3)
        logger.info(
            "Knowledge retrieval completed",
            extra={
                "event": "knowledge_retrieval",
                "mode": payload.mode,
                "elapsed_ms": elapsed,
                "citations": len(citations),
                "llm_calls": 0,
            },
        )
        return SearchResponse(
            status="evidence_found" if found else "insufficient_evidence",
            message="找到相关企业资料，请结合引用原文核对"
            if found
            else "未找到足够企业资料证据，无法确认该政策，请联系相关负责人",
            mode=payload.mode,
            citations=citations,
            elapsed_ms=elapsed,
            stage_ms=stages,
            model_fingerprint=runtime.fingerprint,
        )
