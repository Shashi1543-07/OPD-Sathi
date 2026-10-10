from __future__ import annotations

import tracemalloc
from collections.abc import AsyncIterator
from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import analytics

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
ACTOR_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")
DEPARTMENT_ID = UUID("018e5a3d-6c10-7abc-8def-2123456789ab")


def rollup(
    *,
    department_id: UUID,
    patients: int,
    urgent: int,
    wait_total: int,
    wait_count: int,
    consult_total: int,
    consult_count: int,
    interview_total: int,
    interview_count: int,
    confirmed: int,
    without_edits: int,
    doctor_edited: int,
    languages: dict[str, int],
) -> dict[str, object]:
    return {
        "service_date": date(2026, 10, 6),
        "department_id": department_id,
        "name_en": "General Medicine",
        "name_hi": "सामान्य चिकित्सा",
        "name_mr": "सामान्य औषधोपचार",
        "patient_count": patients,
        "urgent_count": urgent,
        "wait_seconds_total": wait_total,
        "wait_sample_count": wait_count,
        "consult_seconds_total": consult_total,
        "consult_sample_count": consult_count,
        "interview_seconds_total": interview_total,
        "interview_sample_count": interview_count,
        "confirmed_count": confirmed,
        "confirmed_without_edits_count": without_edits,
        "doctor_edited_count": doctor_edited,
        "language_mix": languages,
    }


class Result:
    def __init__(self, rows: list[dict[str, object]] | None = None):
        self.rows = rows or []

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows


class StreamResult:
    def __init__(self, row: dict[str, object], count: int):
        self.row = row
        self.count = count

    def mappings(self) -> StreamResult:
        return self

    async def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        for _ in range(self.count):
            yield self.row


class FakeConnection:
    def __init__(self, *, rows: list[dict[str, object]] | None = None, stream_count: int = 0):
        self.rows = rows or []
        self.stream_count = stream_count
        self.calls: list[tuple[str, object]] = []

    async def execute(self, statement: object, params: object = None) -> Result:
        self.calls.append((str(statement), params))
        return Result(self.rows)

    async def stream(self, statement: object, params: object = None) -> StreamResult:
        self.calls.append((str(statement), params))
        return StreamResult(self.rows[0] if self.rows else {}, self.stream_count)

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def make_request(connection: FakeConnection) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(engine=SimpleNamespace(begin=lambda: connection)))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/analytics/summary",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )


def test_csv_cells_escape_tenant_controlled_spreadsheet_formulas() -> None:
    assert analytics._safe_csv_cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert analytics._safe_csv_cell("General Medicine") == "General Medicine"


@pytest.mark.asyncio
async def test_analytics_rollup_matches_hand_computed_fixture() -> None:
    other_department = uuid4()
    summary = analytics.summarize(
        [
            rollup(
                department_id=DEPARTMENT_ID,
                patients=4,
                urgent=2,
                wait_total=3600,
                wait_count=3,
                consult_total=2400,
                consult_count=2,
                interview_total=180,
                interview_count=3,
                confirmed=3,
                without_edits=2,
                doctor_edited=1,
                languages={"en": 2, "hi": 2},
            ),
            rollup(
                department_id=other_department,
                patients=2,
                urgent=1,
                wait_total=600,
                wait_count=1,
                consult_total=1200,
                consult_count=1,
                interview_total=60,
                interview_count=1,
                confirmed=1,
                without_edits=1,
                doctor_edited=0,
                languages={"mr": 2},
            ),
        ]
    )
    assert summary["patients"] == 6
    assert summary["average_wait_seconds"] == 1050.0
    assert summary["average_consult_seconds"] == 1200.0
    assert summary["urgent_count"] == 3
    assert summary["language_mix"] == {"en": 2, "hi": 2, "mr": 2}
    assert summary["average_interview_seconds"] == 60.0
    assert summary["confirmed_without_edits_percent"] == 75.0
    assert summary["doctor_edit_rate_percent"] == 16.7


@pytest.mark.asyncio
async def test_summary_filters_by_authenticated_tenant_and_audits(monkeypatch) -> None:
    connection = FakeConnection(
        rows=[
            rollup(
                department_id=DEPARTMENT_ID,
                patients=2,
                urgent=1,
                wait_total=120,
                wait_count=1,
                consult_total=60,
                consult_count=1,
                interview_total=30,
                interview_count=1,
                confirmed=2,
                without_edits=1,
                doctor_edited=1,
                languages={"en": 2},
            )
        ]
    )
    scoped: list[UUID] = []
    audits: list[dict[str, object]] = []

    async def scope(_connection: object, hospital_id: UUID) -> None:
        scoped.append(hospital_id)

    async def audit(_connection: object, _request: object, **values: object) -> None:
        audits.append(values)

    monkeypatch.setattr(analytics, "set_hospital_scope", scope)
    monkeypatch.setattr(analytics, "audit", audit)
    response = await analytics.analytics_summary(
        make_request(connection), {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID)}
    )
    sql, params = connection.calls[-1]
    assert response["patients"] == 2
    assert params["hospital_id"] == HOSPITAL_ID  # type: ignore[index]
    assert "analytics_daily_department_tenant" in sql
    assert scoped == [HOSPITAL_ID]
    assert audits[0]["action"] == "analytics.summary.viewed"


@pytest.mark.asyncio
async def test_csv_export_streams_one_hundred_thousand_rows_under_memory_gate(monkeypatch) -> None:
    row = rollup(
        department_id=DEPARTMENT_ID,
        patients=1,
        urgent=0,
        wait_total=0,
        wait_count=0,
        consult_total=0,
        consult_count=0,
        interview_total=0,
        interview_count=0,
        confirmed=0,
        without_edits=0,
        doctor_edited=0,
        languages={"en": 1},
    )
    connection = FakeConnection(rows=[row], stream_count=100_000)

    async def scope(_connection: object, _hospital_id: UUID) -> None:
        return None

    async def audit(_connection: object, _request: object, **_values: object) -> None:
        return None

    monkeypatch.setattr(analytics, "set_hospital_scope", scope)
    monkeypatch.setattr(analytics, "audit", audit)
    response = await analytics.analytics_export(
        make_request(connection), {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID)}, days=30
    )
    tracemalloc.start()
    row_count = 0
    async for chunk in response.body_iterator:
        row_count += chunk.count("\n")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert row_count == 100_001  # header plus 100,000 streamed rows
    peak_mb = peak / (1024 * 1024)
    print(f"CSV streamed {row_count - 1} rows; peak traced memory {peak_mb:.2f} MB / 100 MB")
    assert peak < 100 * 1024 * 1024


def _quiet_row(**values: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        department_id=DEPARTMENT_ID,
        patients=1,
        urgent=0,
        wait_total=0,
        wait_count=0,
        consult_total=0,
        consult_count=0,
        interview_total=0,
        interview_count=0,
        confirmed=0,
        without_edits=0,
        doctor_edited=0,
        languages={"en": 1},
    )
    base.update(values)
    return rollup(**base)  # type: ignore[arg-type]


def test_a_range_lists_each_department_once_with_a_weighted_wait() -> None:
    summary = analytics.summarize(
        [
            _quiet_row(patients=3, urgent=1, wait_total=600, wait_count=2),
            _quiet_row(patients=5, urgent=2, wait_total=2400, wait_count=2),
        ]
    )
    assert summary["patients"] == 8
    assert summary["department_load"] == [
        {
            "department_id": str(DEPARTMENT_ID),
            "name": {
                "en": "General Medicine",
                "hi": "सामान्य चिकित्सा",
                "mr": "सामान्य औषधोपचार",
            },
            "patients": 8,
            "urgent_count": 3,
            # (600 + 2400) / 4 samples, not the mean of the two daily means.
            "average_wait_seconds": 750.0,
        }
    ]


async def _no_scope(_connection: object, _hospital_id: UUID) -> None:
    return None


@pytest.mark.asyncio
async def test_summary_uses_the_report_range_and_department(monkeypatch) -> None:
    connection = FakeConnection(rows=[_quiet_row(patients=4)])
    audits: list[dict[str, object]] = []

    async def audit(_connection: object, _request: object, **values: object) -> None:
        audits.append(values)

    monkeypatch.setattr(analytics, "set_hospital_scope", _no_scope)
    monkeypatch.setattr(analytics, "audit", audit)
    response = await analytics.analytics_summary(
        make_request(connection),
        {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID)},
        days=30,
        department_id=DEPARTMENT_ID,
    )
    sql, params = connection.calls[-1]
    assert "a.service_date >= :start_day" in sql
    assert params["department_id"] == DEPARTMENT_ID  # type: ignore[index]
    assert (params["end_day"] - params["start_day"]).days == 29  # type: ignore[index]
    assert response["patients"] == 4
    assert response["start_day"] == params["start_day"].isoformat()  # type: ignore[index]
    assert audits[0]["meta"] == {"days": 30, "department_id": str(DEPARTMENT_ID)}


@pytest.mark.asyncio
async def test_summary_without_a_range_still_reports_today(monkeypatch) -> None:
    connection = FakeConnection(rows=[])
    monkeypatch.setattr(analytics, "set_hospital_scope", _no_scope)

    async def audit(_connection: object, _request: object, **_values: object) -> None:
        return None

    monkeypatch.setattr(analytics, "audit", audit)
    response = await analytics.analytics_summary(
        make_request(connection), {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID)}
    )
    sql, params = connection.calls[-1]
    assert "a.service_date = :service_date" in sql
    assert response["start_day"] == response["end_day"]
    assert response["patients"] == 0
    assert response["average_wait_seconds"] is None


@pytest.mark.asyncio
async def test_csv_export_follows_the_department_filter(monkeypatch) -> None:
    connection = FakeConnection(rows=[_quiet_row()], stream_count=1)
    monkeypatch.setattr(analytics, "set_hospital_scope", _no_scope)

    async def audit(_connection: object, _request: object, **_values: object) -> None:
        return None

    monkeypatch.setattr(analytics, "audit", audit)
    response = await analytics.analytics_export(
        make_request(connection),
        {"hospital_id": str(HOSPITAL_ID), "sub": str(ACTOR_ID)},
        days=7,
        department_id=DEPARTMENT_ID,
    )
    body = "".join([chunk async for chunk in response.body_iterator])  # type: ignore[misc]
    _, params = connection.calls[-1]
    assert params["department_id"] == DEPARTMENT_ID  # type: ignore[index]
    assert (params["end_day"] - params["start_day"]).days == 6  # type: ignore[index]
    assert body.count("\n") == 2
