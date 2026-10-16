"""Async SQLAlchemy engine lifecycle, health probe, and tenant scope."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"statement_timeout": "5000"}},
    )


async def ping_database(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def set_hospital_scope(connection: AsyncConnection, hospital_id: UUID) -> None:
    """Set PostgreSQL RLS scope for this transaction only."""
    await connection.execute(
        text("SELECT set_config('app.hospital_id', :hospital_id, true)"),
        {"hospital_id": str(hospital_id)},
    )


async def set_secret_lookup_scope(
    connection: AsyncConnection, *, setting: str, secret_hash: str
) -> None:
    """Allow one constant-time secret-hash lookup before tenant scope is known."""
    if setting not in {
        "app.refresh_token_hash",
        "app.kiosk_activation_hash",
        "app.kiosk_secret_hash",
        "app.kiosk_checkin_hash",
        "app.status_token_hash",
    }:
        raise ValueError("unsupported secret lookup scope")
    await connection.execute(
        text("SELECT set_config(:setting, :secret_hash, true)"),
        {"setting": setting, "secret_hash": secret_hash},
    )


@asynccontextmanager
async def tenant_transaction(
    engine: AsyncEngine, hospital_id: UUID
) -> AsyncIterator[AsyncConnection]:
    """Open a transaction whose tenant scope is cleared automatically at commit."""
    async with engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        yield connection
