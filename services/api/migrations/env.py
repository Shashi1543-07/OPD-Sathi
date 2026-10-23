"""Async Alembic environment driven by the application database URL."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _database_url(default: str | None) -> str | None:
    """The migrator's URL: from <NAME>_FILE (production secrets), else the variable itself.

    Production blanks the plain variables and mounts the URLs as files (task 7.3.7).
    """
    for name in ("MIGRATIONS_DATABASE_URL", "DATABASE_URL"):
        reference = os.getenv(f"{name}_FILE", "")
        if reference:
            with open(reference, encoding="utf-8") as handle:
                return handle.read().strip()
        if os.getenv(name):
            return os.environ[name]
    return default


def run_migrations_offline() -> None:
    url = _database_url(config.get_main_option("sqlalchemy.url"))
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url(section["sqlalchemy.url"]) or ""
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
