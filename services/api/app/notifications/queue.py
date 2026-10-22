"""Queue-event notification intent creation and retryable delivery.

Messages go only to visits whose patient ticked the SMS box themselves, which records a
granted, unwithdrawn ``sms`` consent. The worker reads the visit's own copy of the number,
encrypted with ``NOTIFY_KEY``; it never holds the patient-data key.
"""

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.core.crypto import decrypt_field
from app.core.ids import uuid7
from app.notifications.providers import NotificationProvider, render_message

logger = structlog.get_logger(__name__)
RETRY_DELAYS = (1, 5, 30)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
HOURLY_RECIPIENT_LIMIT = 5


def templates_for_position(position: int) -> tuple[str, ...]:
    """Return the one-time queue-position template at an exact threshold."""
    return {6: ("five_ahead",), 3: ("two_ahead",), 1: ("your_turn",)}.get(position, ())


def retry_delay_seconds(failed_attempts: int) -> int | None:
    """Return bounded retry delays; the fourth failed attempt enters dead letter."""
    if failed_attempts < 1:
        raise ValueError("failed_attempts must be positive")
    if failed_attempts >= MAX_ATTEMPTS:
        return None
    return RETRY_DELAYS[failed_attempts - 1]


async def _queue_rows(
    connection: AsyncConnection, hospital_id: UUID, visit_ids: list[UUID]
) -> list[dict[str, Any]]:
    if not visit_ids:
        return []
    result = await connection.execute(
        text(
            "SELECT v.id, v.token_label, v.language, v.sms_opt_in, "
            "v.notify_phone_hmac AS phone_hmac, "
            "(c.id IS NOT NULL) AS sms_consented FROM visits v LEFT JOIN consents c "
            "ON c.hospital_id = v.hospital_id AND c.visit_id = v.id AND c.purpose = 'sms' "
            "AND c.granted AND c.withdrawn_at IS NULL WHERE v.hospital_id = :hospital_id "
            "AND v.id = ANY(:visit_ids) AND v.status = 'queued'"
        ),
        {"hospital_id": hospital_id, "visit_ids": visit_ids},
    )
    return [dict(row) for row in result.mappings().all()]


async def _record_intent(
    connection: AsyncConnection,
    *,
    hospital_id: UUID,
    visit: dict[str, Any],
    template: str,
    settings: Settings,
) -> None:
    if (
        visit["sms_opt_in"] is not True
        or visit.get("sms_consented") is not True
        or not visit["phone_hmac"]
    ):
        return
    # Serialize phone-level cap checks so parallel queue events cannot exceed the limit.
    recipient_hmac = str(visit["phone_hmac"])
    await connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": f"notification:{hospital_id}:{recipient_hmac}"},
    )
    count = await connection.scalar(
        text(
            "SELECT count(*) FROM notifications WHERE hospital_id = :hospital_id "
            "AND recipient_hmac = :recipient_hmac AND created_at >= now() - interval '1 hour' "
            "AND status IN ('pending', 'sending', 'sent')"
        ),
        {"hospital_id": hospital_id, "recipient_hmac": recipient_hmac},
    )
    capped = int(count or 0) >= HOURLY_RECIPIENT_LIMIT
    await connection.execute(
        text(
            "INSERT INTO notifications (id, hospital_id, visit_id, channel, template, status, "
            "dedupe_key, recipient_hmac, last_error_code, next_attempt_at) VALUES "
            "(:id, :hospital_id, :visit_id, :channel, :template, :status, :dedupe_key, "
            ":recipient_hmac, :last_error_code, CASE WHEN :capped THEN NULL ELSE now() END) "
            "ON CONFLICT (hospital_id, dedupe_key) DO NOTHING"
        ),
        {
            "id": uuid7(),
            "hospital_id": hospital_id,
            "visit_id": visit["id"],
            "channel": {"twilio": "sms", "whatsapp": "whatsapp"}.get(
                settings.notification_provider, "console"
            ),
            "template": template,
            "status": "failed" if capped else "pending",
            "dedupe_key": f"{visit['id']}:{template}",
            "recipient_hmac": recipient_hmac,
            "last_error_code": "hourly_limit" if capped else None,
            "capped": capped,
        },
    )


async def record_queue_event_intents(
    connection: AsyncConnection,
    queue_client: Any,
    *,
    hospital_id: UUID,
    department_id: UUID,
    settings: Settings,
    token_issued_visit_id: UUID | None = None,
) -> int:
    """Persist intents for token issue and queue rank thresholds after queue mutation."""
    from app.domain.queue.redis_queue import QueueRedis

    queue = QueueRedis(queue_client, app_env=settings.app_env)
    members = await queue.members(
        hospital_id=hospital_id, department_id=department_id, start=0, stop=5
    )
    positions = [(UUID(member), rank + 1) for rank, (member, _) in enumerate(members)]
    rows = await _queue_rows(connection, hospital_id, [visit_id for visit_id, _ in positions])
    by_id = {row["id"]: row for row in rows}
    created = 0
    if token_issued_visit_id is not None:
        token_row = await _queue_rows(connection, hospital_id, [token_issued_visit_id])
        if token_row:
            await _record_intent(
                connection,
                hospital_id=hospital_id,
                visit=token_row[0],
                template="token_issued",
                settings=settings,
            )
            created += 1
    for visit_id, position in positions:
        row = by_id.get(visit_id)
        if row is None:
            continue
        for template in templates_for_position(position):
            await _record_intent(
                connection,
                hospital_id=hospital_id,
                visit=row,
                template=template,
                settings=settings,
            )
            created += 1
    return created


async def _mark(
    connection: AsyncConnection, row: Any, *, status: str, error_code: str | None
) -> None:
    await connection.execute(
        text(
            "UPDATE notifications SET status = :status, last_error_code = :error_code, "
            "attempt_count = attempt_count + 1, next_attempt_at = NULL, updated_at = now() "
            "WHERE id = :id AND hospital_id = :hospital_id"
        ),
        {
            "status": status,
            "error_code": error_code,
            "id": row["id"],
            "hospital_id": row["hospital_id"],
        },
    )


async def _deliver_one(
    connection: AsyncConnection,
    row: Any,
    *,
    settings: Settings,
    provider: NotificationProvider,
) -> bool:
    """Send one message; True when sent. Problems with this row stay with this row."""
    try:
        body = render_message(row["template"], row["language"], row["token_label"])
    except ValueError:
        await _mark(connection, row, status="failed", error_code="bad_template")
        return False
    if provider.channel == "console":
        # The console provider prints the text only, so the number is never decrypted.
        phone = ""
    else:
        notify_key = settings.notify_key_bytes
        if notify_key is None or not row["notify_phone_enc"]:
            await _mark(connection, row, status="failed", error_code="no_phone")
            return False
        try:
            phone = decrypt_field(
                row["notify_phone_enc"],
                key=notify_key,
                associated_data=f"visits:notify_phone_enc:{row['visit_id']}",
            )
        except ValueError:
            await _mark(connection, row, status="failed", error_code="phone_unreadable")
            return False
    try:
        provider_id = await provider.send(phone=phone, body=body)
    except Exception as exc:
        attempts = int(row["attempt_count"]) + 1
        delay = retry_delay_seconds(attempts)
        await connection.execute(
            text(
                "UPDATE notifications SET attempt_count = :attempts, status = :status, "
                "next_attempt_at = CASE WHEN :delay IS NULL THEN NULL "
                "ELSE now() + (:delay * interval '1 second') END, "
                "last_error_code = 'provider_error', updated_at = now() "
                "WHERE id = :id AND hospital_id = :hospital_id"
            ),
            {
                "attempts": attempts,
                "status": "pending" if delay is not None else "failed",
                "delay": delay,
                "id": row["id"],
                "hospital_id": row["hospital_id"],
            },
        )
        logger.warning(
            "notification_delivery_retry",
            notification_id=str(row["id"]),
            failed_attempts=attempts,
            retry_seconds=delay,
            error_type=type(exc).__name__,
        )
        return False
    await connection.execute(
        text(
            "UPDATE notifications SET status = 'sent', provider_message_id = :provider_id, "
            "attempt_count = attempt_count + 1, next_attempt_at = NULL, "
            "last_error_code = NULL, updated_at = now() "
            "WHERE id = :id AND hospital_id = :hospital_id"
        ),
        {"provider_id": provider_id, "id": row["id"], "hospital_id": row["hospital_id"]},
    )
    return True


async def deliver_pending_once(
    connection: AsyncConnection,
    *,
    settings: Settings,
    provider: NotificationProvider,
    limit: int = 100,
) -> tuple[int, int]:
    """Deliver due records and persist bounded retries without logging phone data.

    Each row runs in its own savepoint: one unreadable or poisoned row is marked failed (or
    rolled back and retried next tick) and never stops the rest of the batch.
    """
    rows = (
        (
            await connection.execute(
                text(
                    "SELECT n.id, n.hospital_id, n.visit_id, n.template, n.attempt_count, "
                    "v.language, v.token_label, v.notify_phone_enc "
                    "FROM notifications n JOIN visits v ON v.hospital_id = n.hospital_id "
                    "AND v.id = n.visit_id JOIN consents c ON c.hospital_id = v.hospital_id "
                    "AND c.visit_id = v.id AND c.purpose = 'sms' AND c.granted "
                    "AND c.withdrawn_at IS NULL WHERE n.status = 'pending' "
                    "AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= now()) "
                    "AND v.sms_opt_in = true ORDER BY n.created_at, n.id LIMIT :limit "
                    "FOR UPDATE OF n SKIP LOCKED"
                ),
                {"limit": limit},
            )
        )
        .mappings()
        .all()
    )
    sent = failed = 0
    for row in rows:
        try:
            async with connection.begin_nested():
                delivered = await _deliver_one(
                    connection, row, settings=settings, provider=provider
                )
        except Exception as exc:
            logger.warning(
                "notification_row_failed",
                notification_id=str(row["id"]),
                error_type=type(exc).__name__,
            )
            failed += 1
            continue
        if delivered:
            sent += 1
        else:
            failed += 1
    return sent, failed
