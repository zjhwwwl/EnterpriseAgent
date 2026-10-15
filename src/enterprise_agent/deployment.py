"""Container-only startup: bounded public settings and file-based credentials.

The ordinary API never receives the initialization or test account. Setup and
tests are separate short-lived containers, and model assets are supplied offline.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from enterprise_agent.application import create_app
from enterprise_agent.config import PROJECT_ROOT, Settings, get_settings
from enterprise_agent.database import Database
from enterprise_agent.logging_config import configure_logging
from enterprise_agent.models import KnowledgeDocument, User, UserRole
from enterprise_agent.rag.runtime import RagRuntime
from enterprise_agent.security import Principal
from enterprise_agent.seed import seed_demo
from enterprise_agent.services.knowledge import KnowledgeService

Mode = Literal["serve", "setup", "tests"]

_FORBIDDEN_INLINE = {
    "EA_DATABASE_URL",
    "EA_MIGRATION_DATABASE_URL",
    "EA_TEST_DATABASE_URL",
    "EA_TEST_MIGRATION_DATABASE_URL",
    "EA_ANALYTICS_DATABASE_URL",
    "EA_TEST_ANALYTICS_DATABASE_URL",
    "EA_REDIS_URL",
    "EA_JWT_SECRET",
    "EA_DEMO_PASSWORD",
    "EA_DEEPSEEK_API_KEY",
    "EA_LANGSMITH_API_KEY",
    "EA_MCP_ACCESS_TOKEN",
}
_PUBLIC_FIELDS = {
    "EA_LOG_LEVEL": "log_level",
    "EA_DOCS_ENABLED": "docs_enabled",
    "EA_RAG_MODEL_THREADS": "rag_model_threads",
    "EA_RAG_TIMEOUT_SECONDS": "rag_timeout_seconds",
    "EA_AGENT_TIMEOUT_SECONDS": "agent_timeout_seconds",
    "EA_AGENT_MODEL_TIMEOUT_SECONDS": "agent_model_timeout_seconds",
    "EA_DEEPSEEK_MODEL": "deepseek_model",
}
_SECRET_NAMES = {
    "postgres-app-password",
    "postgres-superuser-password",
    "postgres-test-password",
    "postgres-analytics-password",
    "postgres-test-analytics-password",
    "jwt-secret",
    "demo-password",
    "deepseek-api-key",
}


class DeploymentSettings(Settings):
    """Disable inherited .env, ambient environment and implicit secret sources."""

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)


def read_secret(secret_dir: Path, name: str, *, minimum: int = 16) -> SecretStr:
    """Only known, small UTF-8 files within the secret directory are accepted."""
    if name not in _SECRET_NAMES:
        raise ValueError("Unknown deployment secret")
    root = secret_dir.resolve()
    path = root / name
    try:
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError
        if path.stat().st_size > 4096:
            raise ValueError
        value = path.read_text(encoding="utf-8").strip()
        if len(value) < minimum or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"Deployment secret {name} is absent or invalid") from None
    return SecretStr(value)


def database_url(role: str, password: SecretStr, database: str) -> SecretStr:
    return SecretStr(
        URL.create(
            "postgresql+psycopg",
            username=role,
            password=password.get_secret_value(),
            host="postgres",
            port=5432,
            database=database,
        ).render_as_string(hide_password=False)
    )


def _flag(environment: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    value = environment.get(name, "true" if default else "false").lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def load_settings(
    mode: Mode, *, secret_dir: Path = Path("/run/secrets"), environment=None
) -> DeploymentSettings:
    """Load explicit public knobs; never fall back to a host .env or owner URL."""
    if mode not in {"serve", "setup", "tests"}:
        raise ValueError("Unknown deployment mode")
    environment = os.environ if environment is None else environment
    if any(environment.get(name) for name in _FORBIDDEN_INLINE):
        raise ValueError("Container credentials must be supplied through secret files")
    if _flag(environment, "EA_LANGSMITH_ENABLED"):
        raise ValueError("Remote tracing is disabled by this deployment profile")
    llm_enabled = _flag(environment, "EA_LLM_ENABLED")
    if mode != "serve" and llm_enabled:
        raise ValueError("Setup and test containers refuse external model calls")
    values = {
        "app_env": "production"
        if mode == "serve"
        else "test"
        if mode == "tests"
        else "development",
        "host": "0.0.0.0",
        "port": 8000,
        "data_dir": PROJECT_ROOT / "var" / "data",
        "rag_model_dir": PROJECT_ROOT / "var" / "models",
        "rag_enabled": True,
        "rag_collection_prefix": "ea_full_stack",
        "chroma_host": "chroma",
        "chroma_port": 8000,
        "redis_url": SecretStr("redis://redis:6379/0"),
        "jwt_secret": read_secret(secret_dir, "jwt-secret", minimum=32),
        "docs_enabled": False,
        "cors_origins": [],
        "analytics_enabled": True,
        "llm_enabled": llm_enabled,
        "langsmith_enabled": False,
    }
    for name, field in _PUBLIC_FIELDS.items():
        if name in environment:
            values[field] = environment[name]
    if llm_enabled:
        values["deepseek_api_key"] = read_secret(secret_dir, "deepseek-api-key")
    if mode == "tests":
        db_name = "enterprise_agent_test"
        values["database_url"] = database_url(
            "ea_test_app", read_secret(secret_dir, "postgres-test-password"), db_name
        )
        values["analytics_database_url"] = database_url(
            "ea_test_analytics_reader",
            read_secret(secret_dir, "postgres-test-analytics-password"),
            db_name,
        )
        values["test_database_url"] = values["database_url"]
        values["test_analytics_database_url"] = values["analytics_database_url"]
    else:
        db_name = "enterprise_agent"
        values["database_url"] = database_url(
            "ea_app", read_secret(secret_dir, "postgres-app-password"), db_name
        )
        values["analytics_database_url"] = database_url(
            "ea_analytics_reader", read_secret(secret_dir, "postgres-analytics-password"), db_name
        )
    if mode in {"setup", "tests"}:
        values["migration_database_url"] = database_url(
            "ea_local_admin", read_secret(secret_dir, "postgres-superuser-password"), db_name
        )
        if mode == "tests":
            values["test_migration_database_url"] = values["migration_database_url"]
        else:
            values["demo_password"] = read_secret(secret_dir, "demo-password", minimum=8)
    return DeploymentSettings(_env_file=None, **values)


async def setup(settings: DeploymentSettings, *, as_of: date) -> dict:
    """Migrate, append fictional records and index through the ordinary Service."""
    if settings.migration_database_url is None or settings.llm_enabled:
        raise ValueError("A model-free initialization account is required")
    owner = create_async_engine(
        settings.migration_database_url.get_secret_value(),
        hide_parameters=True,
        poolclass=NullPool,
    )
    database = Database(settings.database_url.get_secret_value())
    runtime = RagRuntime(settings)
    try:
        configuration = Config(str(PROJECT_ROOT / "alembic.ini"))

        def migrate(connection):
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")

        async with owner.begin() as connection:
            await connection.run_sync(migrate)
        # The shared seed guard requires an explicit non-production environment.
        os.environ["EA_APP_ENV"] = "development"
        get_settings.cache_clear()
        counts = await seed_demo(
            async_sessionmaker(owner, expire_on_commit=False),
            as_of=datetime.combine(as_of, time.min, tzinfo=UTC),
            seed=42,
            orders_count=500,
            demo_password=settings.demo_password.get_secret_value(),
        )
        if not await database.is_ready() or not await runtime.ready():
            raise ValueError("Migrated ordinary database, pinned models and Chroma must be ready")
        # This is real CPU execution, not only a manifest/health check.
        vectors = await runtime.call(runtime.models.embed_documents, ["平台退款政策"])
        scores = await runtime.call(runtime.models.rerank, "退款规则", ["退款申请需要审核"])
        if len(vectors) != 1 or len(vectors[0]) != 512 or len(scores) != 1:
            raise ValueError("CPU inference did not return the expected model outputs")
        async with database.session_factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.username == "demo_admin",
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            )
            if user is None:
                raise ValueError("An enabled synthetic administrator is required")
            actor = Principal(user.id, user.role)
            documents = list(
                await session.scalars(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.deleted_at.is_(None))
                    .order_by(KnowledgeDocument.id)
                )
            )
        service = KnowledgeService(database.session_factory, settings, runtime)
        indexed = []
        for document in documents:
            if document.index_generation and document.index_fingerprint == runtime.fingerprint:
                response = await service.get_document(actor, document.id)
            else:
                response = await service.reindex(actor, document.id)
            if response.status != "indexed" or response.chunk_count == 0:
                raise ValueError("A knowledge document is not indexed")
            indexed.append(
                {
                    "document_id": str(response.id),
                    "chunks": response.chunk_count,
                    "status": response.status,
                }
            )
        report = {
            "schema_version": 1,
            "prepared_at_utc": datetime.now(UTC).isoformat(),
            "database": "enterprise_agent",
            "as_of": as_of.isoformat(),
            "seed_counts": counts,
            "indexed_documents": indexed,
            "provider_requests": 0,
            "inference_provider": "CPUExecutionProvider",
            "embedding_dimension": 512,
            "model_assets_verified": True,
            "destructive_reset": False,
        }
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = settings.data_dir / "setup-report.json.tmp"
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(settings.data_dir / "setup-report.json")
        return report
    finally:
        await runtime.close()
        await database.close()
        await owner.dispose()


def run_tests(settings: DeploymentSettings, extra: list[str]) -> int:
    """Only the disposable test container exports owner/test credentials to pytest."""
    test_owner_url = settings.test_migration_database_url.get_secret_value()
    test_reader_url = settings.test_analytics_database_url.get_secret_value()
    environment = {name: value for name, value in os.environ.items() if not name.startswith("EA_")}
    environment.update(
        {
            "EA_APP_ENV": "test",
            "EA_DATABASE_URL": settings.database_url.get_secret_value(),
            "EA_MIGRATION_DATABASE_URL": settings.migration_database_url.get_secret_value(),
            "EA_ANALYTICS_DATABASE_URL": settings.analytics_database_url.get_secret_value(),
            "EA_TEST_DATABASE_URL": settings.test_database_url.get_secret_value(),
            "EA_TEST_MIGRATION_DATABASE_URL": test_owner_url,
            "EA_TEST_ANALYTICS_DATABASE_URL": test_reader_url,
            "EA_REDIS_URL": "redis://redis:6379/1",
            "EA_JWT_SECRET": settings.jwt_secret.get_secret_value(),
            "EA_LLM_ENABLED": "false",
            "EA_LANGSMITH_ENABLED": "false",
            "EA_RAG_MODEL_DIR": str(settings.rag_model_dir),
            "EA_CHROMA_HOST": "chroma",
            "EA_CHROMA_PORT": "8000",
            "EA_TEST_CHROMA_HOST": "chroma",
            "EA_TEST_CHROMA_PORT": "8000",
            "EA_TEST_CONTAINER_HOST": "postgres",
        }
    )
    (PROJECT_ROOT / "var" / "test-results").mkdir(parents=True, exist_ok=True)
    if extra and extra[0] in {"sh", "bash"}:
        arguments = extra
    else:
        arguments = [
            sys.executable,
            "-m",
            "pytest",
            "--require-postgres",
            "--require-rag",
            "--junitxml=var/test-results/phase9-linux.xml",
            *(extra or ["-q"]),
        ]
    return subprocess.run(arguments, cwd=PROJECT_ROOT, env=environment, check=False).returncode


def health() -> bool:
    try:
        with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
            response = client.get("http://127.0.0.1:8000/health/ready")
            result = response.json()
        return response.status_code == 200 and result.get("status") == "ok"
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=["serve", "setup", "tests", "health", "rag-evaluate"])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 10, 12))
    parser.add_argument("--output", type=Path)
    arguments, extra = parser.parse_known_args()
    if extra and extra[0] == "--":
        extra = extra[1:]
    if arguments.mode != "tests" and extra:
        parser.error("Unknown deployment arguments")
    if arguments.output is not None and arguments.mode != "rag-evaluate":
        parser.error("--output is only supported for rag-evaluate")
    if arguments.mode == "health":
        raise SystemExit(0 if health() else 1)
    try:
        environment = dict(os.environ)
        if arguments.mode == "rag-evaluate":
            # Retrieval evaluates local ONNX models and Chroma only. Even an API
            # configured for chat does not load its provider key for this command.
            environment["EA_LLM_ENABLED"] = "false"
        settings = load_settings(
            "serve" if arguments.mode == "rag-evaluate" else arguments.mode,
            environment=environment,
        )
        configure_logging(settings.log_level, secret_values=settings.secret_values())
        if arguments.mode == "setup":
            report = asyncio.run(setup(settings, as_of=arguments.as_of))
            print(json.dumps(report, ensure_ascii=False), flush=True)
        elif arguments.mode == "tests":
            raise SystemExit(run_tests(settings, extra))
        elif arguments.mode == "rag-evaluate":
            from enterprise_agent.evaluation import run_evaluation

            output = arguments.output or (
                settings.data_dir
                / "evaluation"
                / "phase9"
                / ("rag-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"))
            )
            success = asyncio.run(
                run_evaluation(
                    settings,
                    dataset_path=PROJECT_ROOT / "evals" / "datasets" / "rag.json",
                    output=output,
                    top_k=5,
                )
            )
            raise SystemExit(0 if success else 1)
        else:
            uvicorn.run(
                create_app(settings),
                host=str(settings.host),
                port=settings.port,
                log_config=None,
                access_log=False,
                proxy_headers=False,
            )
    except (ValueError, ValidationError):
        parser.exit(2, "Container configuration refused; check public settings and secret files\n")
    except Exception as error:
        parser.exit(
            1,
            f"Container execution failed ({type(error).__name__}); "
            "verify dependencies and initialization state\n",
        )


if __name__ == "__main__":
    main()
