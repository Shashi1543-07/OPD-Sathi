"""Adversarial checks for the access-token and password primitives."""

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from app.config import Settings
from app.core.security import (
    AuthenticationTokenError,
    create_opaque_secret,
    decode_access_token,
    digest_secret,
    hash_password,
    issue_access_token,
    verify_password,
)


def _token(settings: Settings, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid4()),
        "role": "nurse",
        "hospital_id": str(uuid4()),
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=10),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "token_type": "access",
    }
    claims.update(overrides)
    return jwt.encode(
        claims,
        settings.jwt_secret,
        algorithm="HS256",
        headers={"kid": settings.jwt_kid},
    )


def test_password_hash_is_argon2id_and_verifies() -> None:
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("not the password", encoded)
    assert not verify_password("anything", "not-a-valid-hash")


def test_password_length_is_bounded() -> None:
    with pytest.raises(ValueError):
        hash_password("short")
    with pytest.raises(ValueError):
        hash_password("x" * 1025)


def test_access_token_has_expected_claims_and_key_id() -> None:
    settings = Settings()
    subject = uuid4()
    hospital = uuid4()
    token = issue_access_token(
        subject=subject, role="nurse", hospital_id=hospital, settings=settings
    )

    assert jwt.get_unverified_header(token)["kid"] == settings.jwt_kid
    claims = decode_access_token(token, settings=settings)
    assert claims["sub"] == str(subject)
    assert claims["hospital_id"] == str(hospital)
    assert claims["role"] == "nurse"
    assert claims["token_type"] == "access"


def test_expired_access_token_is_rejected() -> None:
    settings = Settings()
    token = issue_access_token(
        subject=uuid4(),
        role="admin",
        hospital_id=uuid4(),
        settings=settings,
        now=datetime.now(UTC) - timedelta(minutes=11),
    )

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token, settings=settings)


def test_token_signed_by_wrong_key_is_rejected() -> None:
    token = _token(Settings())

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(
            token, settings=Settings(jwt_secret="a-different-secret-with-32-bytes-minimum")
        )


def test_alg_none_is_rejected() -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": str(uuid4()), "role": "admin"}).encode()
    ).rstrip(b"=")
    token = b"eyJhbGciOiJub25lIiwia2lkIjoiazEifQ." + payload + b"."

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token.decode(), settings=Settings())


def test_wrong_audience_is_rejected() -> None:
    token = _token(Settings(), aud="other-service")

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token, settings=Settings())


def test_tampered_claim_is_rejected() -> None:
    parts = _token(Settings()).split(".")
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
    payload["role"] = "admin"
    parts[1] = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    token = ".".join(parts)

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token, settings=Settings())


def test_wrong_role_is_rejected_by_role_policy() -> None:
    token = _token(Settings(), role="admin")

    with pytest.raises(AuthenticationTokenError):
        decode_access_token(token, settings=Settings(), allowed_roles={"doctor", "nurse"})


def test_unknown_role_is_rejected():
    settings = Settings()
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(_token(settings, role="superuser"), settings=settings)


def test_opaque_secrets_are_high_entropy_and_only_stored_as_digests():
    secret = create_opaque_secret()
    assert len(secret) >= 40
    assert digest_secret(secret) != secret
    assert len(digest_secret(secret)) == 64


def test_unknown_key_id_and_wrong_token_purpose_are_rejected() -> None:
    settings = Settings()
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(_token(settings, token_type="refresh"), settings=settings)
    with pytest.raises(AuthenticationTokenError):
        decode_access_token(_token(settings), settings=Settings(jwt_kid="rotated-key"))


def test_production_settings_reject_short_jwt_secret() -> None:
    with pytest.raises(ValueError):
        Settings(app_env="production", jwt_secret="short")


def test_settings_reject_access_lifetime_outside_security_policy() -> None:
    with pytest.raises(ValueError):
        Settings(access_token_minutes=20)


def test_settings_reject_unsafe_environment_and_token_lifetimes() -> None:
    with pytest.raises(ValueError):
        Settings(app_env="staging")
    with pytest.raises(ValueError):
        Settings(refresh_token_hours=0)
