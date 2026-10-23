"""Logs never carry patient data or credentials (CONTRIBUTING.md rule 5; Phase 7, task 7.9.3).

Drives a whole visit and staff sign-in through the real API at DEBUG level, captures every log
line the process writes (structlog's JSON on stdout and the standard library's records), and
fails if any answer, phone number, password, token or code sent or received appears in them.
"""

import json
import logging
from typing import Any

import pytest
import redis
from fastapi.testclient import TestClient

from tests.integration.synthetic import (
    STAFF_PASSWORD,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    integration_settings,
    start_kiosk_session,
    synthetic_hospital,
)

PHONE = "9876501234"
COMPLAINT = "marker-complaint-burning-feet-qx7"
WRONG_PASSWORD = "marker-wrong-password-zz9"


def _clear_rate_limits(hospital: SyntheticHospital) -> None:
    client = redis.Redis.from_url(hospital.settings.redis_state_url)
    try:
        keys = list(client.scan_iter(f"osa:{hospital.settings.app_env}:rl:*"))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


def _login(client: TestClient, hospital: SyntheticHospital, password: str) -> Any:
    _clear_rate_limits(hospital)
    return client.post(
        "/auth/login",
        json={
            "hospital_id": str(hospital.hospital_id),
            "email": f"nurse-{hospital.hospital_id}@example.test",
            "password": password,
        },
    )


def _secrets_in(body: Any) -> set[str]:
    """Every token-like string value in a JSON body (long enough not to match by chance)."""
    found: set[str] = set()
    if isinstance(body, dict):
        for key, value in body.items():
            if isinstance(value, str) and ("token" in key or "code" in key) and len(value) >= 16:
                found.add(value)
            else:
                found |= _secrets_in(value)
    elif isinstance(body, list):
        for item in body:
            found |= _secrets_in(item)
    return found


async def test_a_whole_visit_and_staff_sign_in_leave_no_patient_data_in_the_logs(
    capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    settings = integration_settings().model_copy(update={"log_level": "DEBUG"})
    secrets: set[str] = {PHONE, COMPLAINT, STAFF_PASSWORD, WRONG_PASSWORD}

    async with synthetic_hospital(settings) as hospital:
        with api_client(hospital.settings) as client:
            kiosk = activate_kiosk(client, hospital)
            session = start_kiosk_session(client, kiosk)
            secrets.add(session.token)
            complete_intake(session, complaint=COMPLAINT, phone=PHONE)
            confirmed = session.confirm()
            assert confirmed.status_code == 200, confirmed.text
            secrets |= _secrets_in(confirmed.json())

            # Staff read the record back, which decrypts it.
            for path in ("/queue", "/patients", f"/visits/{session.visit_id}/case-sheet"):
                read = client.get(path, headers=hospital.staff("nurse"))
                assert read.status_code == 200, (path, read.text)

            # A failed and a successful sign-in, and the refresh that follows.
            assert _login(client, hospital, WRONG_PASSWORD).status_code == 401
            signed_in = _login(client, hospital, STAFF_PASSWORD)
            assert signed_in.status_code == 200, signed_in.text
            secrets |= _secrets_in(signed_in.json())
            secrets |= {value for value in signed_in.cookies.values() if len(value) >= 16}
            refreshed = client.post(
                "/auth/refresh", json={"account": signed_in.json()["user"]["id"]}
            )
            assert refreshed.status_code == 200, refreshed.text
            secrets |= _secrets_in(refreshed.json())

    captured = capfd.readouterr()
    logs = "\n".join((captured.out, captured.err, caplog.text))
    # The run did log (request lines at least), so an empty capture cannot pass by accident.
    assert '"event"' in logs, "no structured log lines were captured"
    leaked = sorted(secret for secret in secrets if secret in logs)
    assert not leaked, "patient data or credentials in logs: " + json.dumps(
        [secret[:6] + "…" for secret in leaked]
    )
