"""PostgreSQL and Redis-backed offline replay recovery tests."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import from_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.security import digest_secret
from app.db.session import create_engine as create_application_engine, set_hospital_scope
from app.domain.interview.consent import get_consent_copy
from app.main import create_app


def _database_settings() -> Settings:
    base = Settings()
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or base.database_url
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or base.migrations_database_url
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        pytest.skip("database credentials are required for offline intake integration tests")
    return base.model_copy(
        update={"database_url": app_url, "migrations_database_url": migrator_url}
    )


@pytest.mark.asyncio
async def test_application_engine_enforces_five_second_statement_timeout() -> None:
    settings = _database_settings()
    engine = create_application_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            timeout = await connection.scalar(text("SHOW statement_timeout"))
        assert timeout == "5s"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_offline_replay_survives_api_restart_without_duplicate_visit() -> None:
    settings = _database_settings()
    hospital_id, department_id, kiosk_id = uuid4(), uuid4(), uuid4()
    activation_code = "offline-kiosk-" + uuid4().hex
    idempotency_key = "offline-" + uuid4().hex
    engine = create_async_engine(settings.migrations_database_url)
    redis_client = from_url(settings.redis_state_url, decode_responses=True)
    try:
        try:
            await redis_client.ping()
        except Exception as exc:
            pytest.skip(f"Redis is required for offline replay integration tests: {exc}")

        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'Offline test', :code)"),
                {"id": hospital_id, "code": f"offline-{hospital_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, 'GENMED', 'MED', "
                    "'General Medicine', 'General Medicine', 'General Medicine')"
                ),
                {"id": department_id, "hospital_id": hospital_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, 'Offline test kiosk', :activation_hash)"
                ),
                {
                    "id": kiosk_id,
                    "hospital_id": hospital_id,
                    "activation_hash": digest_secret(activation_code),
                },
            )

        consent = get_consent_copy("en")
        payload = {
            "idempotency_key": idempotency_key,
            "language": "en",
            "consent_granted": True,
            "consent_version": consent["version"],
            "consent_sha256": consent["sha256"],
            "age": 38,
            "sex": "unknown",
            "chief_complaint": "headache",
            "duration": "since yesterday",
            "severity": 3,
            "conditions": "",
            "medications": "",
            "allergies": "",
            "danger_tile_ids": [],
        }

        app = create_app(
            settings, engine_factory=lambda _url: create_async_engine(settings.database_url)
        )
        with TestClient(app) as first_client:
            activation = first_client.post(
                "/kiosks/activate", json={"activation_code": activation_code}
            )
            assert activation.status_code == 200, activation.text
            kiosk_token = activation.json()["access_token"]
            headers = {
                "Authorization": f"Bearer {kiosk_token}",
                "Idempotency-Key": idempotency_key,
            }
            first = first_client.post("/intake/offline-submissions", json=payload, headers=headers)
            assert first.status_code == 201, first.text

        # A new FastAPI lifespan represents the API process restarting while the
        # client still has the queued submission and did not receive confirmation.
        restarted_app = create_app(
            settings, engine_factory=lambda _url: create_async_engine(settings.database_url)
        )
        with TestClient(restarted_app) as restarted_client:
            replay = restarted_client.post(
                "/intake/offline-submissions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {kiosk_token}",
                    "Idempotency-Key": idempotency_key,
                },
            )
            assert replay.status_code == 201, replay.text
            assert replay.json() == first.json()

        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            visits = await connection.scalar(
                text("SELECT count(*) FROM visits WHERE hospital_id = :id"), {"id": hospital_id}
            )
            assert visits == 1
    finally:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM audit_log WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(text("DELETE FROM kiosks WHERE id = :id"), {"id": kiosk_id})
            await connection.execute(
                text("DELETE FROM visits WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM patients WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM departments WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM outbox WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id}
            )
        await redis_client.aclose()
        await engine.dispose()
