"""Authenticated encryption and keyed lookup digests for patient fields.

Ciphertext names the key that sealed it, so the data key can be rotated (Phase 7, task 7.3.6):

- ``v1:<base64>`` and binary ``0x01 ...`` are sealed with key id ``"1"``, the installation's
  first key. Every record written before rotation existed is in this form, and new records
  stay in it while key ``"1"`` is current, so the previous release can still read them.
- ``v2:<id>:<base64>`` and binary ``0x02 <id length> <id> ...`` are sealed with key ``<id>``.

A ``KeyRing`` writes with its current key and reads with any key it holds. A bare 32-byte key
behaves like a ring holding only key ``"1"``.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12
_TAG_SIZE = 16
_VERSION = "v1:"
_VERSION_2 = "v2:"
_BYTES_VERSION = b"\x01"
_BYTES_VERSION_2 = b"\x02"
FIRST_KEY_ID = "1"


@dataclass(frozen=True)
class KeyRing:
    """The current data key (for writing) and every key that can still read old records."""

    current_id: str
    keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if self.current_id not in self.keys:
            raise ValueError("the current key must be in the key ring")
        if any(len(key) != 32 for key in self.keys.values()):
            raise ValueError("AES-256-GCM requires 32-byte keys")
        if any(not key_id or ":" in key_id or len(key_id) > 16 for key_id in self.keys):
            raise ValueError("key ids must have 1-16 characters and no colon")

    def key(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise ValueError("encrypted with a key that is no longer in the key ring") from exc


Key = bytes | KeyRing


def _writer(key: Key) -> tuple[str, bytes]:
    if isinstance(key, KeyRing):
        return key.current_id, key.key(key.current_id)
    return FIRST_KEY_ID, key


def _reader(key: Key, key_id: str) -> bytes:
    if isinstance(key, KeyRing):
        return key.key(key_id)
    if key_id != FIRST_KEY_ID:
        raise ValueError("encrypted with a rotated key; a key ring is needed to read it")
    return key


def key_id_of(value: str | bytes) -> str:
    """The id of the key that sealed a field or a binary record."""
    if isinstance(value, str):
        if value.startswith(_VERSION):
            return FIRST_KEY_ID
        if value.startswith(_VERSION_2):
            key_id, separator, _ = value[len(_VERSION_2) :].partition(":")
            if separator and key_id:
                return key_id
        raise ValueError("unsupported encrypted field version")
    if value.startswith(_BYTES_VERSION):
        return FIRST_KEY_ID
    if value.startswith(_BYTES_VERSION_2) and len(value) > 2 and len(value) >= 2 + value[1]:
        return value[2 : 2 + value[1]].decode("ascii", errors="replace")
    raise ValueError("unsupported or truncated encrypted content")


def load_key(encoded: str, *, setting_name: str) -> bytes:
    """Decode a configured, base64-encoded 256-bit key without leaking it."""
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{setting_name} must be valid base64 encoding of 32 bytes") from exc
    if len(key) != 32:
        raise ValueError(f"{setting_name} must decode to exactly 32 bytes")
    return key


def encrypt_field(value: str, *, key: Key, associated_data: str) -> str:
    """Encrypt one field with AES-256-GCM and bind it to its row and column."""
    key_id, key_bytes = _writer(key)
    if len(key_bytes) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = AESGCM(key_bytes).encrypt(
        nonce, value.encode("utf-8"), associated_data.encode("utf-8")
    )
    payload = base64.b64encode(nonce + ciphertext).decode("ascii")
    if key_id == FIRST_KEY_ID:
        return f"{_VERSION}{payload}"
    return f"{_VERSION_2}{key_id}:{payload}"


def decrypt_field(value: str, *, key: Key, associated_data: str) -> str:
    """Decrypt a versioned field, rejecting tampering and context substitution."""
    key_id = key_id_of(value)
    key_bytes = _reader(key, key_id)
    if len(key_bytes) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    if value.startswith(_VERSION):
        body = value[len(_VERSION) :]
    else:
        body = value[len(_VERSION_2) + len(key_id) + 1 :]
    try:
        payload = base64.b64decode(body, validate=True)
        if len(payload) < _NONCE_SIZE + _TAG_SIZE:
            raise ValueError("encrypted field payload is truncated")
        plaintext = AESGCM(key_bytes).decrypt(
            payload[:_NONCE_SIZE], payload[_NONCE_SIZE:], associated_data.encode("utf-8")
        )
        return plaintext.decode("utf-8")
    except (binascii.Error, InvalidTag, UnicodeDecodeError) as exc:
        raise ValueError("encrypted field failed authentication") from exc


def encrypt_bytes(data: bytes, *, key: Key, associated_data: str) -> bytes:
    """Encrypt binary content (a document photo) with AES-256-GCM, bound to its record."""
    key_id, key_bytes = _writer(key)
    if len(key_bytes) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    sealed = AESGCM(key_bytes).encrypt(nonce, data, associated_data.encode("utf-8"))
    if key_id == FIRST_KEY_ID:
        return _BYTES_VERSION + nonce + sealed
    encoded_id = key_id.encode("ascii")
    return _BYTES_VERSION_2 + bytes([len(encoded_id)]) + encoded_id + nonce + sealed


def decrypt_bytes(value: bytes, *, key: Key, associated_data: str) -> bytes:
    """Decrypt binary content, rejecting tampering and content moved to another record."""
    key_id = key_id_of(value)
    key_bytes = _reader(key, key_id)
    if len(key_bytes) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    header = 1 if value.startswith(_BYTES_VERSION) else 2 + value[1]
    if len(value) < header + _NONCE_SIZE + _TAG_SIZE:
        raise ValueError("unsupported or truncated encrypted content")
    nonce = value[header : header + _NONCE_SIZE]
    try:
        return AESGCM(key_bytes).decrypt(
            nonce, value[header + _NONCE_SIZE :], associated_data.encode("utf-8")
        )
    except InvalidTag as exc:
        raise ValueError("encrypted content failed authentication") from exc


def lookup_digest(value: str, *, key: bytes) -> str:
    """Return a deterministic HMAC-SHA256 lookup digest."""
    if len(key) != 32:
        raise ValueError("HMAC lookup key must be 32 bytes")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
