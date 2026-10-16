"""Calling patients: missed calls, recall, one patient per doctor, departments (synthetic)."""

import asyncio
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.repositories import QueueRepository
from app.db.session import set_hospital_scope
from app.workers.main import close_previous_days
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


def _queued_patient(
    client: TestClient, kiosk: dict[str, str], *, tiles: tuple[str, ...] = ()
) -> tuple[PatientSession, dict[str, Any]]:
    session = start_kiosk_session(client, kiosk)
    complete_intake(session, complaint="fever", tiles=tiles)
    receipt = session.confirm()
    assert receipt.status_code == 200, receipt.text
    return session, dict(receipt.json())


def _post(client: TestClient, hospital: SyntheticHospital, who: str, path: str) -> Any:
    # A fresh Idempotency-Key per request: a repeated key would replay the first answer.
    return client.post(path, headers=hospital.staff(who))


def _board(client: TestClient, hospital: SyntheticHospital, who: str = "nurse") -> dict[str, Any]:
    response = client.get(
        f"/queue?department_id={hospital.departments['GENMED']}", headers=hospital.staff(who)
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _no_grace(client: TestClient, hospital: SyntheticHospital) -> None:
    response = client.patch(
        "/admin/queue-settings", json={"call_grace_seconds": 0}, headers=hospital.staff("admin")
    )
    assert response.status_code == 200, response.text


async def test_a_patient_who_misses_their_call_is_recalled_to_their_place() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            (first, first_receipt), (second, _), (third, _) = (
                _queued_patient(client, kiosk) for _ in range(3)
            )
            call_next = f"/queue/call-next?department_id={hospital.departments['GENMED']}"
            called = _post(client, hospital, "doctor", call_next)
            assert called.status_code == 200, called.text
            assert called.json()["visit_id"] == first.visit_id

            # Too soon: the patient gets the hospital's grace period (3 minutes by default).
            early = _post(client, hospital, "doctor", f"/queue/{first.visit_id}/skip")
            assert early.status_code == 409 and early.json()["code"] == "CALL_GRACE_NOT_OVER"
            assert 0 < int(early.headers["Retry-After"]) <= 180
            _no_grace(client, hospital)
            skipped = _post(client, hospital, "doctor", f"/queue/{first.visit_id}/skip")
            assert skipped.status_code == 200, skipped.text

            status = client.get(f"/status/{first_receipt['status_token']}")
            assert status.json()["state"] == "skipped"
            board = _board(client, hospital)
            assert [item["visit_id"] for item in board["missed"]] == [first.visit_id]
            assert board["missed"][0]["skip_count"] == 1
            assert first.visit_id not in {item["visit_id"] for item in board["items"]}

            # The next call goes to the next patient; the missed one waits for a recall.
            assert _post(client, hospital, "doctor", call_next).json()["visit_id"] == (
                second.visit_id
            )
            assert _post(client, hospital, "doctor", f"/queue/{second.visit_id}/start").is_success
            assert _post(
                client, hospital, "doctor", f"/queue/{second.visit_id}/complete"
            ).is_success

            recalled = _post(client, hospital, "nurse", f"/queue/{first.visit_id}/recall")
            assert recalled.status_code == 200, recalled.text
            # Back on the original arrival time: ahead of the patient who came after them.
            board = _board(client, hospital)
            assert [item["visit_id"] for item in board["items"] if item["status"] == "queued"] == [
                first.visit_id,
                third.visit_id,
            ]
            assert board["missed"] == []
            assert _post(client, hospital, "doctor", call_next).json()["visit_id"] == (
                first.visit_id
            )

            timeline = client.get(
                f"/visits/{first.visit_id}/queue-events", headers=hospital.staff("doctor")
            ).json()["events"]
            assert [(event["from"], event["to"]) for event in timeline] == [
                ("awaiting_confirmation", "queued"),
                ("queued", "called"),
                ("called", "skipped"),
                ("skipped", "queued"),
                ("queued", "called"),
            ]
            assert timeline[0]["actor_type"] == "patient" and timeline[0]["actor_name"] is None
            assert timeline[2]["actor_name"] == "Synthetic doctor"
            assert timeline[3]["actor_role"] == "nurse"

        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT skip_count, recalled_at IS NOT NULL AS recalled FROM visits WHERE id = :v",
            v=first.visit_id,
        )
        assert rows[0]["skip_count"] == 1 and rows[0]["recalled"]


async def test_two_doctors_calling_at_once_never_get_the_same_patient() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            waiting = {_queued_patient(client, kiosk)[0].visit_id for _ in range(2)}
        department = hospital.departments["GENMED"]
        engine = create_async_engine(hospital.settings.database_url)
        try:
            async with engine.connect() as first, engine.connect() as second:
                holding = await first.begin()
                await set_hospital_scope(first, hospital.hospital_id)
                repository = QueueRepository(first, hospital.settings)
                await repository.lock_calls(
                    hospital_id=hospital.hospital_id, department_id=department
                )
                one = await repository.call_next(
                    hospital_id=hospital.hospital_id,
                    department_id=department,
                    actor_id=hospital.users["doctor"],
                    actor_role="doctor",
                )

                async def other_doctor() -> dict[str, Any] | None:
                    async with second.begin():
                        await set_hospital_scope(second, hospital.hospital_id)
                        other = QueueRepository(second, hospital.settings)
                        await other.lock_calls(
                            hospital_id=hospital.hospital_id, department_id=department
                        )
                        return await other.call_next(
                            hospital_id=hospital.hospital_id,
                            department_id=department,
                            actor_id=hospital.users["doctor2"],
                            actor_role="doctor",
                        )

                pressed = asyncio.create_task(other_doctor())
                await asyncio.sleep(0.5)
                # The second doctor waits at the department's call lock until the first is done.
                assert not pressed.done()
                await holding.commit()
                two = await asyncio.wait_for(pressed, timeout=10)
        finally:
            await engine.dispose()
        assert one is not None and two is not None
        assert {str(one["visit_id"]), str(two["visit_id"])} == waiting


async def test_a_doctor_has_one_patient_at_a_time() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            (a, _), (b, _), (c, _) = (_queued_patient(client, kiosk) for _ in range(3))
            call_next = f"/queue/call-next?department_id={hospital.departments['GENMED']}"

            first_call = _post(client, hospital, "doctor", f"/queue/{a.visit_id}/call")
            assert first_call.status_code == 200, first_call.text
            busy = _post(client, hospital, "doctor", f"/queue/{b.visit_id}/call")
            assert busy.status_code == 409 and busy.json()["code"] == "DOCTOR_BUSY"
            assert busy.json()["active_visit_id"] == a.visit_id
            assert busy.json()["active_token"] == first_call.json()["token"]
            taken = _post(client, hospital, "doctor2", f"/queue/{a.visit_id}/start")
            assert taken.status_code == 409 and taken.json()["code"] == "CLAIMED_BY_ANOTHER_DOCTOR"

            # The second doctor is free, so the next call is theirs; a call made by mistake
            # is put back without a missed call on record.
            assert _post(client, hospital, "doctor2", call_next).json()["visit_id"] == b.visit_id
            assert _post(client, hospital, "doctor2", f"/queue/{b.visit_id}/return").is_success

            assert _post(client, hospital, "doctor", f"/queue/{a.visit_id}/start").is_success
            still_busy = _post(client, hospital, "doctor", call_next)
            assert still_busy.status_code == 409 and still_busy.json()["code"] == "DOCTOR_BUSY"
            assert _post(client, hospital, "doctor", f"/queue/{a.visit_id}/complete").is_success
            assert _post(client, hospital, "doctor", call_next).json()["visit_id"] == b.visit_id

            # A nurse calls patients to the desk without holding any of them.
            assert _post(client, hospital, "nurse", f"/queue/{c.visit_id}/call").is_success

        returned = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT skip_count FROM visits WHERE id = :v",
            v=b.visit_id,
        )
        assert returned[0]["skip_count"] == 0


async def test_queue_actions_stay_inside_the_staff_members_department() -> None:
    async with synthetic_hospital() as hospital, synthetic_hospital() as other:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            patient, _ = _queued_patient(client, kiosk)
            visit = patient.visit_id
            for action in ("call", "start", "complete", "skip", "recall", "return", "no-show"):
                response = _post(client, hospital, "ortho_doctor", f"/queue/{visit}/{action}")
                assert response.status_code == 404, (action, response.text)
            genmed = hospital.departments["GENMED"]
            elsewhere = _post(
                client, hospital, "ortho_doctor", f"/queue/call-next?department_id={genmed}"
            )
            assert elsewhere.status_code == 403
            assert _post(client, other, "admin", f"/queue/{visit}/call").status_code == 404
            override = client.post(
                f"/queue/{visit}/override",
                json={"urgency": "urgent", "reason": "Synthetic cross-hospital check"},
                headers=other.staff("admin"),
            )
            assert override.status_code == 404
            timeline = client.get(
                f"/visits/{visit}/queue-events", headers=hospital.staff("ortho_doctor")
            )
            assert timeline.status_code == 404

        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT status, urgency FROM visits WHERE id = :v",
            v=visit,
        )
        assert rows[0]["status"] == "queued"


async def test_only_a_missed_patient_becomes_a_no_show() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            patient, receipt = _queued_patient(client, kiosk)
            visit = patient.visit_id
            _no_grace(client, hospital)
            waiting = _post(client, hospital, "nurse", f"/queue/{visit}/no-show")
            assert waiting.status_code == 409 and waiting.json()["code"] == "INVALID_TRANSITION"
            assert _post(client, hospital, "doctor", f"/queue/{visit}/call").is_success
            called = _post(client, hospital, "nurse", f"/queue/{visit}/no-show")
            assert called.status_code == 409
            assert _post(client, hospital, "doctor", f"/queue/{visit}/skip").is_success
            gone = _post(client, hospital, "nurse", f"/queue/{visit}/no-show")
            assert gone.status_code == 200, gone.text
            assert client.get(f"/status/{receipt['status_token']}").json()["state"] == "no_show"


async def test_end_of_day_cancels_waiting_visits_and_closes_missed_calls() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            waiting, _ = _queued_patient(client, kiosk)
            missed, _ = _queued_patient(client, kiosk)
            _no_grace(client, hospital)
            assert _post(client, hospital, "doctor", f"/queue/{missed.visit_id}/call").is_success
            assert _post(client, hospital, "doctor", f"/queue/{missed.visit_id}/skip").is_success
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE visits SET service_date = service_date - 1 WHERE id IN (:a, :b)",
            a=waiting.visit_id,
            b=missed.visit_id,
        )
        # Run inside this hospital's scope only, so other tests' rows are never touched.
        engine = create_async_engine(hospital.settings.migrations_database_url)
        try:
            async with engine.begin() as connection:
                await set_hospital_scope(connection, hospital.hospital_id)
                await close_previous_days(connection)
        finally:
            await engine.dispose()

        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT v.id, v.status, e.actor_type, e.reason FROM visits v JOIN visit_events e "
            "ON e.hospital_id = v.hospital_id AND e.visit_id = v.id AND e.to_status = v.status "
            "WHERE v.id IN (:a, :b)",
            a=waiting.visit_id,
            b=missed.visit_id,
        )
        closed = {str(row["id"]): (row["status"], row["actor_type"], row["reason"]) for row in rows}
        # Not seen before closing time is not the patient's absence.
        assert closed == {
            waiting.visit_id: ("cancelled", "system", "day_closed"),
            missed.visit_id: ("no_show", "system", "day_closed"),
        }
        grants = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT has_table_privilege('osa_worker', 'visit_events', 'INSERT') AS worker, "
            "has_table_privilege('osa_app', 'visit_events', 'UPDATE') AS app_update, "
            "has_table_privilege('osa_app', 'visit_events', 'DELETE') AS app_delete",
        )
        assert grants[0]["worker"] and not grants[0]["app_update"] and not grants[0]["app_delete"]


async def test_a_tapped_danger_sign_is_marked_until_a_nurse_confirms_it() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            patient, _ = _queued_patient(client, kiosk, tiles=("chest_pain",))
            calm, _ = _queued_patient(client, kiosk)
            board = {item["visit_id"]: item for item in _board(client, hospital)["items"]}
            assert board[patient.visit_id]["urgency"] == "urgent"
            assert board[patient.visit_id]["nurse_check"] is True
            assert board[calm.visit_id]["nurse_check"] is False

            alerts = client.get("/alerts", headers=hospital.staff("nurse")).json()["items"]
            tile = next(
                alert
                for alert in alerts
                if alert["visit_id"] == patient.visit_id and alert["rule_id"] == "RF_DS_TILE"
            )
            assert _post(client, hospital, "nurse", f"/alerts/{tile['id']}/ack").is_success
            board = {item["visit_id"]: item for item in _board(client, hospital)["items"]}
            assert board[patient.visit_id]["nurse_check"] is False


async def test_only_administrators_change_the_call_grace_period() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            initial = client.get("/admin/queue-settings", headers=hospital.staff("admin")).json()
            assert initial["call_grace_seconds"] == 180 and initial["is_default"]
            refused = client.patch(
                "/admin/queue-settings",
                json={"call_grace_seconds": 60},
                headers=hospital.staff("nurse"),
            )
            assert refused.status_code == 403
            too_long = client.patch(
                "/admin/queue-settings",
                json={"call_grace_seconds": 901},
                headers=hospital.staff("admin"),
            )
            assert too_long.status_code == 422
            saved = client.patch(
                "/admin/queue-settings",
                json={"call_grace_seconds": 60},
                headers=hospital.staff("admin"),
            ).json()
            assert saved["call_grace_seconds"] == 60
            assert saved["updated_by_name"] == "Synthetic admin"
            assert _board(client, hospital)["call_grace_seconds"] == 60

        audits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'admin.queue_settings.updated'",
            h=hospital.hospital_id,
        )
        assert [row["meta"]["call_grace_seconds"] for row in audits] == [
            {"before": 180, "after": 60}
        ]
