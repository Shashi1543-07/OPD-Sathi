"""Patient status and staff queue endpoints backed by PostgreSQL and Redis."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

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
from app.db.repositories import QueueRepository
from app.db.session import set_hospital_scope, set_secret_lookup_scope
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import average_consult_minutes, estimate_wait, queue_score
from app.domain.queue.tokens import status_token_digest

router = APIRouter(tags=["queue"])
STAFF_ROLES = ("nurse", "doctor", "admin")


class TransitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OverridePayload(TransitionPayload):
    reason: str = Field(min_length=5, max_length=500)
    urgency: Literal["routine", "priority", "urgent"] | None = None


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


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
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    hospital_id = UUID(principal["hospital_id"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        repository = QueueRepository(connection, request.app.state.settings)
        rows = await repository.queued_visits(hospital_id=hospital_id, department_id=department_id)
        departments: dict[UUID, tuple[int, list[float]]] = {}
        for department in {row["department_id"] for row in rows}:
            departments[department] = await repository.queue_estimate_inputs(
                hospital_id=hospital_id, department_id=department
            )
    groups: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["department_id"], []).append(row)
    items = []
    for department, department_rows in sorted(groups.items(), key=lambda group: str(group[0])):
        department_rows.sort(
            key=lambda item: (
                _queue_score(item, request.app.state.settings),
                str(item["visit_id"]),
            )
        )
        for index, row in enumerate(department_rows, 1):
            items.append(
                {
                    "visit_id": str(row["visit_id"]),
                    "department_id": str(department),
                    "token": row["token_label"],
                    "urgency": row["urgency"],
                    "status": row["status"],
                    "age_years": row["age_years"],
                    "position": index,
                    "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
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
    return {"items": visible, "next_cursor": next_cursor, "limit": limit}


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
                        "UPDATE visits SET urgency = coalesce(:urgency, urgency) "
                        "WHERE hospital_id = :hospital_id "
                        "AND id = :visit_id AND status = 'queued' RETURNING id, department_id, "
                        "urgency, age_years, pregnant, token_label, queued_at"
                    ),
                    {"urgency": payload.urgency, "hospital_id": hospital_id, "visit_id": visit_id},
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
async def staff_queue_socket(websocket: WebSocket, department_id: UUID) -> None:
    """Authenticate via the first frame, then forward atomic Redis queue deltas."""
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
        async with websocket.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
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
            }
            for index, row in enumerate(rows)
        ]
        client = websocket.app.state.redis_state
        queue = QueueRedis(client, app_env=websocket.app.state.settings.app_env)
        _, channel, seq_key = queue.keys(hospital_id, department_id)
        sequence = int(await client.get(seq_key) or 0)
        await websocket.send_json({"type": "queue.snapshot", "seq": sequence, "items": items})
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            import time

            last_ping = time.monotonic()
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if message and message.get("data"):
                    await websocket.send_text(str(message["data"]))
                if time.monotonic() - last_ping >= 25:
                    await websocket.send_json({"type": "ping"})
                    last_ping = time.monotonic()
                try:
                    frame = await asyncio.wait_for(websocket.receive_json(), timeout=0.01)
                    if isinstance(frame, dict) and frame.get("type") == "pong":
                        continue
                except TimeoutError:
                    pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
    except (WebSocketDisconnect, TimeoutError):
        return
