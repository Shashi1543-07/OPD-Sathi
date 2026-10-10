"""Patient status and staff queue endpoints backed by PostgreSQL and Redis."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.ids import uuid7
from app.core.security import AuthenticationTokenError, decode_access_token
from app.db.repositories import QueueRepository, decrypt_case_sheet
from app.db.session import set_hospital_scope, set_secret_lookup_scope
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import average_consult_minutes, estimate_wait, queue_score
from app.domain.queue.tokens import status_token_digest
from app.domain.redflags.engine import RULES

_HOSPITAL_TZ = ZoneInfo("Asia/Kolkata")

router = APIRouter(tags=["queue"])
STAFF_ROLES = ("nurse", "doctor", "admin")
RED_FLAG_REASONS = {rule.id: rule.reason_en for rule in RULES}
# Alerts that do not come from a red-flag rule.
_SPECIAL_ALERTS = {
    "PATIENT_HELP": "Patient asked for help at the kiosk",
    "EMERGENCY_BUTTON": "EMERGENCY button pressed at the kiosk. Go to the kiosk now.",
    "AI_TRIAGE_REVIEW": "AI review suggests this patient may be more urgent. Please check.",
}


class TransitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OverridePayload(TransitionPayload):
    reason: str = Field(min_length=5, max_length=500)
    urgency: Literal["routine", "priority", "urgent"] | None = None


class RouteConfirmPayload(TransitionPayload):
    reason: str = Field(min_length=5, max_length=500)


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


def _staff_queue_department(
    role: str, assigned_department: UUID | None, requested_department: UUID | None
) -> UUID | None:
    if role == "admin":
        return requested_department
    if assigned_department is None:
        raise _problem(403, "FORBIDDEN", "Permission denied", "No staff department is assigned.")
    if requested_department is not None and requested_department != assigned_department:
        raise _problem(
            403,
            "FORBIDDEN",
            "Permission denied",
            "You can only view your assigned department.",
        )
    return assigned_department


def _encode_queue_cursor(offset: int, key: bytes) -> str:
    value = str(offset).encode("ascii")
    signature = hmac.new(key, value, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value + b"." + signature).rstrip(b"=").decode("ascii")


def _decode_queue_cursor(cursor: str, key: bytes) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        value, signature = raw.split(b".", 1)
        expected = hmac.new(key, value, hashlib.sha256).digest()
        offset = int(value)
    except (ValueError, UnicodeError) as exc:
        raise _problem(
            400, "INVALID_CURSOR", "Invalid queue page", "Reload and try again."
        ) from exc
    if not hmac.compare_digest(signature, expected) or offset < 0:
        raise _problem(400, "INVALID_CURSOR", "Invalid queue page", "Reload and try again.")
    return offset


def _queue_score(row: dict[str, Any], settings: Any) -> int:
    arrival = row["queued_at"] or datetime.now(UTC)
    arrival_ms = int(arrival.timestamp() * 1000)
    return queue_score(
        arrival_ms,
        row["urgency"],
        row["age_years"],
        bool(row["pregnant"]),
        urgent_bonus_min=settings.urgent_bonus_min,
        priority_bonus_min=settings.priority_bonus_min,
        vulnerable_bonus_min=settings.vulnerable_bonus_min,
        acuity_score=row.get("acuity_score"),
    )


async def _status_payload(request: Request, token: str) -> dict[str, Any]:
    digest = status_token_digest(token)
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection, setting="app.status_token_hash", secret_hash=digest
        )
        visit = await QueueRepository(connection, request.app.state.settings).visit_status_by_hash(
            digest
        )
        if visit is None:
            raise _problem(
                404, "NOT_FOUND", "Status unavailable", "This status link is unavailable."
            )
        await set_hospital_scope(connection, visit["hospital_id"])
        repository = QueueRepository(connection, request.app.state.settings)
        rows = await repository.queued_visits(
            hospital_id=visit["hospital_id"], department_id=visit["department_id"]
        )
        doctors, samples = await repository.queue_estimate_inputs(
            hospital_id=visit["hospital_id"], department_id=visit["department_id"]
        )
        # Where to go and in which language to show it on the patient's phone.
        place = (
            (
                await connection.execute(
                    text(
                        "SELECT d.name_en, d.name_hi, d.name_mr, h.name AS hospital, v.language "
                        "FROM visits v JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id JOIN hospitals h ON h.id = v.hospital_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {"hospital_id": visit["hospital_id"], "visit_id": visit["visit_id"]},
                )
            )
            .mappings()
            .first()
        )
    try:
        read_model = QueueRedis(
            request.app.state.redis_state, app_env=request.app.state.settings.app_env
        )
        position = await read_model.position(
            hospital_id=visit["hospital_id"],
            department_id=visit["department_id"],
            visit_id=visit["visit_id"],
        )
    except RedisError:
        position = None
    ordered = sorted(
        rows,
        key=lambda item: (_queue_score(item, request.app.state.settings), str(item["visit_id"])),
    )
    if position is None and visit["status"] in {"queued", "called", "in_consult"}:
        found = next(
            (
                index + 1
                for index, row in enumerate(ordered)
                if row["visit_id"] == visit["visit_id"]
            ),
            None,
        )
        position = found
    state = {
        "queued": "waiting",
        "called": "called",
        "in_consult": "in_consult",
        "done": "done",
        "no_show": "no_show",
        "cancelled": "cancelled",
    }.get(visit["status"], "waiting")
    eta = estimate_wait(
        position or 1,
        doctors,
        average_consult_minutes(samples, request.app.state.settings.default_consult_min),
    )
    return {
        "state": state,
        "token": visit["token_label"],
        "position": position if state == "waiting" else None,
        "eta_min": eta.eta_min if state == "waiting" else 0,
        "eta_max": eta.eta_max if state == "waiting" else 0,
        "department": (
            {lang: place[f"name_{lang}"] or place["name_en"] for lang in ("en", "hi", "mr")}
            if place
            else None
        ),
        "hospital": place["hospital"] if place else None,
        "language": place["language"] if place else "en",
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/status/{token}")
async def patient_status(token: str, request: Request) -> dict[str, Any]:
    """Fetch a public status by opaque bearer token; responses are cached for 2 seconds."""
    if len(token) != 43 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for char in token
    ):
        raise _problem(404, "NOT_FOUND", "Status unavailable", "This status link is unavailable.")
    digest = status_token_digest(token)
    cache_key = f"osa:{request.app.state.settings.app_env}:status-cache:{digest}"
    try:
        cached = await request.app.state.redis_state.get(cache_key)
        if cached:
            return cast(dict[str, Any], json.loads(cached))
    except RedisError:
        pass
    payload = await _status_payload(request, token)
    try:
        await request.app.state.redis_state.set(cache_key, json.dumps(payload), ex=2)
    except RedisError:
        pass
    return payload


@router.get("/status/{token}/stream")
async def patient_status_stream(token: str, request: Request) -> StreamingResponse:
    if len(token) != 43 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for char in token
    ):
        raise _problem(404, "NOT_FOUND", "Status unavailable", "This status link is unavailable.")
    initial = await _status_payload(request, token)

    async def events() -> AsyncIterator[str]:
        yield f"event: status\ndata: {json.dumps(initial, separators=(',', ':'))}\n\n"
        digest = status_token_digest(token)
        async with request.app.state.engine.begin() as connection:
            await set_secret_lookup_scope(
                connection, setting="app.status_token_hash", secret_hash=digest
            )
            visit = await QueueRepository(
                connection, request.app.state.settings
            ).visit_status_by_hash(digest)
        if visit is None:
            return
        pubsub = request.app.state.redis_state.pubsub()
        channel = QueueRedis(
            request.app.state.redis_state, app_env=request.app.state.settings.app_env
        ).status_channel(visit["hospital_id"], visit["visit_id"])
        try:
            await pubsub.subscribe(channel)
            last_send = time.monotonic()
            while not await request.is_disconnected():
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("data"):
                    payload = await _status_payload(request, token)
                    yield f"event: status\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    last_send = time.monotonic()
                elif time.monotonic() - last_send >= 15:
                    yield ": keepalive\n\n"
                    last_send = time.monotonic()
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/queue")
async def staff_queue(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    department_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[Literal["queued", "called", "in_consult"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    role = principal["role"]
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        if role != "admin":
            assigned_department = await connection.scalar(
                text(
                    "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                    "AND id = :actor_id AND is_active = true"
                ),
                {"hospital_id": hospital_id, "actor_id": UUID(principal["sub"])},
            )
            department_id = _staff_queue_department(
                role,
                assigned_department if isinstance(assigned_department, UUID) else None,
                department_id,
            )

        department_records = (
            (
                await connection.execute(
                    text(
                        "SELECT id, code, name_en FROM departments "
                        "WHERE hospital_id = :hospital_id "
                        "AND is_active = true AND (:is_admin = true OR "
                        "id = CAST(:department_id AS uuid)) ORDER BY name_en, id"
                    ),
                    {
                        "hospital_id": hospital_id,
                        "department_id": department_id,
                        "is_admin": role == "admin",
                    },
                )
            )
            .mappings()
            .all()
        )
        if department_id is not None and department_id not in {
            row["id"] for row in department_records
        }:
            raise _problem(404, "NOT_FOUND", "Department not found", "Choose an active department.")
        repository = QueueRepository(connection, request.app.state.settings)
        rows = await repository.queued_visits(hospital_id=hospital_id, department_id=department_id)
        active_department_ids = {row["id"] for row in department_records}
        rows = [row for row in rows if row["department_id"] in active_department_ids]
    if status is not None:
        rows = [row for row in rows if row["status"] == status]
    groups: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["department_id"], []).append(row)
    items = []
    for department, group_rows in sorted(groups.items(), key=lambda group: str(group[0])):
        group_rows.sort(
            key=lambda item: (
                _queue_score(item, request.app.state.settings),
                str(item["visit_id"]),
            )
        )
        for index, row in enumerate(group_rows, 1):
            case_sheet = row.get("case_sheet_data") or {}
            complaint = case_sheet.get("chief_complaint") or {}
            items.append(
                {
                    "visit_id": str(row["visit_id"]),
                    "department_id": str(department),
                    "token": row["token_label"],
                    "urgency": row["urgency"],
                    "status": row["status"],
                    "age_years": row["age_years"],
                    "sex": row["sex"],
                    "complaint": complaint.get("text_en") or complaint.get("text_original") or "",
                    "position": index,
                    "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
                    "routing": (row.get("case_sheet_data") or {}).get("routing"),
                    "needs_assistance": row.get("needs_assistance") is True,
                    # Staff only: the score and every line that made it up.
                    "triage": row.get("acuity"),
                }
            )
    offset = (
        _decode_queue_cursor(cursor, request.app.state.settings.cursor_hmac_key.encode())
        if cursor
        else 0
    )
    visible = items[offset : offset + limit]
    next_cursor = (
        _encode_queue_cursor(offset + limit, request.app.state.settings.cursor_hmac_key.encode())
        if offset + limit < len(items)
        else None
    )
    return {
        "items": visible,
        "next_cursor": next_cursor,
        "limit": limit,
        "departments": [
            {"id": str(row["id"]), "code": row["code"], "name": row["name_en"]}
            for row in department_records
        ],
    }


@router.get("/patients")
async def staff_patients(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    days: Annotated[int | None, Query(ge=1, le=90)] = None,
) -> dict[str, Any]:
    """Return a tenant-scoped visit register for staff review.

    ``days`` limits the register to visits since local midnight, ``days - 1`` days ago.
    """
    hospital_id = UUID(principal["hospital_id"])
    role = principal["role"]
    since: datetime | None = None
    if days is not None:
        local_midnight = datetime.now(_HOSPITAL_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since = local_midnight - timedelta(days=days - 1)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        department_id: UUID | None = None
        if role != "admin":
            assigned_department = await connection.scalar(
                text(
                    "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                    "AND id = :actor_id AND is_active = true"
                ),
                {"hospital_id": hospital_id, "actor_id": UUID(principal["sub"])},
            )
            if not isinstance(assigned_department, UUID):
                raise _problem(
                    403, "FORBIDDEN", "Permission denied", "No staff department is assigned."
                )
            department_id = assigned_department
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT v.id AS visit_id, v.token_label AS token, v.status, v.urgency, "
                        "v.age_years, v.language, p.sex, d.name_en AS department, v.registered_at, "
                        "(cs.visit_id IS NOT NULL) AS case_sheet_available, "
                        "cs.data AS case_sheet_data "
                        "FROM visits v JOIN patients p ON p.hospital_id = v.hospital_id "
                        "AND p.id = v.patient_id JOIN departments d "
                        "ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id LEFT JOIN case_sheets cs "
                        "ON cs.hospital_id = v.hospital_id AND cs.visit_id = v.id "
                        "WHERE v.hospital_id = :hospital_id AND d.is_active = true "
                        # A kiosk session ended before a token was issued (the patient quit,
                        # declined consent or walked away) was never a registration.
                        "AND NOT (v.status = 'cancelled' AND v.token_label IS NULL) "
                        "AND (CAST(:department_id AS uuid) IS NULL "
                        "OR v.department_id = CAST(:department_id AS uuid)) "
                        "AND (CAST(:since AS timestamptz) IS NULL "
                        "OR v.registered_at >= CAST(:since AS timestamptz)) "
                        "ORDER BY v.registered_at DESC, v.id DESC LIMIT :limit"
                    ),
                    {
                        "hospital_id": hospital_id,
                        "department_id": department_id,
                        "since": since,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
    return {
        "items": [
            {
                "visit_id": str(row["visit_id"]),
                "token": row["token"],
                "language": row["language"],
                "status": row["status"],
                "urgency": row["urgency"],
                "age_years": row["age_years"],
                "sex": row["sex"],
                "department": row["department"],
                "registered_at": row["registered_at"].isoformat(),
                "complaint": _complaint(
                    decrypt_case_sheet(
                        row["case_sheet_data"], row["visit_id"], request.app.state.settings
                    )
                ),
                "case_sheet_available": row["case_sheet_available"],
            }
            for row in rows
        ],
        "limit": limit,
        "days": days,
    }


def _complaint(case_sheet: dict[str, Any] | None) -> str:
    complaint = (case_sheet or {}).get("chief_complaint") or {}
    return str(complaint.get("text_en") or complaint.get("text_original") or "")


@router.post("/queue/{visit_id}/routing/confirm")
async def confirm_routing(
    visit_id: UUID,
    payload: RouteConfirmPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    """Audit a nurse/admin decision to accept a low-confidence route suggestion."""
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        assigned_department = await connection.scalar(
            text(
                "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                "AND id = :actor_id AND is_active = true"
            ),
            {"hospital_id": hospital_id, "actor_id": actor_id},
        )
        if principal["role"] != "admin" and assigned_department is None:
            raise _problem(
                403, "FORBIDDEN", "Permission denied", "No staff department is assigned."
            )
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT c.data, v.department_id FROM case_sheets c JOIN visits v "
                        "ON v.hospital_id = c.hospital_id AND v.id = c.visit_id "
                        "WHERE c.hospital_id = :hospital_id AND c.visit_id = :visit_id "
                        "AND (CAST(:department_id AS uuid) IS NULL OR "
                        "v.department_id = CAST(:department_id AS uuid)) "
                        "AND v.status IN ('queued', 'called', 'in_consult') FOR UPDATE OF c"
                    ),
                    {
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                        "department_id": assigned_department
                        if principal["role"] != "admin"
                        else None,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _problem(
                404, "ROUTE_UNAVAILABLE", "Route unavailable", "This route is unavailable."
            )
        data = row["data"]
        routing = data.get("routing", {})
        if not routing.get("needs_nurse_confirmation"):
            raise _problem(
                409,
                "ROUTE_ALREADY_CONFIRMED",
                "Route already confirmed",
                "This route no longer needs confirmation.",
            )
        routing["needs_nurse_confirmation"] = False
        await connection.execute(
            text(
                "UPDATE case_sheets SET data = CAST(:data AS jsonb), version = version + 1, "
                "last_edited_by = :actor_id WHERE hospital_id = :hospital_id "
                "AND visit_id = :visit_id"
            ),
            {
                "data": json.dumps(data, separators=(",", ":")),
                "actor_id": actor_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
            },
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="routing.confirmed",
            resource_type="visit",
            resource_id=visit_id,
            meta={"reason": payload.reason, "department_code": routing.get("department_code")},
        )
    return {"visit_id": str(visit_id), "routing": routing, "confirmed": True}


@router.post("/queue/call-next")
async def call_next(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    department_id: Annotated[UUID, Query()],
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        rows = await QueueRepository(connection, request.app.state.settings).queued_visits(
            hospital_id=hospital_id, department_id=department_id
        )
    waiting = [row for row in rows if row["status"] == "queued"]
    if not waiting:
        raise _problem(409, "QUEUE_EMPTY", "No patient waiting", "There is no patient to call.")
    next_row = min(
        waiting,
        key=lambda item: (_queue_score(item, request.app.state.settings), str(item["visit_id"])),
    )
    return await _change_status(request, next_row["visit_id"], principal, "called")


async def _perform_status_change(
    request: Request,
    visit_id: UUID,
    principal: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repo = QueueRepository(connection, request.app.state.settings)
            changed = await repo.transition_visit(
                hospital_id=hospital_id, visit_id=visit_id, target=target, actor_id=actor_id
            )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action=f"queue.{target}",
                resource_type="visit",
                resource_id=visit_id,
            )
    except LookupError as exc:
        raise _problem(404, "NOT_FOUND", "Visit unavailable", "This visit is unavailable.") from exc
    except ValueError as exc:
        raise _problem(409, "INVALID_TRANSITION", "Queue action unavailable", str(exc)) from exc
    queue = QueueRedis(request.app.state.redis_state, app_env=request.app.state.settings.app_env)
    try:
        removed, _ = await queue.remove(
            hospital_id=hospital_id,
            department_id=changed["department_id"],
            visit_id=visit_id,
        )
        if not removed:
            op = (
                {
                    "op": "upsert",
                    "item": {
                        "visit_id": str(visit_id),
                        "token": changed["token_label"],
                        "urgency": changed["urgency"],
                        "status": target,
                    },
                }
                if target == "in_consult"
                else {"op": "remove", "visit_id": str(visit_id)}
            )
            await queue.publish_delta(
                hospital_id=hospital_id,
                department_id=changed["department_id"],
                ops=[op],
            )
        await queue.publish_status(
            hospital_id=hospital_id, visit_id=visit_id, payload={"changed": True}
        )
    except RedisError:
        # The worker owns retries for the committed outbox event.
        pass
    return {"visit_id": str(visit_id), "status": target}


async def _change_status(
    request: Request,
    visit_id: UUID,
    principal: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])

    async def operation() -> dict[str, Any]:
        return await _perform_status_change(request, visit_id, principal, target)

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"visit_id": str(visit_id), "target": target},
        operation=operation,
    )


@router.post("/queue/{visit_id}/call")
async def call_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    return await _change_status(request, visit_id, principal, "called")


@router.post("/queue/{visit_id}/start")
async def start_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "admin"))],
) -> dict[str, Any]:
    return await _change_status(request, visit_id, principal, "in_consult")


@router.post("/queue/{visit_id}/complete")
async def complete_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "admin"))],
) -> dict[str, Any]:
    return await _change_status(request, visit_id, principal, "done")


@router.post("/queue/{visit_id}/no-show")
async def no_show_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    return await _change_status(request, visit_id, principal, "no_show")


@router.post("/queue/{visit_id}/cancel")
async def cancel_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    return await _change_status(request, visit_id, principal, "cancelled")


@router.get("/alerts")
async def open_alerts(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    acknowledged: bool = False,
    department_id: UUID | None = None,
) -> dict[str, Any]:
    """List red-flag events for the authenticated staff member's hospital."""
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        assigned_department = await connection.scalar(
            text(
                "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                "AND id = :actor_id AND is_active = true"
            ),
            {"hospital_id": hospital_id, "actor_id": actor_id},
        )
        if principal["role"] != "admin" and assigned_department is None:
            raise _problem(
                403, "FORBIDDEN", "Permission denied", "No staff department is assigned."
            )
        if principal["role"] != "admin" and assigned_department is not None:
            if department_id is not None and department_id != assigned_department:
                raise _problem(
                    403, "FORBIDDEN", "Permission denied", "This department is unavailable."
                )
            department_id = assigned_department
        result = await connection.execute(
            text(
                "SELECT e.id, e.visit_id, e.rule_id, e.level, e.evidence, e.created_at, "
                "e.source, v.department_id, v.token_label, e.acknowledged_at "
                "FROM red_flag_events e JOIN visits v ON v.hospital_id = e.hospital_id "
                "AND v.id = e.visit_id WHERE e.hospital_id = :hospital_id "
                # An AI "urgent" is a suggestion a nurse must see and confirm.
                "AND (e.source = 'rule' OR (e.source = 'llm' AND e.level = 'urgent') "
                "OR e.rule_id IN ('RF_DS_TILE', 'PATIENT_HELP', "
                "'EMERGENCY_BUTTON', 'AI_TRIAGE_REVIEW')) "
                "AND ((:acknowledged = true AND e.acknowledged_at IS NOT NULL) OR "
                "(:acknowledged = false AND e.acknowledged_at IS NULL)) "
                # Open alerts only matter while the patient is still in the building. An
                # urgent one stays until staff acknowledge it, even if the kiosk session
                # ended (a patient with a danger sign may have walked away from the screen).
                "AND (:acknowledged = true OR v.status IN ('registering', "
                "'awaiting_confirmation', 'queued', 'called', 'in_consult') "
                "OR (v.status = 'cancelled' AND v.token_label IS NULL AND e.level = 'urgent' "
                "AND e.created_at > now() - interval '2 hours')) "
                "AND (CAST(:department_id AS uuid) IS NULL OR "
                "v.department_id = CAST(:department_id AS uuid)) "
                "ORDER BY e.created_at DESC LIMIT 100"
            ),
            {
                "hospital_id": hospital_id,
                "acknowledged": acknowledged,
                "department_id": department_id,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
    return {
        "items": [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"created_at", "acknowledged_at"}
                },
                "reason_en": (
                    "AI suggests (please confirm): " if row.get("source") == "llm" else ""
                )
                + _SPECIAL_ALERTS.get(
                    row["rule_id"],
                    RED_FLAG_REASONS.get(row["rule_id"], "A danger-sign help tile was selected."),
                ),
                "created_at": row["created_at"].isoformat(),
                "acknowledged_at": row["acknowledged_at"].isoformat()
                if row["acknowledged_at"]
                else None,
            }
            for row in rows
        ]
    }


@router.post("/alerts/{alert_id}/ack")
async def acknowledge_alert(
    alert_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """Acknowledge one alert and leave an audit record."""
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        assigned_department = await connection.scalar(
            text(
                "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                "AND id = :actor_id AND is_active = true"
            ),
            {"hospital_id": hospital_id, "actor_id": actor_id},
        )
        if principal["role"] != "admin" and assigned_department is None:
            raise _problem(
                403, "FORBIDDEN", "Permission denied", "No staff department is assigned."
            )
        row = (
            (
                await connection.execute(
                    text(
                        "UPDATE red_flag_events SET acknowledged_by = :actor_id, "
                        "acknowledged_at = now() WHERE hospital_id = :hospital_id "
                        "AND id = :alert_id AND acknowledged_at IS NULL AND EXISTS ("
                        "SELECT 1 FROM visits v WHERE v.hospital_id = red_flag_events.hospital_id "
                        "AND v.id = red_flag_events.visit_id AND "
                        "(CAST(:department_id AS uuid) IS NULL OR "
                        "v.department_id = CAST(:department_id AS uuid))) "
                        "RETURNING id, visit_id"
                    ),
                    {
                        "actor_id": actor_id,
                        "hospital_id": hospital_id,
                        "alert_id": alert_id,
                        "department_id": assigned_department
                        if principal["role"] != "admin"
                        else None,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _problem(
                404, "ALERT_UNAVAILABLE", "Alert unavailable", "This alert is unavailable."
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="red_flag.acknowledged",
            resource_type="red_flag_event",
            resource_id=alert_id,
        )
    return {"id": str(row["id"]), "acknowledged": True}


async def _perform_override_visit(
    visit_id: UUID,
    payload: OverridePayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        row = (
            (
                await connection.execute(
                    text(
                        # A staff decision is part of the explanation staff see later.
                        "UPDATE visits SET urgency = coalesce(:urgency, urgency), "
                        "acuity = CASE WHEN CAST(:urgency AS text) IS NULL THEN acuity "
                        "ELSE coalesce(acuity, '{}'::jsonb) || jsonb_build_object('override', "
                        "jsonb_build_object('level', CAST(:urgency AS text), "
                        "'reason', CAST(:reason AS text), 'by_role', CAST(:role AS text), "
                        "'at', now())) END "
                        "WHERE hospital_id = :hospital_id "
                        "AND id = :visit_id AND status = 'queued' RETURNING id, department_id, "
                        "urgency, age_years, pregnant, token_label, queued_at, acuity_score"
                    ),
                    {
                        "urgency": payload.urgency,
                        "reason": payload.reason,
                        "role": principal["role"],
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise _problem(
                409,
                "OVERRIDE_UNAVAILABLE",
                "Queue override unavailable",
                "Only waiting visits can be reprioritized.",
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="queue.override",
            resource_type="visit",
            resource_id=visit_id,
            meta={"reason": payload.reason, "urgency": payload.urgency},
        )
        await connection.execute(
            text(
                "INSERT INTO outbox (id, hospital_id, topic, payload) VALUES "
                "(:id, :hospital_id, 'queue.changed', CAST(:payload AS jsonb))"
            ),
            {
                "id": uuid7(),
                "hospital_id": hospital_id,
                "payload": json.dumps(
                    {
                        "op": "upsert",
                        "visit_id": str(visit_id),
                        "department_id": str(row["department_id"]),
                    }
                ),
            },
        )
    score = queue_score(
        int(row["queued_at"].timestamp() * 1000),
        row["urgency"],
        row["age_years"],
        bool(row["pregnant"]),
        urgent_bonus_min=request.app.state.settings.urgent_bonus_min,
        priority_bonus_min=request.app.state.settings.priority_bonus_min,
        vulnerable_bonus_min=request.app.state.settings.vulnerable_bonus_min,
        acuity_score=row["acuity_score"],
    )
    try:
        await QueueRedis(
            request.app.state.redis_state, app_env=request.app.state.settings.app_env
        ).enqueue(
            hospital_id=hospital_id,
            department_id=row["department_id"],
            visit_id=visit_id,
            score=score,
            item={
                "visit_id": str(visit_id),
                "token": row["token_label"],
                "urgency": row["urgency"],
                "status": "queued",
            },
        )
    except RedisError:
        pass
    return {"visit_id": str(visit_id), "urgency": row["urgency"], "reweighted": True}


@router.post("/queue/{visit_id}/override")
async def override_visit(
    visit_id: UUID,
    payload: OverridePayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])

    async def operation() -> dict[str, Any]:
        return await _perform_override_visit(visit_id, payload, request, principal)

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"visit_id": str(visit_id), **payload.model_dump()},
        operation=operation,
    )


@router.websocket("/ws/staff/queue")
async def staff_queue_socket(
    websocket: WebSocket,
    department_id: UUID | None = None,
    alerts_only: bool = False,
) -> None:
    """Authenticate via the first frame, then forward queue deltas or hospital alerts."""
    await websocket.accept()
    try:
        auth_frame = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if (
            not isinstance(auth_frame, dict)
            or auth_frame.get("type") != "auth"
            or not isinstance(auth_frame.get("token"), str)
        ):
            await websocket.close(code=4401)
            return
        try:
            claims = decode_access_token(auth_frame["token"], settings=websocket.app.state.settings)
        except AuthenticationTokenError:
            await websocket.close(code=4401)
            return
        if claims.get("role") not in STAFF_ROLES:
            await websocket.close(code=4403)
            return
        hospital_id = UUID(claims["hospital_id"])
        actor_id = UUID(claims["sub"])
        async with websocket.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            assigned_department = await connection.scalar(
                text(
                    "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
                    "AND id = :actor_id AND is_active = true"
                ),
                {"hospital_id": hospital_id, "actor_id": actor_id},
            )
            if claims["role"] != "admin":
                if assigned_department is None:
                    await websocket.close(code=4403)
                    return
                if department_id is not None and department_id != assigned_department:
                    await websocket.close(code=4403)
                    return
                department_id = assigned_department
            rows = await QueueRepository(connection, websocket.app.state.settings).queued_visits(
                hospital_id=hospital_id, department_id=department_id
            )
        rows.sort(
            key=lambda item: (
                _queue_score(item, websocket.app.state.settings),
                str(item["visit_id"]),
            )
        )
        items = [
            {
                "visit_id": str(row["visit_id"]),
                "token": row["token_label"],
                "urgency": row["urgency"],
                "status": row["status"],
                "position": index + 1,
                "needs_assistance": row.get("needs_assistance") is True,
            }
            for index, row in enumerate(rows)
        ]
        client = websocket.app.state.redis_state
        queue = QueueRedis(client, app_env=websocket.app.state.settings.app_env)
        channel: str | None = None
        alert_pattern: str | None = None
        sequence = 0
        if department_id is not None and not alerts_only:
            _, channel, seq_key = queue.keys(hospital_id, department_id)
            sequence = int(await client.get(seq_key) or 0)
        if not alerts_only:
            await websocket.send_json({"type": "queue.snapshot", "seq": sequence, "items": items})
        pubsub = client.pubsub()
        if alerts_only:
            alert_pattern = f"osa:{websocket.app.state.settings.app_env}:{hospital_id}:alerts:"
            alert_pattern += str(department_id) if department_id is not None else "*"
            await pubsub.psubscribe(alert_pattern)
        elif channel is not None:
            await pubsub.subscribe(channel)
        else:
            alert_pattern = f"osa:{websocket.app.state.settings.app_env}:{hospital_id}:alerts:*"
            await pubsub.psubscribe(alert_pattern)
        try:
            import time

            last_ping = time.monotonic()
            last_escalation = last_ping
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message and message.get("data"):
                    await websocket.send_text(str(message["data"]))
                if time.monotonic() - last_ping >= 25:
                    await websocket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                if time.monotonic() - last_escalation >= 60:
                    async with websocket.app.state.engine.begin() as connection:
                        await set_hospital_scope(connection, hospital_id)
                        overdue = await connection.execute(
                            text(
                                "SELECT e.id, e.visit_id, e.rule_id, e.level, e.evidence "
                                "FROM red_flag_events e JOIN visits v "
                                "ON v.hospital_id = e.hospital_id AND v.id = e.visit_id "
                                "WHERE e.hospital_id = :hospital_id "
                                "AND (CAST(:department_id AS uuid) IS NULL OR "
                                "v.department_id = CAST(:department_id AS uuid)) "
                                "AND (e.source = 'rule' OR "
                                "(e.source = 'llm' AND e.level = 'urgent') OR "
                                "e.rule_id IN ('RF_DS_TILE', 'PATIENT_HELP', "
                                "'EMERGENCY_BUTTON', 'AI_TRIAGE_REVIEW')) "
                                "AND e.acknowledged_at IS NULL "
                                "AND e.created_at <= now() - interval '60 seconds' "
                                "ORDER BY e.created_at LIMIT 100"
                            ),
                            {"hospital_id": hospital_id, "department_id": department_id},
                        )
                        for alert in overdue.mappings().all():
                            await websocket.send_json(
                                {
                                    "type": "alert.escalated",
                                    **{
                                        k: str(v) if isinstance(v, UUID) else v
                                        for k, v in alert.items()
                                    },
                                }
                            )
                    last_escalation = time.monotonic()
                try:
                    frame = await asyncio.wait_for(websocket.receive_json(), timeout=0.01)
                    if isinstance(frame, dict) and frame.get("type") == "pong":
                        continue
                except TimeoutError:
                    pass
        finally:
            if alert_pattern is not None:
                await pubsub.punsubscribe(alert_pattern)
            else:
                await pubsub.unsubscribe(channel)
            await pubsub.aclose()
    except (WebSocketDisconnect, TimeoutError):
        return
