"""Database-backed checks for Phase 11 rollup arithmetic, refresh, and tenant scope."""

import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.crypto import encrypt_field
from app.db.session import set_hospital_scope


def _database_urls() -> tuple[str, str] | None:
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://osa_app:local-only@localhost/osa"
    )
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or "postgresql+asyncpg://osa_migrator:local-only@localhost/osa"
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        return None
    return app_url, migrator_url


@pytest.mark.asyncio
async def test_daily_analytics_rollup_refresh_and_tenant_filter() -> None:
    urls = _database_urls()
    if urls is None:
        pytest.skip("database credentials are required for PostgreSQL analytics integration")
    app_url, migrator_url = urls
    hospital_id, other_hospital_id, department_id = uuid4(), uuid4(), uuid4()
    patient_id, visit_id, edited_visit_id, doctor_id = (uuid4() for _ in range(4))
    timestamps = [datetime(2026, 10, 6, 9, minute, tzinfo=UTC) for minute in (0, 2, 10, 20, 30)]
    app_engine = create_async_engine(app_url, poolclass=NullPool)
    migrator_engine = create_async_engine(migrator_url, poolclass=NullPool)
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, :name, :code)"),
                {"id": hospital_id, "name": "Synthetic analytics clinic", "code": str(hospital_id)},
            )
            await connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, 'GENMED', 'GEN', "
                    "'General Medicine', 'सामान्य चिकित्सा', 'सामान्य औषधोपचार')"
                ),
                {"id": department_id, "hospital_id": hospital_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO patients (id, hospital_id, name_enc, sex, preferred_language) "
                    "VALUES (:id, :hospital_id, :name_enc, 'unknown', 'hi')"
                ),
                {
                    "id": patient_id,
                    "hospital_id": hospital_id,
                    "name_enc": encrypt_field(
                        "Synthetic analytics patient",
                        key=b"d" * 32,
                        associated_data=f"patients:name_enc:{patient_id}",
                    ),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'doctor', 'Synthetic Doctor', :email, "
                    "'not-a-login-hash')"
                ),
                {"id": doctor_id, "hospital_id": hospital_id, "email": f"{doctor_id}@test.invalid"},
            )
            for visit_id_value, urgency, queued, started, ended in (
                (visit_id, "urgent", timestamps[0], timestamps[2], timestamps[3]),
                (edited_visit_id, "routine", timestamps[1], timestamps[2], timestamps[4]),
            ):
                await connection.execute(
                    text(
                        "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, "
                        "urgency, service_date, language, registered_at, queued_at, "
                        "consult_started_at, consult_ended_at, token_number, token_label) "
                        "VALUES (:id, :hospital_id, :patient_id, :department_id, 'done', :urgency, "
                        ":service_date, 'hi', :registered_at, :queued_at, :started, :ended, "
                        ":token_number, :token_label)"
                    ),
                    {
                        "id": visit_id_value,
                        "hospital_id": hospital_id,
                        "patient_id": patient_id,
                        "department_id": department_id,
                        "urgency": urgency,
                        "service_date": date(2026, 10, 6),
                        "registered_at": timestamps[0],
                        "queued_at": queued,
                        "started": started,
                        "ended": ended,
                        "token_number": 1 if visit_id_value == visit_id else 2,
                        "token_label": "GEN-001" if visit_id_value == visit_id else "GEN-002",
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data, "
                        "confirmed_by_patient, confirmed_at, last_edited_by) VALUES "
                        "(:id, :hospital_id, :visit_id, '1.0', '{}'::jsonb, true, :confirmed_at, "
                        ":last_edited_by)"
                    ),
                    {
                        "id": uuid4(),
                        "hospital_id": hospital_id,
                        "visit_id": visit_id_value,
                        "confirmed_at": timestamps[0],
                        "last_edited_by": doctor_id if visit_id_value == edited_visit_id else None,
                    },
                )
            for index, created_at in enumerate((timestamps[1], timestamps[2], timestamps[3])):
                await connection.execute(
                    text(
                        "INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, "
                        "question_id, input_mode, created_at) "
                        "VALUES (:id, :hospital_id, :visit_id, "
                        ":turn_index, 'synthetic_q', 'touch', :created_at)"
                    ),
                    {
                        "id": uuid4(),
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                        "turn_index": index,
                        "created_at": created_at,
                    },
                )

        async with migrator_engine.begin() as connection:
            await connection.execute(text("SET ROLE osa_analytics_worker"))
            await connection.execute(
                text(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY "
                    "osa_analytics.analytics_daily_department"
                )
            )
            materialized_count = await connection.scalar(
                text(
                    "SELECT count(*) FROM osa_analytics.analytics_daily_department "
                    "WHERE hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
            assert int(materialized_count or 0) == 1
            await connection.execute(text("RESET ROLE"))

        async with app_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT patient_count, wait_seconds_total, wait_sample_count, "
                            "consult_seconds_total, consult_sample_count, urgent_count, "
                            "interview_seconds_total, interview_sample_count, confirmed_count, "
                            "confirmed_without_edits_count, case_sheet_count, doctor_edited_count, "
                            "language_mix "
                            "FROM osa_analytics.analytics_daily_department_tenant "
                            "WHERE hospital_id = :hospital_id "
                            "AND department_id = :department_id AND service_date = :service_date"
                        ),
                        {
                            "hospital_id": hospital_id,
                            "department_id": department_id,
                            "service_date": date(2026, 10, 6),
                        },
                    )
                )
                .mappings()
                .one()
            )
            assert int(row["patient_count"]) == 2
            assert float(row["wait_seconds_total"]) == 1080.0
            assert int(row["wait_sample_count"]) == 2
            assert float(row["consult_seconds_total"]) == 1800.0
            assert int(row["consult_sample_count"]) == 2
            assert int(row["urgent_count"]) == 1
            assert float(row["interview_seconds_total"]) == 1080.0
            assert int(row["interview_sample_count"]) == 1
            assert int(row["confirmed_count"]) == 2
            assert int(row["confirmed_without_edits_count"]) == 1
            assert int(row["doctor_edited_count"]) == 1
            assert int(row["case_sheet_count"]) == 2
            assert row["language_mix"] == {"hi": 2}
        async with app_engine.begin() as connection:
            await set_hospital_scope(connection, other_hospital_id)
            visible = await connection.scalar(
                text("SELECT count(*) FROM osa_analytics.analytics_daily_department_tenant")
            )
            assert int(visible or 0) == 0
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM visits WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM patients WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM users WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM departments WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id}
            )
        async with migrator_engine.begin() as connection:
            await connection.execute(text("SET ROLE osa_analytics_worker"))
            await connection.execute(
                text(
                    "REFRESH MATERIALIZED VIEW CONCURRENTLY "
                    "osa_analytics.analytics_daily_department"
                )
            )
            await connection.execute(text("RESET ROLE"))
        await app_engine.dispose()
        await migrator_engine.dispose()
