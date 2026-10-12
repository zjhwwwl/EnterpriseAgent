"""Alembic execution using a credential supplied by validated environment settings."""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from enterprise_agent.config import get_settings
from enterprise_agent.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> URL:
    settings = get_settings()
    credential = settings.migration_database_url or settings.database_url
    if credential is None:
        raise RuntimeError("Set EA_MIGRATION_DATABASE_URL or EA_DATABASE_URL before migration")
    try:
        parsed = make_url(credential.get_secret_value())
    except Exception:
        raise RuntimeError("Migration database URL is invalid") from None
    if parsed.get_backend_name() != "postgresql":
        raise RuntimeError("Migrations require PostgreSQL; SQLite is not an accepted substitute")
    return parsed.set(drivername="postgresql+psycopg")


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    except Exception:
        # A driver traceback can include connection credentials or SQL parameter values.
        raise RuntimeError(
            "PostgreSQL migration failed; verify connectivity, credentials and migration state"
        ) from None
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        do_run_migrations(existing_connection)
    else:
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
