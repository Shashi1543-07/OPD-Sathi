"""Settings -> System (Phase 7, task 7.9.7): administrators only, their own hospital only."""

import json
import time

import redis

from app.workers.health import heartbeat_key, system_status_key
from tests.integration.synthetic import api_client, synthetic_hospital


def _redis(settings: object) -> redis.Redis:
    return redis.Redis.from_url(settings.redis_state_url)  # type: ignore[attr-defined]


async def test_the_system_page_shows_the_workers_view_and_only_this_hospitals_kiosks() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        env = hospital.settings.app_env
        client = _redis(hospital.settings)
        now = time.time()
        try:
            client.set(
                system_status_key(env),
                json.dumps({
                    "checked_at": now,
                    "redis": True,
                    "postgres": True,
                    "disk_free_ratio": 0.42,
                    "certificate_expires_at": now + 40 * 86400,
                    "backup_last_success": now - 3 * 3600,
                }),
            )
            client.set(heartbeat_key(env), str(now))
            with api_client(hospital.settings) as api:
                assert api.get("/admin/system", headers=hospital.staff("nurse")).status_code == 403
                response = api.get("/admin/system", headers=hospital.staff("admin"))
        finally:
            client.delete(system_status_key(env))
            client.close()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == hospital.settings.app_version
        assert body["worker"] == "up" and body["redis"] == "up" and body["database"] == "up"
        assert body["disk_free_ratio"] == 0.42
        assert body["certificate"]["days_left"] in (39, 40)
        assert body["backup"]["age_hours"] == 3.0
        kiosk_ids = {kiosk["id"] for kiosk in body["kiosks"]}
        assert str(hospital.kiosk_id) in kiosk_ids
        assert str(other.kiosk_id) not in kiosk_ids
        assert body["ai"]["sms"] == hospital.settings.notification_provider


async def test_without_the_worker_the_page_says_so() -> None:
    async with synthetic_hospital() as hospital:
        client = _redis(hospital.settings)
        try:
            client.delete(system_status_key(hospital.settings.app_env))
            client.delete(heartbeat_key(hospital.settings.app_env))
        finally:
            client.close()
        with api_client(hospital.settings) as api:
            body = api.get("/admin/system", headers=hospital.staff("admin")).json()
        assert body["worker"] == "unknown"
        assert body["backup"]["last_success"] is None
        assert body["checked_at"] is None
