"""Audio socket tests use in-memory Redis and synthetic binary frames."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ai.providers.stt.chain import STTProviderChain
from app.ai.providers.stt.mock import MockSTT
from app.api.v1.audio import router
from app.config import Settings
from app.core.ratelimit import InMemorySlidingWindow
from app.core.security import issue_access_token


class FakeRedis:
    def __init__(self, state: dict[str, str], *, reserve_result: int = 1) -> None:
        self.state = state
        self.reserve_result = reserve_result
        self.removed: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.state.get(key)

    async def eval(self, script: str, *_args: object) -> int | list[int]:
        if "local connection_id" in script:
            return self.reserve_result
        return [1, 1, 0]

    async def zrem(self, key: str, member: str) -> int:
        self.removed.append((key, member))
        return 1


def _socket_app(*, language: str = "en", phase: str = "interview", reserve_result: int = 1):
    settings = Settings(_env_file=None, app_env="test")
    hospital_id, visit_id, kiosk_id = uuid4(), uuid4(), uuid4()
    redis = FakeRedis(
        {
            f"osa:intake:{hospital_id}:{visit_id}": json.dumps(
                {
                    "visit_id": str(visit_id),
                    "kiosk_id": str(kiosk_id),
                    "language": language,
                    "phase": phase,
                }
            )
        },
        reserve_result=reserve_result,
    )
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.redis_state = redis
    app.state.memory_limiter = InMemorySlidingWindow()
    mock = MockSTT(transcripts={"en-IN": ["synthetic spoken answer"]})
    app.state.stt_provider = STTProviderChain(mock, mock)
    token = issue_access_token(
        subject=visit_id,
        role="patient",
        hospital_id=hospital_id,
        settings=settings,
        lifetime=timedelta(minutes=5),
    )
    return app, redis, f"/ws/intake/{visit_id}/audio", token


def test_audio_socket_authenticates_streams_and_releases_kiosk_slot() -> None:
    app, redis, path, token = _socket_app()
    with TestClient(app).websocket_connect(path) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json({"type": "start", "lang": "en"})
        websocket.send_bytes(b"\x00\x00" * 8)
        websocket.send_json({"type": "end"})
        messages = [websocket.receive_json() for _ in range(3)]
    assert [message["type"] for message in messages] == ["partial", "partial", "final"]
    assert len(redis.removed) == 1


def test_audio_socket_rejects_preconsent_session() -> None:
    app, _redis, path, token = _socket_app(phase="consent")
    with TestClient(app).websocket_connect(path) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 4403


def test_audio_socket_enforces_kiosk_concurrency_limit() -> None:
    app, _redis, path, token = _socket_app(reserve_result=0)
    with TestClient(app).websocket_connect(path) as websocket:
        websocket.send_json({"type": "auth", "token": token})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "KIOSK_AUDIO_CAPACITY",
        }
        with pytest.raises(WebSocketDisconnect) as error:
            websocket.receive_json()
        assert error.value.code == 4429
