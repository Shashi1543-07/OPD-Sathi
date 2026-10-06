"""Measure alert delivery over the real Redis pub/sub and staff WebSocket path."""

from __future__ import annotations

import json
import math
import threading
import time
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from app.api.v1 import queue as queue_api
from app.config import get_settings
from app.core.security import issue_access_token


class _Connection:
    def __init__(self, department_id: UUID) -> None:
        self.department_id = department_id

    async def scalar(self, *_args: object, **_kwargs: object) -> UUID:
        return self.department_id


class _Engine:
    def __init__(self, department_id: UUID) -> None:
        self.connection = _Connection(department_id)

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _Engine:
        return self


class _NotifyingPubSub:
    def __init__(self, inner: object, subscribed: threading.Event) -> None:
        self.inner = inner
        self.subscribed = subscribed

    async def psubscribe(self, *patterns: str) -> None:
        await self.inner.psubscribe(*patterns)  # type: ignore[attr-defined]
        self.subscribed.set()

    async def get_message(self, **kwargs: object) -> dict[str, object] | None:
        return await self.inner.get_message(**kwargs)  # type: ignore[attr-defined]

    async def punsubscribe(self, *patterns: str) -> None:
        await self.inner.punsubscribe(*patterns)  # type: ignore[attr-defined]

    async def aclose(self) -> None:
        await self.inner.aclose()  # type: ignore[attr-defined]


class _NotifyingRedis:
    def __init__(self, inner: AsyncRedis, subscribed: threading.Event) -> None:
        self.inner = inner
        self.subscribed = subscribed

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def pubsub(self) -> _NotifyingPubSub:
        return _NotifyingPubSub(self.inner.pubsub(), self.subscribed)


def test_staff_websocket_alert_delivery_p95_under_one_second(monkeypatch) -> None:
    hospital_id = uuid4()
    actor_id = uuid4()
    department_id = uuid4()
    settings = get_settings()
    subscribed = threading.Event()
    redis_async = AsyncRedis.from_url(settings.redis_state_url, decode_responses=True)
    redis_sync = Redis.from_url(settings.redis_state_url, decode_responses=True)
    app = FastAPI()
    app.state.settings = settings
    app.state.engine = _Engine(department_id)
    app.state.redis_state = _NotifyingRedis(redis_async, subscribed)
    app.include_router(queue_api.router)

    async def scope_noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def no_queue_rows(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(queue_api, "set_hospital_scope", scope_noop)
    monkeypatch.setattr(queue_api.QueueRepository, "queued_visits", no_queue_rows)
    token = issue_access_token(
        subject=actor_id,
        role="nurse",
        hospital_id=hospital_id,
        settings=settings,
    )
    channel = f"osa:{settings.app_env}:{hospital_id}:alerts:{department_id}"

    try:
        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws/staff/queue?department_id={department_id}&alerts_only=true"
            ) as websocket:
                websocket.send_json({"type": "auth", "token": token})
                assert subscribed.wait(timeout=2), "staff socket did not subscribe to alerts"
                elapsed_ms: list[float] = []
                for index in range(100):
                    payload = {
                        "type": "alert.urgent",
                        "id": str(uuid4()),
                        "sequence": index,
                    }
                    started = time.perf_counter()
                    redis_sync.publish(channel, json.dumps(payload, separators=(",", ":")))
                    received = json.loads(websocket.receive_text())
                    elapsed_ms.append((time.perf_counter() - started) * 1000)
                    assert received == payload
        p95 = sorted(elapsed_ms)[math.ceil(0.95 * len(elapsed_ms)) - 1]
        print(f"staff alert delivery: n={len(elapsed_ms)}, p95={p95:.2f} ms")
        assert p95 < 1000, f"staff alert delivery p95 was {p95:.2f} ms"
    finally:
        redis_sync.close()
        import asyncio

        asyncio.run(redis_async.aclose())
