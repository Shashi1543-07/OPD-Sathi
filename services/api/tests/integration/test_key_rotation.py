"""Data-key rotation end to end (Phase 7, task 7.3.6), synthetic data only.

A visit is recorded with key 1. The hospital rotates to key 2 (key 1 kept in the ring): the
same records still read. The re-encryption job re-seals everything with key 2. Key 1 is
retired: every record still reads, and nothing is left sealed with the old key.
"""

import base64

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.crypto import key_id_of
from app.db.repositories import CaseSheetRepository
from app.db.session import set_hospital_scope
from app.rekey import reencrypt_hospital
from tests.integration.synthetic import (
    activate_kiosk,
    api_client,
    complete_intake,
    fetch,
    start_kiosk_session,
    synthetic_hospital,
)

NEW_KEY = base64.b64encode(b"2" * 32).decode()


async def _case_sheet(settings: object, hospital_id: object, visit_id: str) -> object:
    engine = create_async_engine(settings.database_url)  # type: ignore[attr-defined]
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)  # type: ignore[arg-type]
            saved = await CaseSheetRepository(connection, settings).get(  # type: ignore[arg-type]
                hospital_id=hospital_id,  # type: ignore[arg-type]
                visit_id=visit_id,  # type: ignore[arg-type]
            )
            return saved["data"] if saved else None
    finally:
        await engine.dispose()


async def test_rotate_reencrypt_and_retire_the_old_key() -> None:
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="cough since a week", phone="9876543210")
            assert session.confirm().status_code == 200
        original = await _case_sheet(hospital.settings, hospital.hospital_id, session.visit_id)
        assert original is not None

        old_key = hospital.settings.app_data_key
        rotated = hospital.settings.model_copy(
            update={
                "app_data_key": NEW_KEY,
                "app_data_key_id": "2",
                "app_data_previous_keys": f"1:{old_key}",
            }
        )
        # During the rotation old records still read through the ring.
        assert await _case_sheet(rotated, hospital.hospital_id, session.visit_id) == original

        engine = create_async_engine(rotated.migrations_database_url)
        try:
            async with engine.begin() as connection:
                await set_hospital_scope(connection, hospital.hospital_id)
                dry = await reencrypt_hospital(
                    connection,
                    hospital_id=hospital.hospital_id,
                    ring=rotated.app_data_keyring,
                    apply=False,
                )
            assert dry["case_sheets.data"].resealed == 1
            assert dry["patients.phone_enc"].resealed == 1
            async with engine.begin() as connection:
                await set_hospital_scope(connection, hospital.hospital_id)
                applied = await reencrypt_hospital(
                    connection,
                    hospital_id=hospital.hospital_id,
                    ring=rotated.app_data_keyring,
                    apply=True,
                    batch=2,
                )
            assert all(report.unreadable == 0 for report in applied.values())
            assert applied["interview_turns.text_original"].resealed >= 1
        finally:
            await engine.dispose()

        # Key 1 retired: everything reads with key 2 alone.
        retired = rotated.model_copy(update={"app_data_previous_keys": ""})
        assert await _case_sheet(retired, hospital.hospital_id, session.visit_id) == original
        rows = await fetch(
            hospital.settings,
            hospital.hospital_id,
            "SELECT p.phone_enc, t.text_original, c.data->>'ciphertext' AS sheet "
            "FROM visits v JOIN patients p ON p.hospital_id = v.hospital_id "
            "AND p.id = v.patient_id JOIN interview_turns t ON t.hospital_id = v.hospital_id "
            "AND t.visit_id = v.id JOIN case_sheets c ON c.hospital_id = v.hospital_id "
            "AND c.visit_id = v.id WHERE v.hospital_id = :h AND v.id = :v",
            h=hospital.hospital_id,
            v=session.visit_id,
        )
        sealed = [value for row in rows for value in row.values() if value]
        assert sealed and all(key_id_of(value) == "2" for value in sealed)


async def test_verify_mode_decrypts_current_key_values_and_finds_unreadable_ones() -> None:
    """Restore drills (task 7.7.5) prove every value reads, not only those to re-seal."""
    async with synthetic_hospital() as hospital:
        with api_client(hospital.settings) as client:
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint="headache", phone="9876543211")
            assert session.confirm().status_code == 200

        async def verify(settings: object) -> dict[str, object]:
            engine = create_async_engine(settings.migrations_database_url)  # type: ignore[attr-defined]
            try:
                async with engine.begin() as connection:
                    await set_hospital_scope(connection, hospital.hospital_id)
                    return await reencrypt_hospital(  # type: ignore[return-value]
                        connection,
                        hospital_id=hospital.hospital_id,
                        ring=settings.app_data_keyring,  # type: ignore[attr-defined]
                        apply=True,  # ignored in verify mode: nothing is written
                        verify=True,
                    )
            finally:
                await engine.dispose()

        reports = await verify(hospital.settings)
        assert reports["patients.phone_enc"].verified == 1  # type: ignore[attr-defined]
        assert reports["case_sheets.data"].verified == 1  # type: ignore[attr-defined]
        assert all(r.unreadable == 0 and r.resealed == 0 for r in reports.values())  # type: ignore[attr-defined]

        # The same records with the wrong key (as after restoring with a lost key) are unreadable.
        wrong = hospital.settings.model_copy(update={"app_data_key": NEW_KEY})
        lost = await verify(wrong)
        assert lost["patients.phone_enc"].unreadable == 1  # type: ignore[attr-defined]
