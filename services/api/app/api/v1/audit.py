"""Administrator-only cursor-paginated audit event access."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text

from app.config import Settings
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.session import set_hospital_scope

router = APIRouter()
AdminPrincipal = Annotated[dict[str, Any], Depends(require_role("admin"))]


def encode_audit_cursor(created_at: datetime, event_id: UUID, key: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.astimezone(UTC).isoformat(), "id": str(event_id)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(key.encode("utf-8"), encoded, hashlib.sha256).digest()[:16]
    signature_part = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded.decode('ascii')}.{signature_part}"


def decode_audit_cursor(cursor: str, key: str) -> tuple[datetime, UUID]:
    try:
        payload_part, signature_part = cursor.split(".", maxsplit=1)
        encoded = payload_part.encode("ascii")
        expected = hmac.new(key.encode("utf-8"), encoded, hashlib.sha256).digest()[:16]
        supplied = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("signature mismatch")
        payload = json.loads(base64.urlsafe_b64decode(encoded + b"=" * (-len(encoded) % 4)))
        if set(payload) != {"created_at", "id"}:
            raise ValueError("invalid cursor fields")
        created_at = datetime.fromisoformat(payload["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include a timezone")
        return created_at.astimezone(UTC), UUID(payload["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ApiProblemError(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid cursor",
            detail="The audit cursor is invalid.",
        ) from exc


@router.get("/audit")
async def list_audit(
    request: Request,
    principal: AdminPrincipal,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    hospital_id = UUID(principal["hospital_id"])
    parameters: dict[str, Any] = {"limit": limit + 1}
    if cursor:
        created_at, event_id = decode_audit_cursor(cursor, settings.cursor_hmac_key)
        parameters.update({"created_at": created_at, "event_id": event_id})
        query = text(
            "SELECT id, actor_type, actor_id, action, resource_type, resource_id, "
            "ip, request_id, meta, created_at FROM audit_log "
            "WHERE (created_at, id) < (:created_at, :event_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        query = text(
            "SELECT id, actor_type, actor_id, action, resource_type, resource_id, "
            "ip, request_id, meta, created_at FROM audit_log "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )

    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        rows = (await connection.execute(query, parameters)).mappings().all()

    has_more = len(rows) > limit
    selected = rows[:limit]
    items = [
        {
            "id": str(row["id"]),
            "actor_type": row["actor_type"],
            "actor_id": str(row["actor_id"]) if row["actor_id"] else None,
            "action": row["action"],
            "resource_type": row["resource_type"],
            "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
            "ip": row["ip"],
            "request_id": row["request_id"],
            "meta": row["meta"],
            "created_at": row["created_at"],
        }
        for row in selected
    ]
    next_cursor = None
    if has_more and selected:
        last = selected[-1]
        next_cursor = encode_audit_cursor(last["created_at"], last["id"], settings.cursor_hmac_key)
    return {"items": items, "next_cursor": next_cursor, "limit": limit}
