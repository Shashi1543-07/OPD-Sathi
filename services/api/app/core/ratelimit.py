"""Redis sliding-window rate limiting with a bounded local fallback."""

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

_SLIDING_WINDOW = """
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local entries = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
  local retry_after = math.max(1, math.ceil((tonumber(entries[2]) + window - now) / 1000))
  return {0, count, retry_after}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class RateLimitResult:
    """Decision and response values for one rate-limited request."""

    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class InMemorySlidingWindow:
    """Per-process fallback for non-auth limits during Redis outages."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now + 0.999))
                return RateLimitResult(False, limit, 0, retry_after)
            events.append(now)
            self._prune_empty(cutoff)
            return RateLimitResult(True, limit, limit - len(events), 0)

    def _prune_empty(self, cutoff: float) -> None:
        if len(self._events) < 10_000:
            return
        for key, events in list(self._events.items()):
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                self._events.pop(key, None)


async def check_rate_limit(
    redis: Redis,
    memory: InMemorySlidingWindow,
    *,
    key: str,
    limit: int,
    window_seconds: int,
    fail_closed: bool,
) -> RateLimitResult | None:
    """Return a decision; `None` means fail-closed storage is unavailable."""
    try:
        allowed, used, retry_after = await redis.eval(
            _SLIDING_WINDOW,
            1,
            key,
            window_seconds * 1000,
            limit,
            uuid4().hex,
        )
    except (RedisError, OSError, TimeoutError):
        if fail_closed:
            return None
        return await memory.check(key, limit, window_seconds)
    used_count = int(used)
    return RateLimitResult(
        allowed=bool(int(allowed)),
        limit=limit,
        remaining=max(0, limit - used_count),
        retry_after=int(retry_after),
    )
