"""Patients answering on their own phone from a hospital's check-in QR (synthetic data)."""

from typing import Any

from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    PatientSession,
    SyntheticHospital,
    api_client,
    complete_intake,
    fetch,
    synthetic_hospital,
)


def _create_point(client: TestClient, hospital: SyntheticHospital) -> dict[str, Any]:
    created = client.post(
        "/admin/kiosks",
        json={"label": "Waiting hall poster", "kind": "mobile"},
        headers=hospital.staff("admin"),
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


def _link(client: TestClient, hospital: SyntheticHospital, point_id: str) -> str:
    response = client.get(f"/admin/kiosks/{point_id}/checkin-link", headers=hospital.staff("admin"))
    assert response.status_code == 200, response.text
    path = str(response.json()["path"])
    assert path.startswith("/m/")
    return path.removeprefix("/m/")


def _start(client: TestClient, code: str, language: str = "hi") -> PatientSession:
    response = client.post(f"/intake/checkin/{code}/sessions", json={"language": language})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["channel"] == "mobile"
    return PatientSession(
        client=client,
        visit_id=body["visit_id"],
        token=body["patient_access_token"],
        consent=body["consent"],
    )


async def test_phone_checkin_reaches_the_queue_like_a_kiosk_visit() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            point = _create_point(client, hospital)
            assert point["kind"] == "mobile"
            assert "activation_code" not in point
            code = _link(client, hospital, point["id"])

            details = client.get(f"/intake/checkin/{code}")
            assert details.status_code == 200, details.text
            assert details.json()["hospital_name"] == "Synthetic test hospital"

            session = _start(client, code)
            complete_intake(session, complaint="bukhar aur khansi")
            receipt = session.confirm()
            assert receipt.status_code == 200, receipt.text
            token = receipt.json()["token"]

            queue = client.get("/queue", headers=hospital.staff("nurse"))
            assert queue.status_code == 200, queue.text
            item = next(row for row in queue.json()["items"] if row["token"] == token)
            assert item["channel"] == "phone"

            status = client.get(f"/status/{receipt.json()['status_token']}")
            assert status.status_code == 200 and status.json()["token"] == token

            # A check-in QR is not a device: no activation code, and its code is no secret.
            refused = client.post(
                f"/admin/kiosks/{point['id']}/activation-code", headers=hospital.staff("admin")
            )
            assert refused.status_code == 409
            assert refused.json()["code"] == "NOT_A_DEVICE_KIOSK"
            assert client.post("/kiosks/token", json={"device_secret": code}).status_code == 401

        consent = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT channel FROM consents WHERE hospital_id = :h AND visit_id = :v",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        assert consent[0]["channel"] == "mobile"


async def test_phone_checkin_refuses_unknown_paused_replaced_and_deleted_codes() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            point = _create_point(client, hospital)
            code = _link(client, hospital, point["id"])

            unknown = "A" * 43
            assert client.get(f"/intake/checkin/{unknown}").json()["code"] == "CHECKIN_UNAVAILABLE"
            # Anything that is not a well-formed code never reaches a public route.
            assert client.get("/intake/checkin/not-a-code").status_code == 401

            paused = client.patch(
                f"/admin/kiosks/{point['id']}",
                json={"is_active": False},
                headers=hospital.staff("admin"),
            )
            assert paused.status_code == 200, paused.text
            assert client.get(f"/intake/checkin/{code}").json()["code"] == "CHECKIN_TURNED_OFF"
            refused = client.post(f"/intake/checkin/{code}/sessions", json={"language": "en"})
            assert refused.status_code == 409

            client.patch(
                f"/admin/kiosks/{point['id']}",
                json={"is_active": True},
                headers=hospital.staff("admin"),
            )
            replaced = client.post(
                f"/admin/kiosks/{point['id']}/checkin-link", headers=hospital.staff("admin")
            )
            assert replaced.status_code == 200, replaced.text
            new_code = str(replaced.json()["path"]).removeprefix("/m/")
            assert new_code != code
            assert client.get(f"/intake/checkin/{code}").status_code == 404
            assert client.get(f"/intake/checkin/{new_code}").status_code == 200
            # The admin view shows the same, current QR again.
            assert _link(client, hospital, point["id"]) == new_code

            deleted = client.delete(f"/admin/kiosks/{point['id']}", headers=hospital.staff("admin"))
            assert deleted.status_code == 200, deleted.text
            assert client.get(f"/intake/checkin/{new_code}").status_code == 404


async def test_phone_checkin_limits_how_many_visits_one_phone_can_start() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            code = _link(client, hospital, _create_point(client, hospital)["id"])
            for _ in range(6):
                _start(client, code, language="en")
            limited = client.post(f"/intake/checkin/{code}/sessions", json={"language": "en"})
            assert limited.status_code == 429
            assert limited.json()["code"] == "RATE_LIMITED"
            assert int(limited.headers["Retry-After"]) > 0


async def test_phone_checkin_codes_never_cross_hospitals() -> None:
    async with synthetic_hospital() as first, synthetic_hospital() as second:
        with api_client(first.settings) as client:
            code = _link(client, first, _create_point(client, first)["id"])
            session = _start(client, code, language="en")
            # The session belongs to the first hospital only.
            other = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=second.staff("admin")
            )
            assert other.status_code == 404
