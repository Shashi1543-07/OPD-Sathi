"""Surgical and personal history, open answers, and the Ayurveda history (synthetic)."""

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    PatientSession,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


def _patch(
    client: TestClient,
    hospital: SyntheticHospital,
    visit_id: str,
    version: str,
    operations: list[dict[str, Any]],
) -> Any:
    return client.patch(
        f"/visits/{visit_id}/case-sheet",
        content=json.dumps(operations),
        headers={
            **hospital.staff("doctor"),
            "If-Match": version,
            "Content-Type": "application/json-patch+json",
        },
    )


async def test_an_ayush_hospital_asks_the_ayurveda_history_and_the_vaidya_adds_findings() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            settings = client.get("/admin/interview-settings", headers=hospital.staff("admin"))
            assert settings.json()["ayurveda_module"] is False
            refused = client.patch(
                "/admin/interview-settings",
                json={"ayurveda_module": True},
                headers=hospital.staff("nurse"),
            )
            assert refused.status_code == 403
            enabled = client.patch(
                "/admin/interview-settings",
                json={"ayurveda_module": True},
                headers=hospital.staff("admin"),
            )
            assert enabled.json()["ayurveda_module"] is True

            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(
                session,
                complaint="headache",
                answers={"ayur_appetite": "Less than before", "surgeries": "Don't know"},
            )
            assert session.confirm().status_code == 200
            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            )
            data = sheet.json()["case_sheet"]
            assert data["ayush"]["appetite"]["text_en"] == "Less than before"
            assert data["unanswered"] == [
                {
                    "question_id": "surgeries",
                    "status": "unknown",
                    "text_en": "Does not know: past operations",
                }
            ]

            patched = _patch(
                client,
                hospital,
                session.visit_id,
                sheet.headers["ETag"],
                [{"op": "add", "path": "/ayush/examination/nadi", "value": "Vata predominant"}],
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["case_sheet"]["ayush"]["examination"] == {
                "nadi": "Vata predominant"
            }
            # Only the listed examination fields exist; anything else is refused.
            invented = _patch(
                client,
                hospital,
                session.visit_id,
                patched.headers["ETag"],
                [{"op": "add", "path": "/ayush/examination/diagnosis", "value": "x"}],
            )
            assert invented.status_code == 422

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'admin.interview_settings.updated'",
            h=hospital.hospital_id,
        )
        assert [row["meta"]["ayurveda_module"] for row in audits] == [
            {"before": False, "after": True}
        ]


async def test_other_hospitals_never_see_ayurveda_questions() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            assert session.identity(age=40, sex="female").status_code == 200
            session.danger_signs()
            asked = _walk(session)
            assert "surgeries" in asked and "habits" in asked
            assert not any(question_id.startswith("ayur_") for question_id in asked)


async def test_dont_know_and_prefer_not_to_say_stand_alone_on_the_kiosk() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            assert session.identity(age=40, sex="female").status_code == 200
            session.danger_signs()
            question = _advance_to(session, "surgeries")
            flags = {option["id"]: option["none"] for option in question["options"]}
            assert flags["unknown"] is True and flags["declined"] is True and flags["none"] is True
            assert flags["appendix"] is False


def _walk(session: PatientSession, target: str | None = None) -> list[Any]:
    """Answer the main problem and skip everything optional, recording each question shown.

    Stops at ``target`` (returning its question as the last item) or when the interview ends.
    """
    body = session.get("/interview/next-question").json()
    asked: list[Any] = []
    for _ in range(40):
        if body.get("complete"):
            return asked
        question = body["question"]
        if question["id"] == target:
            return [*asked, question]
        asked.append(question["id"])
        if question["id"] == "chief_concern":
            payload: dict[str, Any] = {"question_id": "chief_concern", "answer": "headache"}
        elif question["optional"]:
            payload = {"question_id": question["id"], "skip": True}
        else:
            answer: str | int = 3 if question["input"] == "scale" else "not sure"
            payload = {"question_id": question["id"], "answer": answer}
        body = session.post("/interview/answers", payload, idempotent=True).json()
    raise AssertionError("the interview did not end")


def _advance_to(session: PatientSession, target: str) -> dict[str, Any]:
    """The ``target`` question as the kiosk receives it."""
    shown = _walk(session, target)
    if not shown or not isinstance(shown[-1], dict):
        raise AssertionError(f"{target} was never asked")
    return dict(shown[-1])


async def test_a_hospital_adds_the_review_of_systems_and_the_doctor_sees_each_system() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            settings = client.get("/admin/interview-settings", headers=hospital.staff("admin"))
            assert settings.json()["review_of_systems_module"] is False
            assert client.patch(
                "/admin/interview-settings", json={}, headers=hospital.staff("admin")
            ).status_code == 422
            enabled = client.patch(
                "/admin/interview-settings",
                json={"review_of_systems_module": True},
                headers=hospital.staff("admin"),
            )
            assert enabled.status_code == 200, enabled.text
            # Changing one switch leaves the other as it was.
            assert enabled.json()["review_of_systems_module"] is True
            assert enabled.json()["ayurveda_module"] is False

            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(
                session,
                complaint="headache",
                answers={"ros_checklist": "Trouble seeing or hearing"},
            )
            assert session.confirm().status_code == 200
            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            )
            symptoms = [
                item["text_en"]
                for item in sheet.json()["case_sheet"]["hpi"]["associated_symptoms"]
            ]
            assert "Trouble seeing or hearing" in symptoms

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'admin.interview_settings.updated'",
            h=hospital.hospital_id,
        )
        assert [row["meta"] for row in audits] == [
            {"review_of_systems_module": {"before": False, "after": True}}
        ]
