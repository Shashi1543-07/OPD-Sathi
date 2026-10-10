from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.v1 import analytics

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
ACTOR_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")
PRINCIPAL = {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID), "role": "admin"}


class Rows:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    def mappings(self) -> Rows:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.row


class FakeConnection:
    """Keeps one settings row, like the real table, and records every statement."""

    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: object, params: dict[str, Any]) -> Rows:
        sql = str(statement)
        self.statements.append((sql, params))
        if sql.startswith("INSERT INTO hospital_report_settings"):
            self.row = {
                "minutes_saved_per_visit": Decimal(str(params["minutes"])),
                "updated_at": datetime(2026, 10, 10, 9, 30, tzinfo=UTC),
                "updated_by_name": "Demo Administrator",
            }
        return Rows(self.row)

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def request_for(connection: FakeConnection) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(begin=lambda: connection)))
    )


async def _no_scope(_connection: object, _hospital_id: UUID) -> None:
    return None


@pytest.mark.asyncio
async def test_a_hospital_without_a_setting_uses_the_documented_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analytics, "set_hospital_scope", _no_scope)
    settings = await analytics.get_report_settings(request_for(FakeConnection()), PRINCIPAL)
    assert settings == {
        "minutes_saved_per_visit": 3.0,
        "is_default": True,
        "updated_at": None,
        "updated_by_name": None,
    }


@pytest.mark.asyncio
async def test_saving_applies_to_the_whole_hospital_and_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict[str, Any]] = []

    async def audit(_connection: object, _request: object, **values: Any) -> None:
        audits.append(values)

    monkeypatch.setattr(analytics, "set_hospital_scope", _no_scope)
    monkeypatch.setattr(analytics, "audit", audit)
    connection = FakeConnection()
    saved = await analytics.patch_report_settings(
        analytics.ReportSettingsPatch(minutes_saved_per_visit=4.25),
        request_for(connection),
        PRINCIPAL,
    )
    assert saved["minutes_saved_per_visit"] == 4.2
    assert saved["is_default"] is False
    assert saved["updated_by_name"] == "Demo Administrator"
    insert = next(params for sql, params in connection.statements if sql.startswith("INSERT"))
    assert insert == {"hospital_id": HOSPITAL_ID, "minutes": 4.2, "actor_id": ACTOR_ID}
    assert audits[0]["action"] == "analytics.settings.updated"
    assert audits[0]["meta"] == {"minutes_saved_per_visit": {"before": 3.0, "after": 4.2}}


@pytest.mark.parametrize("value", [-0.5, 60.5, 1000])
def test_values_outside_zero_to_sixty_minutes_are_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        analytics.ReportSettingsPatch(minutes_saved_per_visit=value)
    with pytest.raises(ValidationError):
        analytics.ReportSettingsPatch.model_validate({"minutes_saved_per_visit": 3, "extra": 1})
