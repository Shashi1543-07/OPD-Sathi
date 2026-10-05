"""Append-only audit event helper. Call inside the current tenant transaction."""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

AuditActor = Literal["user", "kiosk", "patient", "system"]


async def audit(
    connection: AsyncConnection,
    request: Request,
    *,
    hospital_id: UUID,
    actor_type: AuditActor,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record a security-relevant action without credentials or patient data."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent", "")[:500] or None
    await connection.execute(
        text(
            "INSERT INTO audit_log "
            "(id, hospital_id, actor_type, actor_id, action, resource_type, resource_id, "
            "ip, user_agent, request_id, meta) "
            "VALUES (:id, :hospital_id, :actor_type, :actor_id, :action, :resource_type, "
            ":resource_id, :ip, :user_agent, :request_id, CAST(:meta AS jsonb))"
        ),
        {
            "id": uuid4(),
            "hospital_id": hospital_id,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "ip": client_ip,
            "user_agent": user_agent,
            "request_id": getattr(request.state, "request_id", "unknown"),
            "meta": json.dumps(meta or {}, separators=(",", ":")),
        },
    )
