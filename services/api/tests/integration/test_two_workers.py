"""Two workers share the work and never do it twice (Phase 7, task 7.8.7).

A second worker container is how the queue relay scales. The relay claims outbox events with
FOR UPDATE SKIP LOCKED, so two relays running at the same moment split the events between
them; the scheduled jobs are unique in arq, so each scheduled run executes once however many
workers there are.
"""

import asyncio
import os

import pytest
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine

from app.workers.main import WorkerSettings, relay_once
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


def test_scheduled_jobs_run_once_whatever_the_number_of_workers() -> None:
    assert WorkerSettings.cron_jobs
    assert all(job.unique for job in WorkerSettings.cron_jobs)


async def test_two_relays_at_once_apply_every_event_exactly_once() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            for complaint in ("fever", "cough", "headache", "back pain"):
                session = start_kiosk_session(client, kiosk)
                complete_intake(session, complaint=complaint)
                assert session.confirm().status_code == 200
        pending = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM outbox WHERE hospital_id = :h AND processed_at IS NULL "
            "AND topic = 'queue.changed'",
            h=hospital.hospital_id,
        )
        assert pending[0]["n"] >= 4

        engines = [create_async_engine(_worker_url()) for _ in range(2)]
        clients = [
            redis.from_url(  # type: ignore[no-untyped-call]
                hospital.settings.redis_state_url, decode_responses=True
            )
            for _ in range(2)
        ]

        async def relay(index: int) -> int:
            total = 0
            while True:
                async with engines[index].begin() as connection:
                    count = await relay_once(connection, clients[index], settings=hospital.settings)
                if not count:
                    return total
                total += count

        try:
            done = await asyncio.gather(relay(0), relay(1))
        finally:
            for engine in engines:
                await engine.dispose()
            for client_ in clients:
                await client_.aclose()

        left = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM outbox WHERE hospital_id = :h AND processed_at IS NULL "
            "AND topic = 'queue.changed'",
            h=hospital.hospital_id,
        )
        assert left[0]["n"] == 0
        # Other tests' events may be relayed too, but none twice: the two shares add up to at
        # least this hospital's events, and every one is marked processed exactly once.
        assert sum(done) >= pending[0]["n"]
        attempts = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT max(attempts) AS most FROM outbox WHERE hospital_id = :h",
            h=hospital.hospital_id,
        )
        assert (attempts[0]["most"] or 0) <= 1
