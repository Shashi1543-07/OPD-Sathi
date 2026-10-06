"""Queue-event notification intent creation and retryable delivery."""

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
            "SELECT v.id, v.patient_id, v.token_label, v.language, v.sms_opt_in, "
            "p.phone_hmac FROM visits v JOIN patients p ON p.hospital_id = v.hospital_id "
            "AND p.id = v.patient_id WHERE v.hospital_id = :hospital_id "
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
    if visit["sms_opt_in"] is not True or not visit["phone_hmac"]:
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


async def deliver_pending_once(
    connection: AsyncConnection,
    *,
    settings: Settings,
    provider: NotificationProvider,
    limit: int = 100,
) -> tuple[int, int]:
    """Deliver due records and persist bounded retries without logging phone data."""
    rows = (
        (
            await connection.execute(
                text(
                    "SELECT n.id, n.hospital_id, n.visit_id, n.template, n.attempt_count, "
                    "n.recipient_hmac, v.patient_id, v.language, v.token_label, p.phone_enc "
                    "FROM notifications n JOIN visits v ON v.hospital_id = n.hospital_id "
                    "AND v.id = n.visit_id JOIN patients p ON p.hospital_id = v.hospital_id "
                    "AND p.id = v.patient_id WHERE n.status = 'pending' "
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
        phone = decrypt_field(
            row["phone_enc"],
            key=settings.app_data_key_bytes,
            associated_data=f"patients:phone_enc:{row['patient_id']}",
        )
        if not phone:
            await connection.execute(
                text(
                    "UPDATE notifications SET status = 'failed', last_error_code = 'no_phone', "
                    "updated_at = now() WHERE id = :id AND hospital_id = :hospital_id"
                ),
                {"id": row["id"], "hospital_id": row["hospital_id"]},
            )
            failed += 1
            continue
        body = render_message(row["template"], row["language"], row["token_label"])
        try:
            provider_id = await provider.send(phone=phone, body=body)
        except Exception as exc:
            attempts = int(row["attempt_count"]) + 1
            delay = retry_delay_seconds(attempts)
            status = "pending" if delay is not None else "failed"
            error_code = "provider_error"
            await connection.execute(
                text(
                    "UPDATE notifications SET attempt_count = :attempts, status = :status, "
                    "next_attempt_at = CASE WHEN :delay IS NULL THEN NULL "
                    "ELSE now() + (:delay * interval '1 second') END, "
                    "last_error_code = :error_code, updated_at = now() "
                    "WHERE id = :id AND hospital_id = :hospital_id"
                ),
                {
                    "attempts": attempts,
                    "status": status,
                    "delay": delay,
                    "error_code": error_code,
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
            failed += 1
            continue
        await connection.execute(
            text(
                "UPDATE notifications SET status = 'sent', provider_message_id = :provider_id, "
                "attempt_count = attempt_count + 1, next_attempt_at = NULL, "
                "last_error_code = NULL, updated_at = now() "
                "WHERE id = :id AND hospital_id = :hospital_id"
            ),
            {
                "provider_id": provider_id,
                "id": row["id"],
                "hospital_id": row["hospital_id"],
            },
        )
        sent += 1
    return sent, failed
