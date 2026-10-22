"""Queue messages by SMS: only by the patient's own tick, sent by a worker without patient keys.

Synthetic data only. Typing a phone number records nothing about SMS. Ticking the box records
an ``sms`` consent with the version and hash of the notice beside it, and keeps a copy of the
number that only ``NOTIFY_KEY`` opens. The worker runs here as the real ``osa_worker`` role
with no patient-data key: it can deliver, it cannot read any patient's stored number. The
patient can stop messages from the status page; that erases the copy and cancels what is due.
"""

import os
from typing import Any
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.crypto import decrypt_field
from app.notifications.queue import deliver_pending_once
from app.workers.main import relay_once
from tests.integration.synthetic import (
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    execute,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

PHONE = "9876543210"


class RecordingSms:
    channel = "sms"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, *, phone: str, body: str) -> str:
        self.sent.append((phone, body))
        return f"synthetic-{len(self.sent)}"


def _worker_url() -> str:
    url = os.getenv("TEST_WORKER_DATABASE_URL") or os.getenv("WORKER_DATABASE_URL") or ""
    if not url:
        pytest.skip("TEST_WORKER_DATABASE_URL is required to run as the worker role")
    return url


async def _visit(hospital: SyntheticHospital, visit_id: str) -> dict[str, Any]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT sms_opt_in, notify_phone_enc, notify_phone_hmac FROM visits "
        "WHERE hospital_id = :h AND id = :v",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return dict(rows[0])


async def _sms_consent(hospital: SyntheticHospital, visit_id: str) -> list[dict[str, Any]]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT granted, text_version, channel, withdrawn_by FROM consents "
        "WHERE hospital_id = :h AND visit_id = :v AND purpose = 'sms'",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return [dict(row) for row in rows]


async def _notifications(hospital: SyntheticHospital, visit_id: str) -> list[dict[str, Any]]:
    rows = await fetch(
        hospital.settings,
        hospital.hospital_id,
        "SELECT template, status, last_error_code FROM notifications "
        "WHERE hospital_id = :h AND visit_id = :v ORDER BY created_at",
        h=hospital.hospital_id,
        v=visit_id,
    )
    return [dict(row) for row in rows]


async def _run_worker(hospital: SyntheticHospital, provider: Any) -> None:
    """One worker tick, as the restricted role and with the worker's real keys."""
    settings = hospital.settings.model_copy(update={"app_data_key": "", "app_hmac_key": ""})
    engine = create_async_engine(_worker_url())
    client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_state_url, decode_responses=True
    )
    try:
        async with engine.begin() as connection:
            await relay_once(connection, client, settings=settings)
        async with engine.begin() as connection:
            await deliver_pending_once(connection, settings=settings, provider=provider)
    finally:
        await client.aclose()
        await engine.dispose()


async def test_typing_a_phone_number_records_no_sms_choice() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            assert session.sms_consent is not None
            assert session.sms_consent["version"] == "sms-consent-1.0"
            complete_intake(session, phone=PHONE)
            assert session.confirm().status_code == 200
        visit = await _visit(hospital, session.visit_id)
        assert visit == {"sms_opt_in": False, "notify_phone_enc": None, "notify_phone_hmac": None}
        assert await _sms_consent(hospital, session.visit_id) == []
        assert await _notifications(hospital, session.visit_id) == []


async def test_sms_needs_the_patients_own_tick_on_the_current_notice() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            session.agree()
            no_notice = session.post(
                "/identity", {"age": 30, "sex": "male", "phone": PHONE, "sms_opt_in": True}
            )
            assert no_notice.status_code == 422
            stale = session.post(
                "/identity",
                {
                    "age": 30,
                    "sex": "male",
                    "phone": PHONE,
                    "sms_opt_in": True,
                    "sms_consent": {"version": "sms-consent-0.9", "sha256": "0" * 64},
                },
            )
            assert stale.status_code == 409 and stale.json()["code"] == "CONSENT_COPY_MISMATCH"
            assert await _sms_consent(hospital, session.visit_id) == []
            # Ticked, then the patient went back and unticked: the change is recorded.
            assert session.identity(phone=PHONE, sms_opt_in=True).status_code == 200
            assert session.identity(phone=PHONE, sms_opt_in=False).status_code == 200
        consent = await _sms_consent(hospital, session.visit_id)
        assert consent == [
            {
                "granted": False,
                "text_version": "sms-consent-1.0",
                "channel": "kiosk",
                "withdrawn_by": None,
            }
        ]
        visit = await _visit(hospital, session.visit_id)
        assert visit["sms_opt_in"] is False and visit["notify_phone_enc"] is None
        actions = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT action FROM audit_log WHERE hospital_id = :h AND resource_id = :v "
            "AND action LIKE 'intake.sms_consent.%' ORDER BY created_at",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        assert [row["action"] for row in actions] == [
            "intake.sms_consent.granted",
            "intake.sms_consent.declined",
        ]


async def test_worker_without_patient_keys_delivers_and_patient_can_stop() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, phone=PHONE, sms_opt_in=True)
            receipt = session.confirm()
            assert receipt.status_code == 200, receipt.text
            status_token = receipt.json()["status_token"]
            assert client.get(f"/status/{status_token}").json()["sms"] == "on"

            visit = await _visit(hospital, session.visit_id)
            assert visit["sms_opt_in"] is True and visit["notify_phone_hmac"]
            # The copy opens with NOTIFY_KEY, bound to this visit, and not with the data key.
            assert (
                decrypt_field(
                    visit["notify_phone_enc"],
                    key=hospital.settings.notify_key_bytes or b"",
                    associated_data=f"visits:notify_phone_enc:{session.visit_id}",
                )
                == PHONE
            )
            with pytest.raises(ValueError):
                decrypt_field(
                    visit["notify_phone_enc"],
                    key=hospital.settings.app_data_key_bytes,
                    associated_data=f"visits:notify_phone_enc:{session.visit_id}",
                )

            sms = RecordingSms()
            await _run_worker(hospital, sms)
            assert (PHONE, f"Your queue token is {receipt.json()['token']}.") in sms.sent
            sent = await _notifications(hospital, session.visit_id)
            assert {"template": "token_issued", "status": "sent", "last_error_code": None} in sent

            # A message still due when the patient stops is cancelled, not sent.
            await execute(
                hospital.settings,
                hospital.hospital_id,
                "INSERT INTO notifications (id, hospital_id, visit_id, channel, template, "
                "status, dedupe_key, recipient_hmac, next_attempt_at) VALUES (:id, :h, :v, "
                "'sms', 'two_ahead', 'pending', :key, :hmac, now())",
                id=uuid4(),
                h=hospital.hospital_id,
                v=session.visit_id,
                key=f"{session.visit_id}:two_ahead",
                hmac=visit["notify_phone_hmac"],
            )
            assert client.post(f"/status/{'X' * 43}/sms/stop").status_code == 404
            stopped = client.post(f"/status/{status_token}/sms/stop")
            assert stopped.status_code == 200 and stopped.json() == {"sms": "stopped"}
            assert client.get(f"/status/{status_token}").json()["sms"] == "stopped"
            assert client.post(f"/status/{status_token}/sms/stop").status_code == 409

        visit = await _visit(hospital, session.visit_id)
        assert visit == {"sms_opt_in": False, "notify_phone_enc": None, "notify_phone_hmac": None}
        consent = await _sms_consent(hospital, session.visit_id)
        assert consent[0]["granted"] is True and consent[0]["withdrawn_by"] == "patient"
        rows = await _notifications(hospital, session.visit_id)
        assert {"template": "two_ahead", "status": "failed", "last_error_code": "opted_out"} in rows
        before = len(sms.sent)
        await _run_worker(hospital, sms)
        assert len(sms.sent) == before


async def test_worker_role_cannot_read_patients_phone_numbers() -> None:
    engine = create_async_engine(_worker_url())
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text("SELECT phone_enc FROM patients LIMIT 1"))
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text("SELECT name_enc FROM patients LIMIT 1"))
    finally:
        await engine.dispose()
