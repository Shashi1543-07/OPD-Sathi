"""The audit log screen's API (Phase 7, task 7.3.10): search, export, and reading is audited.

Synthetic data only. An administrator narrows the log by record, person, action and dates,
exports what they found as CSV, and every search and export leaves its own entry. Text from
the log cannot become a spreadsheet formula.
"""

import csv
import io
from datetime import date, timedelta

from tests.integration.synthetic import (
    STAFF_PASSWORD,
    api_client,
    execute,
    fetch,
    synthetic_hospital,
)


async def test_search_by_person_action_and_dates_and_every_read_is_audited() -> None:
    async with synthetic_hospital() as hospital:
        hospital.settings = hospital.settings.model_copy(update={"mfa_required_roles": ""})
        with api_client(hospital.settings) as client:
            # Two sign-ins: one wrong, one right, by the doctor.
            payload = {
                "hospital_id": str(hospital.hospital_id),
                "email": f"doctor-{hospital.hospital_id}@example.test",
            }
            client.post("/auth/login", json={**payload, "password": "wrong-password-0000"})
            assert client.post("/auth/login", json={**payload, "password": STAFF_PASSWORD})
            admin = {"Authorization": f"Bearer {hospital.token('admin')}"}
            doctor_id = str(hospital.users["doctor"])

            mine = client.get(f"/audit?person={doctor_id}", headers=admin)
            assert mine.status_code == 200, mine.text
            actions = {item["action"] for item in mine.json()["items"]}
            assert {"auth.login.failed", "auth.login.success"} <= actions
            assert all(item["actor_id"] == doctor_id for item in mine.json()["items"])
            assert mine.json()["items"][0]["actor_name"] == "Synthetic doctor"

            failed = client.get("/audit?action=login.failed", headers=admin).json()["items"]
            assert [item["action"] for item in failed] == ["auth.login.failed"]

            tomorrow = (date.today() + timedelta(days=2)).isoformat()
            none = client.get(f"/audit?date_from={tomorrow}", headers=admin).json()["items"]
            assert none == []
            backwards = client.get(f"/audit?date_from={tomorrow}&date_to=2026-01-01", headers=admin)
            assert backwards.status_code == 422
            # Only staff may read it, and only administrators.
            doctor = {"Authorization": f"Bearer {hospital.token('doctor')}"}
            assert client.get("/audit", headers=doctor).status_code == 403

        viewed = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h AND action = 'audit.viewed' "
            "ORDER BY created_at",
            h=hospital.hospital_id,
        )
        assert {"person": doctor_id} in [row["meta"] for row in viewed]


async def test_export_is_csv_safe_and_audited() -> None:
    async with synthetic_hospital() as hospital:
        # A staff name that a spreadsheet would run as a formula.
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "UPDATE users SET name = '=HYPERLINK(1)' WHERE hospital_id = :h AND id = :u",
            h=hospital.hospital_id,
            u=hospital.users["doctor"],
        )
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "INSERT INTO audit_log (id, hospital_id, actor_type, actor_id, action, "
            "resource_type, request_id) VALUES (gen_random_uuid(), :h, 'user', :u, "
            "'synthetic.formula', 'test', 'synthetic-test')",
            h=hospital.hospital_id,
            u=hospital.users["doctor"],
        )
        with api_client(hospital.settings) as client:
            admin = {"Authorization": f"Bearer {hospital.token('admin')}"}
            exported = client.get("/audit/export?action=synthetic", headers=admin)
            assert exported.status_code == 200, exported.text
            assert exported.headers["content-type"].startswith("text/csv")
            assert exported.headers["cache-control"] == "no-store"
            rows = list(csv.reader(io.StringIO(exported.text)))
            assert rows[0][:5] == ["time", "actor_type", "actor_id", "actor_name", "action"]
            assert [row[4] for row in rows[1:]] == ["synthetic.formula"]
            assert rows[1][3] == "'=HYPERLINK(1)"
        exports = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT meta FROM audit_log WHERE hospital_id = :h AND action = 'audit.exported'",
            h=hospital.hospital_id,
        )
        assert exports[0]["meta"] == {"action": "synthetic", "rows": 1}
