"""Administrators search and export the audit log (task 7.3.10); every read is audited."""

import base64
import csv
import hashlib
import hmac
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, column, select, table, tuple_

from app.config import Settings
from app.core.audit import audit
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


_KOLKATA = ZoneInfo("Asia/Kolkata")
EXPORT_LIMIT = 50_000
# The audit search is built with the query builder, never by joining SQL strings.
_events = table(
    "audit_log",
    *(
        column(name)
        for name in (
            "id",
            "hospital_id",
            "actor_type",
            "actor_id",
            "action",
            "resource_type",
            "resource_id",
            "ip",
            "request_id",
            "meta",
            "created_at",
        )
    ),
).alias("a")
_staff = table("users", column("id"), column("hospital_id"), column("name")).alias("u")


@dataclass(frozen=True)
class AuditFilters:
    """What an investigation narrows the log to (docs/runbooks/audit-investigation.md)."""

    record: UUID | None = None
    person: UUID | None = None
    action: str | None = None
    date_from: date | None = None
    date_to: date | None = None

    def query(self, *, limit: int, after: tuple[datetime, UUID] | None = None) -> Select[Any]:
        """Matching events, newest first, with the actor's name; values are bound parameters."""
        events = _events.c
        conditions: list[Any] = []
        if self.record is not None:
            conditions.append(events.resource_id == self.record)
        if self.person is not None:
            conditions.append(events.actor_id == self.person)
        if self.action:
            conditions.append(events.action.ilike(f"%{self.action}%"))
        if self.date_from is not None:
            conditions.append(
                events.created_at >= datetime.combine(self.date_from, time.min, _KOLKATA)
            )
        if self.date_to is not None:
            end = datetime.combine(self.date_to + timedelta(days=1), time.min, _KOLKATA)
            conditions.append(events.created_at < end)
        if after is not None:
            conditions.append(tuple_(events.created_at, events.id) < tuple_(*after))
        return (
            select(
                events.id,
                events.actor_type,
                events.actor_id,
                _staff.c.name.label("actor_name"),
                events.action,
                events.resource_type,
                events.resource_id,
                events.ip,
                events.request_id,
                events.meta,
                events.created_at,
            )
            .select_from(
                _events.outerjoin(
                    _staff,
                    and_(
                        _staff.c.hospital_id == events.hospital_id,
                        _staff.c.id == events.actor_id,
                    ),
                )
            )
            .where(*conditions)
            .order_by(events.created_at.desc(), events.id.desc())
            .limit(limit)
        )

    def described(self) -> dict[str, Any]:
        """For the audit entry that records this search (ids, not names or content)."""
        return {
            key: str(value)
            for key, value in (
                ("record", self.record),
                ("person", self.person),
                ("action", self.action),
                ("from", self.date_from),
                ("to", self.date_to),
            )
            if value is not None
        }


def audit_filters(
    record: UUID | None = None,
    person: UUID | None = None,
    action: Annotated[str | None, Query(max_length=80, pattern=r"^[a-z0-9_.]*$")] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AuditFilters:
    if date_from and date_to and date_from > date_to:
        raise ApiProblemError(
            status=422,
            code="VALIDATION_FAILED",
            title="Invalid dates",
            detail="The start date must be on or before the end date.",
        )
    return AuditFilters(record, person, action or None, date_from, date_to)


Filters = Annotated[AuditFilters, Depends(audit_filters)]


def _item(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "actor_type": row["actor_type"],
        "actor_id": str(row["actor_id"]) if row["actor_id"] else None,
        "actor_name": row["actor_name"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
        "ip": row["ip"],
        "request_id": row["request_id"],
        "meta": row["meta"],
        "created_at": row["created_at"],
    }


@router.get("/audit")
async def list_audit(
    request: Request,
    principal: AdminPrincipal,
    filters: Filters,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> dict[str, Any]:
    """Search the hospital's audit log, newest first. Each search is itself audited."""
    settings: Settings = request.app.state.settings
    hospital_id = UUID(principal["hospital_id"])
    after = decode_audit_cursor(cursor, settings.cursor_hmac_key) if cursor else None
    query = filters.query(limit=limit + 1, after=after)

    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        rows = (await connection.execute(query)).mappings().all()
        if not cursor:
            # Reading the log is itself recorded; following pages of one search is not.
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=UUID(principal["sub"]),
                action="audit.viewed",
                resource_type="audit_log",
                meta=filters.described(),
            )

    has_more = len(rows) > limit
    selected = rows[:limit]
    next_cursor = None
    if has_more and selected:
        last = selected[-1]
        next_cursor = encode_audit_cursor(last["created_at"], last["id"], settings.cursor_hmac_key)
    return {"items": [_item(row) for row in selected], "next_cursor": next_cursor, "limit": limit}


@router.get("/audit/export")
async def export_audit(
    request: Request, principal: AdminPrincipal, filters: Filters
) -> StreamingResponse:
    """The matching entries as CSV (at most 50,000), for an investigation file. Audited."""
    hospital_id = UUID(principal["hospital_id"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        rows = (await connection.execute(filters.query(limit=EXPORT_LIMIT))).mappings().all()
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=UUID(principal["sub"]),
            action="audit.exported",
            resource_type="audit_log",
            meta={**filters.described(), "rows": len(rows)},
        )

    def lines() -> Iterator[str]:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "time",
                "actor_type",
                "actor_id",
                "actor_name",
                "action",
                "resource_type",
                "resource_id",
                "ip",
                "request_id",
                "details",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["created_at"].astimezone(_KOLKATA).isoformat(),
                    row["actor_type"],
                    row["actor_id"] or "",
                    _safe_cell(row["actor_name"] or ""),
                    row["action"],
                    row["resource_type"] or "",
                    row["resource_id"] or "",
                    row["ip"] or "",
                    row["request_id"] or "",
                    _safe_cell(json.dumps(row["meta"] or {}, separators=(",", ":"))),
                ]
            )
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)

    name = f"audit-{datetime.now(_KOLKATA):%Y%m%d-%H%M}.csv"
    return StreamingResponse(
        lines(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


def _safe_cell(value: str) -> str:
    """Stop a spreadsheet from treating a cell as a formula (CSV injection)."""
    return "'" + value if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value
