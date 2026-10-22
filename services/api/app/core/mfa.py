"""Second sign-in step for staff: time-based one-time codes and recovery codes (ADR 008).

TOTP follows RFC 6238 as every authenticator app does: SHA-1, 6 digits, a 30-second step, and
one step of clock drift either way. A code is accepted at most once: the caller stores the last
accepted step and passes it back, so a code seen over someone's shoulder cannot be replayed.
Recovery codes are random, single-use, shown once and stored only as Argon2id hashes.
"""

import secrets
import time

import pyotp

from app.core.security import hash_password, verify_password

ISSUER = "OPD Saathi"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
# Unambiguous characters: no 0/O or 1/l/I, so codes read off paper are typed correctly.
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_totp_secret() -> str:
    """A fresh 160-bit base32 secret for an authenticator app."""
    return pyotp.random_base32(32)


def provisioning_uri(secret: str, *, account: str, hospital: str) -> str:
    """The otpauth:// address shown as a QR code during enrolment."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=f"{ISSUER} ({hospital})")


def verify_totp(
    secret: str, code: str, *, last_step: int | None, now: float | None = None
) -> int | None:
    """The time step of a valid, unused code, or None.

    Accepts the current step and one either side. A step at or before ``last_step`` was
    already used and is refused.
    """
    digits = "".join(character for character in code if character.isdigit())
    if len(digits) != 6:
        return None
    moment = time.time() if now is None else now
    totp = pyotp.TOTP(secret)
    current = int(moment // STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if secrets.compare_digest(totp.at(step * STEP_SECONDS), digits):
            return step
    return None


def new_recovery_codes() -> list[str]:
    """Ten single-use codes in the form ``abcd-efgh-jkmn`` (about 60 bits each)."""
    return [
        "-".join(
            "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(4)) for _ in range(3)
        )
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def normalize_recovery_code(code: str) -> str:
    compact = "".join(character for character in code.lower() if character.isalnum())
    return "-".join(compact[index : index + 4] for index in range(0, len(compact), 4))


def hash_recovery_code(code: str) -> str:
    return hash_password(normalize_recovery_code(code))


def match_recovery_code(code: str, hashes: list[str]) -> int | None:
    """The index of the stored hash this code matches, or None."""
    normalized = normalize_recovery_code(code)
    if len(normalized) != 14:
        return None
    for index, stored in enumerate(hashes):
        if stored and verify_password(normalized, stored):
            return index
    return None
