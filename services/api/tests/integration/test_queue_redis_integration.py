"""Exercise atomic sorted-set writes and published sequence deltas on real Redis."""

import asyncio
import json
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.domain.queue.redis_queue import QueueRedis


@pytest.mark.asyncio
async def test_redis_enqueue_reprioritize_remove_and_publish() -> None:
    settings = Settings()
    if "local-only" in settings.redis_state_url:
        pytest.skip("Redis credentials are required for the queue integration test")
    client = Redis.from_url(settings.redis_state_url, decode_responses=True)
    hospital_id, department_id = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    queue = QueueRedis(client, app_env="test")
    queue_key, channel, sequence_key = queue.keys(hospital_id, department_id)
    registry = queue.registry_key()
    pubsub = client.pubsub()
    try:
        try:
            await client.ping()
        except (RedisError, OSError, TimeoutError):
            pytest.skip("Redis is unavailable for the queue integration test")
        await pubsub.subscribe(channel)
        await pubsub.get_message(timeout=1)
        first_result = await queue.enqueue(
            hospital_id=hospital_id,
            department_id=department_id,
            visit_id=first,
            score=100,
            item={"visit_id": str(first), "token": "GEN-001", "urgency": "routine"},
        )
        reprioritized = await queue.enqueue(
            hospital_id=hospital_id,
            department_id=department_id,
            visit_id=first,
            score=50,
            item={"visit_id": str(first), "token": "GEN-001", "urgency": "urgent"},
        )
        second_result = await queue.enqueue(
            hospital_id=hospital_id,
            department_id=department_id,
            visit_id=second,
            score=75,
            item={"visit_id": str(second), "token": "GEN-002", "urgency": "routine"},
        )
        assert first_result == (1, 1, True)
        assert reprioritized == (1, 2, True)
        assert second_result == (2, 3, True)
        assert [
            member
            for member, _ in await queue.members(
                hospital_id=hospital_id, department_id=department_id, start=0, stop=-1
            )
        ] == [str(first), str(second)]
        assert await queue.remove(
            hospital_id=hospital_id, department_id=department_id, visit_id=first
        ) == (True, 4)
        messages = []
        for _ in range(20):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message:
                messages.append(message)
            if len(messages) >= 4:
                break
            await asyncio.sleep(0.01)
        assert len(messages) == 4
        assert [int(json.loads(message["data"])["seq"]) for message in messages] == [1, 2, 3, 4]
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.delete(queue_key, sequence_key)
        await client.srem(registry, queue_key)
        await client.aclose()
