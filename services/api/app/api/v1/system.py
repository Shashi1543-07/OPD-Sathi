"""Settings -> System: the state of the installation at a glance (Phase 7, task 7.9.7).

For the hospital's administrator, who is not expected to read Prometheus: the release, the
last backup, free disk space, the certificate's expiry, whether the database, Redis and the
worker are answering, each kiosk's last check-in, and how AI, voice and SMS are configured.
The worker measures most of it (app.workers.main.refresh_operational_metrics, every 30 s)
and leaves a summary in Redis; this route adds the hospital's kiosks and the API's own view.
"""

import json
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from redis.exceptions import RedisError
from sqlalchemy import text

from app.config import Settings
from app.core.auth import require_role
from app.db.session import set_hospital_scope
from app.workers.health import STALE_AFTER_SECONDS, heartbeat_key, system_status_key

router = APIRouter()
AdminPrincipal = Annotated[dict[str, Any], Depends(require_role("admin"))]
# A kiosk that has not checked in for this long is shown as silent (the KioskSilent alert).
KIOSK_SILENT_SECONDS = 600


def _timestamp(value: object) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC).isoformat()


@router.get("/admin/system")
async def system_status(request: Request, principal: AdminPrincipal) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    hospital_id = UUID(principal["hospital_id"])
    now = time.time()

    snapshot: dict[str, Any] = {}
    worker_age: float | None = None
    redis_ok = True
    try:
        client = request.app.state.redis_state
        raw = await client.get(system_status_key(settings.app_env))
        snapshot = json.loads(raw) if raw else {}
        heartbeat = await client.get(heartbeat_key(settings.app_env))
        worker_age = now - float(heartbeat) if heartbeat else None
    except (RedisError, ValueError):
        redis_ok = False

    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        kiosks = (
            (
                await connection.execute(
                    text(
                        "SELECT id, label, kind, is_active, paired_at, last_seen_at FROM kiosks "
                        "WHERE hospital_id = :hospital_id AND deleted_at IS NULL "
                        "ORDER BY label"
                    ),
                    {"hospital_id": hospital_id},
                )
            )
            .mappings()
            .all()
        )

    def kiosk_state(row: Any) -> str:
        if not row["is_active"]:
            return "inactive"
        if row["kind"] != "device":
            return "poster"
        if row["paired_at"] is None:
            return "not_paired"
        seen = row["last_seen_at"]
        if seen is None or now - seen.timestamp() > KIOSK_SILENT_SECONDS:
            return "silent"
        return "online"

    backup = snapshot.get("backup_last_success")
    backup_hours = round((now - backup) / 3600, 1) if isinstance(backup, (int, float)) else None
    certificate = snapshot.get("certificate_expires_at")
    return {
        "version": settings.app_version,
        "environment": settings.app_env,
        "checked_at": _timestamp(snapshot.get("checked_at")),
        "database": "up",  # this request just used it
        "redis": "up" if redis_ok else "down",
        "worker": (
            "unknown"
            if worker_age is None
            else "up"
            if worker_age < STALE_AFTER_SECONDS
            else "stale"
        ),
        "backup": {
            "last_success": _timestamp(backup),
            "age_hours": backup_hours,
        },
        "disk_free_ratio": snapshot.get("disk_free_ratio"),
        "certificate": {
            "expires_at": _timestamp(certificate),
            "days_left": (
                int((certificate - now) // 86400) if isinstance(certificate, (int, float)) else None
            ),
        },
        "ai": {
            "enabled": settings.llm_enabled,
            "llm": getattr(request.app.state.llm_provider, "name", "mock"),
            "speech_to_text": request.app.state.speech.stt_mode,
            "text_to_speech": request.app.state.speech.tts_mode,
            "sms": settings.notification_provider,
        },
        "kiosks": [
            {
                "id": str(row["id"]),
                "label": row["label"],
                "state": kiosk_state(row),
                "last_seen_at": row["last_seen_at"].isoformat() if row["last_seen_at"] else None,
            }
            for row in kiosks
        ],
    }
