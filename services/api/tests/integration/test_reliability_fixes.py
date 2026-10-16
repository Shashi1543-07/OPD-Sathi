"""Regression tests for defects found in the IIT Bombay readiness audit (synthetic data)."""

import json
from concurrent.futures import ThreadPoolExecutor

from redis.asyncio import from_url

from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)


async def test_route_confirmation_reads_the_encrypted_sheet_and_keeps_it_encrypted() -> None:
    """D8: the nurse's "Confirm route" decrypted nothing, so it always answered 409."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            # No department rule matches, so routing asks a nurse to confirm.
            complete_intake(session, complaint="something feels wrong")
            assert session.confirm().status_code == 200

            confirmed = client.post(
                f"/queue/{session.visit_id}/routing/confirm",
                json={"reason": "Checked with the patient at the desk"},
                headers=hospital.staff("nurse"),
            )
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["routing"]["needs_nurse_confirmation"] is False

            again = client.post(
                f"/queue/{session.visit_id}/routing/confirm",
                json={"reason": "Checked with the patient at the desk"},
                headers=hospital.staff("nurse"),
            )
            assert again.status_code == 409
            assert again.json()["code"] == "ROUTE_ALREADY_CONFIRMED"

            sheet = client.get(
                f"/visits/{session.visit_id}/case-sheet", headers=hospital.staff("nurse")
            )
            assert sheet.status_code == 200, sheet.text
            assert sheet.json()["case_sheet"]["routing"]["needs_nurse_confirmation"] is False

        stored = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT data FROM case_sheets WHERE hospital_id = :h AND visit_id = :v",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        # The column must still hold only ciphertext, never plaintext clinical data.
        assert set(stored[0]["data"]) == {"ciphertext"}


async def test_confirmation_retry_returns_the_same_token_after_lost_session_state() -> None:
    """D9: a committed confirmation whose session update was lost must not strand the patient."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session)
            first = session.confirm()
            assert first.status_code == 200, first.text

            # Simulate the database commit succeeding while the final session write failed.
            redis = from_url(hospital.settings.redis_state_url, decode_responses=True)
            try:
                key = f"osa:intake:{hospital.hospital_id}:{session.visit_id}"
                state = json.loads(await redis.get(key))
                state["phase"] = "summary"
                state.pop("queue", None)
                await redis.set(key, json.dumps(state), ex=600)
            finally:
                await redis.aclose()

            retry = session.confirm()
            assert retry.status_code == 200, retry.text
            assert retry.json()["token"] == first.json()["token"]
            assert retry.json()["status_token"] == first.json()["status_token"]

        visits = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT count(*) AS n FROM visits WHERE hospital_id = :h AND token_number IS NOT NULL",
            h=hospital.hospital_id,
        )
        assert visits[0]["n"] == 1


async def test_simultaneous_confirmations_issue_one_token() -> None:
    """D9: a double tap on a slow network sends two confirmations; both see one token."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: session.confirm(), range(2)))
            assert [response.status_code for response in responses] == [200, 200], [
                response.text for response in responses
            ]
            assert len({response.json()["token"] for response in responses}) == 1

        tokens = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT token_label FROM visits WHERE hospital_id = :h AND token_number IS NOT NULL",
            h=hospital.hospital_id,
        )
        assert len(tokens) == 1
