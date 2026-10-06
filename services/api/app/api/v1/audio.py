"""Consent-gated, bounded audio streaming for the patient intake interview."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from app.ai.providers.stt.base import STTEvent, STTProvider
from app.core.ratelimit import check_rate_limit
from app.core.security import AuthenticationTokenError, decode_access_token

router = APIRouter(tags=["intake"])
_MAX_FRAME_BYTES = 8192
_MAX_UTTERANCE_SECONDS = 60
_IDLE_SECONDS = 15
_KIOSK_SOCKET_LIMIT = 4
_KIOSK_SOCKET_RATE = 60
_LANGUAGES = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
_RESERVE_SCRIPT = """
local now = tonumber(ARGV[1])
local connection_id = ARGV[2]
local expiry = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[4]) then return 0 end
redis.call('ZADD', KEYS[1], expiry, connection_id)
redis.call('EXPIRE', KEYS[1], 100)
return 1
"""


def _event_payload(event: STTEvent) -> dict[str, object]:
    """Map provider events to the small public WebSocket protocol."""
    if event.type == "unavailable":
        return {"type": "error", "code": "STT_UNAVAILABLE"}
    payload: dict[str, object] = {"type": event.type}
    if event.text is not None:
        payload["text"] = event.text
    if event.confidence is not None:
        payload["confidence"] = event.confidence
    return payload


async def _incoming_audio(websocket: WebSocket) -> AsyncIterator[bytes]:
    """Yield only bounded binary frames until the client sends an end control."""
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if elapsed >= _MAX_UTTERANCE_SECONDS:
            await websocket.send_json({"type": "error", "code": "UTTERANCE_TOO_LONG"})
            return
        try:
            packet = await asyncio.wait_for(
                websocket.receive(), timeout=min(_IDLE_SECONDS, _MAX_UTTERANCE_SECONDS - elapsed)
            )
        except TimeoutError:
            await websocket.send_json({"type": "error", "code": "AUDIO_IDLE_TIMEOUT"})
            return
        if packet.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(packet.get("code", 1000))
        raw = packet.get("bytes")
        if raw is not None:
            if not raw or len(raw) > _MAX_FRAME_BYTES or len(raw) % 2 != 0:
                await websocket.send_json({"type": "error", "code": "INVALID_AUDIO_FRAME"})
                return
            yield raw
            continue
        text = packet.get("text")
        try:
            control = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            control = None
        if isinstance(control, dict) and control.get("type") == "end":
            return
        await websocket.send_json({"type": "error", "code": "INVALID_AUDIO_CONTROL"})
        return


@router.websocket("/ws/intake/{visit_id}/audio")
async def intake_audio_socket(websocket: WebSocket, visit_id: UUID) -> None:
    """Authenticate a patient visit and stream audio without persisting it."""
    await websocket.accept()
    connection_id = uuid4().hex
    reservation_key: str | None = None
    reserved = False
    try:
        try:
            auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except (TimeoutError, WebSocketDisconnect):
            await websocket.close(code=4401)
            return
        if not isinstance(auth, dict) or auth.get("type") != "auth":
            await websocket.close(code=4401)
            return
        try:
            claims = decode_access_token(
                auth.get("token", ""), settings=websocket.app.state.settings
            )
            hospital_id = UUID(claims["hospital_id"])
            subject_id = UUID(claims["sub"])
        except (AuthenticationTokenError, KeyError, TypeError, ValueError):
            await websocket.close(code=4401)
            return
        if claims.get("role") != "patient" or subject_id != visit_id:
            await websocket.close(code=4403)
            return

        redis = websocket.app.state.redis_state
        session_key = f"osa:intake:{hospital_id}:{visit_id}"
        raw_state = await redis.get(session_key)
        try:
            state = json.loads(raw_state) if raw_state else None
        except (TypeError, json.JSONDecodeError):
            state = None
        if (
            not isinstance(state, dict)
            or state.get("visit_id") != str(visit_id)
            or state.get("phase") == "consent"
            or state.get("language") not in _LANGUAGES
        ):
            await websocket.close(code=4403)
            return
        kiosk_id = state.get("kiosk_id")
        if not isinstance(kiosk_id, str):
            await websocket.close(code=4403)
            return

        settings = websocket.app.state.settings
        rate = await check_rate_limit(
            redis,
            websocket.app.state.memory_limiter,
            key=f"osa:{settings.app_env}:stt-connect:{kiosk_id}",
            limit=_KIOSK_SOCKET_RATE,
            window_seconds=60,
            fail_closed=False,
        )
        if rate is None or not rate.allowed:
            await websocket.send_json({"type": "error", "code": "RATE_LIMITED"})
            await websocket.close(code=4429)
            return

        now = int(time.time())
        reservation_key = f"osa:{settings.app_env}:stt-sockets:{kiosk_id}"
        try:
            reserved = bool(
                await redis.eval(
                    _RESERVE_SCRIPT,
                    1,
                    reservation_key,
                    now,
                    connection_id,
                    now + 90,
                    _KIOSK_SOCKET_LIMIT,
                )
            )
        except (RedisError, OSError, TimeoutError):
            await websocket.send_json({"type": "error", "code": "STT_UNAVAILABLE"})
            await websocket.close(code=1013)
            return
        if not reserved:
            await websocket.send_json({"type": "error", "code": "KIOSK_AUDIO_CAPACITY"})
            await websocket.close(code=4429)
            return

        await websocket.send_json({"type": "ready"})
        try:
            start = await asyncio.wait_for(websocket.receive_json(), timeout=_IDLE_SECONDS)
        except (TimeoutError, WebSocketDisconnect):
            await websocket.send_json({"type": "error", "code": "AUDIO_IDLE_TIMEOUT"})
            return
        expected_language = state["language"]
        if (
            not isinstance(start, dict)
            or start.get("type") != "start"
            or start.get("lang") != expected_language
        ):
            await websocket.send_json({"type": "error", "code": "INVALID_AUDIO_START"})
            return

        provider: STTProvider = websocket.app.state.stt_provider
        stream = provider.stream(_incoming_audio(websocket), _LANGUAGES[expected_language])
        started = time.monotonic()
        while True:
            remaining = _MAX_UTTERANCE_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                await websocket.send_json({"type": "error", "code": "UTTERANCE_TOO_LONG"})
                break
            try:
                event = await asyncio.wait_for(anext(stream), timeout=remaining)
            except StopAsyncIteration:
                break
            except TimeoutError:
                await websocket.send_json({"type": "error", "code": "STT_UNAVAILABLE"})
                await websocket.send_json({"type": "fallback", "mode": "touch"})
                break
            await websocket.send_json(_event_payload(event))
            if event.type == "unavailable":
                await websocket.send_json({"type": "fallback", "mode": "touch"})
                break
            if event.type == "final":
                break
        await stream.aclose()
    except WebSocketDisconnect:
        return
    except Exception:
        # Keep dependency details and transcript/audio content out of client errors.
        try:
            await websocket.send_json({"type": "error", "code": "STT_UNAVAILABLE"})
            await websocket.send_json({"type": "fallback", "mode": "touch"})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if reserved and reservation_key is not None:
            try:
                await websocket.app.state.redis_state.zrem(reservation_key, connection_id)
            except (RedisError, OSError, RuntimeError):
                pass
