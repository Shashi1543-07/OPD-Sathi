"""Container health check for the worker: is its queue relay still ticking?

The relay runs every two seconds and records a heartbeat in Redis after each tick that
completes, failed or not (``app.workers.main``). A worker whose event loop is stuck, or that
cannot reach Redis, stops recording it and is reported unhealthy, so Docker restarts it.

Usage (Compose health check): python -m app.workers.health
"""

import asyncio
import sys
import time

import redis.asyncio as redis

from app.config import get_settings

STALE_AFTER_SECONDS = 60


def heartbeat_key(app_env: str) -> str:
    return f"osa:{app_env}:worker:heartbeat"


async def heartbeat_age() -> float | None:
    """Seconds since the last completed relay tick, or None when there is none."""
    settings = get_settings()
    client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_state_url, decode_responses=True, socket_timeout=3
    )
    try:
        value = await client.get(heartbeat_key(settings.app_env))
    finally:
        await client.aclose()
    return time.time() - float(value) if value else None


def main() -> int:
    try:
        age = asyncio.run(heartbeat_age())
    except Exception as exc:
        print(f"worker unhealthy: Redis unreachable ({type(exc).__name__})")
        return 1
    if age is None or age > STALE_AFTER_SECONDS:
        print("worker unhealthy: no relay tick in the last minute")
        return 1
    print(f"worker healthy: last relay tick {age:.0f} s ago")
    return 0


if __name__ == "__main__":
    sys.exit(main())
