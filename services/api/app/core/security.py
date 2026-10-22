"""Password hashing and strict, short-lived JWT handling."""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from secrets import token_bytes
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from jwt import InvalidTokenError

from app.config import Settings

TokenRole = Literal["admin", "doctor", "nurse", "kiosk", "patient"]
ALGORITHM = "HS256"


class TokenPurpose(StrEnum):
    """JWT use labels; these are protocol values, not credentials."""

    ACCESS = "access"
    # Proves the password was right; exchanged for a session only with a one-time code.
    MFA_CHALLENGE = "mfa_challenge"


class Restriction(StrEnum):
    """What a staff member must do before their session can do anything else."""

    PASSWORD_CHANGE = "password_change"
    MFA_SETUP = "mfa_setup"


class TokenScheme(StrEnum):
    """OAuth token response scheme value."""

    BEARER = "bearer"


TOKEN_TYPE = TokenPurpose.ACCESS.value
_password_hasher = PasswordHasher(type=Type.ID)


class AuthenticationTokenError(ValueError):
    """Raised when a token is malformed, expired, or has unsafe claims."""


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("password must contain between 12 and 1024 characters")
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify an Argon2id password hash without leaking parser details."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def issue_access_token(
    *,
    subject: UUID,
    role: TokenRole,
    hospital_id: UUID,
    settings: Settings,
    now: datetime | None = None,
    token_id: UUID | None = None,
    lifetime: timedelta | None = None,
    restrictions: tuple[Restriction, ...] = (),
    amr: tuple[str, ...] = (),
    purpose: TokenPurpose = TokenPurpose.ACCESS,
) -> str:
    """Issue a signed token using the current key and fixed claims.

    ``restrictions`` limit a staff session to finishing a password change or MFA enrolment.
    ``amr`` records how the person signed in (``pwd``, ``otp``), as in RFC 8176.
    """
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + (lifetime or timedelta(minutes=settings.access_token_minutes))
    claims: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "hospital_id": str(hospital_id),
        "jti": str(token_id or uuid4()),
        "iat": issued_at,
        "exp": expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "token_type": purpose.value,
        # Issue time in milliseconds: "sign out everywhere" compares at this precision, so
        # a token from the same second as the sign-out is still ended (core.auth).
        "iatm": int(issued_at.timestamp() * 1000),
    }
    if restrictions:
        claims["rst"] = [restriction.value for restriction in restrictions]
    if amr:
        claims["amr"] = list(amr)
    return jwt.encode(
        claims,
        settings.jwt_secret,
        algorithm=ALGORITHM,
        headers={"kid": settings.jwt_kid, "typ": "JWT"},
    )


def create_opaque_secret() -> str:
    """Create a 256-bit URL-safe secret for refresh and activation credentials."""
    return urlsafe_b64encode(token_bytes(32)).rstrip(b"=").decode("ascii")


def digest_secret(secret: str) -> str:
    """Return the fixed-length database lookup digest for an opaque secret."""
    return sha256(secret.encode("utf-8")).hexdigest()


def _verification_secret(kid: object, settings: Settings) -> str:
    """The key for this key id: the current one, or the previous one during a rotation."""
    if kid == settings.jwt_kid:
        return settings.jwt_secret
    if settings.jwt_previous_secret and kid == settings.jwt_previous_kid:
        return settings.jwt_previous_secret
    raise AuthenticationTokenError("unsupported token header")


def decode_access_token(
    token: str,
    *,
    settings: Settings,
    allowed_roles: set[str] | None = None,
    allow_restricted: bool = False,
    purpose: TokenPurpose = TokenPurpose.ACCESS,
) -> dict[str, Any]:
    """Verify signature, key id, issuer, audience, expiry, and required claims.

    A restricted staff token (password change or MFA enrolment pending) is refused unless
    ``allow_restricted``: only the few routes that finish those steps accept it.
    """
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != ALGORITHM:
            raise AuthenticationTokenError("unsupported token header")
        claims = jwt.decode(
            token,
            _verification_secret(header.get("kid"), settings),
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=30,
            options={
                "require": [
                    "sub",
                    "role",
                    "hospital_id",
                    "jti",
                    "iat",
                    "exp",
                    "iss",
                    "aud",
                    "token_type",
                ]
            },
        )
        UUID(claims["sub"])
        UUID(claims["hospital_id"])
        UUID(claims["jti"])
        if claims["token_type"] != purpose.value:
            raise AuthenticationTokenError("wrong token purpose")
        if claims.get("rst") and not allow_restricted:
            raise AuthenticationTokenError("restricted token")
        if claims["role"] not in {"admin", "doctor", "nurse", "kiosk", "patient"}:
            raise AuthenticationTokenError("unknown token role")
        if allowed_roles is not None and claims["role"] not in allowed_roles:
            raise AuthenticationTokenError("role is not authorized")
        return claims
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AuthenticationTokenError):
            raise
        raise AuthenticationTokenError("invalid access token") from exc
