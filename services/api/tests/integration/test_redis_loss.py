"""Losing Redis loses no queue (Phase 7, task 7.7.4; docs/runbooks/redis-loss.md).

PostgreSQL holds every visit; Redis holds the live queue read model. After Redis loses its
data, the rebuild the worker runs at start-up (and `app.cli queue-rebuild`) puts every waiting
patient back in exactly the same place.
"""

import os

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.queue.redis_queue import QueueRedis
from app.workers.main import rebuild_all, relay_once
from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


def _worker_url() -> str:
    url = os.getenv("TEST_WORKER_DATABASE_URL") or os.getenv("WORKER_DATABASE_URL") or ""
    if not url:
        pytest.skip("TEST_WORKER_DATABASE_URL is required to run as the worker role")
    return url


async def test_the_queue_is_rebuilt_in_the_same_order_after_redis_loses_its_data() -> None:
    async with synthetic_hospital() as hospital:
        visits = []
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            for complaint, tiles in (("fever", ()), ("cough", ()), ("chest pain", ("chest_pain",))):
                session = start_kiosk_session(client, kiosk)
                complete_intake(session, complaint=complaint, tiles=tiles)
                assert session.confirm().status_code == 200
                visits.append(session.visit_id)

        engine = create_async_engine(_worker_url())
        client_ = redis.from_url(  # type: ignore[no-untyped-call]
            hospital.settings.redis_state_url, decode_responses=True
        )
        queue = QueueRedis(client_, app_env=hospital.settings.app_env)
        try:
            async with engine.begin() as connection:
                while await relay_once(connection, client_, settings=hospital.settings):
                    pass
            rows = await fetch(
                hospital.settings,
                hospital.hospital_id,
                "SELECT id, department_id FROM visits WHERE hospital_id = :h AND status = 'queued'",
                h=hospital.hospital_id,
            )
            assert {str(row["id"]) for row in rows} == set(visits)

            async def positions() -> dict[str, int | None]:
                return {
                    str(row["id"]): await queue.position(
                        hospital_id=hospital.hospital_id,
                        department_id=row["department_id"],
                        visit_id=row["id"],
                    )
                    for row in rows
                }

            before = await positions()
            assert all(position is not None for position in before.values())

            # Redis loses everything (a crash without its append-only file, a new server).
            await client_.flushdb()
            assert all(position is None for position in (await positions()).values())

            async with engine.begin() as connection:
                await rebuild_all(connection, client_, settings=hospital.settings)
            assert await positions() == before
        finally:
            await client_.aclose()
            await engine.dispose()
