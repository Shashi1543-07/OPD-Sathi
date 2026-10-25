"""Query-count gates for the screens staff keep open (Phase 7, task 7.8.4; synthetic).

A request costs a fixed number of database statements, however many patients wait, however
long a patient's history is and however many days a report covers. A query run once per row
(N+1) shows as a count that grows with the data; a statement added to a busy route, as a count
over its budget. Raise a budget only with a reason; docs/SCALING.md has the index review on a
million visits behind these routes.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from tests.integration.synthetic import (
    PatientSession,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    integration_settings,
    start_kiosk_session,
    synthetic_hospital,
)

# Statements per request, everything included: the hospital scope, the staff member's
# department, the reads and the audit entry.
BUDGET = {
    "board": 6,
    "alerts": 3,
    "doctor's list": 6,
    "register": 3,
    "case-sheet": 6,
    "transcript": 5,
    "history": 7,
    "queue-events": 4,
    "doctor-notes": 6,
    "investigations": 7,
    "documents": 7,
    "summary": 3,
    "timeseries": 3,
}


@contextmanager
def _statements(client: TestClient) -> Iterator[list[str]]:
    """Every SQL statement the application sends while the block runs."""
    sent: list[str] = []
    app: Any = client.app
    engine = app.state.engine.sync_engine

    def record(_connection: Any, _cursor: Any, statement: str, *_: Any) -> None:
        sent.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield sent
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _cost(client: TestClient, path: str, headers: dict[str, str]) -> int:
    with _statements(client) as sent:
        response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return len(sent)


def _assert_gated(small: dict[str, int], large: dict[str, int]) -> None:
    assert large == small, f"a count grew with the data (N+1): {small} -> {large}"
    over = {name: count for name, count in large.items() if count > BUDGET[name]}
    assert not over, f"over budget {BUDGET}: {over}"


def _waiting(client: TestClient, kiosk: dict[str, str], *, tiles: tuple[str, ...] = ()) -> None:
    session = start_kiosk_session(client, kiosk)
    complete_intake(session, complaint="fever", tiles=tiles)
    assert session.confirm().status_code == 200


async def test_the_queue_screens_cost_the_same_however_many_patients_wait() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            medicine = hospital.departments["GENMED"]
            screens = {
                "board": (f"/queue?department_id={medicine}&limit=50", "nurse"),
                "alerts": (f"/alerts?acknowledged=false&department_id={medicine}", "nurse"),
                "doctor's list": ("/queue?limit=50", "doctor"),
                "register": ("/patients?limit=200&days=1", "nurse"),
            }

            def costs() -> dict[str, int]:
                return {
                    name: _cost(client, path, hospital.staff(who))
                    for name, (path, who) in screens.items()
                }

            # One patient called, one urgent with a danger sign nobody has confirmed yet.
            _waiting(client, kiosk)
            _waiting(client, kiosk, tiles=("chest_pain",))
            called = client.post(
                f"/queue/call-next?department_id={medicine}", headers=hospital.staff("doctor")
            )
            assert called.status_code == 200, called.text
            _waiting(client, kiosk)
            few = costs()
            for index in range(5):
                _waiting(client, kiosk, tiles=("chest_pain",) if index == 2 else ())
            many = costs()
            board = client.get(screens["board"][0], headers=hospital.staff("nurse")).json()
            assert len(board["items"]) == 8
    _assert_gated(few, many)


def _returning(
    client: TestClient, kiosk: dict[str, str], card: dict[str, str], complaint: str
) -> PatientSession:
    session = start_kiosk_session(client, kiosk)
    session.agree()
    assert session.identity(age=40, sex="female").status_code == 200
    assert session.post("/opd-card", card).status_code == 200
    session.danger_signs()
    session.answer_all({"chief_concern": complaint})
    assert session.confirm().status_code == 200
    return session


async def test_the_case_sheet_screen_costs_the_same_for_a_short_or_long_history() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first = start_kiosk_session(client, kiosk)
            complete_intake(
                first, complaint="fever", age=40, sex="female", phone="9876500041",
                answers={"allergies": "penicillin"},
            )
            receipt = first.confirm()
            assert receipt.status_code == 200, receipt.text
            card = {key: receipt.json()["opd_card"][key] for key in ("number", "pin")}
            second = _returning(client, kiosk, card, "cough")
            _returning(client, kiosk, card, "fever again")
            fourth = _returning(client, kiosk, card, "headache")
            # The fourth visit also has two versions of the doctor's notes.
            for version in ("0", "1"):
                saved = client.put(
                    f"/visits/{fourth.visit_id}/doctor-notes",
                    json={"findings": f"Version {int(version) + 1}.", "plan": "Review."},
                    headers={**hospital.staff("doctor"), "If-Match": f'"{version}"'},
                )
                assert saved.status_code == 200, saved.text
            screen = ("case-sheet", "transcript", "history", "queue-events", "doctor-notes",
                      "investigations", "documents")

            def costs(visit_id: str) -> dict[str, int]:
                return {
                    path: _cost(client, f"/visits/{visit_id}/{path}", hospital.staff("doctor"))
                    for path in screen
                }

            short, long = costs(second.visit_id), costs(fourth.visit_id)
            history = client.get(
                f"/visits/{fourth.visit_id}/history", headers=hospital.staff("doctor")
            ).json()
            assert len(history["previous_visits"]) == 3
    _assert_gated(short, long)


async def _refresh_rollup(settings: Settings) -> None:
    """Every day of the rollup recounted now, as the worker's nightly rebuild does."""
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SET ROLE osa_analytics_worker"))
            await connection.execute(text("SELECT osa_analytics.rebuild_daily_department()"))
            await connection.execute(text("RESET ROLE"))
    finally:
        await engine.dispose()


async def test_reports_cost_the_same_for_an_empty_or_a_busy_month() -> None:
    settings = integration_settings()
    reports = {
        "summary": "/analytics/summary?days=30",
        "timeseries": "/analytics/timeseries?days=30",
    }
    async with synthetic_hospital(settings) as hospital:
        with api_client(settings) as client:

            def costs() -> dict[str, int]:
                return {
                    name: _cost(client, path, hospital.staff("admin"))
                    for name, path in reports.items()
                }

            empty = costs()
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever")
            assert session.confirm().status_code == 200
            patient = await fetch(
                settings, hospital.hospital_id,
                "SELECT patient_id FROM visits WHERE id = :id", id=session.visit_id,
            )
            # Five earlier days in both departments: ten rows in the rollup, and today's.
            await execute(
                settings, hospital.hospital_id,
                "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, "
                "urgency, service_date, language, registered_at, queued_at, "
                "consult_started_at, consult_ended_at, token_number, token_label) "
                "SELECT gen_random_uuid(), d.hospital_id, :patient, d.id, 'done', 'routine', "
                "(now() AT TIME ZONE 'Asia/Kolkata')::date - g, 'hi', now() - g * interval "
                "'1 day', now() - g * interval '1 day', now() - g * interval '1 day' + "
                "interval '20 minutes', now() - g * interval '1 day' + interval '30 minutes', "
                "g, d.token_prefix || '-' || g FROM departments d "
                "CROSS JOIN generate_series(1, 5) AS g WHERE d.hospital_id = :hospital",
                patient=patient[0]["patient_id"], hospital=hospital.hospital_id,
            )
            await _refresh_rollup(settings)
            busy = costs()
            series = client.get(reports["timeseries"], headers=hospital.staff("admin")).json()
            assert len(series["items"]) == 11
    # The deleted hospital's rows leave the rollup too.
    await _refresh_rollup(settings)
    _assert_gated(empty, busy)
