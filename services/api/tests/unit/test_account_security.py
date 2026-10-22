"""Building blocks of Phase 7 account security: key rotation, TOTP, passwords, secret files."""

import base64
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pyotp
import pytest

from app.config import Settings
from app.core.crypto import (
    KeyRing,
    decrypt_bytes,
    decrypt_field,
    encrypt_bytes,
    encrypt_field,
    key_id_of,
)
from app.core.mfa import (
    STEP_SECONDS,
    hash_recovery_code,
    match_recovery_code,
    new_recovery_codes,
    new_totp_secret,
    provisioning_uri,
    verify_totp,
)
from app.core.passwords import password_problem
from app.core.security import (
    AuthenticationTokenError,
    Restriction,
    TokenPurpose,
    decode_access_token,
    issue_access_token,
)

OLD, NEW = b"o" * 32, b"n" * 32


def _b64(key: bytes) -> str:
    return base64.b64encode(key).decode()


# ---- Data-key rotation (7.3.6) -------------------------------------------------------------


def test_first_key_keeps_the_v1_format_so_the_previous_release_can_read_it() -> None:
    ring = KeyRing(current_id="1", keys={"1": OLD})
    sealed = encrypt_field("Synthetic name", key=ring, associated_data="patients:name_enc:x")
    assert sealed.startswith("v1:")
    # A bare key (what the previous release passes) reads it.
    assert decrypt_field(sealed, key=OLD, associated_data="patients:name_enc:x") == "Synthetic name"


def test_after_rotation_new_records_name_their_key_and_old_ones_still_read() -> None:
    before = encrypt_field("before", key=OLD, associated_data="a")
    ring = KeyRing(current_id="2", keys={"1": OLD, "2": NEW})
    after = encrypt_field("after", key=ring, associated_data="a")
    assert after.startswith("v2:2:") and key_id_of(after) == "2" and key_id_of(before) == "1"
    assert decrypt_field(before, key=ring, associated_data="a") == "before"
    assert decrypt_field(after, key=ring, associated_data="a") == "after"
    with pytest.raises(ValueError, match="key ring"):
        decrypt_field(after, key=OLD, associated_data="a")
    # Once key 1 is retired, anything still sealed with it is reported, not misread.
    retired = KeyRing(current_id="2", keys={"2": NEW})
    with pytest.raises(ValueError, match="no longer in the key ring"):
        decrypt_field(before, key=retired, associated_data="a")


def test_binary_content_rotates_the_same_way() -> None:
    ring = KeyRing(current_id="k2", keys={"1": OLD, "k2": NEW})
    old_photo = encrypt_bytes(b"\xff\xd8 photo", key=OLD, associated_data="doc:1")
    new_photo = encrypt_bytes(b"\xff\xd8 photo", key=ring, associated_data="doc:1")
    assert key_id_of(old_photo) == "1" and key_id_of(new_photo) == "k2"
    for sealed in (old_photo, new_photo):
        assert decrypt_bytes(sealed, key=ring, associated_data="doc:1") == b"\xff\xd8 photo"
    with pytest.raises(ValueError):
        decrypt_bytes(new_photo, key=ring, associated_data="doc:2")


def test_key_ring_and_settings_reject_bad_rings() -> None:
    with pytest.raises(ValueError):
        KeyRing(current_id="2", keys={"1": OLD})
    with pytest.raises(ValueError):
        KeyRing(current_id="1", keys={"1": b"short"})
    settings = Settings(
        app_env="test",
        app_data_key=_b64(NEW),
        app_data_key_id="2",
        app_data_previous_keys=f"1:{_b64(OLD)}",
    )
    assert settings.app_data_keyring.current_id == "2"
    assert set(settings.app_data_keyring.keys) == {"1", "2"}
    with pytest.raises(ValueError, match="other ids"):
        Settings(app_env="test", app_data_key_id="1", app_data_previous_keys=f"1:{_b64(OLD)}")
    with pytest.raises(ValueError, match="APP_DATA_KEY_ID"):
        Settings(app_env="test", app_data_key_id="a:b")


# ---- Secrets from files (7.3.7) -------------------------------------------------------------


def test_settings_read_secrets_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "jwt_secret"
    secret.write_text("file-secret-" + "x" * 40 + "\n", encoding="utf-8")
    monkeypatch.setenv("JWT_SECRET", "from-the-environment")
    monkeypatch.setenv("JWT_SECRET_FILE", str(secret))
    assert Settings().jwt_secret == "file-secret-" + "x" * 40
    monkeypatch.setenv("JWT_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="JWT_SECRET_FILE"):
        Settings()


# ---- Tokens: restrictions, MFA challenges, signing-key rotation -----------------------------


def test_restricted_and_challenge_tokens_open_nothing_by_default() -> None:
    settings = Settings(app_env="test")
    claims = {"subject": uuid4(), "role": "admin", "hospital_id": uuid4(), "settings": settings}
    restricted = issue_access_token(**claims, restrictions=(Restriction.MFA_SETUP,))  # type: ignore[arg-type]
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(restricted, settings=settings)
    assert decode_access_token(restricted, settings=settings, allow_restricted=True)["rst"] == [
        "mfa_setup"
    ]
    challenge = issue_access_token(  # type: ignore[arg-type]
        **claims, lifetime=timedelta(minutes=5), purpose=TokenPurpose.MFA_CHALLENGE
    )
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(challenge, settings=settings)
    assert decode_access_token(challenge, settings=settings, purpose=TokenPurpose.MFA_CHALLENGE)


def test_tokens_signed_with_the_previous_key_work_during_a_rotation() -> None:
    old = Settings(app_env="test", jwt_secret="o" * 40, jwt_kid="k1")
    token = issue_access_token(subject=uuid4(), role="doctor", hospital_id=uuid4(), settings=old)
    rotated = Settings(
        app_env="test",
        jwt_secret="n" * 40,
        jwt_kid="k2",
        jwt_previous_secret="o" * 40,
        jwt_previous_kid="k1",
    )
    assert decode_access_token(token, settings=rotated)["role"] == "doctor"
    finished = Settings(app_env="test", jwt_secret="n" * 40, jwt_kid="k2")
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token, settings=finished)
    with pytest.raises(ValueError, match="JWT_PREVIOUS_KID"):
        Settings(app_env="test", jwt_previous_secret="o" * 40, jwt_previous_kid="k1", jwt_kid="k1")


def test_production_requires_two_step_sign_in_for_administrators() -> None:
    with pytest.raises(ValueError, match="two-step"):
        Settings(
            app_env="production",
            mfa_required_roles="doctor",
            jwt_secret="p" * 40,
            cursor_hmac_key="c" * 40,
            app_data_key=_b64(OLD),
            app_hmac_key=_b64(NEW),
        )
    with pytest.raises(ValueError, match="MFA_REQUIRED_ROLES"):
        Settings(app_env="test", mfa_required_roles="admin,kiosk")


# ---- TOTP and recovery codes (7.3.2) --------------------------------------------------------


def test_a_code_is_accepted_once_within_one_step_of_drift() -> None:
    secret = new_totp_secret()
    now = 1_800_000_000.0
    code = pyotp.TOTP(secret).at(now)
    step = verify_totp(secret, code, last_step=None, now=now)
    assert step == int(now // STEP_SECONDS)
    # The same code (or an older one) cannot be replayed.
    assert verify_totp(secret, code, last_step=step, now=now) is None
    # A phone clock 30 s fast or slow still works; two steps away does not.
    assert verify_totp(secret, code, last_step=None, now=now + STEP_SECONDS) is not None
    assert verify_totp(secret, code, last_step=None, now=now + 3 * STEP_SECONDS) is None
    assert verify_totp(secret, "12 34", last_step=None, now=now) is None
    uri = provisioning_uri(secret, account="admin@example.test", hospital="Synthetic")
    assert uri.startswith("otpauth://totp/") and secret in uri


def test_recovery_codes_are_single_use_hashes_matched_loosely() -> None:
    codes = new_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    hashes = [hash_recovery_code(code) for code in codes]
    assert all(codes[3] not in stored for stored in hashes)
    typed = codes[3].upper().replace("-", " ")
    assert match_recovery_code(typed, hashes) == 3
    hashes[3] = ""
    assert match_recovery_code(codes[3], hashes) is None
    assert match_recovery_code("short", hashes) is None


# ---- Password rules (7.3.5) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [
        "Password@1234",
        "password123456",
        "Nagpur@123456789",
        "Hospital@2026",
        "qwertyuiopasdf",
        "aaaaaaaaaaaaaaa",
        "123456789012",
        "OpdSaathi@2026",
        "Short1!",
    ],
)
def test_common_and_patterned_passwords_are_refused(password: str) -> None:
    assert password_problem(password) is not None


def test_passwords_built_from_the_person_are_refused_and_passphrases_pass() -> None:
    personal = ("Suresh Kumar", "suresh.kumar@gmc.example", "GMC Nagpur")
    assert password_problem("Suresh@123456", personal=personal) is not None
    assert password_problem("correct horse battery staple", personal=personal) is None
    assert password_problem("Synthetic-test-password-2026") is None


def test_the_production_worker_starts_without_patient_keys_and_refuses_them() -> None:
    production = {
        "app_env": "production",
        "jwt_secret": "p" * 40,
        "cursor_hmac_key": "c" * 40,
    }
    worker = Settings(**production, process_role="worker", app_data_key="", app_hmac_key="")
    assert worker.process_role == "worker"
    with pytest.raises(ValueError, match="worker must not be given"):
        Settings(**production, process_role="worker", app_data_key=_b64(OLD), app_hmac_key="")
    # The API, on the other hand, cannot start without them.
    with pytest.raises(ValueError, match="APP_DATA_KEY"):
        Settings(**production, process_role="api", app_data_key="", app_hmac_key="")
