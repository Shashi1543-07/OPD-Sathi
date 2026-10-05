"""Queue outbox relay and Redis read-model repair worker."""

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import redis.asyncio as redis
import structlog
from arq import cron
from arq.connections import RedisSettings
from arq.worker import run_worker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.config import get_settings
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import queue_score

logger = structlog.get_logger(__name__)


async def _visit(
    connection: AsyncConnection, hospital_id: UUID, visit_id: UUID
) -> dict[str, Any] | None:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT id AS visit_id, hospital_id, department_id, status, urgency, "
                    "age_years, pregnant, token_label, queued_at FROM visits "
                    "WHERE hospital_id = :hospital_id AND id = :visit_id"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


def _score(row: dict[str, Any], settings: Any) -> int:
    arrival = row["queued_at"] or datetime.now(UTC)
    return queue_score(
        int(arrival.timestamp() * 1000),
        row["urgency"],
        row["age_years"],
        bool(row["pregnant"]),
        urgent_bonus_min=settings.urgent_bonus_min,
        priority_bonus_min=settings.priority_bonus_min,
        vulnerable_bonus_min=settings.vulnerable_bonus_min,
    )


async def relay_once(connection: AsyncConnection, client: Any, *, settings: Any) -> int:
    """Apply up to 100 pending queue outbox events and mark only successful ones."""
    events = (
        (
            await connection.execute(
                text(
                    "SELECT id, hospital_id, payload FROM outbox WHERE topic = 'queue.changed' "
                    "AND processed_at IS NULL ORDER BY created_at, id LIMIT 100 "
                    "FOR UPDATE SKIP LOCKED"
                )
            )
        )
        .mappings()
        .all()
    )
    queue = QueueRedis(client, app_env=settings.app_env)
    processed = 0
    for event in events:
        event_id = event["id"]
        hospital_id = event["hospital_id"]
        payload = event["payload"]
        try:
            visit_id = UUID(payload["visit_id"])
            row = await _visit(connection, hospital_id, visit_id)
            if payload.get("op") == "remove" or row is None or row["status"] != "queued":
                department_id = (
                    row["department_id"] if row is not None else UUID(payload["department_id"])
                )
                await queue.remove(
                    hospital_id=hospital_id,
                    department_id=department_id,
                    visit_id=visit_id,
                )
                if row is not None and row["status"] in {"called", "in_consult"}:
                    await queue.publish_delta(
                        hospital_id=hospital_id,
                        department_id=row["department_id"],
                        ops=[
                            {
                                "op": "upsert",
                                "item": {
                                    "visit_id": str(visit_id),
                                    "token": row["token_label"],
                                    "urgency": row["urgency"],
                                    "status": row["status"],
                                },
                            }
                        ],
                    )
            else:
                await queue.enqueue(
                    hospital_id=hospital_id,
                    department_id=row["department_id"],
                    visit_id=visit_id,
                    score=_score(row, settings),
                    item={
                        "visit_id": str(visit_id),
                        "token": row["token_label"],
                        "urgency": row["urgency"],
                        "status": row["status"],
                    },
                )
            await queue.publish_status(
                hospital_id=hospital_id,
                visit_id=visit_id,
                payload={"changed": True},
            )
            await connection.execute(
                text("UPDATE outbox SET processed_at = now() WHERE id = :id"), {"id": event_id}
            )
            processed += 1
        except Exception:
            logger.exception("queue_outbox_retry", outbox_id=str(event_id))
            await connection.execute(
                text("UPDATE outbox SET attempts = attempts + 1 WHERE id = :id"), {"id": event_id}
            )
    return processed


async def rebuild_all(connection: AsyncConnection, client: Any, *, settings: Any) -> int:
    """Rebuild every active hospital/department sorted set from durable visits."""
    queue = QueueRedis(client, app_env=settings.app_env)
    old_keys = await client.smembers(queue.registry_key())
    queue_keys = [key for key in old_keys if key != "ready"]
    delete_keys = [*queue_keys, *(key.replace(":q:", ":qseq:") for key in queue_keys)]
    if delete_keys:
        await client.delete(*delete_keys)
    await client.delete(queue.registry_key())
    rows = (
        (
            await connection.execute(
                text(
                    "SELECT id AS visit_id, hospital_id, department_id, status, "
                    "urgency, age_years, "
                    "pregnant, token_label, queued_at FROM visits WHERE status = 'queued' "
                    "ORDER BY hospital_id, "
                    "department_id, queued_at, id"
                )
            )
        )
        .mappings()
        .all()
    )
    groups: dict[tuple[UUID, UUID], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["hospital_id"], row["department_id"]), []).append(dict(row))
    for (hospital_id, department_id), visits in groups.items():
        queue_key, _, sequence_key = queue.keys(hospital_id, department_id)
        await client.delete(queue_key, sequence_key)
        for queue_row in visits:
            await queue.enqueue(
                hospital_id=hospital_id,
                department_id=department_id,
                visit_id=queue_row["visit_id"],
                score=_score(queue_row, settings),
                item={
                    "visit_id": str(queue_row["visit_id"]),
                    "token": queue_row["token_label"],
                    "urgency": queue_row["urgency"],
                    "status": queue_row["status"],
                },
            )
    await client.sadd(queue.registry_key(), "ready")
    return len(rows)


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    database_url = settings.worker_database_url or settings.database_url
    engine = create_async_engine(database_url, pool_pre_ping=True)
    client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_state_url, decode_responses=True
    )
    ctx.update(settings=settings, engine=engine, redis=client)
    registry_key = QueueRedis(client, app_env=settings.app_env).registry_key()
    if not await client.sismember(registry_key, "ready"):
        lock_key = f"osa:{settings.app_env}:qrebuild-lock"
        acquired = await client.set(lock_key, "worker-startup", nx=True, ex=60)
        if acquired:
            try:
                if not await client.sismember(registry_key, "ready"):
                    async with engine.begin() as connection:
                        await rebuild_all(connection, client, settings=settings)
            finally:
                await client.delete(lock_key)
        else:
            for _ in range(100):
                if await client.sismember(registry_key, "ready"):
                    break
                await asyncio.sleep(0.1)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


async def relay_queue_outbox(ctx: dict[str, Any]) -> None:
    async with ctx["engine"].begin() as connection:
        count = await relay_once(connection, ctx["redis"], settings=ctx["settings"])
    if count:
        logger.info("queue_outbox_processed", count=count)


class WorkerSettings:
    """ARQ worker configuration; cron ticks on every even second."""

    functions = [relay_queue_outbox]
    cron_jobs = [cron(relay_queue_outbox, second=set(range(0, 60, 2)))]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_state_url)


if __name__ == "__main__":
    run_worker(cast(Any, WorkerSettings))
