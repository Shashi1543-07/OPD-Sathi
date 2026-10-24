"""Feature switches per hospital (Phase 7, task 7.14.7), synthetic data only.

SMS queue messages are off until an administrator turns them on: kiosks then offer no SMS tick
and nothing is queued. Phone check-in stays on unless the hospital turns it off, which stops
every check-in QR at once. Each change is audited.
"""

from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

PHONE = "9876543210"


async def test_administrators_switch_features_and_each_change_is_audited() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            current = client.get("/admin/feature-settings", headers=hospital.staff("admin"))
            assert current.status_code == 200, current.text
            assert current.json() == {
                "sms_messages": False,
                "phone_checkin": True,
                "sms_provider": False,
                "updated_at": None,
                "updated_by_name": None,
            }
            assert (
                client.patch(
                    "/admin/feature-settings",
                    json={"sms_messages": True},
                    headers=hospital.staff("nurse"),
                ).status_code
                == 403
            )
            assert (
                client.patch(
                    "/admin/feature-settings", json={}, headers=hospital.staff("admin")
                ).status_code
                == 422
            )
            changed = client.patch(
                "/admin/feature-settings",
                json={"sms_messages": True},
                headers=hospital.staff("admin"),
            )
            assert changed.status_code == 200, changed.text
            # Changing one switch leaves the other as it was.
            assert changed.json()["sms_messages"] is True
            assert changed.json()["phone_checkin"] is True
            assert changed.json()["updated_by_name"]
        audit = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h "
            "AND action = 'admin.feature_settings.updated'",
            h=hospital.hospital_id,
        )
        assert [row["meta"] for row in audit] == [
            {"sms_messages": {"before": False, "after": True}}
        ]


async def test_with_sms_off_no_tick_is_offered_or_recorded() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            assert session.sms_consent is None
            # A screen shown before the switch went off may still send a tick: it counts as none.
            session.agree()
            ticked = session.post(
                "/identity",
                {
                    "age": 30,
                    "sex": "male",
                    "phone": PHONE,
                    "sms_opt_in": True,
                    "sms_consent": {"version": "sms-consent-1.0", "sha256": _sms_hash()},
                },
            )
            assert ticked.status_code == 200, ticked.text
            session.danger_signs(())
            session.answer_all({"chief_concern": "fever"})
            assert session.confirm().status_code == 200
        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT v.sms_opt_in, "
            "(SELECT count(*) FROM consents c WHERE c.hospital_id = v.hospital_id "
            "AND c.visit_id = v.id AND c.purpose = 'sms') AS consents, "
            "(SELECT count(*) FROM notifications n WHERE n.hospital_id = v.hospital_id "
            "AND n.visit_id = v.id) AS messages "
            "FROM visits v WHERE v.hospital_id = :h AND v.id = :v",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        assert dict(rows[0]) == {"sms_opt_in": False, "consents": 0, "messages": 0}


async def test_turning_phone_checkin_off_stops_every_qr_at_once() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            point = client.post(
                "/admin/kiosks",
                json={"label": "Waiting hall poster", "kind": "mobile"},
                headers=hospital.staff("admin"),
            ).json()
            link = client.get(
                f"/admin/kiosks/{point['id']}/checkin-link", headers=hospital.staff("admin")
            ).json()
            code = str(link["path"]).removeprefix("/m/")
            assert client.get(f"/intake/checkin/{code}").status_code == 200

            client.patch(
                "/admin/feature-settings",
                json={"phone_checkin": False},
                headers=hospital.staff("admin"),
            )
            details = client.get(f"/intake/checkin/{code}")
            assert details.status_code == 409 and details.json()["code"] == "CHECKIN_TURNED_OFF"
            started = client.post(f"/intake/checkin/{code}/sessions", json={"language": "en"})
            assert started.status_code == 409

            client.patch(
                "/admin/feature-settings",
                json={"phone_checkin": True},
                headers=hospital.staff("admin"),
            )
            assert client.get(f"/intake/checkin/{code}").status_code == 200


def _sms_hash() -> str:
    from app.domain.interview.consent import get_consent_copy

    return get_consent_copy("en", "sms")["sha256"]
