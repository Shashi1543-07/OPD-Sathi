from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Response
from starlette.requests import Request

from app.api.v1 import auth
from app.config import Settings
from app.core.errors import ApiProblemError


class Result:
    def __init__(self, row: dict[str, object] | None = None):
        self.row = row

    def mappings(self) -> Result:
        return self

    def first(self) -> dict[str, object] | None:
        return self.row


class Connection:
    def __init__(self, hospital_id, row: dict[str, object] | None):
        self.hospital_id = hospital_id
        self.row = row
        self.calls: list[object] = []

    async def scalar(self, *_args: object, **_kwargs: object) -> object:
        return self.hospital_id

    async def execute(self, query: object, *_args: object, **_kwargs: object) -> Result:
        self.calls.append(query)
        return Result(self.row)


class Context:
    def __init__(self, connection: Connection):
        self.connection = connection

    async def __aenter__(self) -> Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


def request_for(connection: Connection) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: Context(connection)),
            settings=Settings(),
            redis_state=object(),
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )


@pytest.mark.asyncio
async def test_login_normalizes_identity_and_rotates_credentials_on_success(monkeypatch) -> None:
    hospital_id, actor_id = uuid4(), uuid4()
    row = {
        "id": actor_id,
        "hospital_id": hospital_id,
        "role": "doctor",
        "name": "Synthetic Clinician",
        "email": "clinician@example.test",
        "password_hash": "synthetic-password-hash",
        "failed_logins": 3,
        "locked_until": None,
        "is_active": True,
    }
    connection = Connection(hospital_id, row)
    request = request_for(connection)
    audits: list[dict[str, object]] = []
    cookie: list[str] = []

    async def limit(*_args: object, **_kwargs: object) -> None:
        return None

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(auth, "_limit_request", limit)
    monkeypatch.setattr(auth, "set_hospital_scope", scope)
    monkeypatch.setattr(auth, "audit", audit)
    monkeypatch.setattr(auth, "verify_password", lambda *_args: True)
    monkeypatch.setattr(auth, "_set_refresh_cookie", lambda _r, _s, value: cookie.append(value))
    monkeypatch.setattr(auth, "issue_access_token", lambda **_kwargs: "synthetic-access-token")
    response = await auth.login(
        auth.LoginPayload(
            hospital_id=hospital_id,
            email="  CLINICIAN@EXAMPLE.TEST ",
            password="synthetic-password",
        ),
        request,
        Response(),
    )
    assert response["access_token"] == "synthetic-access-token"
    assert response["user"]["id"] == str(actor_id)
    assert cookie and audits[0]["action"] == "auth.login.success"
    assert len(connection.calls) == 3  # lookup user, clear lockout, persist refresh token


@pytest.mark.asyncio
async def test_login_increments_failed_attempts_without_exposing_account_state(monkeypatch) -> None:
    hospital_id, actor_id = uuid4(), uuid4()
    row = {
        "id": actor_id,
        "hospital_id": hospital_id,
        "role": "nurse",
        "name": "Synthetic Nurse",
        "email": "nurse@example.test",
        "password_hash": "synthetic-password-hash",
        "failed_logins": 1,
        "locked_until": None,
        "is_active": True,
    }
    connection = Connection(hospital_id, row)
    audits: list[dict[str, object]] = []

    async def noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(auth, "_limit_request", noop)
    monkeypatch.setattr(auth, "set_hospital_scope", noop)
    monkeypatch.setattr(auth, "audit", audit)
    monkeypatch.setattr(auth, "verify_password", lambda *_args: False)
    with pytest.raises(ApiProblemError) as failure:
        await auth.login(
            auth.LoginPayload(
                hospital_id=hospital_id, email="nurse@example.test", password="wrong-password"
            ),
            request_for(connection),
            Response(),
        )
    assert failure.value.status == 401
    assert len(connection.calls) == 2 and audits[0]["action"] == "auth.login.failed"
