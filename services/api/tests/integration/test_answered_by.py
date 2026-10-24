"""Who is answering for the patient, on the visit, its consents, case sheet and PDF (7.11.7)."""

from typing import Any

from app.api.v1 import case_sheets
from tests.integration.synthetic import (
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


async def _recorded(hospital: SyntheticHospital, visit_id: str) -> tuple[str, dict[str, str]]:
    visit = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT answered_by FROM visits WHERE hospital_id = :h AND id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    consents = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT purpose, given_by FROM consents WHERE hospital_id = :h AND visit_id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return visit[0]["answered_by"], {row["purpose"]: row["given_by"] for row in consents}


async def test_a_parent_answers_for_a_child_and_the_doctor_sees_it(monkeypatch: Any) -> None:
    printed: dict[str, Any] = {}

    def render(*_args: Any, **kwargs: Any) -> bytes:
        printed.update(kwargs)
        return b"%PDF-1.7 synthetic"

    monkeypatch.setattr(case_sheets, "render_case_sheet_pdf", render)
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(
                session, age=6, sex="male", allow_ai=True, sms_opt_in=False,
                answered_by="guardian",
            )
            assert session.confirm().status_code == 200
            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            )
            assert sheet.json()["patient"]["answered_by"] == "guardian"
            pdf = f"/visits/{session.visit_id}/case-sheet.pdf"
            assert client.get(pdf, headers=hospital.staff("doctor")).status_code == 200
            assert "Answered by a parent or guardian" in printed["patient_line"]

        answered, consents = await _recorded(hospital, session.visit_id)
        assert answered == "guardian"
        assert consents == {"care": "guardian", "ai": "guardian"}
        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'intake.consent.granted'",
            h=hospital.hospital_id,
        )
        assert audits[0]["meta"]["answered_by"] == "guardian"


async def test_a_child_cannot_go_on_until_someone_says_who_is_answering() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            unsaid = session.post("/identity", {"age": 9, "sex": "female"})
            assert unsaid.status_code == 422 and unsaid.json()["code"] == "ANSWERER_REQUIRED"
            # A teenager may answer for themselves, when the kiosk is told so.
            teen = session.post(
                "/identity", {"age": 16, "sex": "female", "answered_by": "patient"}
            )
            assert teen.status_code == 200, teen.text

        answered, consents = await _recorded(hospital, session.visit_id)
        assert answered == "patient" and consents == {"care": "patient"}


async def test_an_adult_answers_for_themselves_unless_someone_helping_says_otherwise() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            alone = start_kiosk_session(client, kiosk)
            complete_intake(alone, age=45)
            helped = start_kiosk_session(client, kiosk)
            # Agreed as the patient, then a relative said they are answering for him.
            helped.agree()
            said = helped.identity(age=82, sex="male", answered_by="representative")
            assert said.status_code == 200, said.text
            sheet_visit = helped.visit_id

        assert await _recorded(hospital, alone.visit_id) == ("patient", {"care": "patient"})
        answered, consents = await _recorded(hospital, sheet_visit)
        assert answered == "representative" and consents == {"care": "representative"}
