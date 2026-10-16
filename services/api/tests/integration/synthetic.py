"""Synthetic hospital setup, teardown and API drivers for integration tests.

Every record is fictional. Each test gets its own hospital, so tests can run against a shared
database without seeing each other's rows (row-level security scopes every query to one
hospital). Teardown removes everything the hospital owns, in foreign-key order.
"""

import base64
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.security import digest_secret, hash_password, issue_access_token
from app.db.session import set_hospital_scope
from app.main import create_app

STAFF_PASSWORD = "Synthetic-test-password-2026"
# (code, token prefix, English name)
DEPARTMENTS = (("GENMED", "MED", "General Medicine"), ("ORTHO", "ORT", "Orthopaedics"))
# name -> (role, department code or None)
STAFF = {
    "nurse": ("nurse", "GENMED"),
    "doctor": ("doctor", "GENMED"),
    "doctor2": ("doctor", "GENMED"),
    "ortho_doctor": ("doctor", "ORTHO"),
    "admin": ("admin", None),
}
# Rows that reference a hospital, deleted children first. Visit children cascade.
_TEARDOWN = (
    "DELETE FROM audit_log WHERE hospital_id = :id",
    "DELETE FROM outbox WHERE hospital_id = :id",
    "DELETE FROM visits WHERE hospital_id = :id",
    "DELETE FROM patients WHERE hospital_id = :id",
    "DELETE FROM kiosks WHERE hospital_id = :id",
    "DELETE FROM refresh_tokens WHERE hospital_id = :id",
    "DELETE FROM hospital_ai_settings WHERE hospital_id = :id",
    "DELETE FROM hospital_report_settings WHERE hospital_id = :id",
    "DELETE FROM hospital_queue_settings WHERE hospital_id = :id",
    "DELETE FROM hospital_interview_settings WHERE hospital_id = :id",
    "DELETE FROM users WHERE hospital_id = :id",
    "DELETE FROM departments WHERE hospital_id = :id",
    "DELETE FROM hospitals WHERE id = :id",
)


def integration_settings() -> Settings:
    """Settings for a real PostgreSQL and Redis; skips the test when none is configured."""
    base = Settings()
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or base.database_url
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or base.migrations_database_url
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        pytest.skip("database credentials are required for integration tests")
    return base.model_copy(
        update={
            "database_url": app_url,
            "migrations_database_url": migrator_url,
            # CI has no .env; use fixed test-only key material when none is configured.
            "app_data_key": base.app_data_key or base64.b64encode(b"d" * 32).decode(),
            "app_hmac_key": base.app_hmac_key or base64.b64encode(b"h" * 32).decode(),
            # Integration tests never call live AI or speech providers.
            "gemini_api_key": "",
            "sarvam_api_key": "",
        }
    )


@dataclass
class SyntheticHospital:
    settings: Settings
    hospital_id: UUID
    departments: dict[str, UUID]
    users: dict[str, UUID]
    kiosk_id: UUID
    activation_code: str
    extra: dict[str, Any] = field(default_factory=dict)

    def token(self, name: str) -> str:
        role, _ = STAFF[name]
        return issue_access_token(
            subject=self.users[name],
            role=role,  # type: ignore[arg-type]
            hospital_id=self.hospital_id,
            settings=self.settings,
        )

    def staff(self, name: str, *, key: str | None = None) -> dict[str, str]:
        """Bearer and Idempotency-Key headers for one staff member."""
        return {
            "Authorization": f"Bearer {self.token(name)}",
            "Idempotency-Key": key or uuid4().hex,
        }


@asynccontextmanager
async def synthetic_hospital(settings: Settings | None = None) -> AsyncIterator[SyntheticHospital]:
    """Create a fictional hospital with two departments, staff and one kiosk; then remove it."""
    settings = settings or integration_settings()
    hospital_id = uuid4()
    departments = {code: uuid4() for code, _, _ in DEPARTMENTS}
    users = {name: uuid4() for name in STAFF}
    kiosk_id = uuid4()
    activation_code = "synthetic-kiosk-" + uuid4().hex
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, :name, :code)"),
                {"id": hospital_id, "name": "Synthetic test hospital", "code": f"t-{hospital_id}"},
            )
            for code, prefix, name in DEPARTMENTS:
                await connection.execute(
                    text(
                        "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                        "name_hi, name_mr) VALUES (:id, :hospital_id, :code, :prefix, :name, "
                        ":name, :name)"
                    ),
                    {
                        "id": departments[code],
                        "hospital_id": hospital_id,
                        "code": code,
                        "prefix": prefix,
                        "name": name,
                    },
                )
            password_hash = hash_password(STAFF_PASSWORD)
            for name, (role, department) in STAFF.items():
                await connection.execute(
                    text(
                        "INSERT INTO users (id, hospital_id, role, name, email, password_hash, "
                        "department_id) VALUES (:id, :hospital_id, :role, :name, :email, "
                        ":password_hash, :department_id)"
                    ),
                    {
                        "id": users[name],
                        "hospital_id": hospital_id,
                        "role": role,
                        "name": f"Synthetic {name}",
                        "email": f"{name}-{hospital_id}@example.test",
                        "password_hash": password_hash,
                        "department_id": departments[department] if department else None,
                    },
                )
            # Already paired (as after activation), so tests mint kiosk tokens directly instead
            # of calling /kiosks/activate, whose per-address limit all tests would share.
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash, "
                    "secret_hash, paired_at) VALUES (:id, :hospital_id, 'Synthetic kiosk', "
                    ":activation_hash, :secret_hash, now() - interval '1 minute')"
                ),
                {
                    "id": kiosk_id,
                    "hospital_id": hospital_id,
                    "activation_hash": digest_secret(activation_code),
                    "secret_hash": digest_secret("synthetic-device-" + uuid4().hex),
                },
            )
        yield SyntheticHospital(
            settings=settings,
            hospital_id=hospital_id,
            departments=departments,
            users=users,
            kiosk_id=kiosk_id,
            activation_code=activation_code,
        )
    finally:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            for statement in _TEARDOWN:
                await connection.execute(text(statement), {"id": hospital_id})
        await engine.dispose()


async def fetch(settings: Settings, hospital_id: UUID, sql: str, **params: Any) -> list[Any]:
    """Run one read as the migrator inside the hospital's scope (test assertions only)."""
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            result = await connection.execute(text(sql), params)
            return list(result.mappings().all())
    finally:
        await engine.dispose()


async def execute(settings: Settings, hospital_id: UUID, sql: str, **params: Any) -> None:
    """Run one write as the migrator inside the hospital's scope (test set-up only)."""
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(text(sql), params)
    finally:
        await engine.dispose()


@contextmanager
def api_client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient over the real application, database and Redis."""
    app = create_app(
        settings, engine_factory=lambda _url: create_async_engine(settings.database_url)
    )
    with TestClient(app) as client:
        yield client


@dataclass
class PatientSession:
    client: TestClient
    visit_id: str
    token: str
    consent: dict[str, Any]

    def headers(self, *, idempotent: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idempotent:
            headers["Idempotency-Key"] = uuid4().hex
        return headers

    def post(self, path: str, json: Any = None, *, idempotent: bool = False) -> Any:
        return self.client.post(
            f"/intake/sessions/{self.visit_id}{path}",
            json=json,
            headers=self.headers(idempotent=idempotent),
        )

    def get(self, path: str) -> Any:
        return self.client.get(f"/intake/sessions/{self.visit_id}{path}", headers=self.headers())

    def agree(self) -> None:
        response = self.post(
            "/consent",
            {"version": self.consent["version"], "sha256": self.consent["sha256"], "granted": True},
        )
        assert response.status_code == 200, response.text

    def identity(self, *, age: int = 34, sex: str = "female", phone: str | None = None) -> Any:
        return self.post("/identity", {"age": age, "sex": sex, "phone": phone})

    def danger_signs(self, tiles: tuple[str, ...] = ()) -> None:
        response = self.post("/danger-signs", {"tile_ids": list(tiles)})
        assert response.status_code == 200, response.text

    def answer_all(self, answers: dict[str, str | int]) -> dict[str, Any]:
        """Answer every question: given answers first, otherwise skip optional ones."""
        response = self.get("/interview/next-question")
        assert response.status_code == 200, response.text
        body = response.json()
        for _ in range(40):
            if body.get("complete"):
                return dict(body)
            question = body["question"]
            if question["id"] in answers:
                payload = {"question_id": question["id"], "answer": answers[question["id"]]}
            elif question["optional"]:
                payload = {"question_id": question["id"], "skip": True}
            else:
                fallback: str | int = 3 if question["input"] == "scale" else "not sure"
                payload = {"question_id": question["id"], "answer": fallback}
            response = self.post("/interview/answers", payload, idempotent=True)
            assert response.status_code == 200, response.text
            body = response.json()
        raise AssertionError("interview did not finish")

    def confirm(self) -> Any:
        return self.post("/confirm")


def activate_kiosk(client: TestClient, hospital: SyntheticHospital) -> dict[str, str]:
    """Kiosk bearer headers for the hospital's (already paired) kiosk."""
    del client  # Kept in the signature: callers read like the real activation flow.
    token = issue_access_token(
        subject=hospital.kiosk_id,
        role="kiosk",
        hospital_id=hospital.hospital_id,
        settings=hospital.settings,
        lifetime=timedelta(hours=1),
    )
    return {"Authorization": f"Bearer {token}"}


def start_kiosk_session(
    client: TestClient, kiosk_headers: dict[str, str], language: str = "en"
) -> PatientSession:
    response = client.post("/intake/sessions", json={"language": language}, headers=kiosk_headers)
    assert response.status_code == 201, response.text
    body = response.json()
    return PatientSession(
        client=client,
        visit_id=body["visit_id"],
        token=body["patient_access_token"],
        consent=body["consent"],
    )


def complete_intake(
    session: PatientSession,
    *,
    complaint: str = "fever",
    age: int = 34,
    sex: str = "female",
    phone: str | None = None,
    tiles: tuple[str, ...] = (),
    answers: dict[str, str | int] | None = None,
) -> dict[str, Any]:
    """Consent, identity, danger signs and the interview, ending at the summary."""
    session.agree()
    identity = session.identity(age=age, sex=sex, phone=phone)
    assert identity.status_code == 200, identity.text
    session.danger_signs(tiles)
    return session.answer_all({"chief_concern": complaint, **(answers or {})})
