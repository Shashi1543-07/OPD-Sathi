"""Queue outbox relay and Redis read-model repair worker."""

import asyncio
import functools
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import redis.asyncio as redis
import structlog
from arq import cron
from arq.connections import RedisSettings
from arq.worker import run_worker
from prometheus_client import start_http_server
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.db.session import create_engine
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import queue_score
from app.notifications.providers import build_notification_provider
from app.notifications.queue import deliver_pending_once, record_queue_event_intents
from app.workers import monitoring
from app.workers.health import (
    STALE_AFTER_SECONDS,
    SYSTEM_STATUS_TTL,
    heartbeat_key,
    system_status_key,
)
from app.workers.metrics import worker_metrics

logger = structlog.get_logger(__name__)


async def _visit(
    connection: AsyncConnection, hospital_id: UUID, visit_id: UUID
) -> dict[str, Any] | None:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT id AS visit_id, hospital_id, department_id, status, urgency, "
                    "age_years, pregnant, token_label, queued_at, acuity_score FROM visits "
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
        acuity_score=row.get("acuity_score"),
    )


async def relay_once(connection: AsyncConnection, client: Any, *, settings: Any) -> int:
    """Apply up to 100 pending queue outbox events and mark only successful ones.

    A failing event is rolled back to its savepoint, counted as an attempt and retried on the
    next tick; the events after it still go through.
    """
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
            # A savepoint per event: a database error here must not abort the batch.
            async with connection.begin_nested():
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
                department_id = (
                    row["department_id"] if row is not None else UUID(payload["department_id"])
                )
                await record_queue_event_intents(
                    connection,
                    client,
                    hospital_id=hospital_id,
                    department_id=department_id,
                    settings=settings,
                    token_issued_visit_id=visit_id if payload.get("notify_token") is True else None,
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


# (open state on an earlier day, what it becomes at close-out)
CLOSE_OUT = (
    # Still waiting when the OPD closed: not seen, but not absent either.
    ("queued", "cancelled"),
    # Called, or called and missed, and never seen.
    ("called", "no_show"),
    ("skipped", "no_show"),
    ("in_consult", "done"),
    ("registering", "cancelled"),
    ("awaiting_confirmation", "cancelled"),
)


async def close_previous_days(connection: AsyncConnection) -> int:
    """End-of-day close-out: earlier days' open visits must not stay in today's queue.

    Every visit closed this way gets a ``day_closed`` line on its timeline, so staff can tell
    a closed-out visit from one a person ended.
    """
    closed = 0
    for source, target in CLOSE_OUT:
        count = await connection.scalar(
            text(
                "WITH closed AS (UPDATE visits SET status = :target "
                "WHERE service_date < (now() AT TIME ZONE 'Asia/Kolkata')::date "
                "AND status = :source RETURNING id, hospital_id), "
                "events AS (INSERT INTO visit_events (id, hospital_id, visit_id, from_status, "
                "to_status, actor_type, reason) SELECT gen_random_uuid(), hospital_id, id, "
                ":source, :target, 'system', 'day_closed' FROM closed) "
                "SELECT count(*) FROM closed"
            ),
            {"source": source, "target": target},
        )
        closed += int(count or 0)
    # The number kept for queue messages serves only the day it was given for.
    await connection.execute(
        text(
            "UPDATE visits SET notify_phone_enc = NULL, notify_phone_hmac = NULL "
            "WHERE service_date < (now() AT TIME ZONE 'Asia/Kolkata')::date "
            "AND (notify_phone_enc IS NOT NULL OR notify_phone_hmac IS NOT NULL)"
        )
    )
    return closed


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
                    "pregnant, token_label, queued_at, acuity_score FROM visits "
                    "WHERE status = 'queued' "
                    "AND service_date >= (now() AT TIME ZONE 'Asia/Kolkata')::date "
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
    if settings.metrics_enabled:
        # Scraped by Prometheus on the private network only; nothing publishes this port.
        start_http_server(settings.worker_metrics_port)
    database_url = settings.worker_database_url or settings.database_url
    engine = create_engine(database_url)
    client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_state_url, decode_responses=True
    )
    ctx.update(settings=settings, engine=engine, redis=client)
    ctx["notification_provider"] = build_notification_provider(settings)
    registry_key = QueueRedis(client, app_env=settings.app_env).registry_key()
    try:
        async with engine.begin() as connection:
            if await close_previous_days(connection):
                await client.srem(registry_key, "ready")
    except Exception as exc:
        logger.warning("day_close_out_failed", error_type=type(exc).__name__)
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


def _measured(job: Callable[[dict[str, Any]], Awaitable[None]]) -> Any:
    """Count each run of a worker job by outcome and record its last success."""

    @functools.wraps(job)
    async def run(ctx: dict[str, Any]) -> None:
        try:
            await job(ctx)
        except Exception:
            worker_metrics().jobs.labels(job.__name__, "failed").inc()
            raise
        worker_metrics().jobs.labels(job.__name__, "succeeded").inc()
        worker_metrics().last_success.labels(job.__name__).set_to_current_time()

    return run


async def collect_operational_metrics(connection: AsyncConnection) -> None:
    """Gauges for the alerts: patients waiting, a stuck outbox, unsent messages, urgent
    alerts no one has acknowledged. Counts and ages only, never patient data."""
    waiting = (
        await connection.execute(
            text(
                "SELECT v.hospital_id::text AS hospital, d.code AS department, count(*) AS n "
                "FROM visits v JOIN departments d "
                "ON d.hospital_id = v.hospital_id AND d.id = v.department_id "
                "WHERE v.status = 'queued' "
                "AND v.service_date >= (now() AT TIME ZONE 'Asia/Kolkata')::date "
                "GROUP BY 1, 2"
            )
        )
    ).mappings()
    worker_metrics().queue_waiting.clear()
    for row in waiting:
        worker_metrics().queue_waiting.labels(row["hospital"], row["department"]).set(row["n"])
    outbox_age = await connection.scalar(
        text(
            "SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0) FROM outbox "
            "WHERE topic = 'queue.changed' AND processed_at IS NULL"
        )
    )
    worker_metrics().outbox_oldest_age.set(float(outbox_age or 0))
    pending = await connection.scalar(
        text("SELECT count(*) FROM notifications WHERE status = 'pending'")
    )
    worker_metrics().notifications_pending.set(float(pending or 0))
    alert_age = await connection.scalar(
        text(
            "SELECT coalesce(extract(epoch FROM now() - min(created_at)), 0) "
            "FROM red_flag_events WHERE level = 'urgent' AND acknowledged_at IS NULL "
            "AND (created_at AT TIME ZONE 'Asia/Kolkata')::date "
            ">= (now() AT TIME ZONE 'Asia/Kolkata')::date"
        )
    )
    worker_metrics().urgent_alerts_open_age.set(float(alert_age or 0))
    # Paired kiosks (not phone check-in posters) silent for 10 minutes; the kiosk app
    # checks in every minute while it is open.
    silent = (
        await connection.execute(
            text(
                "SELECT hospital_id::text AS hospital, count(*) FILTER ("
                "WHERE last_seen_at IS NULL OR last_seen_at < now() - interval '10 minutes'"
                ") AS n FROM kiosks WHERE kind = 'device' AND is_active "
                "AND paired_at IS NOT NULL AND deleted_at IS NULL GROUP BY 1"
            )
        )
    ).mappings()
    worker_metrics().kiosks_silent.clear()
    for row in silent:
        worker_metrics().kiosks_silent.labels(row["hospital"]).set(row["n"])


@_measured
async def refresh_operational_metrics(ctx: dict[str, Any]) -> None:
    """Alert gauges, and the summary Settings -> System shows (system_status_key)."""
    settings = ctx["settings"]
    gauges = worker_metrics()
    snapshot: dict[str, Any] = {"checked_at": time.time()}
    try:
        await ctx["redis"].ping()
        snapshot["redis"] = True
    except Exception:
        snapshot["redis"] = False
    gauges.dependency_up.labels("redis").set(1 if snapshot["redis"] else 0)
    probes: list[tuple[str, str, Any, Callable[[str], float | None]]] = [
        ("disk_free_ratio", settings.monitor_disk_path, gauges.disk_free_ratio,
         monitoring.disk_free_ratio),
        ("certificate_expires_at", settings.monitor_tls_address, gauges.tls_cert_expiry,
         monitoring.certificate_expiry),
        ("backup_last_success", settings.backup_status_file, gauges.backup_last_success,
         monitoring.backup_last_success),
    ]
    for name, target, gauge, probe in probes:
        value = await asyncio.to_thread(probe, target) if target else None
        snapshot[name] = value
        if value is not None:
            gauge.set(value)
    try:
        async with ctx["engine"].begin() as connection:
            await collect_operational_metrics(connection)
        snapshot["postgres"] = True
    except Exception:
        snapshot["postgres"] = False
        raise
    finally:
        gauges.dependency_up.labels("postgres").set(1 if snapshot["postgres"] else 0)
        if snapshot["redis"]:
            await ctx["redis"].set(
                system_status_key(settings.app_env), json.dumps(snapshot), ex=SYSTEM_STATUS_TTL
            )


@_measured
async def relay_queue_outbox(ctx: dict[str, Any]) -> None:
    try:
        async with ctx["engine"].begin() as connection:
            count = await relay_once(connection, ctx["redis"], settings=ctx["settings"])
        async with ctx["engine"].begin() as connection:
            sent, failed = await deliver_pending_once(
                connection,
                settings=ctx["settings"],
                provider=ctx["notification_provider"],
            )
    finally:
        # The container health check (app.workers.health) reads this: a tick that ran,
        # even one that failed, shows the worker's loop is alive.
        await ctx["redis"].set(
            heartbeat_key(ctx["settings"].app_env), str(time.time()), ex=STALE_AFTER_SECONDS * 5
        )
    if count:
        logger.info("queue_outbox_processed", count=count)
    if sent or failed:
        worker_metrics().notifications.labels("sent").inc(sent)
        worker_metrics().notifications.labels("failed").inc(failed)
        logger.info("notifications_processed", sent=sent, failed=failed)


@_measured
async def refresh_analytics(ctx: dict[str, Any]) -> None:
    """Refresh the daily materialized rollup owned by the restricted worker schema."""
    async with ctx["engine"].begin() as connection:
        await connection.execute(text("SET ROLE osa_analytics_worker"))
        await connection.execute(
            text("REFRESH MATERIALIZED VIEW CONCURRENTLY osa_analytics.analytics_daily_department")
        )
        await connection.execute(text("RESET ROLE"))
    logger.info("analytics_materialized_view_refreshed")


@_measured
async def close_out_previous_days(ctx: dict[str, Any]) -> None:
    """Periodic close-out so the queue resets after midnight without a restart."""
    async with ctx["engine"].begin() as connection:
        closed = await close_previous_days(connection)
        if closed:
            await rebuild_all(connection, ctx["redis"], settings=ctx["settings"])
            logger.info("previous_day_visits_closed", count=closed)


class WorkerSettings:
    """ARQ worker configuration; cron ticks on every even second."""

    functions = [relay_queue_outbox]
    cron_jobs = [
        cron(relay_queue_outbox, second=set(range(0, 60, 2))),
        cron(refresh_analytics, minute=set(range(0, 60, 5))),
        cron(close_out_previous_days, minute={1, 16, 31, 46}),
        cron(refresh_operational_metrics, second={15, 45}),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_state_url)


if __name__ == "__main__":
    run_worker(cast(Any, WorkerSettings))
