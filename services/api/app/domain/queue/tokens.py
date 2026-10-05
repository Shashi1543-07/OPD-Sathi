"""Stable token formatting and opaque patient-status credentials."""

import base64
import hashlib
import hmac
import re
from datetime import date
from uuid import UUID


def daily_counter_key(
    app_env: str, hospital_id: UUID, department_id: UUID, service_date: date
) -> str:
    """Return the Redis key for one hospital, department, and IST service date."""
    day = service_date.strftime("%Y%m%d")
    return f"osa:{app_env}:{hospital_id}:tok:{department_id}:{day}"


def token_label(prefix: str, number: int) -> str:
    """Format a department token with at least three digits."""
    if not re.fullmatch(r"[A-Z0-9]{1,8}", prefix) or number < 1:
        raise ValueError("token prefix and positive token number are required")
    return f"{prefix}-{number:03d}"


def create_status_token(*, key: bytes, hospital_id: UUID, visit_id: UUID) -> str:
    """Derive a stable 256-bit status bearer token without storing its raw value."""
    if len(key) < 32:
        raise ValueError("status token key must contain at least 32 bytes")
    message = f"osa:status:v1:{hospital_id}:{visit_id}".encode("ascii")
    mac = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def status_token_digest(token: str) -> str:
    """Return the database lookup digest for a status token."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()
