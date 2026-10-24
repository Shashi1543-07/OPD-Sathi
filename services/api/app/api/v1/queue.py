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

from fastapi import APIRouter, Depends, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.ids import uuid7
from app.core.ratelimit import check_rate_limit
from app.core.security import AuthenticationTokenError, decode_access_token
from app.db.repositories import (
    CaseSheetRepository,
    IntakeRepository,
    PatientRepository,
    QueueRepository,
    decrypt_case_sheet,
)
from app.db.session import set_hospital_scope, set_secret_lookup_scope
from app.domain.patients import card_pin, normalize_mrn
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import average_consult_minutes, estimate_wait, visit_score
from app.domain.queue.state import QueueRuleError
from app.domain.queue.tokens import status_token_digest
from app.domain.redflags.engine import RULES

_HOSPITAL_TZ = ZoneInfo("Asia/Kolkata")

router = APIRouter(tags=["queue"])
STAFF_ROLES = ("nurse", "doctor", "admin")
RED_FLAG_REASONS = {rule.id: rule.reason_en for rule in RULES}
_CHANNELS = {"device": "kiosk", "mobile": "phone"}
# How often an open staff board re-sends overdue alerts and re-checks the account behind it.
_SOCKET_REVIEW_SECONDS = 60.0
# Alerts that do not come from a red-flag rule.
_SPECIAL_ALERTS = {
    "PATIENT_HELP": "Patient asked for help",
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
    return visit_score(row, settings)


# Titles for queue rules a valid action can still run into (see QueueRepository).
_RULE_TITLES = {
    "DOCTOR_BUSY": "Finish your current patient first",
    "CLAIMED_BY_ANOTHER_DOCTOR": "Another doctor's patient",
    "CALL_GRACE_NOT_OVER": "Too early to mark absent",
}


def _rule_problem(exc: QueueRuleError) -> ApiProblemError:
    retry = exc.extra.get("retry_after_seconds")
    return ApiProblemError(
        status=409,
        code=exc.code,
        title=_RULE_TITLES.get(exc.code, "Queue action unavailable"),
        detail=exc.detail,
        headers={"Retry-After": str(retry)} if retry is not None else None,
        extra=exc.extra,
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
        # Where to go and in which language to show it on the patient's phone, and whether
        # the patient's AI help is on (the page lets them stop it).
        place = (
            (
                await connection.execute(
                    text(
                        "SELECT d.name_en, d.name_hi, d.name_mr, h.name AS hospital, v.language, "
                        "CASE WHEN a.withdrawn_at IS NOT NULL THEN 'withdrawn' "
                        "WHEN a.granted THEN 'on' ELSE 'off' END AS ai_help, "
                        "CASE WHEN s.withdrawn_at IS NOT NULL THEN 'stopped' "
                        "WHEN s.granted AND v.sms_opt_in THEN 'on' ELSE 'off' END AS sms "
                        "FROM visits v JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id JOIN hospitals h ON h.id = v.hospital_id "
                        "LEFT JOIN consents a ON a.hospital_id = v.hospital_id "
                        "AND a.visit_id = v.id AND a.purpose = 'ai' "
                        "LEFT JOIN consents s ON s.hospital_id = v.hospital_id "
                        "AND s.visit_id = v.id AND s.purpose = 'sms' "
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
    # Without Redis, count only those still waiting, as the Redis waiting set does.
    ordered = sorted(
        (row for row in rows if row["status"] == "queued"),
        key=lambda item: (_queue_score(item, request.app.state.settings), str(item["visit_id"])),
    )
    if position is None and visit["status"] == "queued":
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
        # Called but not there: the patient should come to the desk to be recalled.
        "skipped": "skipped",
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
        "ai_help": place["ai_help"] if place else "off",
        # Queue messages by SMS: "on" can be stopped from the status page, never started.
        "sms": place["sms"] if place else "off",
        # The hospital's full privacy notice, which the page links to.
        "privacy_path": f"/privacy/{visit['hospital_id']}",
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


_STATUS_TOKEN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


@router.post("/status/{token}/ai-consent/withdraw")
async def withdraw_ai_help(token: str, request: Request) -> dict[str, Any]:
    """The patient stops online AI help for their visit, from their own status page.

    The status link shows it is their visit. It can only stop AI help, never start it, so a
    photographed token slip cannot switch AI on. From then on nothing more about the visit is
    sent to AI services, and the doctor no longer sees the AI draft.
    """
    unavailable = _problem(
        404, "NOT_FOUND", "Status unavailable", "This status link is unavailable."
    )
    if len(token) != 43 or not set(token) <= _STATUS_TOKEN_CHARACTERS:
        raise unavailable
    digest = status_token_digest(token)
    settings = request.app.state.settings
    limit = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{settings.app_env}:rl:ai-withdraw:{digest}",
        limit=10,
        window_seconds=600,
        fail_closed=False,
    )
    if limit is not None and not limit.allowed:
        raise ApiProblemError(
            status=429,
            code="RATE_LIMITED",
            title="Too many requests",
            detail="Please wait a moment and try again.",
            headers={"Retry-After": str(limit.retry_after)},
        )
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection, setting="app.status_token_hash", secret_hash=digest
        )
        visit = await QueueRepository(connection, settings).visit_status_by_hash(digest)
        if visit is None:
            raise unavailable
        await set_hospital_scope(connection, visit["hospital_id"])
        withdrawn = await IntakeRepository(connection, settings).withdraw_ai_consent(
            hospital_id=visit["hospital_id"], visit_id=visit["visit_id"], by="patient"
        )
        if withdrawn:
            await audit(
                connection,
                request,
                hospital_id=visit["hospital_id"],
                actor_type="patient",
                actor_id=visit["visit_id"],
                action="consent.ai_withdrawn",
                resource_type="visit",
                resource_id=visit["visit_id"],
                meta={"by": "patient"},
            )
    if not withdrawn:
        raise _problem(
            409, "AI_HELP_NOT_ON", "AI help is not on", "AI help is not on for this visit."
        )
    try:
        await request.app.state.redis_state.delete(
            f"osa:{settings.app_env}:status-cache:{digest}"
        )
    except RedisError:
        pass  # The cached status expires within two seconds.
    return {"ai_help": "withdrawn"}


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
                        "id = CAST(:department_id AS uuid)) "
                        # General Medicine first: kiosks send patients there by default,
                        # so it is the busiest queue and the one the board opens on.
                        "ORDER BY CASE WHEN code = 'GENMED' THEN 0 ELSE 1 END, name_en, id"
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
        missed = [
            row
            for row in await repository.missed_visits(
                hospital_id=hospital_id, department_id=department_id
            )
            if row["department_id"] in active_department_ids
        ]
        grace_seconds = await repository.call_grace_seconds(hospital_id)
    actor_id = UUID(principal["sub"])
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
                    # Where the patient registered: a kiosk, their own phone, or the desk.
                    "channel": _CHANNELS.get(str(row.get("checkin_kind")), "desk"),
                    # Staff only: the score and every line that made it up.
                    "triage": row.get("acuity"),
                    "called_at": row["called_at"].isoformat() if row.get("called_at") else None,
                    "called_by_name": row.get("called_by_name"),
                    "called_by_me": row.get("called_by") == actor_id,
                    "skip_count": int(row.get("skip_count") or 0),
                    # A danger sign the patient tapped that no nurse has confirmed yet.
                    "nurse_check": row.get("tile_unconfirmed") is True,
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
        # Called and not there: shown apart from the line until staff recall them.
        "missed": [
            {
                "visit_id": str(row["visit_id"]),
                "department_id": str(row["department_id"]),
                "token": row["token_label"],
                "urgency": row["urgency"],
                "age_years": row["age_years"],
                "sex": row["sex"],
                "complaint": _complaint(row.get("case_sheet_data")),
                "skip_count": int(row["skip_count"] or 0),
                "skipped_at": row["skipped_at"].isoformat() if row["skipped_at"] else None,
                "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
            }
            for row in missed
        ],
        "call_grace_seconds": grace_seconds,
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


class LinkPatientPayload(TransitionPayload):
    opd_number: str = Field(min_length=6, max_length=24)


async def _staff_department(
    connection: Any, *, hospital_id: UUID, actor_id: UUID, role: str
) -> UUID | None:
    """The staff member's department (None for administrators, who act hospital-wide)."""
    if role == "admin":
        return None
    assigned = await connection.scalar(
        text(
            "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
            "AND id = :actor_id AND is_active = true"
        ),
        {"hospital_id": hospital_id, "actor_id": actor_id},
    )
    if not isinstance(assigned, UUID):
        raise _problem(403, "FORBIDDEN", "Permission denied", "No staff department is assigned.")
    return assigned


@router.get("/patients/lookup")
async def lookup_patients(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    opd_number: Annotated[str | None, Query(max_length=24)] = None,
    phone: Annotated[str | None, Query(pattern=r"^[6-9][0-9]{9}$")] = None,
) -> dict[str, Any]:
    """Find a registered patient by OPD card number or phone, to confirm with them at the desk.

    A phone number can belong to several family members, so it returns every match; staff
    check age and sex with the patient before linking. Only what verification needs is
    returned (no clinical details), and every lookup is audited.
    """
    if (opd_number is None) == (phone is None):
        raise _problem(
            422, "VALIDATION_FAILED", "One search term", "Search by OPD number or by phone."
        )
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _staff_department(
            connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
        )
        repository = PatientRepository(connection, request.app.state.settings)
        if opd_number is not None:
            number = normalize_mrn(opd_number)
            record = (
                await repository.by_mrn(hospital_id=hospital_id, mrn=number) if number else None
            )
            candidates = []
            if record is not None:
                summary = await connection.execute(
                    text(
                        "SELECT count(*) AS visit_count, max(service_date) AS last_visit "
                        "FROM visits WHERE hospital_id = :hospital_id "
                        "AND patient_id = :patient_id AND token_number IS NOT NULL"
                    ),
                    {"hospital_id": hospital_id, "patient_id": record["id"]},
                )
                candidates = [{**record, **dict(summary.mappings().one())}]
        else:
            candidates = await repository.by_phone(hospital_id=hospital_id, phone=phone or "")
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="patient.lookup",
            resource_type="patient",
            meta={
                "by": "opd_number" if opd_number is not None else "phone",
                "matches": len(candidates),
            },
        )
    this_year = datetime.now(_HOSPITAL_TZ).year
    return {
        "items": [
            {
                "opd_number": item["mrn"],
                "age_years": this_year - item["year_of_birth"] if item["year_of_birth"] else None,
                "sex": item["sex"],
                "visit_count": int(item["visit_count"] or 0),
                "last_visit": item["last_visit"].isoformat() if item["last_visit"] else None,
                # Sent back when re-issuing, so a repeated request cannot replace it twice.
                "card_version": int(item["card_version"]),
            }
            for item in candidates
        ]
    }


class ReissueCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opd_number: str = Field(min_length=6, max_length=24)
    # The version the lookup showed; a card re-issued meanwhile is not replaced again.
    card_version: int = Field(ge=1, le=32767)


@router.post("/patients/card/reissue")
async def reissue_opd_card(
    payload: ReissueCardPayload,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    """Give a patient who lost their OPD card a new PIN; the old PIN stops working (7.11.4).

    The number stays the same, so the patient's record and earlier visits are unchanged. Staff
    check the patient's age and sex against the lookup first. The new PIN is returned once,
    to be written on a new card, and is never stored or logged. Audited.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    number = normalize_mrn(payload.opd_number)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _staff_department(
            connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
        )
        repository = PatientRepository(connection, request.app.state.settings)
        record = await repository.by_mrn(hospital_id=hospital_id, mrn=number) if number else None
        if record is None:
            raise _problem(
                404, "PATIENT_NOT_FOUND", "No such OPD number", "No patient has this OPD number."
            )
        version = await repository.reissue_card(
            hospital_id=hospital_id,
            patient_id=record["id"],
            expected_version=payload.card_version,
        )
        if version is None:
            raise _problem(
                409,
                "CARD_CHANGED",
                "Card already re-issued",
                "This card was re-issued meanwhile. Search again before re-issuing it.",
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="patient.card_reissued",
            resource_type="patient",
            resource_id=record["id"],
            meta={"version_before": payload.card_version, "version_after": version},
        )
    response.headers["Cache-Control"] = "no-store"
    return {
        "opd_number": record["mrn"],
        "pin": card_pin(
            key=request.app.state.settings.app_hmac_key_bytes,
            hospital_id=hospital_id,
            patient_id=record["id"],
            version=version,
        ),
        "card_version": version,
    }


@router.post("/queue/{visit_id}/link-patient")
async def link_patient(
    visit_id: UUID,
    payload: LinkPatientPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse", "admin"))],
) -> dict[str, Any]:
    """Join a visit to a registered patient after checking who they are at the desk.

    For patients without their OPD card. The nurse confirms identity in person first; the
    link is audited, and the doctor's view says it was made by staff.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    number = normalize_mrn(payload.opd_number)
    if number is None:
        raise _problem(422, "VALIDATION_FAILED", "Not an OPD number", "Check the number.")

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            department = await _staff_department(
                connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
            )
            visit_department = await connection.scalar(
                text(
                    "SELECT department_id FROM visits WHERE hospital_id = :hospital_id "
                    "AND id = :visit_id AND status IN ('registering', 'awaiting_confirmation', "
                    "'queued', 'called', 'in_consult', 'skipped')"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            )
            if visit_department is None or (
                department is not None and visit_department != department
            ):
                raise _problem(404, "NOT_FOUND", "Visit unavailable", "This visit is unavailable.")
            repository = PatientRepository(connection, request.app.state.settings)
            record = await repository.by_mrn(hospital_id=hospital_id, mrn=number)
            if record is None:
                raise _problem(404, "PATIENT_NOT_FOUND", "No such OPD number", "Check the number.")
            try:
                changed = await repository.link_visit(
                    hospital_id=hospital_id,
                    visit_id=visit_id,
                    patient_id=record["id"],
                    source="staff",
                )
            except ValueError as exc:
                raise _problem(
                    409,
                    "ALREADY_LINKED",
                    "Already linked",
                    "This visit already belongs to a patient with other visits.",
                ) from exc
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action="queue.patient_linked",
                resource_type="visit",
                resource_id=visit_id,
                meta={"source": "staff", "changed": changed},
            )
        return {"visit_id": str(visit_id), "opd_number": number, "linked": True}

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"visit_id": str(visit_id), "opd_number": number},
        operation=operation,
    )


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
        live_visit = await connection.scalar(
            text(
                "SELECT id FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id "
                "AND (CAST(:department_id AS uuid) IS NULL OR "
                "department_id = CAST(:department_id AS uuid)) "
                "AND status IN ('queued', 'called', 'in_consult') FOR UPDATE"
            ),
            {
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "department_id": assigned_department if principal["role"] != "admin" else None,
            },
        )
        # The case sheet column holds ciphertext; only the repository can read or write it.
        repository = CaseSheetRepository(connection, request.app.state.settings)
        saved = (
            await repository.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
            if live_visit is not None
            else None
        )
        if saved is None:
            raise _problem(
                404, "ROUTE_UNAVAILABLE", "Route unavailable", "This route is unavailable."
            )
        data = saved["data"]
        routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
        if not routing.get("needs_nurse_confirmation"):
            raise _problem(
                409,
                "ROUTE_ALREADY_CONFIRMED",
                "Route already confirmed",
                "This route no longer needs confirmation.",
            )
        routing["needs_nurse_confirmation"] = False
        data["routing"] = routing
        version = await repository.replace_if_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=int(saved["version"]),
            data=data,
            editor_id=actor_id,
        )
        if version is None:
            raise _problem(
                409,
                "CASE_SHEET_CONFLICT",
                "Case sheet changed",
                "Reload the queue and confirm the route again.",
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


async def _publish_change(
    request: Request, hospital_id: UUID, visit_id: UUID, changed: dict[str, Any]
) -> None:
    """Update the Redis waiting set and the live boards after a committed change."""
    target = changed["status"]
    queue = QueueRedis(request.app.state.redis_state, app_env=request.app.state.settings.app_env)
    item = {
        "visit_id": str(visit_id),
        "token": changed["token_label"],
        "urgency": changed["urgency"],
        "status": target,
    }
    try:
        if target == "queued":
            # Recalled or put back: the original arrival time gives the patient their place.
            await queue.enqueue(
                hospital_id=hospital_id,
                department_id=changed["department_id"],
                visit_id=visit_id,
                score=_queue_score(changed, request.app.state.settings),
                item=item,
            )
        else:
            removed, _ = await queue.remove(
                hospital_id=hospital_id,
                department_id=changed["department_id"],
                visit_id=visit_id,
            )
            if not removed:
                op = (
                    {"op": "upsert", "item": item}
                    if target in {"called", "in_consult"}
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


@router.post("/queue/call-next")
async def call_next(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
    department_id: Annotated[UUID, Query()],
) -> dict[str, Any]:
    """Call the next patient in calling order (urgent first, then queue score).

    Calls in a department are serialised, so two doctors pressing this together get two
    different patients. A doctor who already has a patient called or in consultation is
    asked to finish with them first.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])

    async def operation() -> dict[str, Any]:
        try:
            async with request.app.state.engine.begin() as connection:
                await set_hospital_scope(connection, hospital_id)
                department = await _staff_department(
                    connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
                )
                if department is not None and department != department_id:
                    raise _problem(
                        403,
                        "FORBIDDEN",
                        "Permission denied",
                        "You can only call patients in your own department.",
                    )
                repository = QueueRepository(connection, request.app.state.settings)
                await repository.lock_calls(hospital_id=hospital_id, department_id=department_id)
                changed = await repository.call_next(
                    hospital_id=hospital_id,
                    department_id=department_id,
                    actor_id=actor_id,
                    actor_role=principal["role"],
                )
                if changed is None:
                    raise _problem(
                        409, "QUEUE_EMPTY", "No patient waiting", "There is no patient to call."
                    )
                await audit(
                    connection,
                    request,
                    hospital_id=hospital_id,
                    actor_type="user",
                    actor_id=actor_id,
                    action="queue.called",
                    resource_type="visit",
                    resource_id=changed["visit_id"],
                    meta={"from": "queued", "via": "call_next"},
                )
        except QueueRuleError as exc:
            raise _rule_problem(exc) from exc
        await _publish_change(request, hospital_id, changed["visit_id"], changed)
        return {
            "visit_id": str(changed["visit_id"]),
            "status": "called",
            "token": changed["token_label"],
        }

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"call_next": str(department_id)},
        operation=operation,
    )


async def _perform_status_change(
    request: Request,
    visit_id: UUID,
    principal: dict[str, Any],
    target: str,
    *,
    expected: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            department = await _staff_department(
                connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
            )
            repo = QueueRepository(connection, request.app.state.settings)
            if target == "called":
                # Every call in a department takes the same lock as call-next, first.
                visit_department = await connection.scalar(
                    text(
                        "SELECT department_id FROM visits WHERE hospital_id = :hospital_id "
                        "AND id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
                if isinstance(visit_department, UUID):
                    await repo.lock_calls(hospital_id=hospital_id, department_id=visit_department)
            changed = await repo.transition_visit(
                hospital_id=hospital_id,
                visit_id=visit_id,
                target=target,
                actor_id=actor_id,
                actor_role=principal["role"],
                department_id=department,
                expected=expected,
            )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action=action or f"queue.{target}",
                resource_type="visit",
                resource_id=visit_id,
                meta={"from": changed["previous_status"]},
            )
    except LookupError as exc:
        raise _problem(404, "NOT_FOUND", "Visit unavailable", "This visit is unavailable.") from exc
    except QueueRuleError as exc:
        raise _rule_problem(exc) from exc
    except ValueError as exc:
        raise _problem(409, "INVALID_TRANSITION", "Queue action unavailable", str(exc)) from exc
    await _publish_change(request, hospital_id, visit_id, changed)
    return {"visit_id": str(visit_id), "status": target, "token": changed["token_label"]}


async def _change_status(
    request: Request,
    visit_id: UUID,
    principal: dict[str, Any],
    target: str,
    *,
    expected: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])

    async def operation() -> dict[str, Any]:
        return await _perform_status_change(
            request, visit_id, principal, target, expected=expected, action=action
        )

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"visit_id": str(visit_id), "target": target, "from": expected},
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


@router.post("/queue/{visit_id}/skip")
async def skip_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """Mark a called patient absent once the call grace period has passed.

    They keep their token, urgency and arrival time, so a recall puts them back where they were.
    """
    return await _change_status(request, visit_id, principal, "skipped")


@router.post("/queue/{visit_id}/recall")
async def recall_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """Put a skipped patient back in the queue on their original arrival time."""
    return await _change_status(
        request, visit_id, principal, "queued", expected="skipped", action="queue.recalled"
    )


@router.post("/queue/{visit_id}/return")
async def return_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """Put a called patient back in the queue unchanged, e.g. after a call made by mistake."""
    return await _change_status(
        request, visit_id, principal, "queued", expected="called", action="queue.returned"
    )


@router.post("/queue/{visit_id}/no-show")
async def no_show_visit(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """Close a skipped visit as a no-show (the patient has left). Only skipped visits qualify."""
    return await _change_status(request, visit_id, principal, "no_show")


@router.get("/visits/{visit_id}/queue-events")
async def visit_queue_events(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role(*STAFF_ROLES))],
) -> dict[str, Any]:
    """The visit's queue timeline (token, calls, missed calls, recalls) for its department."""
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        department = await _staff_department(
            connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
        )
        visit_department = await connection.scalar(
            text(
                "SELECT department_id FROM visits WHERE hospital_id = :hospital_id "
                "AND id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if visit_department is None or (department is not None and visit_department != department):
            raise _problem(404, "NOT_FOUND", "Visit unavailable", "This visit is unavailable.")
        events = await QueueRepository(connection, request.app.state.settings).visit_events(
            hospital_id=hospital_id, visit_id=visit_id
        )
    return {
        "visit_id": str(visit_id),
        "events": [
            {
                "from": event["from_status"],
                "to": event["to_status"],
                "actor_type": event["actor_type"],
                "actor_name": event["actor_name"],
                "actor_role": event["actor_role"],
                "reason": event["reason"],
                "at": event["created_at"].isoformat(),
            }
            for event in events
        ],
    }


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
                "'AI_TRIAGE_REVIEW')) "
                "AND ((:acknowledged = true AND e.acknowledged_at IS NOT NULL) OR "
                "(:acknowledged = false AND e.acknowledged_at IS NULL)) "
                # Open alerts only matter while the patient is still in the building. An
                # urgent one stays until staff acknowledge it, even if the kiosk session
                # ended (a patient with a danger sign may have walked away from the screen)
                # or the patient missed their call.
                "AND (:acknowledged = true OR v.status IN ('registering', "
                "'awaiting_confirmation', 'queued', 'called', 'in_consult', 'skipped') "
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
        department = await _staff_department(
            connection, hospital_id=hospital_id, actor_id=actor_id, role=principal["role"]
        )
        visit_department = await connection.scalar(
            text(
                "SELECT department_id FROM visits WHERE hospital_id = :hospital_id "
                "AND id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if visit_department is None or (department is not None and visit_department != department):
            raise _problem(404, "NOT_FOUND", "Visit unavailable", "This visit is unavailable.")
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
    score = _queue_score(dict(row), request.app.state.settings)
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
                if time.time() >= claims["exp"]:
                    # The sign-in expired: the board reconnects with a fresh token.
                    await websocket.close(code=4401)
                    return
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message and message.get("data"):
                    await websocket.send_text(str(message["data"]))
                if time.monotonic() - last_ping >= 25:
                    await websocket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                if time.monotonic() - last_escalation >= _SOCKET_REVIEW_SECONDS:
                    async with websocket.app.state.engine.begin() as connection:
                        await set_hospital_scope(connection, hospital_id)
                        # An account deactivated or moved to another department while its
                        # board is open stops receiving this department's patients.
                        account = (
                            (
                                await connection.execute(
                                    text(
                                        "SELECT is_active, department_id FROM users "
                                        "WHERE hospital_id = :hospital_id AND id = :actor_id"
                                    ),
                                    {"hospital_id": hospital_id, "actor_id": actor_id},
                                )
                            )
                            .mappings()
                            .first()
                        )
                        if (
                            account is None
                            or account["is_active"] is not True
                            or (
                                claims["role"] != "admin"
                                and account["department_id"] != department_id
                            )
                        ):
                            await websocket.close(code=4403)
                            return
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
                                "'AI_TRIAGE_REVIEW')) "
                                "AND e.acknowledged_at IS NULL "
                                "AND e.created_at <= now() - interval '60 seconds' "
                                # Same rule as GET /alerts: only patients still in the
                                # building. Without it, every unacknowledged alert from
                                # finished or cancelled visits (even past days) came back
                                # each minute and filled the board with stale alerts.
                                "AND (v.status IN ('registering', 'awaiting_confirmation', "
                                "'queued', 'called', 'in_consult', 'skipped') "
                                "OR (v.status = 'cancelled' AND v.token_label IS NULL "
                                "AND e.level = 'urgent' "
                                "AND e.created_at > now() - interval '2 hours')) "
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


@router.post("/status/{token}/sms/stop")
async def stop_sms(token: str, request: Request) -> dict[str, Any]:
    """The patient stops queue messages from their own status page.

    Like stopping AI help, the link can only switch messages off, never on. The consent is
    marked withdrawn, the number kept for messages is erased and anything not yet sent is
    cancelled.
    """
    unavailable = _problem(
        404, "NOT_FOUND", "Status unavailable", "This status link is unavailable."
    )
    if len(token) != 43 or not set(token) <= _STATUS_TOKEN_CHARACTERS:
        raise unavailable
    digest = status_token_digest(token)
    settings = request.app.state.settings
    limit = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{settings.app_env}:rl:sms-stop:{digest}",
        limit=10,
        window_seconds=600,
        fail_closed=False,
    )
    if limit is not None and not limit.allowed:
        raise ApiProblemError(
            status=429,
            code="RATE_LIMITED",
            title="Too many requests",
            detail="Please wait a moment and try again.",
            headers={"Retry-After": str(limit.retry_after)},
        )
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection, setting="app.status_token_hash", secret_hash=digest
        )
        visit = await QueueRepository(connection, settings).visit_status_by_hash(digest)
        if visit is None:
            raise unavailable
        await set_hospital_scope(connection, visit["hospital_id"])
        stopped = await IntakeRepository(connection, settings).withdraw_sms_consent(
            hospital_id=visit["hospital_id"], visit_id=visit["visit_id"], by="patient"
        )
        if stopped:
            await audit(
                connection,
                request,
                hospital_id=visit["hospital_id"],
                actor_type="patient",
                actor_id=visit["visit_id"],
                action="consent.sms_withdrawn",
                resource_type="visit",
                resource_id=visit["visit_id"],
                meta={"by": "patient"},
            )
    if not stopped:
        raise _problem(409, "SMS_NOT_ON", "SMS is not on", "Queue messages are not on.")
    try:
        await request.app.state.redis_state.delete(
            f"osa:{settings.app_env}:status-cache:{digest}"
        )
    except RedisError:
        pass  # The cached status expires within two seconds.
    return {"sms": "stopped"}
