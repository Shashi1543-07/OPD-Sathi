"""Old papers (R6): photos kept encrypted for the visit's staff; AI reads them only with consent.

Synthetic data only. The photos are a few bytes with a real JPEG or PNG signature, and the
AI's reading comes from a stub of Gemini's network client, so nothing leaves the machine.
"""

import time
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    GeminiStub,
    PatientSession,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
    with_gemini,
)

_MARKER = b"synthetic paper photo"
_JPEG = b"\xff\xd8\xff\xe0" + _MARKER + b"\x00" * 64
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_PRINTED_DATE = (date.today() - timedelta(days=17)).isoformat()
_READING: dict[str, Any] = {
    "readable": True,
    "document_type": "lab_report",
    "document_date": _PRINTED_DATE,
    "facility": "Synthetic Diagnostics",
    "diagnoses": [],
    "medicines": [],
    "lab_results": [
        {
            "test": "Haemoglobin",
            "value": "9.8",
            "unit": "g/dL",
            "reference_range": "(12.0 - 15.0)",
            "flag": "L",
        },
        {
            "test": "Platelet count",
            "value": "2,10,000",
            "unit": "/cumm",
            "reference_range": "1,50,000 - 4,50,000",
            "flag": "",
        },
        {
            "test": "Fasting glucose",
            "value": "132",
            "unit": "mg/dL",
            "reference_range": "70 - 100",
            "flag": "H",
        },
        {
            "test": "Urine culture",
            "value": "No growth",
            "unit": "",
            "reference_range": "",
            "flag": "",
        },
    ],
    "procedures": [],
}


def _add(
    session: PatientSession,
    photo: bytes = _JPEG,
    *,
    kind: str = "lab_report",
    mime: str = "image/jpeg",
) -> Any:
    return session.client.post(
        f"/intake/sessions/{session.visit_id}/documents?kind={kind}",
        content=photo,
        headers={**session.headers(), "Content-Type": mime},
    )


def _papers(
    client: TestClient, hospital: SyntheticHospital, visit_id: str, who: str = "doctor"
) -> dict[str, Any]:
    response = client.get(f"/visits/{visit_id}/documents", headers=hospital.staff(who))
    assert response.status_code == 200, response.text
    return dict(response.json())


def _once_read(
    client: TestClient, hospital: SyntheticHospital, visit_id: str, document_id: str
) -> dict[str, Any]:
    """The paper as the doctor sees it once AI has finished with it."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        paper = next(
            item
            for item in _papers(client, hospital, visit_id)["documents"]
            if item["id"] == document_id
        )
        if paper["reading_status"] != "pending":
            return dict(paper)
        time.sleep(0.1)
    raise AssertionError("the paper was not read in time")


def _review(
    client: TestClient,
    hospital: SyntheticHospital,
    visit_id: str,
    document_id: str,
    decision: str,
    who: str = "doctor",
) -> Any:
    return client.post(
        f"/visits/{visit_id}/documents/{document_id}/review",
        json={"decision": decision},
        headers=hospital.staff(who),
    )


async def _audit_actions(hospital: SyntheticHospital) -> list[str]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT action FROM audit_log WHERE hospital_id = :h AND action LIKE 'document%' "
        "ORDER BY created_at",
        h=hospital.hospital_id,
    )
    return [row["action"] for row in rows]


async def test_a_patients_photo_is_kept_encrypted_for_the_visits_staff() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever")
            added = _add(session)
            assert added.status_code == 201, added.text
            paper = added.json()
            assert paper["kind"] == "lab_report" and paper["size"] == len(_JPEG)
            listed = session.get("/documents").json()
            assert [item["id"] for item in listed["documents"]] == [paper["id"]]
            assert listed["limit"] == 5
            assert session.confirm().status_code == 200

            shown = _papers(client, hospital, session.visit_id)
            assert shown["earlier"] == [] and shown["earlier_shown"] is True
            (seen,) = shown["documents"]
            # The patient did not allow AI help, so the paper is kept but not read.
            assert seen["reading_status"] == "skipped" and seen["reading"] is None
            assert seen["source"] == "patient" and seen["added_by"] is None
            assert seen["review_status"] == "unreviewed"
            photo = client.get(
                f"/visits/{session.visit_id}/documents/{paper['id']}/content",
                headers=hospital.staff("nurse"),
            )
            assert photo.status_code == 200
            assert photo.content == _JPEG
            assert photo.headers["content-type"] == "image/jpeg"
            assert photo.headers["cache-control"] == "private, no-store"
            assert photo.headers["x-content-type-options"] == "nosniff"

            # After confirming, the patient's papers are fixed: show any others to the doctor.
            for refused in (
                _add(session),
                session.get("/documents"),
                client.delete(
                    f"/intake/sessions/{session.visit_id}/documents/{paper['id']}",
                    headers=session.headers(),
                ),
            ):
                assert refused.status_code == 409, refused.text
                assert refused.json()["code"] == "SESSION_COMPLETE"

        stored = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT d.object_key, d.extracted, c.ciphertext FROM documents d "
            "JOIN document_contents c ON c.hospital_id = d.hospital_id AND c.document_id = d.id "
            "WHERE d.hospital_id = :h",
            h=hospital.hospital_id,
        )
        assert len(stored) == 1
        assert stored[0]["object_key"] == f"pg:{paper['id']}"
        assert stored[0]["extracted"] is None
        ciphertext = bytes(stored[0]["ciphertext"])
        assert _MARKER not in ciphertext and len(ciphertext) > len(_JPEG)
        assert await _audit_actions(hospital) == [
            "document.added",
            "documents.read",
            "document.photo_viewed",
        ]


async def test_a_patient_takes_photos_back_until_confirming_and_adds_at_most_five() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="knee pain")
            first = _add(session, kind="prescription").json()["id"]
            second = _add(session, _PNG, kind="other", mime="image/png").json()["id"]
            path = f"/intake/sessions/{session.visit_id}/documents/{first}"
            removed = client.delete(path, headers=session.headers())
            assert removed.status_code == 204, removed.text
            again = client.delete(path, headers=session.headers())
            assert again.status_code == 404
            assert [item["id"] for item in session.get("/documents").json()["documents"]] == [
                second
            ]
            for _ in range(4):
                assert _add(session).status_code == 201
            sixth = _add(session)
            assert sixth.status_code == 409 and sixth.json()["code"] == "TOO_MANY_DOCUMENTS"

        left = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT (SELECT count(*) FROM documents WHERE hospital_id = :h) AS papers, "
            "(SELECT count(*) FROM document_contents WHERE hospital_id = :h) AS photos",
            h=hospital.hospital_id,
        )
        assert dict(left[0]) == {"papers": 5, "photos": 5}
        assert (await _audit_actions(hospital)).count("document.removed") == 1


async def test_only_real_photos_of_known_kinds_are_kept() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="cough")
            pdf = b"%PDF-1.7\n" + b"\x00" * 64
            refusals = {
                "pdf": _add(session, pdf, mime="application/pdf"),
                "renamed pdf": _add(session, pdf),
                "png named jpeg": _add(session, _PNG),
                "empty": _add(session, b""),
                "too large": _add(session, b"\xff\xd8\xff" + b"\x00" * 1_000_000),
                "unknown kind": _add(session, kind="x-ray"),
            }
            codes = {name: response.status_code for name, response in refusals.items()}
            assert codes == {
                "pdf": 415,
                "renamed pdf": 415,
                "png named jpeg": 415,
                "empty": 400,
                "too large": 413,
                "unknown kind": 422,
            }
            kept_png = _add(session, _PNG, kind="discharge_summary", mime="image/png")
            assert kept_png.status_code == 201, kept_png.text

        kept = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT kind, mime FROM documents WHERE hospital_id = :h",
            h=hospital.hospital_id,
        )
        assert [dict(row) for row in kept] == [{"kind": "discharge_summary", "mime": "image/png"}]


async def test_ai_reads_a_paper_only_with_consent_and_the_doctor_decides() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            allowed = start_kiosk_session(client, kiosk)
            complete_intake(allowed, complaint="tiredness", allow_ai=True)
            declined = start_kiosk_session(client, kiosk)
            complete_intake(declined, complaint="tiredness", allow_ai=False)
            stub = GeminiStub(value=_READING)
            with with_gemini(client, stub):
                unread = _add(declined).json()["id"]
                assert stub.calls == 0
                paper = _add(allowed).json()["id"]
                seen = _once_read(client, hospital, allowed.visit_id, paper)
            assert stub.calls == 1
            assert _papers(client, hospital, declined.visit_id)["documents"][0]["id"] == unread
            assert (
                _papers(client, hospital, declined.visit_id)["documents"][0]["reading_status"]
                == "skipped"
            )

            assert seen["reading_status"] == "complete"
            assert seen["document_date"] == _PRINTED_DATE
            reading = seen["reading"]
            assert reading["readable"] is True and reading["facility"] == "Synthetic Diagnostics"
            flags = {
                result["test"]: (result["printed_flag"], result["range_flag"])
                for result in reading["lab_results"]
            }
            # Compared only with the range printed on the paper; text results are not judged.
            assert flags == {
                "Haemoglobin": ("low", "below"),
                "Platelet count": (None, "within"),
                "Fasting glucose": ("high", "above"),
                "Urine culture": (None, None),
            }

            # Reviewing is the doctor's decision, and it can be changed.
            by_nurse = _review(client, hospital, allowed.visit_id, paper, "accepted", "nurse")
            assert by_nurse.status_code == 403
            accepted = _review(client, hospital, allowed.visit_id, paper, "accepted")
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["review_status"] == "accepted"
            assert accepted.json()["reviewed_by"] == "Synthetic doctor"
            rejected = _review(client, hospital, allowed.visit_id, paper, "rejected")
            assert rejected.json()["review_status"] == "rejected"
            # A paper that was not read has nothing to review.
            nothing = _review(client, hospital, declined.visit_id, unread, "accepted")
            assert nothing.status_code == 409 and nothing.json()["code"] == "NO_READING"

        stored = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT extracted::text AS extracted FROM documents WHERE hospital_id = :h "
            "AND id = :d",
            h=hospital.hospital_id,
            d=paper,
        )
        # What AI read is stored encrypted, like the photo.
        assert '"ciphertext"' in stored[0]["extracted"]
        assert "Haemoglobin" not in stored[0]["extracted"]
        actions = await _audit_actions(hospital)
        assert actions.count("document.reading_reviewed") == 2


async def test_stopping_ai_help_hides_what_ai_read_but_keeps_the_photo() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever", allow_ai=True)
            with with_gemini(client, GeminiStub(value=_READING)):
                paper = _add(session).json()["id"]
                assert _once_read(client, hospital, session.visit_id, paper)["reading"]
            assert session.confirm().status_code == 200
            stopped = client.post(
                f"/visits/{session.visit_id}/ai-consent/withdraw",
                headers=hospital.staff("nurse"),
            )
            assert stopped.status_code == 200, stopped.text

            (seen,) = _papers(client, hospital, session.visit_id)["documents"]
            assert seen["ai_withdrawn"] is True and seen["reading"] is None
            refused = _review(client, hospital, session.visit_id, paper, "accepted")
            assert refused.status_code == 409 and refused.json()["code"] == "AI_HELP_WITHDRAWN"
            photo = client.get(
                f"/visits/{session.visit_id}/documents/{paper}/content",
                headers=hospital.staff("doctor"),
            )
            assert photo.status_code == 200 and photo.content == _JPEG


async def test_a_failed_reading_leaves_the_photo_for_the_doctor_to_read() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever", allow_ai=True)
            with with_gemini(client, GeminiStub(mode="down")):
                paper = _add(session).json()["id"]
                seen = _once_read(client, hospital, session.visit_id, paper)
            assert seen["reading_status"] == "failed" and seen["reading"] is None
            refused = _review(client, hospital, session.visit_id, paper, "accepted")
            assert refused.status_code == 409 and refused.json()["code"] == "NO_READING"
            # Nothing the model returned can stand in for a reading.
            with with_gemini(client, GeminiStub(value={"readable": True})):
                broken = _add(session).json()["id"]
                assert _once_read(client, hospital, session.visit_id, broken)["reading_status"] == (
                    "failed"
                )


async def test_staff_add_papers_only_while_the_patient_is_under_care() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever")
            assert _add(session).status_code == 201
            assert session.confirm().status_code == 200
            path = f"/visits/{session.visit_id}/documents"
            photo = {"Content-Type": "image/jpeg"}
            added = client.post(
                f"{path}?kind=prescription",
                content=_JPEG,
                headers={**hospital.staff("nurse"), **photo},
            )
            assert added.status_code == 201, added.text
            assert added.json()["source"] == "staff"
            assert added.json()["added_by"] == "Synthetic nurse"
            assert added.json()["reading_status"] == "skipped"
            # Administrators oversee; they do not add clinical papers.
            admin = client.post(path, content=_JPEG, headers={**hospital.staff("admin"), **photo})
            assert admin.status_code == 403
            doctor = {**hospital.staff("doctor"), **photo}
            for _ in range(8):
                more = client.post(path, content=_JPEG, headers=doctor)
                assert more.status_code == 201, more.text
            full = client.post(path, content=_JPEG, headers=doctor)
            assert full.status_code == 409 and full.json()["code"] == "TOO_MANY_DOCUMENTS"
            assert len(_papers(client, hospital, session.visit_id)["documents"]) == 10

        # Once the visit is over and in the past, no more papers are added; the record stays.
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE visits SET status = 'done', service_date = service_date - 7 "
            "WHERE hospital_id = :h AND id = :v",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        with api_client(hospital.settings) as client:
            closed = client.post(path, content=_JPEG, headers={**hospital.staff("nurse"), **photo})
            assert closed.status_code == 409 and closed.json()["code"] == "VISIT_CLOSED"
            assert len(_papers(client, hospital, session.visit_id)["documents"]) == 10


async def test_earlier_papers_follow_the_patient_while_under_care() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first = start_kiosk_session(client, kiosk)
            complete_intake(first, complaint="fever", age=40)
            earlier = _add(first).json()["id"]
            receipt = first.confirm()
            assert receipt.status_code == 200, receipt.text
            stranger = start_kiosk_session(client, kiosk)
            complete_intake(stranger, complaint="cough", age=25, sex="male")
            unrelated = _add(stranger).json()["id"]
            assert stranger.confirm().status_code == 200

            returning = start_kiosk_session(client, kiosk)
            returning.agree()
            assert returning.identity(age=40).status_code == 200
            linked = returning.post("/opd-card", receipt.json()["opd_card"])
            assert linked.status_code == 200, linked.text
            returning.danger_signs()
            returning.answer_all({"chief_concern": "fever again"})
            assert returning.confirm().status_code == 200

            shown = _papers(client, hospital, returning.visit_id)
            assert shown["documents"] == []
            assert [paper["id"] for paper in shown["earlier"]] == [earlier]
            assert shown["earlier"][0]["visit_id"] == first.visit_id
            through_today = f"/visits/{returning.visit_id}/documents"
            photo = client.get(
                f"{through_today}/{earlier}/content", headers=hospital.staff("doctor")
            )
            assert photo.status_code == 200 and photo.content == _JPEG
            # Another patient's paper is not reachable through this visit.
            other = client.get(
                f"{through_today}/{unrelated}/content", headers=hospital.staff("doctor")
            )
            assert other.status_code == 404
            # Earlier papers are seen, never decided on, from today's visit.
            decided = _review(client, hospital, returning.visit_id, earlier, "accepted")
            assert decided.status_code == 404

        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE visits SET status = 'done', service_date = service_date - 7 "
            "WHERE hospital_id = :h AND id = :v",
            h=hospital.hospital_id,
            v=returning.visit_id,
        )
        with api_client(hospital.settings) as client:
            closed = _papers(client, hospital, returning.visit_id)
            assert closed["earlier_shown"] is False and closed["earlier"] == []
            refused = client.get(
                f"{through_today}/{earlier}/content", headers=hospital.staff("doctor")
            )
            assert refused.status_code == 404
            oversight = _papers(client, hospital, returning.visit_id, "admin")
            assert [paper["id"] for paper in oversight["earlier"]] == [earlier]
