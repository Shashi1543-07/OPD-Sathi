"""Rate limiting decisions and outage behavior."""

import asyncio
import time
from collections import deque

import pytest
from redis.exceptions import RedisError

from app.core.ratelimit import InMemorySlidingWindow, check_rate_limit


class FakeRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.used = 0

    async def eval(self, *_args: object) -> list[int]:
        if self.fail:
            raise RedisError("offline")
        self.used += 1
        if self.used > 10:
            return [0, self.used - 1, 17]
        return [1, self.used, 0]


@pytest.mark.asyncio
async def test_sliding_window_allows_ten_and_throttles_eleven():
    redis = FakeRedis()
    memory = InMemorySlidingWindow()
    decisions = [
        await check_rate_limit(
            redis, memory, key="login", limit=10, window_seconds=60, fail_closed=True
        )
        for _ in range(11)
    ]
    assert all(result is not None for result in decisions)
    assert [result.allowed for result in decisions if result is not None].count(True) == 10
    last = decisions[-1]
    assert last is not None and not last.allowed
    assert last.retry_after == 17 and last.remaining == 0


@pytest.mark.asyncio
async def test_login_limit_fails_closed_when_redis_is_down():
    decision = await check_rate_limit(
        FakeRedis(fail=True),
        InMemorySlidingWindow(),
        key="login",
        limit=10,
        window_seconds=60,
        fail_closed=True,
    )
    assert decision is None


@pytest.mark.asyncio
async def test_other_limits_use_memory_fallback_when_redis_is_down():
    memory = InMemorySlidingWindow()
    results = [
        await check_rate_limit(
            FakeRedis(fail=True),
            memory,
            key="other",
            limit=2,
            window_seconds=60,
            fail_closed=False,
        )
        for _ in range(3)
    ]
    assert [result.allowed for result in results if result is not None] == [True, True, False]
    assert results[-1] is not None and results[-1].retry_after >= 1


def test_local_window_prunes_old_events():
    limiter = InMemorySlidingWindow()

    async def run() -> None:
        assert (await limiter.check("key", 1, 0)).allowed
        assert (await limiter.check("key", 1, 0)).allowed

    asyncio.run(run())


def test_local_limiter_prunes_stale_keys_at_its_size_bound():
    limiter = InMemorySlidingWindow()
    now = time.monotonic()
    limiter._events.update({f"stale-{index}": deque([now - 120]) for index in range(10_000)})

    async def run() -> None:
        assert (await limiter.check("fresh", 1, 60)).allowed

    asyncio.run(run())
    assert set(limiter._events) == {"fresh"}
