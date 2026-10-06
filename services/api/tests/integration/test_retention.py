"""Database-backed checks for the dry-run-first retention command."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.core.crypto import decrypt_field, encrypt_field
from app.db.session import set_hospital_scope
from scripts.retention import _run


def _database_settings() -> Settings:
    base = Settings()
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or base.database_url
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or base.migrations_database_url
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        pytest.skip("database credentials are required for retention integration tests")
    return base.model_copy(update={"database_url": app_url, "migrations_database_url": migrator_url})


@pytest.mark.asyncio
async def test_retention_dry_run_preserves_rows_then_apply_pseudonymizes_old_visit() -> None:
    settings = _database_settings()
    engine = create_async_engine(settings.migrations_database_url)
    hospital_id, department_id, patient_id, visit_id = (uuid4() for _ in range(4))
    old = datetime.now(UTC) - timedelta(days=120)
    encrypted_name = encrypt_field(
        "Synthetic Patient",
        key=settings.app_data_key_bytes,
        associated_data=f"patients:name_enc:{patient_id}",
    )
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'Retention test', :code)"),
                {"id": hospital_id, "code": f"retention-{hospital_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, 'GENMED', 'MED', 'General "
                    "Medicine', 'General Medicine', 'General Medicine')"
                ),
                {"id": department_id, "hospital_id": hospital_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO patients (id, hospital_id, name_enc, year_of_birth, sex) "
                    "VALUES (:id, :hospital_id, :name_enc, 1980, 'female')"
                ),
                {"id": patient_id, "hospital_id": hospital_id, "name_enc": encrypted_name},
            )
            await connection.execute(
                text(
                    "INSERT INTO visits (id, hospital_id, patient_id, department_id, "
                    "service_date, registered_at, age_years, pregnant, created_at) VALUES "
                    "(:id, :hospital_id, :patient_id, :department_id, CURRENT_DATE, :old, "
                    "46, true, :old)"
                ),
                {
                    "id": visit_id,
                    "hospital_id": hospital_id,
                    "patient_id": patient_id,
                    "department_id": department_id,
                    "old": old,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, "
                    "question_id, input_mode, text_original, created_at) VALUES "
                    "(:id, :hospital_id, :visit_id, 0, 'chief_concern', 'touch', "
                    "'synthetic concern', :old)"
                ),
                {"id": uuid4(), "hospital_id": hospital_id, "visit_id": visit_id, "old": old},
            )
            await connection.execute(
                text(
                    "INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data, "
                    "created_at) VALUES (:id, :hospital_id, :visit_id, '1.0', '{\"clinical\": "
                    "\"synthetic concern\"}'::jsonb, :old)"
                ),
                {"id": uuid4(), "hospital_id": hospital_id, "visit_id": visit_id, "old": old},
            )

        await _run(
            hospital_id=hospital_id,
            apply=False,
            retention_days=30,
            anonymise_days=90,
            settings=settings,
        )
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            original_name = await connection.scalar(
                text("SELECT name_enc FROM patients WHERE id = :id"), {"id": patient_id}
            )
            assert original_name == encrypted_name

        await _run(
            hospital_id=hospital_id,
            apply=True,
            retention_days=30,
            anonymise_days=90,
            settings=settings,
        )
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            remaining_turns = await connection.scalar(
                text("SELECT count(*) FROM interview_turns WHERE visit_id = :id"), {"id": visit_id}
            )
            remaining_sheets = await connection.scalar(
                text("SELECT count(*) FROM case_sheets WHERE visit_id = :id"), {"id": visit_id}
            )
            patient = (
                await connection.execute(
                    text(
                        "SELECT name_enc, phone_enc, abha_enc, year_of_birth, sex "
                        "FROM patients WHERE id = :id"
                    ),
                    {"id": patient_id},
                )
            ).mappings().one()
            visit = (
                await connection.execute(
                    text("SELECT age_years, pregnant, urgency_reasons FROM visits WHERE id = :id"),
                    {"id": visit_id},
                )
            ).mappings().one()
            assert remaining_turns == remaining_sheets == 0
            assert decrypt_field(
                patient["name_enc"],
                key=settings.app_data_key_bytes,
                associated_data=f"patients:name_enc:{patient_id}",
            ) == "Anonymous"
            assert patient["phone_enc"] is None and patient["abha_enc"] is None
            assert patient["year_of_birth"] is None and patient["sex"] == "unknown"
            assert visit["age_years"] is None and visit["pregnant"] is None
            assert visit["urgency_reasons"] == []
    finally:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM audit_log WHERE hospital_id = :id"), {"id": hospital_id}
            )
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
            await connection.execute(text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id})
        await engine.dispose()
