"""Prepare real RAG data only in the explicitly configured local test database.

No reset, migration, model request, external source, or development DB write.
Run with infrastructure environment loaded before the isolated browser server.
"""

import asyncio
import json
import sys
from datetime import UTC, datetime

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from enterprise_agent.config import PROJECT_ROOT, Settings
from enterprise_agent.models import User, UserRole
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.security import Principal
from enterprise_agent.seed import seed_demo
from enterprise_agent.services.knowledge import KnowledgeService


async def prepare():
    settings = Settings()
    required = (
        (settings.test_database_url, "ea_test_app"),
        (settings.test_migration_database_url, "ea_local_admin"),
        (settings.test_analytics_database_url, "ea_test_analytics_reader"),
    )
    for secret, role in required:
        if secret is None:
            raise RuntimeError("Dedicated test database credentials are required")
        url = make_url(secret.get_secret_value())
        if (
            url.database != "enterprise_agent_test"
            or url.username != role
            or url.host not in {"localhost", "127.0.0.1"}
            or url.get_backend_name() != "postgresql"
        ):
            raise RuntimeError("Frontend demo refuses non-local or non-test database access")
    owner = create_async_engine(
        settings.test_migration_database_url.get_secret_value(), hide_parameters=True
    )
    runtime = None
    engine = None
    try:
        counts = await seed_demo(
            async_sessionmaker(owner),
            as_of=datetime.now(UTC),
            demo_password="DemoOnly!2026",
        )
        configured = settings.model_copy(
            update={
                "app_env": "test",
                "database_url": SecretStr(settings.test_database_url.get_secret_value()),
                "analytics_database_url": settings.test_analytics_database_url,
                "data_dir": PROJECT_ROOT / "var" / "frontend-test",
                "rag_enabled": True,
                "rag_collection_prefix": "ea_frontend_test",
            }
        )
        engine = create_async_engine(
            configured.database_url.get_secret_value(), hide_parameters=True
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            administrator = await session.scalar(
                select(User).where(User.username == "demo_admin", User.is_active.is_(True))
            )
            if administrator is None or administrator.role != UserRole.ADMIN:
                raise RuntimeError("An enabled simulated administrator is required")
            actor = Principal(administrator.id, administrator.role)
        runtime = RagRuntime(configured)
        if not runtime.models.ready() or not await runtime.call(runtime.store.ready):
            raise RuntimeError("Prepared local models and real Chroma are required")
        service = KnowledgeService(factory, configured, runtime)
        documents = await service.list_documents(actor, limit=100)
        indexed = []
        for document in documents.items:
            result = await service.reindex(actor, document.id)
            if result.status != "indexed":
                raise RuntimeError("A real document index failed; browser checks cannot proceed")
            indexed.append(
                {"id": str(result.id), "title": result.title, "chunks": result.chunk_count}
            )
        summary = {
            "prepared_at_utc": datetime.now(UTC).isoformat(),
            "database": "enterprise_agent_test",
            "collection_prefix": "ea_frontend_test",
            "data_dir": "var/frontend-test",
            "reset_performed": False,
            "seed_counts": counts,
            "indexed_documents": indexed,
            "model_calls": 0,
        }
        output = PROJECT_ROOT / "var" / "test-results" / "phase7-browser-preparation.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps({"database": summary["database"], "indexed": len(indexed), "model_calls": 0})
        )
    finally:
        if runtime is not None:
            await runtime.close()
        if engine is not None:
            await engine.dispose()
        await owner.dispose()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(prepare())
