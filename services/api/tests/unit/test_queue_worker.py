import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.domain.queue.redis_queue import ENQUEUE_LUA, PUBLISH_DELTA_LUA, REMOVE_LUA
from app.workers.main import rebuild_all, relay_once

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
DEPARTMENT_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = rows

    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class FakeConnection:
    def __init__(
        self,
        *,
        event: dict[str, object],
        visit: dict[str, object],
        rows: list[dict[str, object]] | None = None,
    ):
        self.event = event
        self.visit = visit
        self.rows = rows or []
        self.processed = False
        self.attempts = 0
        self.statements: list[str] = []

    async def execute(self, statement: object, _params: object = None) -> FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT id, hospital_id, payload FROM outbox" in sql:
            return FakeResult([] if self.processed else [self.event])
        if "WHERE status = 'queued' ORDER BY" in sql:
            return FakeResult(self.rows)
        if "SELECT id AS visit_id" in sql and "WHERE hospital_id" in sql:
            return FakeResult([self.visit])
        if "SET processed_at = now()" in sql:
            self.processed = True
        if "SET attempts = attempts + 1" in sql:
            self.attempts += 1
        return FakeResult([])


class FakeRedis:
    def __init__(self, *, fail: bool = False, rows: list[dict[str, object]] | None = None):
        self.fail = fail
        self.rows = rows or []
        self.scripts: list[str] = []
        self.calls: list[tuple[object, ...]] = []
        self.published: list[tuple[str, str]] = []
        self.registry: set[str] = set()
        self.deleted: list[str] = []

    async def smembers(self, _key: str) -> set[str]:
        return set(self.registry)

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)

    async def sadd(self, _key: str, value: str) -> int:
        self.registry.add(value)
        return 1

    async def eval(self, script: str, *_args: object) -> list[int] | int:
        if self.fail:
            raise RedisConnectionError("simulated Redis outage")
        self.scripts.append(script)
        self.calls.append((script, *_args))
        if script == ENQUEUE_LUA:
            return [1, 1, 1]
        if script == REMOVE_LUA:
            return [1, 1]
        if script == PUBLISH_DELTA_LUA:
            return 2
        raise AssertionError("unexpected Redis script")

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def zrange(self, _key: str, _start: int, _stop: int, *, withscores: bool) -> list[object]:
        assert withscores
        return []


def _event_and_visit(status: str = "queued") -> tuple[dict[str, object], dict[str, object]]:
    visit_id = uuid4()
    now = datetime.now(UTC)
    return (
        {
            "id": uuid4(),
            "hospital_id": HOSPITAL_ID,
            "payload": {"op": "upsert", "visit_id": str(visit_id)},
        },
        {
            "visit_id": visit_id,
            "hospital_id": HOSPITAL_ID,
            "department_id": DEPARTMENT_ID,
            "status": status,
            "urgency": "routine",
            "age_years": 30,
            "pregnant": False,
            "token_label": "GEN-001",
            "queued_at": now,
        },
    )


@pytest.mark.asyncio
async def test_outbox_relay_is_idempotent_and_publishes_patient_status() -> None:
    event, visit = _event_and_visit()
    connection = FakeConnection(event=event, visit=visit)
    client = FakeRedis()
    settings = Settings(app_env="test")

    assert await relay_once(connection, client, settings=settings) == 1
    assert connection.processed
    assert client.scripts == [ENQUEUE_LUA]
    assert len(client.published) == 1
    assert await relay_once(connection, client, settings=settings) == 0


@pytest.mark.asyncio
async def test_outbox_retries_after_redis_recovery() -> None:
    event, visit = _event_and_visit()
    connection = FakeConnection(event=event, visit=visit)
    client = FakeRedis(fail=True)
    settings = Settings(app_env="test")

    assert await relay_once(connection, client, settings=settings) == 0
    assert connection.attempts == 1
    assert not connection.processed

    client.fail = False
    assert await relay_once(connection, client, settings=settings) == 1
    assert connection.processed


@pytest.mark.asyncio
async def test_rebuild_uses_database_scores_and_marks_registry_ready() -> None:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    labels = [
        ("A", 0, "routine", 30),
        ("B", 20, "priority", 30),
        ("C", 30, "urgent", 30),
        ("D", 10, "routine", 70),
    ]
    rows = [
        {
            "visit_id": uuid4(),
            "hospital_id": HOSPITAL_ID,
            "department_id": DEPARTMENT_ID,
            "status": "queued",
            "urgency": urgency,
            "age_years": age,
            "pregnant": False,
            "token_label": label,
            "queued_at": start + timedelta(minutes=minutes),
        }
        for label, minutes, urgency, age in labels
    ]
    connection = FakeConnection(event={}, visit={}, rows=rows)
    client = FakeRedis(rows=rows)
    settings = Settings(app_env="test")
    assert await rebuild_all(connection, client, settings=settings) == 4
    scores = [
        (int(call[-2]), json.loads(str(call[-1]))["ops"][0]["item"]["token"])
        for call in client.calls
        if call[0] == ENQUEUE_LUA
    ]
    assert [label for _, label in sorted(scores)] == ["C", "B", "D", "A"]
    assert "ready" in client.registry
