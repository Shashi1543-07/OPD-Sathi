"""Exercise the sliding-window Lua script against the configured Redis service."""

from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import Settings
from app.core.ratelimit import InMemorySlidingWindow, check_rate_limit


@pytest.mark.asyncio
async def test_real_redis_throttles_the_eleventh_request() -> None:
    settings = Settings()
    if "local-only" in settings.redis_state_url:
        pytest.skip("Redis credentials are required for the Redis integration test")
    redis = Redis.from_url(settings.redis_state_url, decode_responses=True)
    key = f"osa:test:rl:{uuid4()}"
    try:
        try:
            await redis.ping()
        except (RedisError, OSError, TimeoutError):
            pytest.skip("Redis is unavailable for the sliding-window integration test")
        results = [
            await check_rate_limit(
                redis,
                InMemorySlidingWindow(),
                key=key,
                limit=10,
                window_seconds=60,
                fail_closed=True,
            )
            for _ in range(11)
        ]
        assert all(result is not None for result in results)
        assert [result.allowed for result in results if result is not None] == [
            *([True] * 10),
            False,
        ]
        assert results[-1] is not None and results[-1].retry_after >= 1
    finally:
        await redis.delete(key)
        await redis.aclose()
