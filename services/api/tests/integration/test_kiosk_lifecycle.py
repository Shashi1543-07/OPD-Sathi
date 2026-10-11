"""PostgreSQL-backed kiosk pairing lifecycle and administrator archive (delete)."""

import base64
import os
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.security import hash_password
from app.db.session import set_hospital_scope
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
        pytest.skip("database credentials are required for PostgreSQL kiosk lifecycle tests")
    return base.model_copy(
        update={
            "database_url": app_url,
            "migrations_database_url": migrator_url,
            # CI has no .env; use fixed test-only key material when none is configured.
            "app_data_key": base.app_data_key or base64.b64encode(b"d" * 32).decode(),
            "app_hmac_key": base.app_hmac_key or base64.b64encode(b"h" * 32).decode(),
        }
    )


@pytest.mark.asyncio
async def test_kiosk_pairing_renewal_repairing_and_archive() -> None:
    settings = _database_settings()
    migrator_engine = create_async_engine(settings.migrations_database_url)
    hospital_id, admin_id, department_id = uuid4(), uuid4(), uuid4()
    admin_email = f"admin-{admin_id}@example.test"
    password = "Synthetic-test-password-2026"
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'Kiosk test', :code)"),
                {"id": hospital_id, "code": f"kiosk-{hospital_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, 'GENMED', 'MED', "
                    "'General Medicine', 'सामान्य', 'सामान्य')"
                ),
                {"id": department_id, "hospital_id": hospital_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'admin', 'Synthetic admin', :email, :hash)"
                ),
                {
                    "id": admin_id,
                    "hospital_id": hospital_id,
                    "email": admin_email,
                    "hash": hash_password(password),
                },
            )

        app = create_app(
            settings, engine_factory=lambda _url: create_async_engine(settings.database_url)
        )
        with TestClient(app) as client:
            login = client.post(
                "/auth/login",
                json={"hospital_id": str(hospital_id), "email": admin_email, "password": password},
            )
            assert login.status_code == 200
            bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}

            def admin(method: str, path: str, **kwargs: object) -> object:
                headers = {**bearer, "Idempotency-Key": uuid4().hex}
                return client.request(method, path, headers=headers, **kwargs)  # type: ignore[arg-type]

            created = admin("POST", "/admin/kiosks", json={"label": "Reception"})
            kiosk_id = created.json()["id"]
            first = client.post(
                "/kiosks/activate", json={"activation_code": created.json()["activation_code"]}
            )
            assert first.status_code == 200
            secret_a = first.json()["device_secret"]

            # The device secret renews the short-lived access token indefinitely.
            renewed = client.post("/kiosks/token", json={"device_secret": secret_a})
            assert renewed.status_code == 200
            kiosk_a = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
            started = client.post("/intake/sessions", json={"language": "en"}, headers=kiosk_a)
            assert started.status_code == 201

            # Turned off: refused but still paired, so turning it on needs no new code.
            admin("PATCH", f"/admin/kiosks/{kiosk_id}", json={"is_active": False})
            assert client.post("/kiosks/token", json={"device_secret": secret_a}).status_code == 403
            turned_off = client.post("/intake/sessions", json={"language": "en"}, headers=kiosk_a)
            assert turned_off.status_code == 409
            assert turned_off.json()["code"] == "KIOSK_TURNED_OFF"
            admin("PATCH", f"/admin/kiosks/{kiosk_id}", json={"is_active": True})
            assert client.post("/kiosks/token", json={"device_secret": secret_a}).status_code == 200

            # A new code re-pairs the same kiosk record to another device.
            issued = admin("POST", f"/admin/kiosks/{kiosk_id}/activation-code")
            assert issued.status_code == 200 and issued.json()["id"] == kiosk_id
            time.sleep(6)  # past the clock slack allowed for token issue times
            second = client.post(
                "/kiosks/activate", json={"activation_code": issued.json()["activation_code"]}
            )
            assert second.status_code == 200
            assert client.post("/kiosks/token", json={"device_secret": secret_a}).status_code == 401
            replaced = client.post("/intake/sessions", json={"language": "en"}, headers=kiosk_a)
            assert replaced.status_code == 401
            assert replaced.json()["code"] == "KIOSK_NOT_PAIRED"

            listed = admin("GET", "/admin/kiosks").json()["items"]
            assert listed[0]["registrations_total"] == 1
            assert listed[0]["activation_pending"] is False

            # Delete archives: the device stops, history keeps pointing at the kiosk.
            assert admin("DELETE", f"/admin/kiosks/{kiosk_id}").status_code == 200
            assert (
                client.post(
                    "/kiosks/token", json={"device_secret": second.json()["device_secret"]}
                ).status_code
                == 401
            )
            assert admin("GET", "/admin/kiosks").json()["items"] == []
            assert admin("DELETE", f"/admin/users/{admin_id}").status_code == 409

            # A department with staff cannot be deleted until they are moved.
            doctor = admin(
                "POST",
                "/admin/users",
                json={
                    "role": "doctor",
                    "name": "Synthetic doctor",
                    "email": f"doc-{uuid4()}@example.test",
                    "password": password,
                    "department_id": str(department_id),
                },
            )
            blocked = admin("DELETE", f"/admin/departments/{department_id}")
            assert blocked.status_code == 409 and blocked.json()["code"] == "DEPARTMENT_IN_USE"
            assert admin("DELETE", f"/admin/users/{doctor.json()['id']}").status_code == 200

        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            visit_kiosk = await connection.scalar(
                text("SELECT kiosk_id FROM visits WHERE hospital_id = :id"), {"id": hospital_id}
            )
            assert str(visit_kiosk) == kiosk_id
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            for table in (
                "audit_log",
                "consents",
                "visits",
                "patients",
                "refresh_tokens",
                "kiosks",
                "users",
                "departments",
            ):
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE hospital_id = :id"), {"id": hospital_id}
                )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id}
            )
        await migrator_engine.dispose()
