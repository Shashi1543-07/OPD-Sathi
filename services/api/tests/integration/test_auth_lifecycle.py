"""PostgreSQL-backed identity endpoints with a deterministic Redis substitute."""

import os
import time
from collections import defaultdict, deque
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.cli import _create_admin, _create_kiosk
from app.config import Settings
from app.core.security import digest_secret, hash_password, issue_access_token
from app.db.session import set_hospital_scope
from app.main import create_app


class FakeRedis:
    def __init__(self, **_kwargs: object) -> None:
        self.events: dict[str, deque[float]] = defaultdict(deque)

    async def eval(
        self, _script: str, _key_count: int, key: str, window_ms: int, limit: int, _request_id: str
    ) -> list[int]:
        now = time.monotonic()
        events = self.events[key]
        while events and events[0] <= now - window_ms / 1000:
            events.popleft()
        if len(events) >= limit:
            return [0, len(events), 1]
        events.append(now)
        return [1, len(events), 0]

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _database_settings() -> Settings:
    base = Settings()
    app_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL") or base.database_url
    migrator_url = (
        os.getenv("TEST_MIGRATIONS_DATABASE_URL")
        or os.getenv("MIGRATIONS_DATABASE_URL")
        or base.migrations_database_url
    )
    if "local-only" in app_url or "local-only" in migrator_url:
        pytest.skip("database credentials are required for PostgreSQL identity integration tests")
    return base.model_copy(
        update={
            "database_url": app_url,
            "migrations_database_url": migrator_url,
            # These flows predate two-step sign-in; test_staff_accounts.py covers it.
            "mfa_required_roles": "",
        }
    )


@pytest.mark.asyncio
async def test_login_lockout_refresh_reuse_kiosk_and_audit() -> None:
    settings = _database_settings()
    migrator_engine = create_async_engine(settings.migrations_database_url)
    hospital_id = uuid4()
    admin_id = uuid4()
    failing_user_id = uuid4()
    kiosk_id = uuid4()
    admin_email = f"admin-{admin_id}@example.test"
    failing_email = f"staff-{failing_user_id}@example.test"
    password = "Synthetic-test-password-2026"
    activation_code = "synthetic-kiosk-code-" + uuid4().hex
    try:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("INSERT INTO hospitals (id, name, code) VALUES (:id, 'Auth test', :code)"),
                {"id": hospital_id, "code": f"auth-{hospital_id}"},
            )
            for user_id, email in ((admin_id, admin_email), (failing_user_id, failing_email)):
                await connection.execute(
                    text(
                        "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                        "VALUES (:id, :hospital_id, 'admin', 'Synthetic staff', "
                        ":email, :password_hash)"
                    ),
                    {
                        "id": user_id,
                        "hospital_id": hospital_id,
                        "email": email,
                        "password_hash": hash_password(password),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, 'Synthetic kiosk', :activation_hash)"
                ),
                {
                    "id": kiosk_id,
                    "hospital_id": hospital_id,
                    "activation_hash": digest_secret(activation_code),
                },
            )

        app = create_app(
            settings,
            engine_factory=lambda _url: create_async_engine(settings.database_url),
            redis_factory=lambda *_args, **kwargs: FakeRedis(**kwargs),
        )
        with TestClient(app) as client:
            payload = {"hospital_id": str(hospital_id), "email": admin_email, "password": password}
            logged_in = client.post("/auth/login", json=payload)
            assert logged_in.status_code == 200
            assert logged_in.json()["user"]["role"] == "admin"
            # Each staff account has its own refresh cookie, named after the account.
            account = {"account": logged_in.json()["user"]["id"]}
            cookie_name = f"osa_refresh_{UUID(account['account']).hex}"
            old_refresh = logged_in.cookies.get(cookie_name)
            assert old_refresh
            refresh_cookie = logged_in.headers["set-cookie"].lower()
            assert "httponly" in refresh_cookie and "samesite=strict" in refresh_cookie

            rotated = client.post("/auth/refresh", json=account)
            assert rotated.status_code == 200
            assert rotated.json()["user"]["id"] == account["account"]
            new_refresh = rotated.cookies.get(cookie_name)
            assert new_refresh and new_refresh != old_refresh
            client.cookies.set(cookie_name, old_refresh, domain="testserver.local", path="/auth")
            reused = client.post("/auth/refresh", json=account)
            assert reused.status_code == 401
            client.cookies.set(cookie_name, new_refresh, domain="testserver.local", path="/auth")
            revoked_family = client.post("/auth/refresh", json=account)
            assert revoked_family.status_code == 401

            # Two staff accounts signed in side by side in one browser keep their own sessions.
            first = client.post("/auth/login", json=payload)
            second = client.post(
                "/auth/login",
                json={**payload, "email": failing_email},
            )
            assert first.status_code == 200 and second.status_code == 200
            for signed_in in (first, second):
                user_id = signed_in.json()["user"]["id"]
                renewed = client.post("/auth/refresh", json={"account": user_id})
                assert renewed.status_code == 200
                assert renewed.json()["user"]["id"] == user_id

            nurse = issue_access_token(
                subject=uuid4(), role="nurse", hospital_id=hospital_id, settings=settings
            )
            assert (
                client.get("/audit", headers={"Authorization": f"Bearer {nurse}"}).status_code
                == 403
            )

            failing_payload = {
                "hospital_id": str(hospital_id),
                "email": failing_email,
                "password": "incorrect-password",
            }
            failures = [client.post("/auth/login", json=failing_payload) for _ in range(5)]
            assert [response.status_code for response in failures] == [401, 401, 401, 401, 423]
            assert failures[0].json()["code"] == "INVALID_CREDENTIALS"
            assert failures[-1].json()["code"] == "ACCOUNT_LOCKED"

            wrong_activation = client.post("/kiosks/activate", json={"activation_code": "x" * 48})
            assert wrong_activation.status_code == 401
            activation = client.post("/kiosks/activate", json={"activation_code": activation_code})
            assert activation.status_code == 200
            assert activation.json()["token_type"] == "bearer"
            reused_activation = client.post(
                "/kiosks/activate", json={"activation_code": activation_code}
            )
            assert reused_activation.status_code == 401

            logout_login = client.post("/auth/login", json=payload)
            assert logout_login.status_code == 200
            logged_out = client.post("/auth/logout", json=account)
            assert logged_out.status_code == 204
            assert "max-age=0" in logged_out.headers["set-cookie"].lower()
            assert client.post("/auth/refresh", json=account).status_code == 401

            admin_token = logged_in.json()["access_token"]
            events = client.get("/audit", headers={"Authorization": f"Bearer {admin_token}"})
            assert events.status_code == 200
            actions = {item["action"] for item in events.json()["items"]}
            assert {
                "auth.login.success",
                "auth.refresh.reuse_detected",
                "kiosk.activated",
                "auth.logout",
            } <= actions
            assert "auth.login.locked" in actions
            first_page = client.get(
                "/audit?limit=1", headers={"Authorization": f"Bearer {admin_token}"}
            )
            assert first_page.status_code == 200
            cursor = first_page.json()["next_cursor"]
            assert cursor
            second_page = client.get(
                "/audit",
                params={"limit": 1, "cursor": cursor},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert second_page.status_code == 200
            assert first_page.json()["items"][0]["id"] != second_page.json()["items"][0]["id"]
    finally:
        async with migrator_engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text("DELETE FROM audit_log WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM refresh_tokens WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(text("DELETE FROM kiosks WHERE id = :id"), {"id": kiosk_id})
            await connection.execute(
                text("DELETE FROM users WHERE hospital_id = :id"), {"id": hospital_id}
            )
            await connection.execute(
                text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id}
            )
        await migrator_engine.dispose()


@pytest.mark.asyncio
async def test_eleventh_login_request_is_rate_limited() -> None:
    settings = _database_settings()
    fake_redis = FakeRedis()
    app = create_app(
        settings,
        engine_factory=lambda _url: create_async_engine(settings.database_url),
        redis_factory=lambda *_args, **_kwargs: fake_redis,
    )
    with TestClient(app) as client:
        payload = {
            "hospital_id": str(uuid4()),
            "email": "unknown@example.test",
            "password": "synthetic-test-password",
        }
        responses = [client.post("/auth/login", json=payload) for _ in range(11)]
    assert [response.status_code for response in responses[:10]] == [401] * 10
    assert responses[10].status_code == 429
    assert responses[10].headers["Retry-After"] == "1"
    assert responses[10].headers["X-RateLimit-Limit"] == "10"
    assert responses[10].headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_operator_cli_bootstraps_a_scoped_admin_and_kiosk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _database_settings()
    monkeypatch.setattr("app.cli.get_settings", lambda: settings)
    hospital_id: UUID | None = None
    engine = create_async_engine(settings.migrations_database_url)
    code: str | None = None
    try:
        hospital_id = await _create_admin(
            hospital_id=None,
            hospital_name="CLI integration synthetic hospital",
            hospital_code=f"cli-{uuid4()}",
            name="Synthetic operator admin",
            email=f"operator-{uuid4()}@example.test",
            password="Synthetic-operator-password-2026",
        )
        code = await _create_kiosk(hospital_id=hospital_id, label="CLI synthetic kiosk")
        assert code is not None
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            user_count = await connection.scalar(
                text("SELECT count(*) FROM users WHERE hospital_id = :id"),
                {"id": hospital_id},
            )
            kiosk_hash = await connection.scalar(
                text("SELECT activation_code_hash FROM kiosks WHERE hospital_id = :id"),
                {"id": hospital_id},
            )
            bootstrap_audit = await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE hospital_id = :id "
                    "AND action = 'bootstrap.admin.created'"
                ),
                {"id": hospital_id},
            )
            kiosk_audit = await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE hospital_id = :id "
                    "AND action = 'kiosk.provisioned'"
                ),
                {"id": hospital_id},
            )
        assert user_count == 1
        assert kiosk_hash == digest_secret(code)
        assert bootstrap_audit == 1
        assert kiosk_audit == 1
    finally:
        if hospital_id is not None:
            async with engine.begin() as connection:
                await set_hospital_scope(connection, hospital_id)
                for table in ("audit_log", "refresh_tokens", "kiosks", "users"):
                    await connection.execute(
                        text(f"DELETE FROM {table} WHERE hospital_id = :id"),
                        {"id": hospital_id},
                    )
                await connection.execute(
                    text("DELETE FROM hospitals WHERE id = :id"), {"id": hospital_id}
                )
        await engine.dispose()
