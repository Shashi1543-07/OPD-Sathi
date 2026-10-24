"""The doctor's own notes: versioned, audited, encrypted, and on the PDF (synthetic, 7.11.1)."""

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1 import case_sheets
from app.api.v1.visit_access import care_date
from tests.integration.synthetic import (
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

NOTES = {
    "findings": "Temperature 38.4 °C. Throat red, no exudate.",
    "assessment": "Viral pharyngitis likely.",
    "plan": "Paracetamol as needed, fluids. Return if breathing is difficult.",
}


def _visit(client: TestClient, hospital: SyntheticHospital) -> str:
    session = start_kiosk_session(client, activate_kiosk(client, hospital))
    complete_intake(session, complaint="fever", age=30)
    assert session.confirm().status_code == 200
    return session.visit_id


def _save(
    client: TestClient,
    hospital: SyntheticHospital,
    visit_id: str,
    body: dict[str, Any],
    *,
    version: str,
    who: str = "doctor",
) -> Any:
    return client.put(
        f"/visits/{visit_id}/doctor-notes",
        json=body,
        headers={**hospital.staff(who), "If-Match": version},
    )


async def test_each_save_is_a_new_version_and_earlier_wording_stays() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = _visit(client, hospital)
            empty = client.get(f"/visits/{visit_id}/doctor-notes", headers=hospital.staff("doctor"))
            assert empty.status_code == 200, empty.text
            assert empty.json() == {
                "visit_id": visit_id,
                "version": 0,
                "editable": True,
                "versions": [],
            }
            assert empty.headers["ETag"] == '"0"'

            follow_up = (care_date() + timedelta(days=7)).isoformat()
            first = _save(
                client, hospital, visit_id, {**NOTES, "follow_up_on": follow_up}, version='"0"'
            )
            assert first.status_code == 200, first.text
            assert first.json()["version"] == 1 and first.headers["ETag"] == '"1"'
            assert first.json()["versions"][0]["by_name"] == "Synthetic doctor"
            assert first.json()["versions"][0]["follow_up_on"] == follow_up

            second = _save(
                client,
                hospital,
                visit_id,
                {**NOTES, "plan": "Paracetamol as needed.", "follow_up_on": follow_up},
                version='"1"',
            )
            assert second.status_code == 200, second.text
            versions = second.json()["versions"]
            assert [item["version"] for item in versions] == [2, 1]
            assert versions[0]["plan"] == "Paracetamol as needed."
            assert versions[1]["plan"] == NOTES["plan"]

            # Nurses of the department read the notes; they cannot write them.
            read = client.get(f"/visits/{visit_id}/doctor-notes", headers=hospital.staff("nurse"))
            assert read.status_code == 200 and read.json()["version"] == 2

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT action, meta FROM audit_log WHERE hospital_id = :h "
            "AND action LIKE 'doctor_notes.%' ORDER BY created_at",
            h=hospital.hospital_id,
        )
        saves = [row["meta"] for row in audits if row["action"] == "doctor_notes.saved"]
        assert saves == [
            {
                "version_before": 0,
                "version_after": 1,
                "changed": ["findings", "assessment", "plan", "follow_up_on"],
            },
            {"version_before": 1, "version_after": 2, "changed": ["plan"]},
        ]
        assert sum(row["action"] == "doctor_notes.read" for row in audits) == 2
        # The audit log names the parts that changed, never what the doctor wrote.
        assert "pharyngitis" not in str(audits)

        stored = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT body::text AS body FROM doctor_notes WHERE hospital_id = :h",
            h=hospital.hospital_id,
        )
        assert len(stored) == 2
        assert all("pharyngitis" not in row["body"] for row in stored)


async def test_a_stale_save_is_refused_and_only_the_treating_doctor_writes() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = _visit(client, hospital)
            assert _save(client, hospital, visit_id, NOTES, version='"0"').status_code == 200

            # A second doctor of the department started from the empty notes.
            stale = _save(client, hospital, visit_id, NOTES, version='"0"', who="doctor2")
            assert stale.status_code == 409 and stale.json()["code"] == "NOTES_CONFLICT"
            assert _save(client, hospital, visit_id, NOTES, version='"1"', who="doctor2").json()[
                "version"
            ] == 2

            nurse = _save(client, hospital, visit_id, NOTES, version='"2"', who="nurse")
            assert nurse.status_code == 403
            assert (
                _save(client, hospital, visit_id, NOTES, version='"2"', who="ortho_doctor")
            ).status_code == 404
            unread = client.get(
                f"/visits/{visit_id}/doctor-notes", headers=hospital.staff("ortho_doctor")
            )
            assert unread.status_code == 404

            missing = client.put(
                f"/visits/{visit_id}/doctor-notes", json=NOTES, headers=hospital.staff("doctor")
            )
            assert missing.status_code == 428
            empty = _save(client, hospital, visit_id, {"findings": "  "}, version='"2"')
            assert empty.status_code == 422
            past = _save(
                client,
                hospital,
                visit_id,
                {**NOTES, "follow_up_on": (care_date() - timedelta(days=1)).isoformat()},
                version='"2"',
            )
            assert past.status_code == 422 and past.json()["code"] == "INVALID_FOLLOW_UP"


async def test_notes_close_once_the_patient_has_left_care() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = _visit(client, hospital)
            assert _save(client, hospital, visit_id, NOTES, version='"0"').status_code == 200
            await execute(
                hospital.settings,
                hospital.hospital_id,
                "UPDATE visits SET status = 'done', service_date = service_date - 2 "
                "WHERE hospital_id = :h AND id = :v",
                h=hospital.hospital_id,
                v=visit_id,
            )
            notes = client.get(f"/visits/{visit_id}/doctor-notes", headers=hospital.staff("doctor"))
            assert notes.json()["editable"] is False and notes.json()["version"] == 1
            closed = _save(client, hospital, visit_id, NOTES, version='"1"')
            assert closed.status_code == 409 and closed.json()["code"] == "NOTES_CLOSED"


async def test_the_pdf_prints_the_latest_notes(monkeypatch: Any) -> None:
    printed: dict[str, Any] = {}

    def render(*_args: Any, **kwargs: Any) -> bytes:
        printed.update(kwargs)
        return b"%PDF-1.7 synthetic"

    monkeypatch.setattr(case_sheets, "render_case_sheet_pdf", render)
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = _visit(client, hospital)
            pdf = client.get(f"/visits/{visit_id}/case-sheet.pdf", headers=hospital.staff("doctor"))
            assert pdf.status_code == 200 and printed["doctor_notes"] is None

            _save(client, hospital, visit_id, NOTES, version='"0"')
            _save(client, hospital, visit_id, {**NOTES, "plan": "Fluids."}, version='"1"')
            client.get(f"/visits/{visit_id}/case-sheet.pdf", headers=hospital.staff("doctor"))
            notes = printed["doctor_notes"]
            assert notes["plan"] == "Fluids." and notes["version"] == 2
            assert notes["by_name"] == "Written by Synthetic doctor"
