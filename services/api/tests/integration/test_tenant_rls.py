"""PostgreSQL tests for forced row-level tenant isolation."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.session import set_hospital_scope, set_secret_lookup_scope


@pytest.mark.asyncio
async def test_forced_hospital_rls_and_append_only_audit() -> None:
    settings = Settings()
    app_database_url = (
        os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or settings.database_url
    )
    migrator_database_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or settings.migrations_database_url
    )
    if "local-only" in app_database_url or "local-only" in migrator_database_url:
        pytest.skip("database credentials are required for PostgreSQL RLS integration tests")

    hospital_a = uuid4()
    hospital_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    kiosk_a = uuid4()
    refresh_a = uuid4()
    audit_a = uuid4()
    activation_hash = "a1" * 32
    refresh_hash = "b2" * 32
    app_engine = create_async_engine(app_database_url)
    migrator_engine = create_async_engine(migrator_database_url)
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_a)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'RLS A', :code)"),
                {
                    "id": hospital_a,
                    "code": f"rls-{hospital_a}",
                },
            )
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_b)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'RLS B', :code)"),
                {"id": hospital_b, "code": f"rls-{hospital_b}"},
            )
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_a)
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'admin', 'RLS A', :email, 'hash')"
                ),
                {
                    "id": user_a,
                    "hospital_id": hospital_a,
                    "email": f"a-{user_a}@example.test",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, request_id) "
                    "VALUES (:id, :hospital_id, 'system', 'test', 'rls', 'integration-test')"
                ),
                {"id": audit_a, "hospital_id": hospital_a},
            )
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, 'RLS kiosk', :secret_hash)"
                ),
                {"id": kiosk_a, "hospital_id": hospital_a, "secret_hash": activation_hash},
            )
            await connection.execute(
                text(
                    "INSERT INTO refresh_tokens "
                    "(id, hospital_id, user_id, family_id, token_hash, expires_at) "
                    "VALUES (:id, :hospital_id, :user_id, :family_id, :token_hash, :expires_at)"
                ),
                {
                    "id": refresh_a,
                    "hospital_id": hospital_a,
                    "user_id": user_a,
                    "family_id": uuid4(),
                    "token_hash": refresh_hash,
                    "expires_at": datetime.now(UTC) + timedelta(hours=8),
                },
            )
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_b)
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'admin', 'RLS B', :email, 'hash')"
                ),
                {
                    "id": user_b,
                    "hospital_id": hospital_b,
                    "email": f"b-{user_b}@example.test",
                },
            )

        async with app_engine.connect() as connection:
            async with connection.begin():
                no_scope_count = await connection.scalar(text("SELECT count(*) FROM users"))
                assert no_scope_count == 0
                await set_secret_lookup_scope(
                    connection,
                    setting="app.refresh_token_hash",
                    secret_hash=refresh_hash,
                )
                refresh_tenant = await connection.scalar(
                    text("SELECT hospital_id FROM refresh_tokens WHERE token_hash = :hash"),
                    {"hash": refresh_hash},
                )
                assert refresh_tenant == hospital_a
                await set_secret_lookup_scope(
                    connection,
                    setting="app.kiosk_activation_hash",
                    secret_hash=activation_hash,
                )
                kiosk_tenant = await connection.scalar(
                    text("SELECT hospital_id FROM kiosks WHERE activation_code_hash = :hash"),
                    {"hash": activation_hash},
                )
                assert kiosk_tenant == hospital_a

        async with app_engine.connect() as connection:
            async with connection.begin():
                await set_hospital_scope(connection, hospital_a)
                visible_users = await connection.scalars(text("SELECT id FROM users ORDER BY id"))
                assert set(visible_users.all()) == {user_a}

                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO users "
                                "(id, hospital_id, role, name, email, password_hash) "
                                "VALUES (:id, :hospital_id, 'admin', 'cross-tenant', "
                                "'cross@example.test', 'hash')"
                            ),
                            {"id": uuid4(), "hospital_id": hospital_b},
                        )

                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text("UPDATE audit_log SET action = 'tampered' WHERE id = :id"),
                            {"id": audit_a},
                        )

                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text("DELETE FROM audit_log WHERE id = :id"),
                            {"id": audit_a},
                        )
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_a)
            await connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": audit_a})
            await connection.execute(
                text("DELETE FROM refresh_tokens WHERE id = :id"), {"id": refresh_a}
            )
            await connection.execute(text("DELETE FROM kiosks WHERE id = :id"), {"id": kiosk_a})
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_a})
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_a}
            )
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_b)
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_b})
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_b}
            )
        await app_engine.dispose()
        await migrator_engine.dispose()
