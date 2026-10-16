"""Phone check-in codes: the secret in the QR that lets a patient start on their own phone.

A code is derived, not stored: HMAC-SHA256 over the hospital, the check-in point and a
version number, with the application HMAC key. An administrator can therefore show the same
QR again at any time, and replace it (old posters stop working) by moving to the next
version. Only a SHA-256 digest is stored, for lookup before the hospital is known.
"""

import base64
import hashlib
import hmac
import re
from uuid import UUID

CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def checkin_code(*, key: bytes, hospital_id: UUID, point_id: UUID, version: int) -> str:
    """The 256-bit URL-safe check-in code for one version of a check-in point."""
    if len(key) < 32:
        raise ValueError("check-in code key must contain at least 32 bytes")
    if version < 1:
        raise ValueError("check-in code version must be positive")
    message = f"osa:checkin:v1:{hospital_id}:{point_id}:{version}".encode("ascii")
    mac = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def checkin_code_digest(code: str) -> str:
    """The database lookup digest of a check-in code."""
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def is_checkin_code(value: str) -> bool:
    return CODE_PATTERN.fullmatch(value) is not None
