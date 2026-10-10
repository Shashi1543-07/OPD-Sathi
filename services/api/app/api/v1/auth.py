"""Staff authentication, refresh-cookie rotation, and kiosk activation."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text

from app.config import Settings
from app.core.audit import audit
from app.core.auth import require_kiosk
from app.core.errors import ApiProblemError
from app.core.ratelimit import check_rate_limit
from app.core.security import (
    TokenScheme,
    create_opaque_secret,
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


def _set_refresh_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        "osa_refresh",
        value,
        max_age=settings.refresh_token_hours * 60 * 60,
        path="/auth",
        secure=settings.app_env != "development",
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        "osa_refresh",
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
    refresh_value = create_opaque_secret()
    refresh_id = uuid4()
    family_id = uuid4()
    invalid_reason: str | None = None
    locked = False
    user: dict[str, Any] | None = None

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
                    "locked_until, is_active FROM users "
                    "WHERE hospital_id = :hospital_id AND lower(email) = :email FOR UPDATE"
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
                    await connection.execute(
                        text(
                            "INSERT INTO refresh_tokens "
                            "(id, hospital_id, user_id, family_id, token_hash, expires_at) "
                            "VALUES (:id, :hospital_id, :user_id, :family_id, "
                            ":token_hash, :expires_at)"
                        ),
                        {
                            "id": refresh_id,
                            "hospital_id": payload.hospital_id,
                            "user_id": row["id"],
                            "family_id": family_id,
                            "token_hash": digest_secret(refresh_value),
                            "expires_at": now + timedelta(hours=settings.refresh_token_hours),
                        },
                    )
                    await audit(
                        connection,
                        request,
                        hospital_id=payload.hospital_id,
                        actor_type="user",
                        actor_id=row["id"],
                        action="auth.login.success",
                        resource_type="user",
                        resource_id=row["id"],
                        meta={"role": row["role"]},
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

    _set_refresh_cookie(response, settings, refresh_value)
    token = issue_access_token(
        subject=user["id"],
        role=user["role"],
        hospital_id=user["hospital_id"],
        settings=settings,
    )
    return {
        "access_token": token,
        "token_type": TokenScheme.BEARER.value,
        "expires_in": settings.access_token_minutes * 60,
        "user": {
            "id": str(user["id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "hospital_id": str(user["hospital_id"]),
        },
    }


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="refresh",
        limit=30,
        window_seconds=60,
        fail_closed=False,
    )
    refresh_value = request.cookies.get("osa_refresh")
    if not refresh_value:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "Your session has expired.")

    token_hash = digest_secret(refresh_value)
    failure: str | None = None
    new_refresh = create_opaque_secret()
    new_refresh_id = uuid4()
    access_token: str | None = None
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
                                "SELECT id, hospital_id, role, name, email, is_active FROM users "
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
                            "INSERT INTO refresh_tokens "
                            "(id, hospital_id, user_id, family_id, token_hash, expires_at) "
                            "VALUES (:id, :hospital_id, :user_id, :family_id, "
                            ":token_hash, :expires_at)"
                        ),
                        {
                            "id": new_refresh_id,
                            "hospital_id": token["hospital_id"],
                            "user_id": token["user_id"],
                            "family_id": token["family_id"],
                            "token_hash": digest_secret(new_refresh),
                            "expires_at": expires_at,
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
                    access_token = issue_access_token(
                        subject=account["id"],
                        role=account["role"],
                        hospital_id=account["hospital_id"],
                        settings=settings,
                    )

    if failure or access_token is None or user is None:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "Your session has expired.")
    _set_refresh_cookie(response, settings, new_refresh)
    return {
        "access_token": access_token,
        "token_type": TokenScheme.BEARER.value,
        "expires_in": settings.access_token_minutes * 60,
        "user": {
            "id": str(user["id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "hospital_id": str(user["hospital_id"]),
        },
    }


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> Response:
    settings: Settings = request.app.state.settings
    await _limit_request(
        request,
        response,
        name="logout",
        limit=30,
        window_seconds=60,
        fail_closed=False,
    )
    refresh_value = request.cookies.get("osa_refresh")
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
    _clear_refresh_cookie(response, settings)
    response.status_code = 204
    return response


@router.post("/kiosks/heartbeat")
async def kiosk_heartbeat(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_kiosk())],
) -> dict[str, bool]:
    """Called by an open kiosk about once a minute so administrators can see it is online."""
    try:
        hospital_id, kiosk_id = UUID(principal["hospital_id"]), UUID(principal["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "The session is invalid.") from exc
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        active = await IntakeRepository(connection, request.app.state.settings).touch_kiosk(
            hospital_id=hospital_id, kiosk_id=kiosk_id
        )
    return {"active": active}


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
                        "WHERE activation_code_hash = :activation_hash AND is_active = true LIMIT 1"
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
                            "AND is_active = true FOR UPDATE"
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
                        "secret_hash = :secret_hash, last_seen_at = now() WHERE id = :id"
                    ),
                    {"secret_hash": digest_secret(device_secret), "id": kiosk["id"]},
                )
                claims = {
                    "id": kiosk["id"],
                    "hospital_id": kiosk["hospital_id"],
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
            "The activation code is invalid or has already been used.",
        )
    token = issue_access_token(
        subject=claims["id"],
        role="kiosk",
        hospital_id=claims["hospital_id"],
        settings=settings,
        lifetime=timedelta(hours=settings.kiosk_token_hours),
    )
    return {
        "access_token": token,
        "token_type": TokenScheme.BEARER.value,
        "expires_in": settings.kiosk_token_hours * 60 * 60,
        "device_secret": device_secret,
    }
