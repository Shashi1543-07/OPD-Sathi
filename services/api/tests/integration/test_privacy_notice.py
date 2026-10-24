"""The full privacy notice and the grievance officer's contact (Phase 7, task 7.5.2).

Synthetic data only. Anyone can read a hospital's notice in English, Hindi or Marathi (it is
what a patient reads before agreeing to anything); administrators set whom to complain to;
the token page and the phone check-in page link to the notice.
"""

from uuid import uuid4

from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


async def test_the_notice_is_public_and_shows_the_hospitals_grievance_officer() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            path = f"/privacy-notice/{hospital.hospital_id}"
            notice = client.get(path, params={"language": "hi"})
            assert notice.status_code == 200, notice.text
            body = notice.json()
            assert body["hospital_name"] == "Synthetic test hospital"
            assert body["grievance_contact"] is None
            assert body["notice"]["language"] == "hi"
            assert body["notice"]["version"] == "privacy-notice-1.0"
            assert body["notice"]["review_status"] == "draft_needs_clinician_review"
            assert "डेटा संरक्षण बोर्ड" in body["notice"]["text"]
            assert client.get(path, params={"language": "fr"}).status_code == 422
            assert client.get(f"/privacy-notice/{uuid4()}").status_code == 404

            settings = client.get("/admin/privacy-settings", headers=hospital.staff("admin"))
            assert settings.status_code == 200, settings.text
            assert settings.json()["grievance_contact"] == ""
            assert settings.json()["notice_path"] == f"/privacy/{hospital.hospital_id}"
            contact = "Dr S. Rao, Grievance Officer · Room 12 · 020-2555-0101"
            assert (
                client.put(
                    "/admin/privacy-settings",
                    json={"grievance_contact": contact},
                    headers=hospital.staff("nurse"),
                ).status_code
                == 403
            )
            assert (
                client.put(
                    "/admin/privacy-settings",
                    json={"grievance_contact": "x" * 501},
                    headers=hospital.staff("admin"),
                ).status_code
                == 422
            )
            saved = client.put(
                "/admin/privacy-settings",
                json={"grievance_contact": f"  {contact}  "},
                headers=hospital.staff("admin"),
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["grievance_contact"] == contact
            assert saved.json()["updated_by_name"]
            assert client.get(path).json()["grievance_contact"] == contact
        audit = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'admin.privacy_settings.updated'",
            h=hospital.hospital_id,
        )
        assert [row["meta"] for row in audit] == [{"grievance_contact_set": True}]


async def test_the_token_page_and_phone_check_in_link_to_the_notice() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            expected = f"/privacy/{hospital.hospital_id}"
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session)
            receipt = session.confirm()
            assert receipt.status_code == 200, receipt.text
            status = client.get(f"/status/{receipt.json()['status_token']}")
            assert status.json()["privacy_path"] == expected

            point = client.post(
                "/admin/kiosks",
                json={"label": "Waiting hall poster", "kind": "mobile"},
                headers=hospital.staff("admin"),
            ).json()
            link = client.get(
                f"/admin/kiosks/{point['id']}/checkin-link", headers=hospital.staff("admin")
            ).json()
            code = str(link["path"]).removeprefix("/m/")
            assert client.get(f"/intake/checkin/{code}").json()["privacy_path"] == expected
