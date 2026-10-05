"""PostgreSQL checks for Phase 3 tenant isolation, encryption and keyset paging."""

import base64
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.core.ids import uuid7
from app.core.pagination import Page
from app.core.security import issue_access_token
from app.db.repositories import PatientRepository, _list_page
from app.db.session import set_hospital_scope
from app.main import create_app


def _database_urls() -> tuple[str, str] | None:
    settings = Settings()
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or settings.database_url
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or settings.migrations_database_url
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        return None
    return app_url, migrator_url


@pytest.mark.asyncio
async def test_domain_rls_and_encrypted_patient_fields() -> None:
    urls = _database_urls()
    if urls is None:
        pytest.skip("database credentials are required for PostgreSQL integration tests")
    app_url, migrator_url = urls
    hospital_id = uuid4()
    department_id = uuid7()
    settings = Settings(
        app_data_key=base64.b64encode(b"d" * 32).decode(),
        app_hmac_key=base64.b64encode(b"h" * 32).decode(),
    )
    app_engine = create_async_engine(app_url)
    migrator_engine = create_async_engine(migrator_url)
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text(
                    "INSERT INTO hospitals (id, name, code) "
                    "VALUES (:id, 'Synthetic Phase 3', :code)"
                ),
                {"id": hospital_id, "code": f"phase3-{hospital_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, 'GENMED', 'MED', 'Medicine', "
                    "'चिकित्सा', 'औषधोपचार')"
                ),
                {"id": department_id, "hospital_id": hospital_id},
            )
        async with app_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            patient_id = await PatientRepository(connection, settings).create(
                hospital_id=hospital_id,
                name="Synthetic Person Phase Three",
                phone="+15555550123",
                abha="ABHA-SYNTHETIC-123",
            )
            page: Page[dict] = await PatientRepository(connection, settings).list(
                hospital_id=hospital_id, limit=10, cursor=None
            )
            assert page.items[0]["name"] == "Synthetic Person Phase Three"
            assert page.items[0]["phone"] == "+15555550123"
        async with app_engine.connect() as connection:
            async with connection.begin():
                for table_name in (
                    "departments",
                    "patients",
                    "visits",
                    "consents",
                    "case_sheets",
                    "interview_turns",
                    "red_flag_events",
                    "outbox",
                    "notifications",
                    "documents",
                ):
                    policy_enabled = await connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE relname = :table_name"
                        ),
                        {"table_name": table_name},
                    )
                    assert policy_enabled is True, table_name
                    unscoped_rows = await connection.scalar(
                        text(f"SELECT count(*) FROM {table_name}")
                    )
                    assert unscoped_rows == 0, table_name
                await set_hospital_scope(connection, hospital_id)
                encrypted = (
                    (
                        await connection.execute(
                            text(
                                "SELECT name_enc, phone_enc, phone_hmac, abha_enc FROM patients "
                                "WHERE hospital_id = :hospital_id AND id = :patient_id"
                            ),
                            {"hospital_id": hospital_id, "patient_id": patient_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                serialized = " ".join(str(value) for value in encrypted.values())
                assert "Synthetic Person Phase Three" not in serialized
                assert "+15555550123" not in serialized
                assert "ABHA-SYNTHETIC-123" not in serialized
                assert encrypted["name_enc"].startswith("v1:")
                assert encrypted["phone_enc"].startswith("v1:")
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM patients WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await connection.execute(
                text("DELETE FROM departments WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
        await app_engine.dispose()
        await migrator_engine.dispose()


@pytest.mark.asyncio
async def test_keyset_paging_has_no_gaps_when_rows_arrive_mid_scan() -> None:
    urls = _database_urls()
    if urls is None:
        pytest.skip("database credentials are required for PostgreSQL integration tests")
    app_url, migrator_url = urls
    hospital_id = uuid4()
    initial_ids = [uuid7() for _ in range(10_000)]
    arriving_ids = [uuid7() for _ in range(100)]
    batch_time = datetime.now(UTC) - timedelta(days=1)
    app_engine = create_async_engine(app_url)
    migrator_engine = create_async_engine(migrator_url)
    key = b"phase-three-pagination-key"
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text(
                    "INSERT INTO hospitals (id, name, code) VALUES (:id, 'Pagination Test', :code)"
                ),
                {"id": hospital_id, "code": f"phase3-page-{hospital_id}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO outbox (id, hospital_id, topic, payload, created_at) "
                    "VALUES (:id, :hospital_id, 'pagination.test', '{}'::jsonb, :created_at)"
                ),
                [
                    {"id": row_id, "hospital_id": hospital_id, "created_at": batch_time}
                    for row_id in initial_ids
                ],
            )

        collected: list = []
        cursor: str | None = None
        page_number = 0
        async with app_engine.connect() as connection:
            async with connection.begin():
                await set_hospital_scope(connection, hospital_id)
                while True:
                    page = await _list_page(
                        connection,
                        table_name="outbox",
                        hospital_id=hospital_id,
                        limit=100,
                        cursor=cursor,
                        cursor_key=key,
                        columns=("id", "created_at"),
                    )
                    collected.extend(row["id"] for row in page.items)
                    cursor = page.next_cursor
                    page_number += 1
                    if page_number == 2:
                        async with migrator_engine.begin() as writer:
                            await set_hospital_scope(writer, hospital_id)
                            await writer.execute(
                                text(
                                    "INSERT INTO outbox (id, hospital_id, topic, payload, "
                                    "created_at) "
                                    "VALUES (:id, :hospital_id, 'pagination.late', '{}'::jsonb, "
                                    ":created_at)"
                                ),
                                [
                                    {
                                        "id": row_id,
                                        "hospital_id": hospital_id,
                                        "created_at": batch_time + timedelta(seconds=1),
                                    }
                                    for row_id in arriving_ids
                                ],
                            )
                    if cursor is None:
                        break
        expected = set(initial_ids + arriving_ids)
        assert len(collected) == len(expected)
        assert set(collected) == expected
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM outbox WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
        await app_engine.dispose()
        await migrator_engine.dispose()


def test_admin_write_is_audited_and_idempotent() -> None:
    urls = _database_urls()
    if urls is None:
        pytest.skip("database credentials are required for PostgreSQL integration tests")
    import asyncio

    app_url, migrator_url = urls
    settings = Settings(database_url=app_url, migrations_database_url=migrator_url)
    hospital_id = uuid4()
    actor_id = uuid4()
    code = f"phase3-admin-{hospital_id}"
    migrator_engine = create_async_engine(migrator_url, poolclass=NullPool)

    async def create_tenant() -> None:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'Admin Test', :code)"),
                {"id": hospital_id, "code": code},
            )

    async def remove_tenant() -> None:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM departments WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await connection.execute(
                text("DELETE FROM audit_log WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )

    asyncio.run(create_tenant())
    token = issue_access_token(
        subject=actor_id,
        role="admin",
        hospital_id=hospital_id,
        settings=settings,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "phase3-department-create-1",
    }
    body = {
        "code": "GENMED",
        "token_prefix": "MED",
        "name_en": "General Medicine",
        "name_hi": "सामान्य चिकित्सा",
        "name_mr": "सामान्य औषधोपचार",
    }
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            first = client.post("/admin/departments", json=body, headers=headers)
            replay = client.post("/admin/departments", json=body, headers=headers)
            assert first.status_code == 201, first.text
            assert replay.status_code == 201, replay.text
            assert first.json() == replay.json()
            changed = {**body, "code": "ORTHO", "token_prefix": "ORT"}
            reused = client.post("/admin/departments", json=changed, headers=headers)
            assert reused.status_code == 422
            assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

            listed = client.get(
                "/admin/departments",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert listed.status_code == 200, listed.text
            department = listed.json()["items"][0]
            assert department["id"] == first.json()["id"]

            patched = client.patch(
                f"/admin/departments/{department['id']}",
                json={"name_en": "Medicine"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "phase3-department-patch-1",
                },
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["name_en"] == "Medicine"
    finally:
        asyncio.run(remove_tenant())
        asyncio.run(migrator_engine.dispose())
