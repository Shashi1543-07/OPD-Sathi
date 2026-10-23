"""Kiosk device secrets rotate without re-pairing (Phase 7, task 7.3.9). Synthetic data only.

A secret older than the rotation interval is replaced at the next token renewal. The device
keeps working with the old one until it has used the new one once (a renewal lost to the
network must never strand a kiosk); after that, the old secret is refused.
"""

from uuid import uuid4

from tests.integration.synthetic import api_client, execute, fetch, synthetic_hospital


async def _age_secret(hospital: object, kiosk_id: str) -> None:
    await execute(
        hospital.settings,  # type: ignore[attr-defined]
        hospital.hospital_id,  # type: ignore[attr-defined]
        "UPDATE kiosks SET secret_rotated_at = now() - interval '40 days' "
        "WHERE hospital_id = :h AND id = :k",
        h=hospital.hospital_id,  # type: ignore[attr-defined]
        k=kiosk_id,
    )


async def test_secrets_rotate_and_a_lost_renewal_never_strands_the_kiosk() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            admin = hospital.staff("admin")
            created = client.post("/admin/kiosks", json={"label": "Rotation kiosk"}, headers=admin)
            kiosk_id = created.json()["id"]
            paired = client.post(
                "/kiosks/activate", json={"activation_code": created.json()["activation_code"]}
            )
            first = paired.json()["device_secret"]

            def renew(secret: str) -> object:
                return client.post(
                    "/kiosks/token",
                    json={"device_secret": secret},
                    headers={"X-Request-ID": uuid4().hex},
                )

            # A fresh secret is not rotated.
            fresh = renew(first)
            assert fresh.status_code == 200 and "device_secret" not in fresh.json()

            await _age_secret(hospital, kiosk_id)
            rotated = renew(first)
            assert rotated.status_code == 200
            lost = rotated.json()["device_secret"]
            assert lost != first
            # That answer never reached the kiosk: it renews again with the old secret, which
            # still works and brings yet another new secret.
            retried = renew(first)
            assert retried.status_code == 200
            newest = retried.json()["device_secret"]
            assert newest not in {first, lost}

            # Once the device uses its newest secret, every older one is refused.
            confirmed = renew(newest)
            assert confirmed.status_code == 200 and "device_secret" not in confirmed.json()
            assert renew(first).status_code == 401
            assert renew(lost).status_code == 401
            assert renew(newest).status_code == 200

        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS rotations FROM audit_log WHERE hospital_id = :h "
            "AND action = 'kiosk.secret_rotated'",
            h=hospital.hospital_id,
        )
        assert rows[0]["rotations"] >= 1
