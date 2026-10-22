"""A staff member's own account: password, two-step sign-in and sign-in sessions.

Phase 7, tasks 7.3.1 (password change), 7.3.2 (MFA, ADR 008) and 7.3.4 (sessions). Every
change is audited. Changing the password signs the person out everywhere else at once.
"""

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.api.v1.auth import (
    SESSION_USER_COLUMNS,
    _clear_refresh_cookie,
    _error,
    _limit_request,
    _refresh_cookie_name,
    session_body,
    start_session,
)
from app.config import Settings
from app.core.audit import audit
from app.core.auth import end_staff_sessions, require_role
from app.core.crypto import decrypt_field, encrypt_field
from app.core.mfa import (
    hash_recovery_code,
    new_recovery_codes,
    new_totp_secret,
    provisioning_uri,
    verify_totp,
)
from app.core.passwords import password_problem
from app.core.security import digest_secret, hash_password, verify_password
from app.db.session import set_hospital_scope

router = APIRouter(prefix="/auth", tags=["account"])
_staff = require_role("admin", "doctor", "nurse")
StaffPrincipal = Annotated[dict[str, Any], Depends(_staff)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class PasswordChangePayload(Payload):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class CodePayload(Payload):
    code: str = Field(min_length=6, max_length=12)


def _ids(principal: dict[str, Any]) -> tuple[UUID, UUID]:
    return UUID(principal["hospital_id"]), UUID(principal["sub"])


def _secret_context(user_id: UUID) -> str:
    return f"users:mfa_secret_enc:{user_id}"


async def _account(connection: Any, hospital_id: UUID, user_id: UUID) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    f"SELECT {SESSION_USER_COLUMNS}, password_hash, mfa_secret_enc, "
                    "mfa_last_step, mfa_recovery_hashes, password_changed_at, "
                    "(SELECT name FROM hospitals WHERE id = :hospital_id) AS hospital_name "
                    "FROM users WHERE hospital_id = :hospital_id AND id = :user_id "
                    "AND deleted_at IS NULL FOR UPDATE"
                ),
                {"hospital_id": hospital_id, "user_id": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None or not row["is_active"]:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "Your session has expired.")
    return dict(row)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/me")
async def my_account(request: Request, principal: StaffPrincipal) -> dict[str, Any]:
    """Who is signed in, and what their account still needs."""
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
    return {
        "id": str(account["id"]),
        "name": account["name"],
        "email": account["email"],
        "role": account["role"],
        "hospital_id": str(account["hospital_id"]),
        "hospital_name": account["hospital_name"],
        "mfa_enabled": account["mfa_enabled_at"] is not None,
        "mfa_required": account["role"] in settings.mfa_required_role_set,
        "recovery_codes_left": sum(1 for value in account["mfa_recovery_hashes"] or [] if value),
        "must_change_password": bool(account["must_change_password"]),
        "password_changed_at": (
            account["password_changed_at"].isoformat() if account["password_changed_at"] else None
        ),
    }


@router.post("/password")
async def change_password(
    payload: PasswordChangePayload,
    request: Request,
    response: Response,
    principal: StaffPrincipal,
) -> dict[str, Any]:
    """Choose a new password (also after an administrator's reset).

    Every other sign-in of this person ends at once; this browser gets a fresh session.
    """
    await _limit_request(
        request, response, name="password", limit=10, window_seconds=300, fail_closed=True
    )
    hospital_id, user_id = _ids(principal)
    failure: tuple[str, str, str] | None = None
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
        if not verify_password(payload.current_password, account["password_hash"]):
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=user_id,
                action="auth.password.change_refused",
                resource_type="user",
                resource_id=user_id,
                meta={"reason": "current_password"},
            )
            failure = (
                "CURRENT_PASSWORD_WRONG",
                "Current password not accepted",
                "Enter the password you signed in with.",
            )
        elif verify_password(payload.new_password, account["password_hash"]):
            failure = (
                "PASSWORD_UNCHANGED",
                "Choose a different password",
                "The new password must differ from the current one.",
            )
        else:
            problem = password_problem(
                payload.new_password,
                personal=(account["name"], account["email"], account["hospital_name"] or ""),
            )
            if problem:
                failure = ("PASSWORD_TOO_WEAK", "Choose a stronger password", problem)
        if failure is None:
            await connection.execute(
                text(
                    "UPDATE users SET password_hash = :password_hash, "
                    "must_change_password = false, password_changed_at = now(), "
                    "failed_logins = 0, locked_until = NULL "
                    "WHERE hospital_id = :hospital_id AND id = :user_id"
                ),
                {
                    "password_hash": hash_password(payload.new_password),
                    "hospital_id": hospital_id,
                    "user_id": user_id,
                },
            )
            await connection.execute(
                text(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE hospital_id = "
                    ":hospital_id AND user_id = :user_id AND revoked_at IS NULL"
                ),
                {"hospital_id": hospital_id, "user_id": user_id},
            )
            # End the old access tokens before issuing the new one.
            await end_staff_sessions(request, user_id)
            session = await start_session(
                connection, request, response, {**account, "must_change_password": False}
            )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=user_id,
                action="auth.password.changed",
                resource_type="user",
                resource_id=user_id,
                meta={"was_reset": bool(account["must_change_password"])},
            )
    if failure is not None:
        code, title, detail = failure
        raise _error(422, code, title, detail)
    return session


@router.post("/mfa/setup")
async def start_mfa_setup(
    request: Request, response: Response, principal: StaffPrincipal
) -> dict[str, Any]:
    """A new authenticator secret for this account (not active until a code confirms it)."""
    _no_store(response)
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    secret = new_totp_secret()
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
        if account["mfa_enabled_at"] is not None:
            raise _error(
                409, "MFA_ALREADY_ON", "Two-step sign-in is on", "It is already set up."
            )
        await connection.execute(
            text(
                "UPDATE users SET mfa_secret_enc = :secret WHERE hospital_id = :hospital_id "
                "AND id = :user_id"
            ),
            {
                "secret": encrypt_field(
                    secret,
                    key=settings.app_data_keyring,
                    associated_data=_secret_context(user_id),
                ),
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
            action="auth.mfa.setup_started",
            resource_type="user",
            resource_id=user_id,
        )
    return {
        "secret": secret,
        "otpauth_uri": provisioning_uri(
            secret, account=account["email"], hospital=account["hospital_name"] or "Hospital"
        ),
    }


async def _check_code(account: dict[str, Any], code: str, settings: Settings) -> int:
    if not account["mfa_secret_enc"]:
        raise _error(409, "MFA_NOT_STARTED", "Set up first", "Start the set-up again.")
    secret = decrypt_field(
        account["mfa_secret_enc"],
        key=settings.app_data_keyring,
        associated_data=_secret_context(account["id"]),
    )
    step = verify_totp(secret, code, last_step=account["mfa_last_step"])
    if step is None:
        raise _error(
            422,
            "INVALID_MFA_CODE",
            "Code not accepted",
            "Check the code in your authenticator app and the phone's clock, then try again.",
        )
    return step


async def _store_codes(
    connection: Any, hospital_id: UUID, user_id: UUID, step: int, *, enable: bool
) -> list[str]:
    codes = new_recovery_codes()
    await connection.execute(
        text(
            "UPDATE users SET mfa_last_step = :step, "
            "mfa_recovery_hashes = CAST(:hashes AS jsonb), "
            "mfa_enabled_at = CASE WHEN :enable THEN now() ELSE mfa_enabled_at END "
            "WHERE hospital_id = :hospital_id AND id = :user_id"
        ),
        {
            "step": step,
            "hashes": json.dumps([hash_recovery_code(code) for code in codes]),
            "enable": enable,
            "hospital_id": hospital_id,
            "user_id": user_id,
        },
    )
    return codes


@router.post("/mfa/enable")
async def enable_mfa(
    payload: CodePayload, request: Request, response: Response, principal: StaffPrincipal
) -> dict[str, Any]:
    """Confirm the authenticator with its first code; returns ten one-time recovery codes."""
    _no_store(response)
    await _limit_request(
        request, response, name="mfa-enable", limit=10, window_seconds=300, fail_closed=True
    )
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
        if account["mfa_enabled_at"] is not None:
            raise _error(409, "MFA_ALREADY_ON", "Two-step sign-in is on", "It is already set up.")
        step = await _check_code(account, payload.code, settings)
        codes = await _store_codes(connection, hospital_id, user_id, step, enable=True)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=user_id,
            action="auth.mfa.enabled",
            resource_type="user",
            resource_id=user_id,
        )
    # A fresh access token without the set-up restriction, so the person can carry on.
    return {
        "recovery_codes": codes,
        **session_body(settings, {**account, "mfa_enabled_at": "now"}),
    }


@router.post("/mfa/recovery-codes")
async def replace_recovery_codes(
    payload: CodePayload, request: Request, response: Response, principal: StaffPrincipal
) -> dict[str, Any]:
    """Ten new recovery codes; the old ones stop working."""
    _no_store(response)
    await _limit_request(
        request, response, name="mfa-codes", limit=10, window_seconds=300, fail_closed=True
    )
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
        if account["mfa_enabled_at"] is None:
            raise _error(409, "MFA_NOT_ON", "Two-step sign-in is off", "Set it up first.")
        step = await _check_code(account, payload.code, settings)
        codes = await _store_codes(connection, hospital_id, user_id, step, enable=False)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=user_id,
            action="auth.mfa.recovery_codes_replaced",
            resource_type="user",
            resource_id=user_id,
        )
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
async def disable_mfa(
    payload: CodePayload, request: Request, response: Response, principal: StaffPrincipal
) -> dict[str, Any]:
    """Turn two-step sign-in off, where the person's role does not require it."""
    await _limit_request(
        request, response, name="mfa-disable", limit=10, window_seconds=300, fail_closed=True
    )
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _account(connection, hospital_id, user_id)
        if account["role"] in settings.mfa_required_role_set:
            raise _error(
                409,
                "MFA_REQUIRED_FOR_ROLE",
                "Two-step sign-in is required",
                "Your role must use two-step sign-in.",
            )
        if account["mfa_enabled_at"] is None:
            raise _error(409, "MFA_NOT_ON", "Two-step sign-in is off", "It is not on.")
        await _check_code(account, payload.code, settings)
        await connection.execute(
            text(
                "UPDATE users SET mfa_enabled_at = NULL, mfa_secret_enc = NULL, "
                "mfa_last_step = NULL, mfa_recovery_hashes = '[]'::jsonb "
                "WHERE hospital_id = :hospital_id AND id = :user_id"
            ),
            {"hospital_id": hospital_id, "user_id": user_id},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=user_id,
            action="auth.mfa.disabled",
            resource_type="user",
            resource_id=user_id,
        )
    return session_body(settings, {**account, "mfa_enabled_at": None})


async def _current_family(connection: Any, request: Request, user_id: UUID) -> UUID | None:
    value = request.cookies.get(_refresh_cookie_name(user_id))
    if not value:
        return None
    family = await connection.scalar(
        text("SELECT family_id FROM refresh_tokens WHERE token_hash = :token_hash"),
        {"token_hash": digest_secret(value)},
    )
    return family if isinstance(family, UUID) else None


async def list_sessions(
    connection: Any, *, hospital_id: UUID, user_id: UUID, current: UUID | None
) -> list[dict[str, Any]]:
    """A person's sign-ins that can still renew, newest first."""
    rows = (
        (
            await connection.execute(
                text(
                    "SELECT family_id, max(device) AS device, min(created_at) AS signed_in_at, "
                    "max(coalesce(last_used_at, created_at)) AS last_used_at "
                    "FROM refresh_tokens WHERE hospital_id = :hospital_id "
                    "AND user_id = :user_id GROUP BY family_id "
                    "HAVING bool_or(revoked_at IS NULL AND expires_at > now()) "
                    "ORDER BY 4 DESC LIMIT 50"
                ),
                {"hospital_id": hospital_id, "user_id": user_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "id": str(row["family_id"]),
            "device": row["device"] or "Unknown device",
            "signed_in_at": row["signed_in_at"].isoformat(),
            "last_used_at": row["last_used_at"].isoformat(),
            "current": row["family_id"] == current,
        }
        for row in rows
    ]


@router.get("/sessions")
async def my_sessions(request: Request, principal: StaffPrincipal) -> dict[str, Any]:
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        current = await _current_family(connection, request, user_id)
        items = await list_sessions(
            connection, hospital_id=hospital_id, user_id=user_id, current=current
        )
    return {"items": items}


async def _end_families(
    connection: Any,
    request: Request,
    *,
    hospital_id: UUID,
    user_id: UUID,
    keep: UUID | None = None,
    only: UUID | None = None,
) -> int:
    result = await connection.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE hospital_id = :hospital_id "
            "AND user_id = :user_id AND revoked_at IS NULL "
            "AND (CAST(:only AS uuid) IS NULL OR family_id = :only) "
            "AND (CAST(:keep AS uuid) IS NULL OR family_id <> :keep)"
        ),
        {"hospital_id": hospital_id, "user_id": user_id, "only": only, "keep": keep},
    )
    ended = int(result.rowcount or 0)
    await audit(
        connection,
        request,
        hospital_id=hospital_id,
        actor_type="user",
        actor_id=user_id,
        action="auth.sessions.ended",
        resource_type="user",
        resource_id=user_id,
        meta={"scope": "one" if only else "others", "sessions": ended},
    )
    return ended


@router.delete("/sessions/{session_id}", status_code=204)
async def end_session(
    session_id: UUID, request: Request, response: Response, principal: StaffPrincipal
) -> Response:
    """Sign out one of this person's sign-ins (it cannot renew; open screens stop within
    ten minutes)."""
    settings: Settings = request.app.state.settings
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        current = await _current_family(connection, request, user_id)
        ended = await _end_families(
            connection, request, hospital_id=hospital_id, user_id=user_id, only=session_id
        )
    if not ended:
        raise _error(404, "NOT_FOUND", "Not found", "That sign-in has already ended.")
    if session_id == current:
        _clear_refresh_cookie(response, settings, _refresh_cookie_name(user_id))
    response.status_code = 204
    return response


@router.post("/sessions/end-others")
async def end_other_sessions(request: Request, principal: StaffPrincipal) -> dict[str, int]:
    """Sign out everywhere except this browser."""
    hospital_id, user_id = _ids(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        current = await _current_family(connection, request, user_id)
        ended = await _end_families(
            connection, request, hospital_id=hospital_id, user_id=user_id, keep=current
        )
    return {"ended": ended}
