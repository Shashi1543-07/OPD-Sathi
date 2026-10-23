"""The worker's monitoring gauges, read as the real worker role (Phase 7, task 7.9.1).

The worker holds only column grants (it bypasses row-level security), so this proves the
grants are enough and that the gauges count what the alerts need: who is waiting where, and
an urgent alert no one has acknowledged.
"""

import os

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import create_async_engine

from app.workers.main import collect_operational_metrics
from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    start_kiosk_session,
    synthetic_hospital,
)


def _worker_url() -> str:
    url = os.getenv("TEST_WORKER_DATABASE_URL") or os.getenv("WORKER_DATABASE_URL") or ""
    if not url:
        pytest.skip("TEST_WORKER_DATABASE_URL is required to run as the worker role")
    return url


def _sample(name: str, **labels: str) -> float | None:
    return REGISTRY.get_sample_value(name, labels or None)


async def test_waiting_patients_and_unacknowledged_urgent_alerts_are_measured() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="chest pain", tiles=("chest_pain",))
            confirmed = session.confirm()
            assert confirmed.status_code == 200, confirmed.text

        engine = create_async_engine(_worker_url())
        try:
            async with engine.begin() as connection:
                await collect_operational_metrics(connection)
        finally:
            await engine.dispose()

        hospital_label = str(hospital.hospital_id)
        waiting = sum(
            _sample("queue_waiting_patients", hospital=hospital_label, department=code) or 0
            for code in ("GENMED", "ORTHO")
        )
        assert waiting == 1
        # The danger-sign tile raised an urgent alert that no one has acknowledged yet.
        assert (_sample("urgent_alerts_oldest_unacknowledged_age_seconds") or 0) > 0
        assert (_sample("outbox_oldest_pending_age_seconds") or 0) >= 0
        assert _sample("notifications_pending") is not None
