"""Idempotent Redis sorted-set read model with atomic sequence deltas."""

import json
from typing import Any
from uuid import UUID

ENQUEUE_LUA = """
local changed = redis.call('ZADD', KEYS[1], 'CH', ARGV[2], ARGV[1])
local rank = redis.call('ZRANK', KEYS[1], ARGV[1])
local seq = tonumber(redis.call('GET', KEYS[3]) or '0')
if changed == 1 then
  seq = redis.call('INCR', KEYS[3])
  local delta = cjson.decode(ARGV[3])
  delta.seq = seq
  delta.ops[1].item.position = rank + 1
  delta.ops[1].item.score = tonumber(ARGV[2])
  redis.call('PUBLISH', KEYS[2], cjson.encode(delta))
end
return {rank + 1, seq, changed}
"""

REMOVE_LUA = """
local changed = redis.call('ZREM', KEYS[1], ARGV[1])
local seq = tonumber(redis.call('GET', KEYS[3]) or '0')
if changed == 1 then
  seq = redis.call('INCR', KEYS[3])
  local delta = cjson.decode(ARGV[2])
  delta.seq = seq
  redis.call('PUBLISH', KEYS[2], cjson.encode(delta))
end
return {changed, seq}
"""

PUBLISH_DELTA_LUA = """
local seq = redis.call('INCR', KEYS[2])
local delta = cjson.decode(ARGV[1])
delta.seq = seq
redis.call('PUBLISH', KEYS[1], cjson.encode(delta))
return seq
"""


class QueueRedis:
    def __init__(self, client: Any, *, app_env: str):
        self.client = client
        self.app_env = app_env

    def keys(self, hospital_id: UUID, department_id: UUID) -> tuple[str, str, str]:
        base = f"osa:{self.app_env}:{hospital_id}"
        queue = f"{base}:q:{department_id}"
        return queue, f"{base}:qchannel:{department_id}", f"{base}:qseq:{department_id}"

    def registry_key(self) -> str:
        return f"osa:{self.app_env}:qregistry"

    def status_channel(self, hospital_id: UUID, visit_id: UUID) -> str:
        return f"osa:{self.app_env}:{hospital_id}:status:{visit_id}"

    async def enqueue(
        self,
        *,
        hospital_id: UUID,
        department_id: UUID,
        visit_id: UUID,
        score: int,
        item: dict[str, Any],
    ) -> tuple[int, int, bool]:
        queue_key, channel, sequence_key = self.keys(hospital_id, department_id)
        await self.client.sadd(self.registry_key(), queue_key)
        delta = json.dumps(
            {"type": "queue.delta", "ops": [{"op": "upsert", "item": item}]},
            separators=(",", ":"),
        )
        result = await self.client.eval(
            ENQUEUE_LUA, 3, queue_key, channel, sequence_key, str(visit_id), score, delta
        )
        return int(result[0]), int(result[1]), bool(result[2])

    async def remove(
        self, *, hospital_id: UUID, department_id: UUID, visit_id: UUID
    ) -> tuple[bool, int]:
        queue_key, channel, sequence_key = self.keys(hospital_id, department_id)
        delta = json.dumps(
            {"type": "queue.delta", "ops": [{"op": "remove", "visit_id": str(visit_id)}]},
            separators=(",", ":"),
        )
        result = await self.client.eval(
            REMOVE_LUA, 3, queue_key, channel, sequence_key, str(visit_id), delta
        )
        return bool(result[0]), int(result[1])

    async def position(
        self, *, hospital_id: UUID, department_id: UUID, visit_id: UUID
    ) -> int | None:
        queue_key, _, _ = self.keys(hospital_id, department_id)
        rank = await self.client.zrank(queue_key, str(visit_id))
        return None if rank is None else int(rank) + 1

    async def members(
        self, *, hospital_id: UUID, department_id: UUID, start: int, stop: int
    ) -> list[tuple[str, float]]:
        queue_key, _, _ = self.keys(hospital_id, department_id)
        rows = await self.client.zrange(queue_key, start, stop, withscores=True)
        return [(str(member), float(score)) for member, score in rows]

    async def publish_status(
        self, *, hospital_id: UUID, visit_id: UUID, payload: dict[str, Any]
    ) -> None:
        await self.client.publish(
            self.status_channel(hospital_id, visit_id),
            json.dumps(payload, separators=(",", ":")),
        )

    async def publish_delta(
        self, *, hospital_id: UUID, department_id: UUID, ops: list[dict[str, Any]]
    ) -> int:
        """Publish an atomic sequenced state delta for rows outside the waiting set."""
        _, channel, sequence_key = self.keys(hospital_id, department_id)
        delta = json.dumps({"type": "queue.delta", "ops": ops}, separators=(",", ":"))
        return int(await self.client.eval(PUBLISH_DELTA_LUA, 2, channel, sequence_key, delta))
