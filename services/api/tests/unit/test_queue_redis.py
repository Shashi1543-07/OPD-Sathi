from uuid import UUID

import pytest

from app.domain.queue.redis_queue import (
    ENQUEUE_LUA,
    PUBLISH_DELTA_LUA,
    REMOVE_LUA,
    QueueRedis,
)

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
DEPARTMENT_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")
VISIT_ID = UUID("018e5a3d-6c10-7abc-8def-2123456789ab")


class FakeRedis:
    def __init__(self) -> None:
        self.eval_calls: list[tuple[object, ...]] = []
        self.published: list[tuple[str, str]] = []
        self.set_members: list[tuple[str, str]] = []
        self.missing_rank = False

    async def sadd(self, key: str, value: str) -> int:
        self.set_members.append((key, value))
        return 1

    async def eval(self, script: str, *args: object) -> list[int] | int:
        self.eval_calls.append((script, *args))
        if script == ENQUEUE_LUA:
            return [5, 7, 1]
        if script == REMOVE_LUA:
            return [1, 8]
        if script == PUBLISH_DELTA_LUA:
            return 9
        raise AssertionError("unexpected script")

    async def zrank(self, _key: str, _member: str) -> int | None:
        return None if self.missing_rank else 3

    async def zrange(
        self, _key: str, _start: int, _stop: int, *, withscores: bool
    ) -> list[tuple[str, float]]:
        assert withscores
        return [(str(VISIT_ID), 123.0)]

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


@pytest.mark.asyncio
async def test_redis_queue_keys_and_atomic_operations() -> None:
    client = FakeRedis()
    queue = QueueRedis(client, app_env="test")
    queue_key, channel, seq_key = queue.keys(HOSPITAL_ID, DEPARTMENT_ID)
    assert queue_key == f"osa:test:{HOSPITAL_ID}:q:{DEPARTMENT_ID}"
    assert channel.endswith(f"qchannel:{DEPARTMENT_ID}")
    assert seq_key.endswith(f"qseq:{DEPARTMENT_ID}")
    assert queue.status_channel(HOSPITAL_ID, VISIT_ID).endswith(f"status:{VISIT_ID}")
    assert queue.registry_key() == "osa:test:qregistry"

    position, sequence, changed = await queue.enqueue(
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        visit_id=VISIT_ID,
        score=123,
        item={"visit_id": str(VISIT_ID), "token": "GEN-001"},
    )
    assert (position, sequence, changed) == (5, 7, True)
    assert client.set_members == [(queue.registry_key(), queue_key)]
    assert client.eval_calls[0][0] == ENQUEUE_LUA
    assert await queue.remove(
        hospital_id=HOSPITAL_ID, department_id=DEPARTMENT_ID, visit_id=VISIT_ID
    ) == (True, 8)
    assert client.eval_calls[1][0] == REMOVE_LUA
    assert (
        await queue.position(
            hospital_id=HOSPITAL_ID, department_id=DEPARTMENT_ID, visit_id=VISIT_ID
        )
        == 4
    )
    client.missing_rank = True
    assert (
        await queue.position(
            hospital_id=HOSPITAL_ID, department_id=DEPARTMENT_ID, visit_id=VISIT_ID
        )
        is None
    )
    assert await queue.members(
        hospital_id=HOSPITAL_ID, department_id=DEPARTMENT_ID, start=0, stop=-1
    ) == [(str(VISIT_ID), 123.0)]
    assert (
        await queue.publish_delta(
            hospital_id=HOSPITAL_ID,
            department_id=DEPARTMENT_ID,
            ops=[{"op": "remove", "visit_id": str(VISIT_ID)}],
        )
        == 9
    )
    assert client.eval_calls[2][0] == PUBLISH_DELTA_LUA
    await queue.publish_status(
        hospital_id=HOSPITAL_ID, visit_id=VISIT_ID, payload={"changed": True}
    )
    assert len(client.published) == 1
