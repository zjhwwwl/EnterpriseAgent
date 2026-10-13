"""PostgreSQL is the authority for permissions and published index generations."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.models import KnowledgeChunk, KnowledgeDocument, UserRole
from enterprise_agent.security import Principal


def visible_documents(actor: Principal):
    filters = [KnowledgeDocument.deleted_at.is_(None)]
    if actor.role not in (UserRole.ADMIN, UserRole.SUPPORT):
        filters.append(KnowledgeDocument.visibility == "public")
    return filters


class KnowledgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def document(self, actor: Principal, document_id: UUID, *, lock=False):
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            *visible_documents(actor),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(query)

    async def page(self, actor: Principal, offset: int, limit: int):
        filters = visible_documents(actor)
        total = await self.session.scalar(
            select(func.count()).select_from(KnowledgeDocument).where(*filters)
        )
        documents = list(
            await self.session.scalars(
                select(KnowledgeDocument)
                .where(*filters)
                .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
                .offset(offset)
                .limit(limit)
            )
        )
        return documents, total

    async def chunk_count(self, document: KnowledgeDocument) -> int:
        return await self.session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id == document.id,
                KnowledgeChunk.index_generation == document.index_generation,
            )
        )

    async def active_chunks(
        self, actor: Principal, *, fingerprint: str | None = None, ids=None, document_ids=None
    ):
        query = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(
                KnowledgeDocument,
                KnowledgeDocument.id == KnowledgeChunk.document_id,
            )
            .where(
                *visible_documents(actor),
                KnowledgeDocument.index_generation > 0,
                KnowledgeDocument.active_index_id.is_not(None),
                KnowledgeChunk.index_generation == KnowledgeDocument.index_generation,
            )
        )
        if fingerprint is not None:
            query = query.where(KnowledgeDocument.index_fingerprint == fingerprint)
        if ids is not None:
            query = query.where(KnowledgeChunk.id.in_(ids))
        if document_ids is not None:
            query = query.where(KnowledgeDocument.id.in_(document_ids))
        return list(
            (
                await self.session.execute(
                    query.order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index).limit(5001)
                )
            ).all()
        )
