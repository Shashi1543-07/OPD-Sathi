"""End-to-end scenarios from section 10 of the IIT Bombay readiness plan (synthetic data).

Each test drives the real API, database and Redis the way a kiosk, a phone, a nurse and a
doctor would. Scenarios already covered elsewhere are listed in docs/E2E_SCENARIOS.md with
their tests; this file holds the ones that needed a test of their own.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi.testclient import TestClient

from app.api.v1.speech import MAX_IMAGE_BYTES
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
_PDF = b"%PDF-1.7\n" + b"\x00" * 256


def _start_on_phone(client: TestClient, hospital: SyntheticHospital) -> PatientSession:
    """A patient scans the hospital's phone check-in QR and starts on their own phone."""
    point = client.post(
        "/admin/kiosks",
        json={"label": "Waiting hall poster", "kind": "mobile"},
        headers=hospital.staff("admin"),
    )
    assert point.status_code == 201, point.text
    link = client.get(
        f"/admin/kiosks/{point.json()['id']}/checkin-link", headers=hospital.staff("admin")
    )
    code = str(link.json()["path"]).removeprefix("/m/")
    started = client.post(f"/intake/checkin/{code}/sessions", json={"language": "hi"})
    assert started.status_code == 201, started.text
    body = started.json()
    return PatientSession(
        client=client,
        visit_id=body["visit_id"],
        token=body["patient_access_token"],
        consent=body["consent"],
        ai_consent=body["ai_consent"],
    )


def _reply(question: dict[str, Any]) -> dict[str, Any]:
    """A valid answer to any question: skip what may be skipped, otherwise a neutral answer."""
    if question["optional"]:
        return {"question_id": question["id"], "skip": True}
    answer: str | int = 3 if question["input"] == "scale" else "not sure"
    return {"question_id": question["id"], "answer": answer}


def _turns(hospital: SyntheticHospital, visit_id: str) -> Any:
    return fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT question_id FROM interview_turns WHERE hospital_id = :h AND visit_id = :v "
        "ORDER BY turn_index",
        h=hospital.hospital_id,
        v=visit_id,
    )


async def test_scenario_03_an_unfinished_interview_never_reaches_the_queue() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            left = start_kiosk_session(client, kiosk)
            left.agree()
            assert left.identity(age=58, sex="male").status_code == 200
            left.danger_signs(("chest_pain",))
            first = left.get("/interview/next-question").json()["question"]
            assert left.post(
                "/interview/answers",
                {"question_id": first["id"], "answer": "pain in chest"},
                idempotent=True,
            ).is_success
            # Not confirmed: there is nothing to confirm yet.
            early = left.confirm()
            assert early.status_code == 409 and early.json()["code"] == "SUMMARY_NOT_READY"
            # The patient walks away; the kiosk's idle reset ends the session.
            ended = client.delete(f"/intake/sessions/{left.visit_id}", headers=kiosk)
            assert ended.status_code == 204, ended.text
            assert left.get("/interview/next-question").status_code == 410

            board = client.get("/queue", headers=hospital.staff("nurse")).json()
            assert left.visit_id not in {item["visit_id"] for item in board["items"]}
            register = client.get("/patients", headers=hospital.staff("nurse")).json()
            assert left.visit_id not in {item["visit_id"] for item in register["items"]}
            # The danger sign they tapped still reaches a nurse, who can go and find them.
            alerts = client.get("/alerts", headers=hospital.staff("nurse")).json()["items"]
            assert any(
                alert["visit_id"] == left.visit_id and alert["level"] == "urgent"
                for alert in alerts
            )

            # On a phone, closing the page and coming back resumes the same visit and question.
            phone = _start_on_phone(client, hospital)
            phone.agree()
            assert phone.identity().status_code == 200
            phone.danger_signs()
            asked = phone.get("/interview/next-question").json()["question"]["id"]
            assert phone.post(
                "/interview/answers",
                {"question_id": asked, "answer": "fever"},
                idempotent=True,
            ).is_success
            resumed = phone.get("/interview/next-question").json()
            again = phone.get("/interview/next-question").json()
            assert resumed["question"]["id"] == again["question"]["id"] != asked

        visits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT id, status, token_label FROM visits WHERE hospital_id = :h",
            h=hospital.hospital_id,
        )
        states = {str(row["id"]): (row["status"], row["token_label"]) for row in visits}
        assert states == {
            left.visit_id: ("cancelled", None),
            phone.visit_id: ("registering", None),
        }


async def test_scenarios_05_and_10_an_answer_sent_twice_is_saved_once() -> None:
    """A double tap, or a retry after the reply was lost on a flaky network."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            assert session.identity().status_code == 200
            session.danger_signs()
            answer = {"question_id": "chief_concern", "answer": "fever"}

            # The reply to the first request was lost; the kiosk sends the answer again.
            first = session.post("/interview/answers", answer, idempotent=True)
            assert first.status_code == 200, first.text
            retry = session.post("/interview/answers", answer, idempotent=True)
            assert retry.status_code == 200, retry.text
            assert retry.json()["question"]["id"] == first.json()["question"]["id"]

            # Two taps at the same moment on the next question.
            following = _reply(first.json()["question"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                both = list(
                    pool.map(
                        lambda _: session.post("/interview/answers", following, idempotent=True),
                        range(2),
                    )
                )
            assert [response.status_code for response in both] == [200, 200], [
                response.text for response in both
            ]
            assert len({response.json()["question"]["id"] for response in both}) == 1

            # The last answer, retried after the interview finished, shows the summary.
            body = both[0].json()
            while not body.get("complete"):
                last = _reply(body["question"])
                body = session.post("/interview/answers", last, idempotent=True).json()
            replay = session.post("/interview/answers", last, idempotent=True)
            assert replay.status_code == 200 and replay.json()["complete"] is True
            assert session.confirm().status_code == 200

        turns = [row["question_id"] for row in await _turns(hospital, session.visit_id)]
        assert turns.count("chief_concern") == 1
        assert turns.count(following["question_id"]) == 1
        assert len(turns) == len(set(turns))


async def test_scenario_08_a_patient_who_left_registers_again_with_their_card() -> None:
    """Marked absent, then a no-show after leaving: the next visit joins the same record."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            first = start_kiosk_session(client, kiosk)
            complete_intake(first, age=45, sex="male")
            receipt = first.confirm().json()
            zero = client.patch(
                "/admin/queue-settings",
                json={"call_grace_seconds": 0},
                headers=hospital.staff("admin"),
            )
            assert zero.status_code == 200
            for who, action in (("doctor", "call"), ("doctor", "skip"), ("nurse", "no-show")):
                moved = client.post(
                    f"/queue/{first.visit_id}/{action}", headers=hospital.staff(who)
                )
                assert moved.status_code == 200, (action, moved.text)
            # Too late to recall: the visit is closed, and the status page says so.
            late = client.post(f"/queue/{first.visit_id}/recall", headers=hospital.staff("nurse"))
            assert late.status_code == 409
            assert client.get(f"/status/{receipt['status_token']}").json()["state"] == "no_show"

            back = start_kiosk_session(client, kiosk)
            back.agree()
            assert back.identity(age=45, sex="male").status_code == 200
            linked = back.post(
                "/opd-card",
                {"number": receipt["opd_card"]["number"], "pin": receipt["opd_card"]["pin"]},
            )
            assert linked.status_code == 200, linked.text
            back.danger_signs()
            back.answer_all({"chief_concern": "fever"})
            again = back.confirm().json()
            assert again["opd_card"]["number"] == receipt["opd_card"]["number"]
            history = client.get(
                f"/visits/{back.visit_id}/history", headers=hospital.staff("doctor")
            ).json()
            assert [visit["status"] for visit in history["previous_visits"]] == ["no_show"]


async def test_scenario_09_check_in_works_while_the_ai_service_is_down() -> None:
    outage = GeminiStub("down")
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client, with_gemini(client, outage):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            # The patient said yes to AI help, so the system really tries to use it.
            complete_intake(
                session,
                complaint="fever with cough",
                answers={"duration": "4 to 7 days"},
                allow_ai=True,
            )
            photo = client.post(
                f"/intake/sessions/{session.visit_id}/prescription-photo",
                content=_JPEG,
                headers={**session.headers(), "Content-Type": "image/jpeg"},
            )
            # The patient is told to say or type the medicines instead.
            assert photo.status_code == 503 and photo.json()["code"] == "AI_UNAVAILABLE"
            confirmed = session.confirm()
            assert confirmed.status_code == 200, confirmed.text
            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("doctor")
            )
            assert sheet.status_code == 200, sheet.text
            data = sheet.json()["case_sheet"]
            # The deterministic case sheet, with no AI draft for the doctor to trust.
            assert data["chief_complaint"]["text_original"] == "fever with cough"
            assert not (data.get("ai_summary") or {}).get("brief_en")
            board = client.get("/queue", headers=hospital.staff("nurse")).json()["items"]
            assert confirmed.json()["token"] in {item["token"] for item in board}
        assert outage.calls > 0  # AI was tried and failed; nothing else did.


async def test_scenario_09_a_hanging_ai_service_only_slows_confirmation_a_little() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client, with_gemini(client, GeminiStub("hanging")):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever", allow_ai=True)
            started = time.monotonic()
            confirmed = session.confirm()
            elapsed = time.monotonic() - started
            assert confirmed.status_code == 200, confirmed.text
            # The AI checks at confirmation give up after 5 seconds and the rules decide.
            assert elapsed < 8, f"confirmation took {elapsed:.1f} s"


async def test_scenario_14_only_real_photos_of_a_sensible_size_are_read() -> None:
    reading = {
        "readable": True,
        "medicines": [{"name": "Paracetamol", "dose": "500 mg", "how_often": "1-0-1"}],
    }
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            models = GeminiStub("ok", reading)
            with with_gemini(client, models):
                session = start_kiosk_session(client, activate_kiosk(client, hospital))
                session.agree()
                assert session.allow_ai().status_code == 200
                assert session.identity().status_code == 200
                session.danger_signs()

                def send(content: bytes, content_type: str) -> Any:
                    return client.post(
                        f"/intake/sessions/{session.visit_id}/prescription-photo",
                        content=content,
                        headers={**session.headers(), "Content-Type": content_type},
                    )

                refused = {
                    "a PDF": (send(_PDF, "application/pdf"), 415, "UNSUPPORTED_IMAGE"),
                    "a PDF named as a photo": (send(_PDF, "image/png"), 415, "UNSUPPORTED_IMAGE"),
                    "an empty upload": (send(b"", "image/jpeg"), 400, "EMPTY_IMAGE"),
                    "a photo over the limit": (
                        send(_JPEG + b"\x00" * MAX_IMAGE_BYTES, "image/jpeg"),
                        413,
                        "IMAGE_TOO_LARGE",
                    ),
                    "a very large upload": (
                        send(_JPEG + b"\x00" * 2_000_000, "image/jpeg"),
                        413,
                        "REQUEST_TOO_LARGE",
                    ),
                }
                for case, (response, status, code) in refused.items():
                    assert (response.status_code, response.json()["code"]) == (status, code), case
                # None of the refused files reached the model.
                assert models.calls == 0

                read = send(_JPEG, "image/jpeg")
                assert read.status_code == 200, read.text
                assert read.json()["medicines"] == reading["medicines"]
                assert models.calls == 1
                # Five readings per visit in ten minutes; then the patient says or types them.
                for _ in range(4):
                    assert send(_JPEG, "image/jpeg").status_code == 200
                limited = send(_JPEG, "image/jpeg")
                assert limited.status_code == 429


async def test_scenario_15_simultaneous_registrations_get_distinct_tokens_in_order() -> None:
    patients = 12
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            with ThreadPoolExecutor(max_workers=patients) as pool:
                sessions: list[PatientSession] = list(
                    pool.map(lambda _: start_kiosk_session(client, kiosk), range(patients))
                )
            assert len({session.visit_id for session in sessions}) == patients
            for session in sessions:
                complete_intake(session, complaint="fever")
            with ThreadPoolExecutor(max_workers=patients) as pool:
                receipts = list(pool.map(lambda session: session.confirm(), sessions))
            assert [receipt.status_code for receipt in receipts] == [200] * patients, [
                receipt.text for receipt in receipts if receipt.status_code != 200
            ]
            tokens = sorted(receipt.json()["token"] for receipt in receipts)
            assert tokens == [f"MED-{number:03d}" for number in range(1, patients + 1)]
            assert len({receipt.json()["status_token"] for receipt in receipts}) == patients
            board = client.get("/queue", headers=hospital.staff("nurse")).json()["items"]
            assert sorted(item["position"] for item in board) == list(range(1, patients + 1))
