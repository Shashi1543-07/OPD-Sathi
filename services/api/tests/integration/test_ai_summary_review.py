"""A doctor accepts, rewrites or rejects the AI draft, which cites its sources (synthetic)."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.ai.clinical_summary import apply_summary
from app.db.repositories import CaseSheetRepository
from app.db.session import set_hospital_scope
from tests.integration.synthetic import (
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

DRAFT = {
    "doctor_brief_en": "40-year-old female reports fever for 1-3 days.",
    "brief_statements": [
        {
            "text_en": "40-year-old female reports fever for 1-3 days.",
            "question_ids": ["chief_concern", "duration"],
        }
    ],
    "chief_complaint_en": "Fever",
    "associated_symptoms_en": [],
    "pertinent_negatives_en": [],
    "past_conditions_en": [],
    "medications": [],
    "allergies_en": [],
    "family_history_en": [],
    "answers_en": [],
}


async def _visit_with_ai_draft(client: TestClient, hospital: SyntheticHospital) -> str:
    """A confirmed visit whose case sheet carries an AI draft, as the enrichment step writes it."""
    session = start_kiosk_session(client, activate_kiosk(client, hospital))
    complete_intake(session, complaint="fever", age=40)
    assert session.confirm().status_code == 200
    engine = create_async_engine(hospital.settings.database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital.hospital_id)
            repository = CaseSheetRepository(connection, hospital.settings)
            saved = await repository.get(
                hospital_id=hospital.hospital_id, visit_id=session.visit_id, for_update=True
            )
            assert saved is not None
            data = apply_summary(
                saved["data"], DRAFT, model="test-model", asked=["chief_concern", "duration"]
            )
            await repository.replace_if_version(
                hospital_id=hospital.hospital_id,
                visit_id=session.visit_id,
                expected_version=int(saved["version"]),
                data=data,
                editor_id=None,
            )
    finally:
        await engine.dispose()
    return session.visit_id


def _review(
    client: TestClient,
    hospital: SyntheticHospital,
    visit_id: str,
    body: dict[str, Any],
    *,
    who: str = "doctor",
    version: str | None = None,
) -> Any:
    if version is None:
        sheet = client.get(f"/visits/{visit_id}/case-sheet", headers=hospital.staff(who))
        version = sheet.headers.get("ETag", '"1"')
    return client.post(
        f"/visits/{visit_id}/ai-summary/review",
        json=body,
        headers={**hospital.staff(who), "If-Match": version},
    )


async def test_a_doctor_accepts_a_sourced_ai_draft() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = await _visit_with_ai_draft(client, hospital)
            sheet = client.get(f"/visits/{visit_id}/case-sheet", headers=hospital.staff("doctor"))
            summary = sheet.json()["case_sheet"]["ai_summary"]
            assert summary["review"] is None
            assert summary["statements"][0]["sources"] == ["chief_concern", "duration"]

            accepted = _review(client, hospital, visit_id, {"decision": "accepted"})
            assert accepted.status_code == 200, accepted.text
            review = accepted.json()["case_sheet"]["ai_summary"]["review"]
            assert review["status"] == "accepted" and review["by_name"] == "Synthetic doctor"
            assert accepted.json()["version"] == int(sheet.headers["ETag"].strip('"')) + 1
            # Accepting keeps the sentences and their sources.
            assert accepted.json()["case_sheet"]["ai_summary"]["statements"]

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'case_sheet.ai_summary_reviewed'",
            h=hospital.hospital_id,
        )
        assert [row["meta"]["decision"] for row in audits] == ["accepted"]


async def test_a_rewrite_keeps_the_ai_wording_and_a_rejection_hides_the_draft() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = await _visit_with_ai_draft(client, hospital)
            edited = _review(
                client,
                hospital,
                visit_id,
                {"decision": "edited", "brief_en": "Fever for two days, no cough."},
            )
            assert edited.status_code == 200, edited.text
            summary = edited.json()["case_sheet"]["ai_summary"]
            assert summary["brief_en"] == "Fever for two days, no cough."
            assert summary["statements"] == []
            assert summary["review"]["original_brief_en"] == DRAFT["doctor_brief_en"]

            # A later decision keeps the model's original wording on record.
            rejected = _review(client, hospital, visit_id, {"decision": "rejected"})
            review = rejected.json()["case_sheet"]["ai_summary"]["review"]
            assert review["status"] == "rejected"
            assert review["original_brief_en"] == DRAFT["doctor_brief_en"]


async def test_only_the_treating_department_doctor_reviews_a_current_draft() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            visit_id = await _visit_with_ai_draft(client, hospital)
            accept = {"decision": "accepted"}
            assert _review(client, hospital, visit_id, accept, who="nurse").status_code == 403
            assert (
                _review(client, hospital, visit_id, accept, who="ortho_doctor").status_code == 404
            )
            stale = _review(client, hospital, visit_id, accept, version='"1"')
            assert stale.status_code == 409 and stale.json()["code"] == "CASE_SHEET_CONFLICT"
            empty_edit = _review(client, hospital, visit_id, {"decision": "edited"})
            assert empty_edit.status_code == 422

            plain = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(plain, complaint="fever")
            assert plain.confirm().status_code == 200
            none = _review(client, hospital, plain.visit_id, accept)
            assert none.status_code == 409 and none.json()["code"] == "NO_AI_SUMMARY"
