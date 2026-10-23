"""Staff authentication, refresh-cookie rotation, and kiosk activation."""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.config import Settings
from app.core.audit import audit
from app.core.auth import require_kiosk
from app.core.crypto import decrypt_field
from app.core.errors import ApiProblemError
from app.core.mfa import match_recovery_code, verify_totp
from app.core.ratelimit import check_rate_limit
from app.core.security import (
    AuthenticationTokenError,
    Restriction,
    TokenPurpose,
    TokenScheme,
    create_opaque_secret,
    decode_access_token,
    digest_secret,
    hash_password,
    issue_access_token,
    verify_password,
)
from app.db.repositories import IntakeRepository
from app.db.session import set_hospital_scope, set_secret_lookup_scope

router = APIRouter()
_DUMMY_PASSWORD_HASH = hash_password("unavailable-account-dummy-password")


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class LoginPayload(StrictPayload):
    hospital_id: UUID
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or any(character.isspace() for character in normalized):
            raise ValueError("email must be valid")
        return normalized


class KioskActivationPayload(StrictPayload):
    activation_code: str = Field(min_length=40, max_length=64)


class KioskTokenPayload(StrictPayload):
    device_secret: str = Field(min_length=40, max_length=64)


async def _hospital_name(connection: Any, hospital_id: UUID) -> str:
    """The kiosk shows its own hospital's name (each hospital's kiosks are its own)."""
    name = await connection.scalar(
        text("SELECT name FROM hospitals WHERE id = :hospital_id"),
        {"hospital_id": hospital_id},
    )
    return str(name or "")


def _kiosk_token_response(
    settings: Settings, *, kiosk_id: UUID, hospital_id: UUID
) -> dict[str, Any]:
    token = issue_access_token(
        subject=kiosk_id,
        role="kiosk",
        hospital_id=hospital_id,
        settings=settings,
        lifetime=timedelta(hours=settings.kiosk_token_hours),
    )
    return {
        "access_token": token,
        "token_type": TokenScheme.BEARER.value,
        "expires_in": settings.kiosk_token_hours * 60 * 60,
    }


def _error(
    status: int,
    code: str,
    title: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> ApiProblemError:
    return ApiProblemError(
        status=status,
        code=code,
        title=title,
        detail=detail,
        headers=headers,
    )


_LEGACY_REFRESH_COOKIE = "osa_refresh"
MFA_CHALLENGE_MINUTES = 5


def device_label(user_agent: str) -> str:
    """A short, non-identifying label such as "Chrome on Windows" for the sessions list."""
    agent = user_agent.lower()
    browser = next(
        (
            name
            for marker, name in (
                ("edg/", "Edge"),
                ("opr/", "Opera"),
                ("samsungbrowser", "Samsung Internet"),
                ("firefox", "Firefox"),
                ("chrome", "Chrome"),
                ("safari", "Safari"),
            )
            if marker in agent
        ),
        "Browser",
    )
    system = next(
        (
            name
            for marker, name in (
                ("android", "Android"),
                ("iphone", "iPhone"),
                ("ipad", "iPad"),
                ("windows", "Windows"),
                ("mac os", "Mac"),
                ("linux", "Linux"),
            )
            if marker in agent
        ),
        "an unknown device",
    )
    return f"{browser} on {system}"


def session_restrictions(settings: Settings, user: dict[str, Any]) -> tuple[Restriction, ...]:
    """What this person must do before their session can do anything else."""
    restrictions: list[Restriction] = []
    if user.get("must_change_password"):
        restrictions.append(Restriction.PASSWORD_CHANGE)
    if user["role"] in settings.mfa_required_role_set and user.get("mfa_enabled_at") is None:
        restrictions.append(Restriction.MFA_SETUP)
    return tuple(restrictions)


def session_body(settings: Settings, user: dict[str, Any]) -> dict[str, Any]:
    """The access token and profile a signed-in browser receives."""
    restrictions = session_restrictions(settings, user)
    mfa_enabled = user.get("mfa_enabled_at") is not None
    token = issue_access_token(
        subject=user["id"],
        role=user["role"],
        hospital_id=user["hospital_id"],
        settings=settings,
        restrictions=restrictions,
        amr=("pwd", "otp") if mfa_enabled else ("pwd",),
    )
    return {
        "access_token": token,
        "token_type": TokenScheme.BEARER.value,
        "expires_in": settings.access_token_minutes * 60,
        "restrictions": [restriction.value for restriction in restrictions],
        "user": {
            "id": str(user["id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "hospital_id": str(user["hospital_id"]),
            "mfa_enabled": mfa_enabled,
        },
    }


async def start_session(
    connection: Any, request: Request, response: Response, user: dict[str, Any]
) -> dict[str, Any]:
    """Open a new sign-in (refresh-token family) for this browser and return its tokens."""
    settings: Settings = request.app.state.settings
    refresh_value = create_opaque_secret()
    now = datetime.now(UTC)
    await connection.execute(
        text(
            "INSERT INTO refresh_tokens "
            "(id, hospital_id, user_id, family_id, token_hash, expires_at, device, last_used_at) "
            "VALUES (:id, :hospital_id, :user_id, :family_id, :token_hash, :expires_at, "
            ":device, :now)"
        ),
        {
            "id": uuid4(),
            "hospital_id": user["hospital_id"],
            "user_id": user["id"],
            "family_id": uuid4(),
            "token_hash": digest_secret(refresh_value),
            "expires_at": now + timedelta(hours=settings.refresh_token_hours),
            "device": device_label(request.headers.get("user-agent", "")),
            "now": now,
        },
    )
    _set_refresh_cookie(response, settings, refresh_value, user["id"])
    return session_body(settings, user)


class AccountPayload(StrictPayload):
    """Which signed-in staff account a browser tab is renewing or signing out."""

    account: UUID | None = None


def _refresh_cookie_name(user_id: UUID | str) -> str:
    """One refresh cookie per staff account, so several staff can work in one browser.

    Each tab tells the server which account it belongs to; without this, every tab shared
    one cookie and a tab silently became whoever signed in last.
    """
    return f"osa_refresh_{UUID(str(user_id)).hex}"


def _incoming_refresh_cookie(
    request: Request, payload: AccountPayload | None
) -> tuple[str | None, str | None]:
    """Return (cookie name, value) for the requested account, else the legacy cookie."""
    if payload is not None and payload.account is not None:
        name = _refresh_cookie_name(payload.account)
        value = request.cookies.get(name)
        if value:
            return name, value
    value = request.cookies.get(_LEGACY_REFRESH_COOKIE)
    return (_LEGACY_REFRESH_COOKIE, value) if value else (None, None)


def _set_refresh_cookie(
    response: Response, settings: Settings, value: str, user_id: UUID | str
) -> None:
    response.set_cookie(
        _refresh_cookie_name(user_id),
        value,
        max_age=settings.refresh_token_hours * 60 * 60,
        path="/auth",
        secure=settings.app_env != "development",
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(
        name,
        path="/auth",
        secure=settings.app_env != "development",
        httponly=True,
        samesite="strict",
    )


async def _limit_request(
    request: Request,
    response: Response,
    *,
    name: str,
    limit: int,
    window_seconds: int,
    fail_closed: bool,
) -> None:
    settings: Settings = request.app.state.settings
    subject = request.client.host if request.client else "unknown"
    key = f"osa:{settings.app_env}:rl:{name}:{subject}"
    result = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=key,
        limit=limit,
        window_seconds=window_seconds,
        fail_closed=fail_closed,
    )
    if result is None:
        raise _error(
            503,
            "DEPENDENCY_UNAVAILABLE",
            "Service temporarily unavailable",
            "Authentication protection is temporarily unavailable. Try again shortly.",
        )
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    if not result.allowed:
        response.headers["Retry-After"] = str(result.retry_after)
        raise _error(
            429,
            "RATE_LIMITED",
            "Too Many Requests",
            "Too many requests. Wait before trying again.",
            headers={
                "Retry-After": str(result.retry_after),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
            },
        )


@router.get("/auth/hospitals")
async def hospital_directory(request: Request) -> dict[str, Any]:
    """List hospital names for the staff sign-in picker (no staff or patient data)."""
    settings: Settings = request.app.state.settings
    if not settings.public_hospital_directory or not settings.worker_database_url:
        return {"items": []}
    engine = getattr(request.app.state, "directory_engine", None)
    if engine is None:
        from app.db.session import create_engine

        engine = create_engine(settings.worker_database_url)
        request.app.state.directory_engine = engine
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text("SELECT id, name FROM hospitals ORDER BY created_at LIMIT 50")
                )
            ).all()
    except Exception:
        return {"items": []}
    return {"items": [{"id": str(row.id), "name": row.name} for row in rows]}


@router.post("/auth/login")
async def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="login",
        limit=10,
        window_seconds=60,
        fail_closed=True,
    )
    now = datetime.now(UTC)
    invalid_reason: str | None = None
    locked = False
    user: dict[str, Any] | None = None
    session: dict[str, Any] | None = None

    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, payload.hospital_id)
        hospital = await connection.scalar(
            text("SELECT id FROM hospitals WHERE id = :hospital_id"),
            {"hospital_id": payload.hospital_id},
        )
        if hospital is None:
            verify_password(payload.password, _DUMMY_PASSWORD_HASH)
            invalid_reason = "invalid_credentials"
        else:
            result = await connection.execute(
                text(
                    "SELECT id, hospital_id, role, name, email, password_hash, failed_logins, "
                    "locked_until, is_active, must_change_password, mfa_enabled_at FROM users "
                    "WHERE hospital_id = :hospital_id AND lower(email) = :email "
                    "AND deleted_at IS NULL FOR UPDATE"
                ),
                {"hospital_id": payload.hospital_id, "email": payload.email},
            )
            row = result.mappings().first()
            if row is None:
                verify_password(payload.password, _DUMMY_PASSWORD_HASH)
                invalid_reason = "invalid_credentials"
            elif row["locked_until"] is not None and row["locked_until"] > now:
                locked = True
                invalid_reason = "account_locked"
                await audit(
                    connection,
                    request,
                    hospital_id=payload.hospital_id,
                    actor_type="user",
                    actor_id=row["id"],
                    action="auth.login.locked",
                    resource_type="user",
                    resource_id=row["id"],
                    meta={"result": invalid_reason},
                )
            else:
                valid_password = verify_password(payload.password, row["password_hash"])
                if not valid_password or not row["is_active"]:
                    failed_logins = row["failed_logins"] if row["locked_until"] is None else 0
                    failed_logins += 1
                    locked = failed_logins >= 5
                    locked_until = now + timedelta(minutes=15) if locked else None
                    invalid_reason = "account_locked" if locked else "invalid_credentials"
                    await connection.execute(
                        text(
                            "UPDATE users SET failed_logins = :failed_logins, "
                            "locked_until = :locked_until "
                            "WHERE id = :user_id"
                        ),
                        {
                            "failed_logins": min(failed_logins, 5),
                            "locked_until": locked_until,
                            "user_id": row["id"],
                        },
                    )
                    await audit(
                        connection,
                        request,
                        hospital_id=payload.hospital_id,
                        actor_type="user",
                        actor_id=row["id"],
                        action="auth.login.locked" if locked else "auth.login.failed",
                        resource_type="user",
                        resource_id=row["id"],
                        meta={"result": invalid_reason},
                    )
                else:
                    user = dict(row)
                    await connection.execute(
                        text(
                            "UPDATE users SET failed_logins = 0, locked_until = NULL "
                            "WHERE id = :user_id"
                        ),
                        {"user_id": row["id"]},
                    )
                    if row["mfa_enabled_at"] is not None:
                        # The password was right; the session waits for the second step.
                        await audit(
                            connection,
                            request,
                            hospital_id=payload.hospital_id,
                            actor_type="user",
                            actor_id=row["id"],
                            action="auth.login.mfa_challenged",
                            resource_type="user",
                            resource_id=row["id"],
                        )
                    else:
                        session = await start_session(connection, request, response, user)
                        await audit(
                            connection,
                            request,
                            hospital_id=payload.hospital_id,
                            actor_type="user",
                            actor_id=row["id"],
                            action="auth.login.success",
                            resource_type="user",
                            resource_id=row["id"],
                            meta={"role": row["role"], "mfa": False},
                        )

    if user is None:
        if locked:
            raise _error(
                423,
                "ACCOUNT_LOCKED",
                "Account temporarily locked",
                "Too many failed sign-in attempts. Wait 15 minutes before trying again.",
            )
        raise _error(
            401,
            "INVALID_CREDENTIALS",
            "Sign-in failed",
            "The hospital, email, or password could not be verified.",
        )

    if session is not None:
        return session
    challenge = issue_access_token(
        subject=user["id"],
        role=user["role"],
        hospital_id=user["hospital_id"],
        settings=settings,
        lifetime=timedelta(minutes=MFA_CHALLENGE_MINUTES),
        purpose=TokenPurpose.MFA_CHALLENGE,
    )
    return {"mfa_required": True, "mfa_token": challenge, "expires_in": MFA_CHALLENGE_MINUTES * 60}


class MfaVerifyPayload(StrictPayload):
    """The second sign-in step: the challenge from /auth/login and one code."""

    mfa_token: str = Field(min_length=20, max_length=2048)
    code: str | None = Field(default=None, min_length=6, max_length=12)
    recovery_code: str | None = Field(default=None, min_length=12, max_length=20)


@router.post("/auth/mfa/verify")
async def verify_mfa(
    payload: MfaVerifyPayload, request: Request, response: Response
) -> dict[str, Any]:
    """Exchange a sign-in challenge and a one-time (or recovery) code for a session.

    Wrong codes count towards the same five-try lockout as wrong passwords. A challenge can
    open one session only, and a code cannot be used twice.
    """
    settings: Settings = request.app.state.settings
    await _limit_request(
        request, response, name="mfa", limit=10, window_seconds=60, fail_closed=True
    )
    if (payload.code is None) == (payload.recovery_code is None):
        raise _error(
            422,
            "VALIDATION_FAILED",
            "One code needed",
            "Enter the code from your authenticator app, or one recovery code.",
        )
    try:
        challenge = decode_access_token(
            payload.mfa_token, settings=settings, purpose=TokenPurpose.MFA_CHALLENGE
        )
    except AuthenticationTokenError as exc:
        raise _error(
            401, "MFA_CHALLENGE_EXPIRED", "Sign in again", "Enter your password again."
        ) from exc
    used_key = f"osa:{settings.app_env}:mfa-challenge-used:{challenge['jti']}"
    if await request.app.state.redis_state.exists(used_key):
        raise _error(401, "MFA_CHALLENGE_EXPIRED", "Sign in again", "Enter your password again.")
    hospital_id, user_id = UUID(challenge["hospital_id"]), UUID(challenge["sub"])
    now = datetime.now(UTC)
    session: dict[str, Any] | None = None
    locked = False
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT id, hospital_id, role, name, email, is_active, "
                        "must_change_password, mfa_enabled_at, "
                        "failed_logins, locked_until, mfa_secret_enc, mfa_last_step, "
                        "mfa_recovery_hashes FROM users "
                        "WHERE hospital_id = :hospital_id AND id = :user_id "
                        "AND deleted_at IS NULL FOR UPDATE"
                    ),
                    {"hospital_id": hospital_id, "user_id": user_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None or not row["is_active"] or row["mfa_enabled_at"] is None:
            raise _error(
                401, "MFA_CHALLENGE_EXPIRED", "Sign in again", "Enter your password again."
            )
        if row["locked_until"] is not None and row["locked_until"] > now:
            locked = True
        else:
            secret = decrypt_field(
                row["mfa_secret_enc"],
                key=settings.app_data_keyring,
                associated_data=f"users:mfa_secret_enc:{user_id}",
            )
            hashes = list(row["mfa_recovery_hashes"] or [])
            step: int | None = None
            recovery_index: int | None = None
            if payload.code is not None:
                step = verify_totp(secret, payload.code, last_step=row["mfa_last_step"])
            else:
                recovery_index = match_recovery_code(payload.recovery_code or "", hashes)
            if step is None and recovery_index is None:
                failed = (row["failed_logins"] if row["locked_until"] is None else 0) + 1
                locked = failed >= 5
                await connection.execute(
                    text(
                        "UPDATE users SET failed_logins = :failed, locked_until = :until "
                        "WHERE hospital_id = :hospital_id AND id = :user_id"
                    ),
                    {
                        "failed": min(failed, 5),
                        "until": now + timedelta(minutes=15) if locked else None,
                        "hospital_id": hospital_id,
                        "user_id": user_id,
                    },
                )
                await audit(
                    connection,
                    request,
                    hospital_id=hospital_id,
                    actor_type="user",
                    actor_id=user_id,
                    action="auth.login.locked" if locked else "auth.mfa.failed",
                    resource_type="user",
                    resource_id=user_id,
                    meta={"method": "totp" if payload.code is not None else "recovery"},
                )
            else:
                if recovery_index is not None:
                    hashes[recovery_index] = ""
                await connection.execute(
                    text(
                        "UPDATE users SET failed_logins = 0, locked_until = NULL, "
                        "mfa_last_step = coalesce(:step, mfa_last_step), "
                        "mfa_recovery_hashes = CAST(:hashes AS jsonb) "
                        "WHERE hospital_id = :hospital_id AND id = :user_id"
                    ),
                    {
                        "step": step,
                        "hashes": json.dumps(hashes),
                        "hospital_id": hospital_id,
                        "user_id": user_id,
                    },
                )
                session = await start_session(connection, request, response, dict(row))
                await audit(
                    connection,
                    request,
                    hospital_id=hospital_id,
                    actor_type="user",
                    actor_id=user_id,
                    action="auth.login.success",
                    resource_type="user",
                    resource_id=user_id,
                    meta={
                        "role": row["role"],
                        "mfa": "totp" if step is not None else "recovery_code",
                        "recovery_codes_left": sum(1 for value in hashes if value),
                    },
                )
    if locked:
        raise _error(
            423,
            "ACCOUNT_LOCKED",
            "Account temporarily locked",
            "Too many failed sign-in attempts. Wait 15 minutes before trying again.",
        )
    if session is None:
        raise _error(401, "INVALID_MFA_CODE", "Code not accepted", "Check the code and try again.")
    await request.app.state.redis_state.set(used_key, "1", ex=(MFA_CHALLENGE_MINUTES + 1) * 60)
    return session


@router.post("/auth/refresh")
async def refresh(
    request: Request, response: Response, payload: AccountPayload | None = None
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="refresh",
        limit=30,
        window_seconds=60,
        fail_closed=False,
    )
    cookie_name, refresh_value = _incoming_refresh_cookie(request, payload)
    if not refresh_value:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "Your session has expired.")

    token_hash = digest_secret(refresh_value)
    failure: str | None = None
    new_refresh = create_opaque_secret()
    new_refresh_id = uuid4()
    renewed = False
    user: dict[str, Any] | None = None
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection,
            setting="app.refresh_token_hash",
            secret_hash=token_hash,
        )
        initial = (
            (
                await connection.execute(
                    text(
                        "SELECT id, hospital_id, user_id, family_id FROM refresh_tokens "
                        "WHERE token_hash = :token_hash LIMIT 1"
                    ),
                    {"token_hash": token_hash},
                )
            )
            .mappings()
            .first()
        )
        if initial is None:
            failure = "invalid"
        else:
            await set_hospital_scope(connection, initial["hospital_id"])
            token = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, hospital_id, user_id, family_id, expires_at, revoked_at "
                            "FROM refresh_tokens WHERE id = :id FOR UPDATE"
                        ),
                        {"id": initial["id"]},
                    )
                )
                .mappings()
                .first()
            )
            if token is None:
                failure = "invalid"
            elif token["revoked_at"] is not None:
                await connection.execute(
                    text(
                        "UPDATE refresh_tokens SET revoked_at = COALESCE(revoked_at, :now) "
                        "WHERE hospital_id = :hospital_id AND user_id = :user_id "
                        "AND family_id = :family_id"
                    ),
                    {
                        "now": datetime.now(UTC),
                        "hospital_id": token["hospital_id"],
                        "user_id": token["user_id"],
                        "family_id": token["family_id"],
                    },
                )
                await audit(
                    connection,
                    request,
                    hospital_id=token["hospital_id"],
                    actor_type="user",
                    actor_id=token["user_id"],
                    action="auth.refresh.reuse_detected",
                    resource_type="refresh_token_family",
                    resource_id=token["family_id"],
                    meta={"result": "family_revoked"},
                )
                failure = "invalid"
            elif token["expires_at"] <= datetime.now(UTC):
                await connection.execute(
                    text("UPDATE refresh_tokens SET revoked_at = :now WHERE id = :id"),
                    {"now": datetime.now(UTC), "id": token["id"]},
                )
                failure = "invalid"
            else:
                account = (
                    (
                        await connection.execute(
                            text(
                                "SELECT id, hospital_id, role, name, email, is_active, "
                                "must_change_password, mfa_enabled_at "
                                "FROM users "
                                "WHERE id = :user_id"
                            ),
                            {"user_id": token["user_id"]},
                        )
                    )
                    .mappings()
                    .first()
                )
                if account is None or not account["is_active"]:
                    await connection.execute(
                        text(
                            "UPDATE refresh_tokens SET revoked_at = :now "
                            "WHERE hospital_id = :hospital_id AND user_id = :user_id "
                            "AND family_id = :family_id"
                        ),
                        {
                            "now": datetime.now(UTC),
                            "hospital_id": token["hospital_id"],
                            "user_id": token["user_id"],
                            "family_id": token["family_id"],
                        },
                    )
                    failure = "invalid"
                else:
                    user = dict(account)
                    expires_at = datetime.now(UTC) + timedelta(hours=settings.refresh_token_hours)
                    await connection.execute(
                        text(
                            "INSERT INTO refresh_tokens (id, hospital_id, user_id, family_id, "
                            "token_hash, expires_at, device, last_used_at) "
                            "SELECT :id, :hospital_id, :user_id, :family_id, :token_hash, "
                            ":expires_at, device, now() FROM refresh_tokens WHERE id = :old_id"
                        ),
                        {
                            "id": new_refresh_id,
                            "hospital_id": token["hospital_id"],
                            "user_id": token["user_id"],
                            "family_id": token["family_id"],
                            "token_hash": digest_secret(new_refresh),
                            "expires_at": expires_at,
                            "old_id": token["id"],
                        },
                    )
                    await connection.execute(
                        text(
                            "UPDATE refresh_tokens SET revoked_at = :now, replaced_by = :new_id "
                            "WHERE id = :id"
                        ),
                        {"now": datetime.now(UTC), "new_id": new_refresh_id, "id": token["id"]},
                    )
                    await audit(
                        connection,
                        request,
                        hospital_id=token["hospital_id"],
                        actor_type="user",
                        actor_id=token["user_id"],
                        action="auth.refresh.success",
                        resource_type="refresh_token",
                        resource_id=new_refresh_id,
                    )
                    renewed = True

    if failure or not renewed or user is None:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "Your session has expired.")
    _set_refresh_cookie(response, settings, new_refresh, user["id"])
    if cookie_name == _LEGACY_REFRESH_COOKIE:
        # Move a session from the old shared cookie to its own per-account cookie.
        _clear_refresh_cookie(response, settings, _LEGACY_REFRESH_COOKIE)
    return session_body(settings, user)


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request, response: Response, payload: AccountPayload | None = None
) -> Response:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="logout",
        limit=30,
        window_seconds=60,
        fail_closed=False,
    )
    cookie_name, refresh_value = _incoming_refresh_cookie(request, payload)
    if refresh_value:
        token_hash = digest_secret(refresh_value)
        async with request.app.state.engine.begin() as connection:
            await set_secret_lookup_scope(
                connection,
                setting="app.refresh_token_hash",
                secret_hash=token_hash,
            )
            token = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, hospital_id, user_id FROM refresh_tokens "
                            "WHERE token_hash = :token_hash LIMIT 1"
                        ),
                        {"token_hash": token_hash},
                    )
                )
                .mappings()
                .first()
            )
            if token is not None:
                await set_hospital_scope(connection, token["hospital_id"])
                await connection.execute(
                    text(
                        "UPDATE refresh_tokens SET revoked_at = :now "
                        "WHERE id = :id AND revoked_at IS NULL"
                    ),
                    {"now": datetime.now(UTC), "id": token["id"]},
                )
                await audit(
                    connection,
                    request,
                    hospital_id=token["hospital_id"],
                    actor_type="user",
                    actor_id=token["user_id"],
                    action="auth.logout",
                    resource_type="refresh_token",
                    resource_id=token["id"],
                )
    if cookie_name is not None:
        _clear_refresh_cookie(response, settings, cookie_name)
    response.status_code = 204
    return response


@router.post("/kiosks/heartbeat")
async def kiosk_heartbeat(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_kiosk())],
) -> dict[str, Any]:
    """Called by an open kiosk about once a minute so administrators can see it is online.

    It also returns the hospital's name, so a renamed hospital shows up on its kiosks.
    """
    try:
        hospital_id, kiosk_id = UUID(principal["hospital_id"]), UUID(principal["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "The session is invalid.") from exc
    issued_at = principal.get("iat")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        active = await IntakeRepository(connection, request.app.state.settings).touch_kiosk(
            hospital_id=hospital_id,
            kiosk_id=kiosk_id,
            issued_at=issued_at if isinstance(issued_at, int) else None,
        )
        hospital_name = await _hospital_name(connection, hospital_id)
    return {"active": active, "hospital_name": hospital_name}


@router.post("/kiosks/token")
async def renew_kiosk_token(
    payload: KioskTokenPayload,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    """Give a paired kiosk device a new access token in exchange for its device secret.

    Access tokens stay short-lived; the device secret (issued once at activation and kept
    on the device) lets a kiosk stay signed in for as long as an administrator keeps it in
    service, without anyone typing an activation code again. Deleting or re-pairing the
    kiosk invalidates the secret at once (401: the device forgets it). A deactivated kiosk
    is refused with 403 but keeps its secret, so activating it again needs no new code.
    The secret is not rotated on use, so a renewal lost to a flaky hospital network never
    strands the kiosk.
    """
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="kiosk-token",
        limit=120,
        window_seconds=60,
        fail_closed=False,
    )
    secret_hash = digest_secret(payload.device_secret)
    kiosk: dict[str, Any] | None = None
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection, setting="app.kiosk_secret_hash", secret_hash=secret_hash
        )
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT id, hospital_id, is_active FROM kiosks "
                        "WHERE secret_hash = :secret_hash AND deleted_at IS NULL LIMIT 1"
                    ),
                    {"secret_hash": secret_hash},
                )
            )
            .mappings()
            .first()
        )
        if row is not None and not row["is_active"]:
            raise _error(
                403,
                "KIOSK_TURNED_OFF",
                "Kiosk turned off",
                "An administrator has turned this kiosk off. Ask staff to turn it on again.",
            )
        if row is not None:
            kiosk = dict(row)
            await set_hospital_scope(connection, kiosk["hospital_id"])
            await connection.execute(
                text(
                    "UPDATE kiosks SET last_seen_at = now() "
                    "WHERE hospital_id = :hospital_id AND id = :id"
                ),
                {"hospital_id": kiosk["hospital_id"], "id": kiosk["id"]},
            )
            await audit(
                connection,
                request,
                hospital_id=kiosk["hospital_id"],
                actor_type="kiosk",
                actor_id=kiosk["id"],
                action="kiosk.token_renewed",
                resource_type="kiosk",
                resource_id=kiosk["id"],
            )
            kiosk["hospital_name"] = await _hospital_name(connection, kiosk["hospital_id"])
    if kiosk is None:
        raise _error(
            401,
            "KIOSK_NOT_PAIRED",
            "Kiosk sign-in ended",
            "This kiosk was deleted or set up on another device. "
            "Ask an administrator for a new activation code.",
        )
    return {
        **_kiosk_token_response(settings, kiosk_id=kiosk["id"], hospital_id=kiosk["hospital_id"]),
        "hospital_name": kiosk["hospital_name"],
    }


@router.post("/kiosks/activate")
async def activate_kiosk(
    payload: KioskActivationPayload,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="kiosk-activate",
        limit=10,
        window_seconds=60,
        fail_closed=False,
    )
    activation_hash = digest_secret(payload.activation_code)
    device_secret = create_opaque_secret()
    claims: dict[str, Any] | None = None
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection,
            setting="app.kiosk_activation_hash",
            secret_hash=activation_hash,
        )
        kiosk = (
            (
                await connection.execute(
                    text(
                        "SELECT id, hospital_id FROM kiosks "
                        "WHERE activation_code_hash = :activation_hash AND is_active = true "
                        "AND deleted_at IS NULL AND (activation_code_expires_at IS NULL "
                        "OR activation_code_expires_at > now()) LIMIT 1"
                    ),
                    {"activation_hash": activation_hash},
                )
            )
            .mappings()
            .first()
        )
        if kiosk is not None:
            await set_hospital_scope(connection, kiosk["hospital_id"])
            locked_kiosk = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, hospital_id FROM kiosks WHERE id = :id "
                            "AND activation_code_hash = :activation_hash "
                            "AND is_active = true AND deleted_at IS NULL FOR UPDATE"
                        ),
                        {"id": kiosk["id"], "activation_hash": activation_hash},
                    )
                )
                .mappings()
                .first()
            )
            if locked_kiosk is not None:
                await connection.execute(
                    text(
                        "UPDATE kiosks SET activation_code_hash = NULL, "
                        "activation_code_expires_at = NULL, secret_hash = :secret_hash, "
                        "paired_at = now(), last_seen_at = now() WHERE id = :id"
                    ),
                    {"secret_hash": digest_secret(device_secret), "id": kiosk["id"]},
                )
                claims = {
                    "id": kiosk["id"],
                    "hospital_id": kiosk["hospital_id"],
                    "hospital_name": await _hospital_name(connection, kiosk["hospital_id"]),
                }
                await audit(
                    connection,
                    request,
                    hospital_id=kiosk["hospital_id"],
                    actor_type="kiosk",
                    actor_id=kiosk["id"],
                    action="kiosk.activated",
                    resource_type="kiosk",
                    resource_id=kiosk["id"],
                )
    if claims is None:
        raise _error(
            401,
            "INVALID_ACTIVATION_CODE",
            "Kiosk activation failed",
            "The activation code is invalid, expired, or has already been used. "
            "Ask an administrator for a new one.",
        )
    return {
        **_kiosk_token_response(settings, kiosk_id=claims["id"], hospital_id=claims["hospital_id"]),
        "kiosk_id": str(claims["id"]),
        "hospital_name": claims["hospital_name"],
        "device_secret": device_secret,
    }
