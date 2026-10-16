"""A patient session can never take a patient out of the queue or change confirmed answers.

Phase 5 security fixes, each of which failed before its fix (synthetic data only):
- ending a kiosk or phone session after the token was issued cancelled the queued visit;
- answers could still be corrected after confirmation when the session state lagged behind
  the database, and a corrected main problem took the patient out of the queue;
- "Call staff" had no limit and kept working after the visit had finished;
- after confirmation the session kept every answer, the status link and the card PIN (R12);
- a staff board stayed connected after its sign-in expired or its account was deactivated.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from redis.asyncio import from_url
from starlette.websockets import WebSocketDisconnect

from app.api.v1 import queue as queue_api
from app.core.security import issue_access_token
from tests.integration.synthetic import (
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


async def _session_state(hospital: SyntheticHospital, visit_id: str) -> dict[str, Any] | None:
    redis = from_url(hospital.settings.redis_state_url, decode_responses=True)
    try:
        raw = await redis.get(f"osa:intake:{hospital.hospital_id}:{visit_id}")
    finally:
        await redis.aclose()
    return json.loads(raw) if raw else None


async def _put_session_state(
    hospital: SyntheticHospital, visit_id: str, state: dict[str, Any]
) -> None:
    redis = from_url(hospital.settings.redis_state_url, decode_responses=True)
    try:
        await redis.set(f"osa:intake:{hospital.hospital_id}:{visit_id}", json.dumps(state), ex=600)
    finally:
        await redis.aclose()


async def _status(hospital: SyntheticHospital, visit_id: str) -> str:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT status FROM visits WHERE hospital_id = :h AND id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return str(rows[0]["status"])


async def test_ending_a_session_after_the_token_leaves_the_patient_in_the_queue() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            queued = start_kiosk_session(client, kiosk)
            complete_intake(queued)
            receipt = queued.confirm()
            assert receipt.status_code == 200, receipt.text

            # The kiosk's idle reset, or the patient's own phone, after the token was issued.
            for headers in (queued.headers(), kiosk):
                ended = client.delete(f"/intake/sessions/{queued.visit_id}", headers=headers)
                assert ended.status_code == 409, ended.text
                assert ended.json()["code"] == "SESSION_COMPLETE"
            board = client.get("/queue", headers=hospital.staff("nurse")).json()["items"]
            assert queued.visit_id in {item["visit_id"] for item in board}
            status = client.get(f"/status/{receipt.json()['status_token']}").json()
            assert status["state"] == "waiting"

            # A check-in left half-way is ended, and says so in the visit's timeline.
            unfinished = start_kiosk_session(client, kiosk)
            unfinished.agree()
            assert unfinished.identity().status_code == 200
            ended = client.delete(f"/intake/sessions/{unfinished.visit_id}", headers=kiosk)
            assert ended.status_code == 204, ended.text
            again = client.delete(
                f"/intake/sessions/{unfinished.visit_id}", headers=unfinished.headers()
            )
            assert again.status_code == 410

        assert await _status(hospital, queued.visit_id) == "queued"
        assert await _status(hospital, unfinished.visit_id) == "cancelled"
        events = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT from_status, to_status, actor_type, reason FROM visit_events "
            "WHERE hospital_id = :h AND visit_id = :v",
            h=hospital.hospital_id,
            v=unfinished.visit_id,
        )
        assert [dict(row) for row in events] == [
            {
                "from_status": "registering",
                "to_status": "cancelled",
                "actor_type": "patient",
                "reason": "session_ended",
            }
        ]
        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT actor_type, actor_id FROM audit_log WHERE hospital_id = :h "
            "AND action = 'intake.session.cancelled'",
            h=hospital.hospital_id,
        )
        assert [(row["actor_type"], row["actor_id"]) for row in audits] == [
            ("kiosk", hospital.kiosk_id)
        ]


async def test_answers_stay_fixed_after_confirmation_even_if_the_session_lags() -> None:
    """The confirmation committed, but the session update was lost (as in D9)."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="fever")
            before_confirmation = await _session_state(hospital, session.visit_id)
            assert before_confirmation is not None and before_confirmation["phase"] == "summary"
            assert session.confirm().status_code == 200
            await _put_session_state(hospital, session.visit_id, before_confirmation)

            # A different main problem would reopen the interview and leave the queue.
            changed = client.patch(
                f"/intake/sessions/{session.visit_id}/interview/answers",
                json={"question_id": "chief_concern", "answer": "chest pain and breathlessness"},
                headers=session.headers(),
            )
            assert changed.status_code == 409, changed.text
            assert changed.json()["code"] == "SESSION_COMPLETE"
            back = session.post("/interview/back")
            assert back.status_code == 409

        assert await _status(hospital, session.visit_id) == "queued"
        corrections = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM interview_turns WHERE hospital_id = :h "
            "AND visit_id = :v AND turn_index >= 100",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        assert corrections[0]["n"] == 0


async def test_call_staff_is_limited_per_visit_and_ends_with_the_visit() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            waiting = start_kiosk_session(client, kiosk)
            waiting.agree()
            for _ in range(5):
                assert waiting.post("/help").status_code == 200
            limited = waiting.post("/help")
            assert limited.status_code == 429, limited.text
            assert int(limited.headers["Retry-After"]) > 0

            seen = start_kiosk_session(client, kiosk)
            complete_intake(seen)
            assert seen.confirm().status_code == 200
            for action in ("call", "start", "complete"):
                done = client.post(
                    f"/queue/{seen.visit_id}/{action}", headers=hospital.staff("doctor")
                )
                assert done.status_code == 200, (action, done.text)
            finished = seen.post("/help")
            assert finished.status_code == 409, finished.text


async def test_a_confirmed_session_keeps_none_of_the_answers() -> None:
    """R12: session data is cleared after submission; the database keeps the record."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="burning feet since winter", phone="9876500021")
            first = session.confirm()
            assert first.status_code == 200, first.text
            receipt = first.json()

            state = await _session_state(hospital, session.visit_id)
            assert state is not None
            assert set(state) <= {"visit_id", "kiosk_id", "channel", "language", "phase"}
            assert state["phase"] == "confirmed"
            raw = json.dumps(state)
            for secret in (
                "burning feet",
                receipt["status_token"],
                receipt["opd_card"]["pin"],
                "9876500021",
            ):
                assert secret not in raw

            # A repeated confirmation still answers with the same receipt, from the database.
            again = session.confirm()
            assert again.status_code == 200, again.text
            assert {key: again.json()[key] for key in ("token", "status_token", "opd_card")} == {
                key: receipt[key] for key in ("token", "status_token", "opd_card")
            }
            # And the patient can still call staff from the token screen.
            assert session.post("/help").status_code == 200


def _close_code(socket: Any, *, within: float) -> int:
    """Wait for the server to close the board's socket and return the close code."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(socket.receive_json).result(timeout=within)
    except WebSocketDisconnect as closed:
        return int(closed.code)
    finally:
        pool.shutdown(wait=False)
    raise AssertionError("the server sent a message instead of closing the socket")


async def test_a_staff_board_disconnects_when_its_sign_in_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue_api, "_SOCKET_REVIEW_SECONDS", 0.2)
    async with synthetic_hospital() as hospital:
        genmed = hospital.departments["GENMED"]
        with api_client(hospital.settings) as client:
            # A sign-in that expires while the board is open: the board must reconnect.
            short = issue_access_token(
                subject=hospital.users["nurse"],
                role="nurse",
                hospital_id=hospital.hospital_id,
                settings=hospital.settings,
                lifetime=timedelta(seconds=2),
            )
            with client.websocket_connect(f"/ws/staff/queue?department_id={genmed}") as socket:
                socket.send_json({"type": "auth", "token": short})
                assert socket.receive_json()["type"] == "queue.snapshot"
                assert _close_code(socket, within=10) == 4401

            # An account deactivated while its board is open stops receiving patients.
            with client.websocket_connect(f"/ws/staff/queue?department_id={genmed}") as socket:
                socket.send_json({"type": "auth", "token": hospital.token("doctor2")})
                assert socket.receive_json()["type"] == "queue.snapshot"
                deactivated = client.patch(
                    f"/admin/users/{hospital.users['doctor2']}",
                    json={"is_active": False},
                    headers=hospital.staff("admin"),
                )
                assert deactivated.status_code == 200, deactivated.text
                assert _close_code(socket, within=10) == 4403
