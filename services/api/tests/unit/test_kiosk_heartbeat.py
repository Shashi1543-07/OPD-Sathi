from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.api.v1 import auth
from app.db.repositories import IntakeRepository

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
KIOSK_ID = UUID("018e5a3d-6c10-7abc-8def-3123456789ab")


class FakeConnection:
    def __init__(self, *, active: bool) -> None:
        self.active = active
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def scalar(self, statement: object, params: dict[str, Any]) -> bool:
        self.statements.append((str(statement), params))
        return self.active

    async def execute(self, statement: object, params: dict[str, Any]) -> None:
        self.statements.append((str(statement), params))

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def request_for(connection: FakeConnection) -> Any:
    state = SimpleNamespace(engine=SimpleNamespace(begin=lambda: connection), settings=None)
    return SimpleNamespace(app=SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_an_active_kiosk_is_marked_seen_at_most_once_a_minute() -> None:
    connection = FakeConnection(active=True)
    seen = await IntakeRepository(connection, None).touch_kiosk(  # type: ignore[arg-type]
        hospital_id=HOSPITAL_ID, kiosk_id=KIOSK_ID
    )
    assert seen is True
    update, params = connection.statements[-1]
    assert update.startswith("UPDATE kiosks SET last_seen_at = now()")
    assert "interval '50 seconds'" in update
    assert params == {"hospital_id": HOSPITAL_ID, "kiosk_id": KIOSK_ID}


@pytest.mark.asyncio
async def test_an_inactive_kiosk_is_never_reported_online() -> None:
    connection = FakeConnection(active=False)
    seen = await IntakeRepository(connection, None).touch_kiosk(  # type: ignore[arg-type]
        hospital_id=HOSPITAL_ID, kiosk_id=KIOSK_ID
    )
    assert seen is False
    assert not any(sql.startswith("UPDATE") for sql, _ in connection.statements)


@pytest.mark.asyncio
async def test_heartbeat_reports_whether_the_kiosk_may_still_check_patients_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped: list[UUID] = []

    async def scope(_connection: object, hospital_id: UUID) -> None:
        scoped.append(hospital_id)

    monkeypatch.setattr(auth, "set_hospital_scope", scope)
    principal = {"hospital_id": str(HOSPITAL_ID), "sub": str(KIOSK_ID), "role": "kiosk"}
    for active in (True, False):
        connection = FakeConnection(active=active)
        result = await auth.kiosk_heartbeat(request_for(connection), principal)
        assert result == {"active": active}
    assert scoped == [HOSPITAL_ID, HOSPITAL_ID]
