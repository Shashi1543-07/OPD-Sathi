"""The OPD card a returning patient brings to their next visit: a number and a PIN.

The number (``26-000123``: year and a per-hospital sequence) is the hospital registration
number. The six-digit PIN proves the patient holds the card. It is derived, never stored:
HMAC-SHA256 over the hospital, the patient and a card version, with the application HMAC key,
so staff can show it again and a lost card is replaced by moving to the next version.

A card links a new visit to an earlier record only when the details given today are
consistent with that record (age within a year, same sex). Matching never relies on names,
phone numbers or approximate demographics alone: those only help staff look a patient up.
"""

import hashlib
import hmac
import re
from uuid import UUID

_MRN_INPUT = re.compile(r"(\d{2})\s*[-/ ]?\s*(\d{6,9})")


def format_mrn(year: int, value: int) -> str:
    """The registration number for the ``value``-th new patient of ``year``."""
    if value < 1:
        raise ValueError("registration sequence values start at 1")
    return f"{year % 100:02d}-{value:06d}"


def normalize_mrn(raw: str) -> str | None:
    """Accept what patients type ("26-000123", "26 000123", "26000123"); None if not a number."""
    match = _MRN_INPUT.fullmatch(raw.strip())
    if match is None:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def card_pin(*, key: bytes, hospital_id: UUID, patient_id: UUID, version: int) -> str:
    """The six-digit PIN printed on the patient's current OPD card."""
    if len(key) < 32:
        raise ValueError("card PIN key must contain at least 32 bytes")
    if version < 1:
        raise ValueError("card version must be positive")
    message = f"osa:card-pin:v1:{hospital_id}:{patient_id}:{version}".encode("ascii")
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:8], 'big') % 1_000_000:06d}"


def pin_matches(entered: str, expected: str) -> bool:
    """Constant-time comparison of a typed PIN (spaces ignored) with the card's PIN."""
    digits = "".join(character for character in entered if character.isdigit())
    return hmac.compare_digest(digits.encode("ascii"), expected.encode("ascii"))


def demographics_consistent(
    *,
    stored_birth_year: int | None,
    stored_sex: str | None,
    age: int | None,
    sex: str | None,
    current_year: int,
) -> bool:
    """Whether today's age and sex could belong to the person on the earlier record.

    Unknown values never block a match (the record or today's answer may lack them); a
    birth year more than a year apart, or a different recorded sex, does.
    """
    if stored_birth_year is not None and age is not None:
        if abs((current_year - age) - stored_birth_year) > 1:
            return False
    known = {"female", "male"}
    if stored_sex in known and sex in known and stored_sex != sex:
        return False
    return True
