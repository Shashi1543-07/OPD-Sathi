"""Least privilege, proven (Phase 7, task 7.3.8): what the application's database role cannot do.

The API connects as ``osa_app``. Even with a bug or an injection in the application, that role
cannot change the schema, step outside row-level security, rewrite history (audit log, visit
timeline), delete or re-assign a consent, or undo a withdrawal after registration. No service
connects as a superuser.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    integration_settings,
    start_kiosk_session,
    synthetic_hospital,
)

REPOSITORY = Path(__file__).resolve().parents[4]


async def _as_app(sql: str, hospital_id: object = None, **params: object) -> None:
    """Run one statement as the application role, scoped to a hospital when given."""
    settings = integration_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            if hospital_id is not None:
                await connection.execute(
                    text("SELECT set_config('app.hospital_id', :h, true)"), {"h": str(hospital_id)}
                )
            await connection.execute(text(sql), params)
    finally:
        await engine.dispose()


async def test_service_roles_are_not_superusers_and_the_app_cannot_bypass_rls() -> None:
    settings = integration_settings()
    rows = await fetch(
        settings,
        uuid4(),
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
        "WHERE rolname IN ('osa_app', 'osa_worker', 'osa_migrator')",
    )
    roles = {row["rolname"]: row for row in rows}
    assert set(roles) == {"osa_app", "osa_worker", "osa_migrator"}
    assert not any(role["rolsuper"] for role in roles.values())
    assert not any(role["rolcreaterole"] or role["rolcreatedb"] for role in roles.values())
    assert roles["osa_app"]["rolbypassrls"] is False
    # Every service URL in Compose names one of these roles, never the postgres superuser.
    compose = (REPOSITORY / "docker-compose.yml").read_text(encoding="utf-8")
    users = set(re.findall(r"postgresql\+asyncpg://([a-z_]+):", compose))
    assert users == {"osa_app", "osa_worker", "osa_migrator"}


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE osa_attack (id int)",
        "ALTER TABLE visits ADD COLUMN osa_attack int",
        "ALTER TABLE visits DISABLE ROW LEVEL SECURITY",
        "DROP TABLE audit_log",
        "TRUNCATE audit_log",
    ],
)
async def test_the_app_role_cannot_change_the_schema(statement: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied|must be owner"):
        await _as_app(statement)


async def test_the_app_role_cannot_share_its_access() -> None:
    # PostgreSQL only warns ("no privileges were granted"); what matters is that none were.
    await _as_app("GRANT SELECT ON visits TO PUBLIC")
    rows = await fetch(
        integration_settings(),
        uuid4(),
        "SELECT has_table_privilege('public', 'visits', 'SELECT') AS public_reads",
    )
    assert rows[0]["public_reads"] is False


async def test_rows_are_invisible_without_scope_and_rls_cannot_be_switched_off() -> None:
    async with synthetic_hospital() as hospital:
        settings = integration_settings()
        engine = create_async_engine(settings.database_url)
        try:
            async with engine.connect() as connection:
                count = await connection.scalar(
                    text("SELECT count(*) FROM users WHERE hospital_id = :h"),
                    {"h": hospital.hospital_id},
                )
                assert count == 0  # The rows exist; without scope the app sees none.
            async with engine.connect() as connection:
                await connection.execute(text("SET row_security = off"))
                with pytest.raises(DBAPIError, match="row-level security"):
                    await connection.execute(text("SELECT count(*) FROM users"))
        finally:
            await engine.dispose()


async def test_history_cannot_be_rewritten_and_consents_cannot_be_reassigned() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, allow_ai=True)
            assert session.confirm().status_code == 200
        visit, h = session.visit_id, hospital.hospital_id
        for statement in (
            "UPDATE audit_log SET action = 'forged' WHERE hospital_id = :h",
            "DELETE FROM audit_log WHERE hospital_id = :h",
            "UPDATE visit_events SET reason = 'forged' WHERE hospital_id = :h",
            "DELETE FROM visit_events WHERE hospital_id = :h",
            "DELETE FROM consents WHERE hospital_id = :h",
            "UPDATE consents SET visit_id = gen_random_uuid() WHERE hospital_id = :h",
            "UPDATE consents SET purpose = 'care' WHERE hospital_id = :h AND purpose = 'ai'",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                await _as_app(statement, h, h=h)

        # The withdrawal of AI help after the token was issued cannot be undone.
        await execute(
            hospital.settings,
            h,
            "UPDATE consents SET withdrawn_at = now(), withdrawn_by = 'patient' "
            "WHERE hospital_id = :h AND visit_id = :v AND purpose = 'ai'",
            h=h,
            v=visit,
        )
        with pytest.raises(DBAPIError, match="cannot be restored"):
            await _as_app(
                "UPDATE consents SET withdrawn_at = NULL, withdrawn_by = NULL "
                "WHERE hospital_id = :h AND visit_id = :v AND purpose = 'ai'",
                h,
                h=h,
                v=visit,
            )
