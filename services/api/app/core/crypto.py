"""Authenticated encryption and keyed lookup digests for patient fields."""

import base64
import binascii
import hashlib
import hmac
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12
_TAG_SIZE = 16
_VERSION = "v1:"


def load_key(encoded: str, *, setting_name: str) -> bytes:
    """Decode a configured, base64-encoded 256-bit key without leaking it."""
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{setting_name} must be valid base64 encoding of 32 bytes") from exc
    if len(key) != 32:
        raise ValueError(f"{setting_name} must decode to exactly 32 bytes")
    return key


def encrypt_field(value: str, *, key: bytes, associated_data: str) -> str:
    """Encrypt one field with AES-256-GCM and bind it to its row and column."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), associated_data.encode("utf-8"))
    payload = base64.b64encode(nonce + ciphertext).decode("ascii")
    return f"{_VERSION}{payload}"


def decrypt_field(value: str, *, key: bytes, associated_data: str) -> str:
    """Decrypt a versioned field, rejecting tampering and context substitution."""
    if len(key) != 32:
        raise ValueError("AES-256-GCM requires a 32-byte key")
    if not value.startswith(_VERSION):
        raise ValueError("unsupported encrypted field version")
    try:
        payload = base64.b64decode(value[len(_VERSION) :], validate=True)
        if len(payload) < _NONCE_SIZE + _TAG_SIZE:
            raise ValueError("encrypted field payload is truncated")
        plaintext = AESGCM(key).decrypt(
            payload[:_NONCE_SIZE], payload[_NONCE_SIZE:], associated_data.encode("utf-8")
        )
        return plaintext.decode("utf-8")
    except (binascii.Error, InvalidTag, UnicodeDecodeError) as exc:
        raise ValueError("encrypted field failed authentication") from exc


def lookup_digest(value: str, *, key: bytes) -> str:
    """Return a deterministic HMAC-SHA256 lookup digest."""
    if len(key) != 32:
        raise ValueError("HMAC lookup key must be 32 bytes")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
