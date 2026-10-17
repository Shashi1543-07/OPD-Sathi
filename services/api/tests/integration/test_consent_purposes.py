"""Consent by purpose (R9): care is required, online AI help is a separate and optional yes.

Synthetic data only. A "no" keeps the whole visit on the server: no request reaches the AI
service, there is no voice input and no photo reading, and the visit still gets its token. A
"yes" can be changed until the summary is confirmed and withdrawn later, from the patient's
status page or at the desk; after that nothing more is sent, and the doctor no longer sees the
AI draft.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai.clinical_summary import apply_summary
from app.db.repositories import CaseSheetRepository
from app.db.session import set_hospital_scope
from tests.integration.synthetic import (
    GeminiStub,
    PatientSession,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
    with_gemini,
)

_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 256
_DRAFT = {
    "doctor_brief_en": "Adult reports a cough for 4-7 days.",
    "brief_statements": [
        {"text_en": "Adult reports a cough for 4-7 days.", "question_ids": ["chief_concern"]}
    ],
    "chief_complaint_en": "Cough",
    "associated_symptoms_en": [],
    "pertinent_negatives_en": [],
    "past_conditions_en": [],
    "medications": [],
    "allergies_en": [],
    "family_history_en": [],
    "answers_en": [],
}


def _capabilities(client: TestClient, session: PatientSession) -> dict[str, Any]:
    response = client.get("/intake/capabilities", headers=session.headers())
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _consents(hospital: SyntheticHospital, visit_id: str) -> list[dict[str, Any]]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT purpose, granted, text_version, channel, withdrawn_by, "
        "withdrawn_at IS NOT NULL AS withdrawn FROM consents "
        "WHERE hospital_id = :h AND visit_id = :v ORDER BY purpose",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return [dict(row) for row in rows]


async def _audit_actions(hospital: SyntheticHospital, visit_id: str) -> list[tuple[str, Any]]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT action, actor_type FROM audit_log WHERE hospital_id = :h "
        "AND resource_id = :v AND (action LIKE '%ai_consent%' OR action LIKE '%ai_withdrawn') "
        "ORDER BY created_at",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return [(row["action"], row["actor_type"]) for row in rows]


async def _store_ai_draft(hospital: SyntheticHospital, visit_id: str) -> None:
    """Put an AI draft on the case sheet, as the background summary step writes it."""
    engine = create_async_engine(hospital.settings.database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital.hospital_id)
            repository = CaseSheetRepository(connection, hospital.settings)
            saved = await repository.get(
                hospital_id=hospital.hospital_id, visit_id=visit_id, for_update=True
            )
            assert saved is not None
            await repository.replace_if_version(
                hospital_id=hospital.hospital_id,
                visit_id=visit_id,
                expected_version=int(saved["version"]),
                data=apply_summary(saved["data"], _DRAFT, model="test", asked=["chief_concern"]),
                editor_id=None,
            )
    finally:
        await engine.dispose()


async def test_saying_no_to_ai_help_keeps_the_visit_on_the_server() -> None:
    ai_service = GeminiStub("ok")
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client, with_gemini(client, ai_service, speech=True):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            notice = session.ai_consent or {}
            assert notice["version"] == "ai-consent-1.0" and notice["language"] == "en"
            assert "Google Gemini" in notice["text"] and "phone number" in notice["text"]
            # The AI question comes after the care consent, never instead of it.
            early = session.allow_ai(False)
            assert early.status_code == 409 and early.json()["code"] == "CONSENT_REQUIRED"

            complete_intake(
                session,
                complaint="cough since a week",
                answers={"duration": "4 to 7 days"},
                allow_ai=False,
            )
            assert _capabilities(client, session) == {
                "stt": "off",
                "tts": "gemini",
                "ai": False,
                "online": False,
            }
            heard = client.post(
                f"/intake/sessions/{session.visit_id}/transcribe",
                content=b"\x00\x00" * 900,
                headers={**session.headers(), "Content-Type": "audio/webm"},
            )
            assert heard.status_code == 503 and heard.json()["code"] == "STT_UNAVAILABLE"
            readback = session.post("/speak", {"kind": "readback"})
            assert readback.status_code == 503
            photo = client.post(
                f"/intake/sessions/{session.visit_id}/prescription-photo",
                content=_JPEG,
                headers={**session.headers(), "Content-Type": "image/jpeg"},
            )
            assert photo.status_code == 503 and photo.json()["code"] == "AI_UNAVAILABLE"

            receipt = session.confirm()
            assert receipt.status_code == 200, receipt.text
            status_token = receipt.json()["status_token"]
            assert client.get(f"/status/{status_token}").json()["ai_help"] == "off"
            stop = client.post(f"/status/{status_token}/ai-consent/withdraw")
            assert stop.status_code == 409 and stop.json()["code"] == "AI_HELP_NOT_ON"
            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            ).json()
            assert sheet["patient"]["ai_help"] == "off"

        # Nothing about this visit reached the AI service.
        assert ai_service.calls == 0
        assert [
            (row["purpose"], row["granted"], row["text_version"])
            for row in await _consents(hospital, session.visit_id)
        ] == [("ai", False, "ai-consent-1.0"), ("care", True, "consent-1.0")]
        assert await _audit_actions(hospital, session.visit_id) == [
            ("intake.ai_consent.declined", "patient")
        ]


async def test_a_patient_who_said_yes_can_stop_ai_help_from_their_status_page() -> None:
    ai_service = GeminiStub("ok")
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client, with_gemini(client, ai_service, speech=True):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(
                session,
                complaint="cough since a week",
                answers={"duration": "4 to 7 days"},
                allow_ai=True,
            )
            assert _capabilities(client, session) == {
                "stt": "gemini",
                "tts": "gemini",
                "ai": True,
                "online": True,
            }
            assert ai_service.calls > 0  # With the patient's yes, AI does its work.
            receipt = session.confirm()
            assert receipt.status_code == 200, receipt.text
            status_token = receipt.json()["status_token"]
            assert client.get(f"/status/{status_token}").json()["ai_help"] == "on"
            await _store_ai_draft(hospital, session.visit_id)
            doctor = hospital.staff("doctor")
            before = client.get(f"/visits/{session.visit_id}/case-sheet", headers=doctor)
            assert before.json()["case_sheet"]["ai_summary"]["brief_en"]

            assert client.post(f"/status/{'X' * 43}/ai-consent/withdraw").status_code == 404
            stopped = client.post(f"/status/{status_token}/ai-consent/withdraw")
            assert stopped.status_code == 200 and stopped.json() == {"ai_help": "withdrawn"}
            assert client.get(f"/status/{status_token}").json()["ai_help"] == "withdrawn"
            again = client.post(f"/status/{status_token}/ai-consent/withdraw")
            assert again.status_code == 409

            # The doctor no longer sees or uses the AI draft; the rest of the sheet stays.
            after = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            )
            assert after.status_code == 200, after.text
            assert after.json()["case_sheet"]["ai_summary"] is None
            assert after.json()["case_sheet"]["chief_complaint"]["text_original"]
            assert after.json()["patient"]["ai_help"] == "withdrawn"
            review = client.post(
                f"/visits/{session.visit_id}/ai-summary/review",
                json={"decision": "accepted"},
                headers={**hospital.staff("doctor"), "If-Match": after.headers["ETag"]},
            )
            assert review.status_code == 409 and review.json()["code"] == "AI_HELP_WITHDRAWN"

        rows = await _consents(hospital, session.visit_id)
        assert [(row["purpose"], row["withdrawn"], row["withdrawn_by"]) for row in rows] == [
            ("ai", True, "patient"),
            ("care", False, None),
        ]
        assert await _audit_actions(hospital, session.visit_id) == [
            ("intake.ai_consent.granted", "patient"),
            ("consent.ai_withdrawn", "patient"),
        ]


async def test_the_answer_can_change_until_confirmation_and_staff_can_stop_ai_help() -> None:
    ai_service = GeminiStub("ok")
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client, with_gemini(client, ai_service):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            stale = session.post(
                "/consent/ai", {"version": "ai-consent-0.9", "sha256": "0" * 64, "granted": True}
            )
            assert stale.status_code == 409 and stale.json()["code"] == "CONSENT_COPY_MISMATCH"
            assert session.allow_ai(False).status_code == 200
            assert session.allow_ai(True).status_code == 200  # Changed their mind.
            complete_intake(session, complaint="headache since morning")
            assert session.confirm().status_code == 200
            late = session.allow_ai(False)
            assert late.status_code == 409 and late.json()["code"] == "SESSION_COMPLETE"

            # The patient asks at the desk instead of using their phone.
            path = f"/visits/{session.visit_id}/ai-consent/withdraw"
            stopped = client.post(path, headers=hospital.staff("nurse"))
            assert stopped.status_code == 200, stopped.text
            assert client.post(path, headers=hospital.staff("nurse")).status_code == 409
            capabilities = _capabilities(client, session)
            assert capabilities["online"] is False and capabilities["ai"] is False

        rows = await _consents(hospital, session.visit_id)
        ai_row = next(row for row in rows if row["purpose"] == "ai")
        assert ai_row["granted"] and ai_row["withdrawn"] and ai_row["withdrawn_by"] == "staff"
        actions = await _audit_actions(hospital, session.visit_id)
        assert actions[-1] == ("consent.ai_withdrawn", "user")
