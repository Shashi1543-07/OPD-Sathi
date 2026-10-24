"""Prior investigations: accepted readings across visits, in date order (synthetic, 7.11.3).

The papers are uploaded through the API; what AI read from each is written as the reading
job would write it (encrypted to the document), so the dates and decisions are exact.
"""

import json
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1.visit_access import care_date
from app.core.crypto import encrypt_field
from tests.integration.synthetic import (
    PatientSession,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

_JPEG = b"\xff\xd8\xff\xe0synthetic investigation" + b"\x00" * 64


def _reading(printed: date | None, haemoglobin: str, flag: str = "") -> dict[str, Any]:
    return {
        "readable": True,
        "document_type": "lab_report",
        "document_date": printed.isoformat() if printed else "",
        "facility": "Synthetic Diagnostics",
        "diagnoses": [],
        "medicines": [],
        "lab_results": [
            {
                "test": "Haemoglobin",
                "value": haemoglobin,
                "unit": "g/dL",
                "reference_range": "12.0 - 15.0",
                "flag": flag,
            }
        ],
        "procedures": [],
    }


def _paper(session: PatientSession) -> str:
    added = session.client.post(
        f"/intake/sessions/{session.visit_id}/documents?kind=lab_report",
        content=_JPEG,
        headers={**session.headers(), "Content-Type": "image/jpeg"},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["id"])


async def _read(
    hospital: SyntheticHospital,
    document_id: str,
    reading: dict[str, Any],
    *,
    printed: date | None,
    decision: str,
) -> None:
    sealed = encrypt_field(
        json.dumps(reading),
        key=hospital.settings.app_data_keyring,
        associated_data=f"documents:extracted:{document_id}",
    )
    reviewed = decision != "unreviewed"
    await execute(
        hospital.settings,
        hospital.hospital_id,
        "UPDATE documents SET ocr_status = 'complete', extracted = CAST(:extracted AS jsonb), "
        "document_date = :printed, review_status = :decision, "
        "reviewed_by = CASE WHEN CAST(:reviewed AS boolean) THEN CAST(:doctor AS uuid) END, "
        "reviewed_at = CASE WHEN CAST(:reviewed AS boolean) THEN now() END "
        "WHERE hospital_id = :h AND id = :d",
        extracted=json.dumps({"ciphertext": sealed}),
        printed=printed,
        decision=decision,
        reviewed=reviewed,
        doctor=hospital.users["doctor"],
        h=hospital.hospital_id,
        d=document_id,
    )


def _investigations(
    client: TestClient, hospital: SyntheticHospital, visit_id: str, who: str = "doctor"
) -> dict[str, Any]:
    response = client.get(f"/visits/{visit_id}/investigations", headers=hospital.staff(who))
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_accepted_readings_from_every_visit_in_date_order_and_nothing_unreviewed() -> None:
    # The hospital's day (India): an undated paper takes its visit's date, and from 18:30 UTC
    # that is already tomorrow on a runner whose clock is in UTC.
    today = care_date()
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first = start_kiosk_session(client, kiosk)
            complete_intake(first, complaint="fever", age=40)
            old_report = _paper(first)
            rejected = _paper(first)
            receipt = first.confirm()
            assert receipt.status_code == 200, receipt.text

            returning = start_kiosk_session(client, kiosk)
            returning.agree()
            assert returning.identity(age=40).status_code == 200
            assert returning.post("/opd-card", receipt.json()["opd_card"]).status_code == 200
            returning.danger_signs()
            new_report = _paper(returning)
            undated = _paper(returning)
            unreviewed = _paper(returning)
            returning.answer_all({"chief_concern": "fever again"})
            assert returning.confirm().status_code == 200

        older, newer = today - timedelta(days=90), today - timedelta(days=10)
        await _read(hospital, old_report, _reading(older, "9.8", "L"), printed=older,
                    decision="accepted")
        await _read(hospital, rejected, _reading(older, "3.0"), printed=older,
                    decision="rejected")
        await _read(hospital, new_report, _reading(newer, "12.9"), printed=newer,
                    decision="accepted")
        await _read(hospital, undated, _reading(None, "13.4"), printed=None,
                    decision="accepted")
        await _read(hospital, unreviewed, _reading(newer, "1.0"), printed=newer,
                    decision="unreviewed")

        with api_client(hospital.settings) as client:
            timeline = _investigations(client, hospital, returning.visit_id)
            papers = timeline["papers"]
            assert [paper["document_id"] for paper in papers] == [old_report, new_report, undated]
            assert [paper["date_source"] for paper in papers] == ["printed", "printed", "visit"]
            assert papers[0]["visit_id"] == first.visit_id
            assert papers[2]["date"] == today.isoformat()
            assert papers[0]["reviewed_by"] == "Synthetic doctor"
            assert timeline["awaiting_review"] == 1 and timeline["earlier_shown"] is True

            (haemoglobin,) = timeline["tests"]
            assert haemoglobin["test"] == "Haemoglobin"
            results = [entry["result"] for entry in haemoglobin["results"]]
            assert [result["value"] for result in results] == ["9.8", "12.9", "13.4"]
            # Flags come from the paper itself: the lab's own mark and its printed range.
            assert results[0]["printed_flag"] == "low" and results[0]["range_flag"] == "below"
            assert results[1]["printed_flag"] is None and results[1]["range_flag"] == "within"
            # Rejected and unreviewed readings never appear.
            assert "3.0" not in str(timeline) and "1.0" not in [r["value"] for r in results]

            nurse = _investigations(client, hospital, returning.visit_id, "nurse")
            assert len(nurse["papers"]) == 3
            other = client.get(
                f"/visits/{returning.visit_id}/investigations",
                headers=hospital.staff("ortho_doctor"),
            )
            assert other.status_code == 404

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'investigations.read' ORDER BY created_at",
            h=hospital.hospital_id,
        )
        assert audits[0]["meta"] == {"papers": 3, "tests": 1}


async def test_earlier_visits_drop_out_once_the_patient_leaves_care() -> None:
    today = care_date()
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first = start_kiosk_session(client, kiosk)
            complete_intake(first, complaint="fever", age=40)
            old_report = _paper(first)
            receipt = first.confirm()
            returning = start_kiosk_session(client, kiosk)
            returning.agree()
            assert returning.identity(age=40).status_code == 200
            assert returning.post("/opd-card", receipt.json()["opd_card"]).status_code == 200
            returning.danger_signs()
            returning.answer_all({"chief_concern": "fever again"})
            assert returning.confirm().status_code == 200

        older = today - timedelta(days=30)
        await _read(hospital, old_report, _reading(older, "11.0"), printed=older,
                    decision="accepted")
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE visits SET status = 'done', service_date = service_date - 7 "
            "WHERE hospital_id = :h AND id = :v",
            h=hospital.hospital_id,
            v=returning.visit_id,
        )
        with api_client(hospital.settings) as client:
            closed = _investigations(client, hospital, returning.visit_id)
            assert closed["earlier_shown"] is False and closed["papers"] == []
            oversight = _investigations(client, hospital, returning.visit_id, "admin")
            assert [paper["document_id"] for paper in oversight["papers"]] == [old_report]
