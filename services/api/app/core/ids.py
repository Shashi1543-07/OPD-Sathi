"""UUIDv7 identifiers for time-ordered application records."""

import hashlib
import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    """Return a UUIDv7 using the current Unix millisecond and random payload."""
    timestamp_ms = time.time_ns() // 1_000_000
    if timestamp_ms >= 1 << 48:
        raise ValueError("current time is outside the UUIDv7 timestamp range")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return UUID(int=value)


def stable_uuid7(name: str) -> UUID:
    """Derive a repeatable, non-secret UUIDv7 for deterministic reference seeds."""
    timestamp_ms = 1_767_225_600_000  # 2026-01-01 UTC
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    random_bits = int.from_bytes(digest[:10], "big") & ((1 << 74) - 1)
    random_a = (random_bits >> 62) & ((1 << 12) - 1)
    random_b = random_bits & ((1 << 62) - 1)
    value = (timestamp_ms << 80) | (0x7 << 76) | (random_a << 64) | (0b10 << 62) | random_b
    return UUID(int=value)
