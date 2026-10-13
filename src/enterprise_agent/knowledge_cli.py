"""Index seeded or uploaded documents through the same audited Service as the API."""

import asyncio
import json
import sys

from sqlalchemy import select

from enterprise_agent.config import get_settings
from enterprise_agent.database import Database
from enterprise_agent.models import KnowledgeDocument, User, UserRole
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.security import Principal
from enterprise_agent.services.knowledge import KnowledgeService


async def run():
    settings = get_settings()
    if settings.app_env == "production" or settings.database_url is None:
        raise RuntimeError(
            "Local knowledge initialization requires a configured development database"
        )
    database = Database(settings.database_url.get_secret_value())
    runtime = RagRuntime(settings)
    try:
        if not await database.is_ready() or not await runtime.ready():
            raise RuntimeError("Migrate PostgreSQL, prepare pinned models and start Chroma first")
        async with database.session_factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.username == "demo_admin",
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            )
            if user is None:
                raise RuntimeError("The fictional demo administrator is not seeded")
            documents = list(
                await session.scalars(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.deleted_at.is_(None))
                    .order_by(KnowledgeDocument.id)
                )
            )
        service = KnowledgeService(database.session_factory, settings, runtime)
        actor = Principal(user_id=user.id, role=user.role)
        for document in documents:
            if document.index_generation and document.index_fingerprint == runtime.fingerprint:
                print(
                    json.dumps(
                        {
                            "document_id": str(document.id),
                            "status": "already_indexed",
                            "generation": document.index_generation,
                        }
                    )
                )
                continue
            result = await service.reindex(actor, document.id)
            print(
                json.dumps(
                    {
                        "document_id": str(result.id),
                        "status": result.status,
                        "generation": result.index_generation,
                        "chunks": result.chunk_count,
                    }
                )
            )
    finally:
        await runtime.close()
        await database.close()


def main():
    try:
        with asyncio.Runner(
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
        ) as runner:
            runner.run(run())
    except Exception:
        print(
            "Knowledge initialization failed; verify DB, models, Chroma and original checksums",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
