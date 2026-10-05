"""Authenticated keyset cursors for stable, tenant-scoped lists."""

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.errors import ApiProblemError

T = TypeVar("T")
_CURSOR_MAC_BYTES = 16


class Page[T](BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[T]
    next_cursor: str | None
    limit: int


@dataclass(frozen=True)
class CursorPosition:
    created_at: datetime
    id: UUID


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def encode_cursor(position: CursorPosition, *, key: bytes) -> str:
    payload = json.dumps(
        {"created_at": position.created_at.astimezone(UTC).isoformat(), "id": str(position.id)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).digest()[:_CURSOR_MAC_BYTES]
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def decode_cursor(value: str, *, key: bytes) -> CursorPosition:
    try:
        payload_segment, signature_segment = value.split(".", maxsplit=1)
        payload = _b64decode(payload_segment)
        signature = _b64decode(signature_segment)
        expected = hmac.new(key, payload, hashlib.sha256).digest()[:_CURSOR_MAC_BYTES]
        if not hmac.compare_digest(signature, expected):
            raise ValueError("bad cursor signature")
        decoded = json.loads(payload)
        if set(decoded) != {"created_at", "id"}:
            raise ValueError("bad cursor fields")
        created_at = datetime.fromisoformat(decoded["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("cursor timestamp requires timezone")
        return CursorPosition(created_at=created_at, id=UUID(decoded["id"]))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, binascii.Error) as exc:
        raise ApiProblemError(
            status=400,
            code="VALIDATION_FAILED",
            title="Invalid cursor",
            detail="The pagination cursor is invalid or has expired.",
        ) from exc
