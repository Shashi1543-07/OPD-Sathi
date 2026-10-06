from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import admin
from app.config import Settings
from app.core.errors import ApiProblemError
from app.core.pagination import Page


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, _script: str, _count: int, key: str, *args: str) -> int:
        reservation = json.loads(self.values[key])
        if reservation.get("owner") != args[0]:
            return 0
        if len(args) > 1:
            self.values[key] = args[1]
        else:
            self.values.pop(key, None)
        return 1


class FakeConnection:
    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def request_for(method: str = "POST", path: str = "/admin/kiosks") -> Request:
    connection = FakeConnection()
    settings = Settings()
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: connection),
            settings=settings,
            redis_state=FakeRedis(),
        )
    )
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"idempotency-key", b"synthetic-admin-test")],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )


def principal() -> dict[str, str]:
    return {"hospital_id": str(uuid4()), "sub": str(uuid4())}


@pytest.mark.asyncio
async def test_admin_list_is_scoped_audited_and_serializes_page(monkeypatch) -> None:
    hospital_id, actor_id = uuid4(), uuid4()
    audited: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    async def scope(_connection: object, _hospital_id: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **values: object) -> None:
        audited.append(values)

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def list(self, resource: str, **kwargs: object) -> Page[dict[str, object]]:
            calls.append({"resource": resource, **kwargs})
            return Page(items=[{"id": "synthetic"}], next_cursor=None, limit=5)

    monkeypatch.setattr(admin, "set_hospital_scope", scope)
    monkeypatch.setattr(admin, "audit", audit)
    monkeypatch.setattr(admin, "AdminRepository", Repository)
    response = await admin.list_resource(
        "departments",
        request_for("GET", "/admin/departments"),
        {"hospital_id": str(hospital_id), "sub": str(actor_id)},
        limit=5,
        cursor="synthetic-cursor",
    )

    assert response == {"items": [{"id": "synthetic"}], "next_cursor": None, "limit": 5}
    assert calls == [
        {
            "resource": "departments",
            "hospital_id": hospital_id,
            "limit": 5,
            "cursor": "synthetic-cursor",
        }
    ]
    assert audited[0]["action"] == "admin.departments.listed"
    assert audited[0]["meta"] == {"limit": 5, "cursor": True}


@pytest.mark.asyncio
async def test_admin_kiosk_create_shows_activation_code_only_once(monkeypatch) -> None:
    hospital_id, actor_id, kiosk_id = uuid4(), uuid4(), uuid4()
    calls: list[dict[str, object]] = []
    audited: list[dict[str, object]] = []

    async def scope(_connection: object, _hospital_id: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **values: object) -> None:
        audited.append(values)

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def create(self, resource: str, **kwargs: object) -> dict[str, str]:
            calls.append({"resource": resource, **kwargs})
            return {"id": str(kiosk_id), "label": "Synthetic kiosk"}

    monkeypatch.setattr(admin, "set_hospital_scope", scope)
    monkeypatch.setattr(admin, "audit", audit)
    monkeypatch.setattr(admin, "AdminRepository", Repository)
    request = request_for(path="/admin/kiosks")
    payload = admin.KioskCreate(label="Synthetic kiosk")
    actor = {"hospital_id": str(hospital_id), "sub": str(actor_id)}

    first = await admin.create_kiosk(payload, request, actor)
    replay = await admin.create_kiosk(payload, request, actor)

    assert first["activation_code"]
    assert replay == {"id": str(kiosk_id), "label": "Synthetic kiosk"}
    values = calls[0]["values"]
    assert isinstance(values, dict) and values["activation_code_hash"]
    assert "activation_code" not in values
    assert audited[0]["action"] == "admin.kiosks.created"


@pytest.mark.asyncio
async def test_admin_user_creation_hashes_password_and_normalizes_email(monkeypatch) -> None:
    hospital_id, actor_id, user_id = uuid4(), uuid4(), uuid4()
    recorded: list[dict[str, object]] = []

    async def scope(_connection: object, _hospital_id: object) -> None:
        return None

    async def audit(*_args: object, **_kwargs: object) -> None:
        return None

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def create(self, resource: str, **kwargs: object) -> dict[str, str]:
            recorded.append({"resource": resource, **kwargs})
            return {"id": str(user_id), "email": kwargs["values"]["email"]}

    monkeypatch.setattr(admin, "set_hospital_scope", scope)
    monkeypatch.setattr(admin, "audit", audit)
    monkeypatch.setattr(admin, "AdminRepository", Repository)
    result = await admin.create_user(
        admin.UserCreate(
            role="doctor",
            name="Synthetic Doctor",
            email="  STAFF@EXAMPLE.TEST ",
            password="synthetic-password-2026",
            department_id=None,
        ),
        request_for(path="/admin/users"),
        {"hospital_id": str(hospital_id), "sub": str(actor_id)},
    )
    values = recorded[0]["values"]
    assert result["email"] == "staff@example.test"
    assert isinstance(values, dict)
    assert values["email"] == "staff@example.test"
    assert values["password_hash"] != "synthetic-password-2026"
    assert values["password_hash"].startswith("$argon2")


@pytest.mark.asyncio
async def test_admin_patch_rejects_empty_and_missing_records(monkeypatch) -> None:
    request = request_for(method="PATCH", path="/admin/users/00000000-0000-7000-8000-000000000001")
    with pytest.raises(ApiProblemError) as empty:
        await admin._patch("users", uuid4(), {}, request, principal())
    assert empty.value.status == 422

    async def scope(_connection: object, _hospital_id: object) -> None:
        return None

    async def audit(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("missing records must not be audited")

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def patch(self, *_args: object, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(admin, "set_hospital_scope", scope)
    monkeypatch.setattr(admin, "audit", audit)
    monkeypatch.setattr(admin, "AdminRepository", Repository)
    with pytest.raises(ApiProblemError) as missing:
        await admin._patch("users", uuid4(), {"name": "Changed"}, request, principal())
    assert missing.value.status == 404


def test_admin_payload_validation_normalizes_and_rejects_invalid_updates() -> None:
    user = admin.UserCreate(
        role="nurse",
        name="Nurse",
        email="  Nurse@EXAMPLE.test ",
        password="synthetic-password-2026",
    )
    assert user.email == "nurse@example.test"
    with pytest.raises(ValueError):
        admin.UserCreate(
            role="nurse", name="Nurse", email="bad email", password="synthetic-password-2026"
        )
    with pytest.raises(ValueError):
        admin.DepartmentPatch(name_en=None)
    with pytest.raises(ValueError):
        admin.UserPatch(email="bad email")
    with pytest.raises(ValueError):
        admin.KioskPatch(label=None)
