"""Bearer-token dependencies and the explicit public-route inventory."""

import re
import time
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import UUID

import structlog
from fastapi import Request
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import Settings, get_settings
from app.core.errors import ApiProblemError, api_problem_exception_handler
from app.core.security import AuthenticationTokenError, Restriction, decode_access_token

logger = structlog.get_logger(__name__)
STAFF_ROLES = frozenset({"admin", "doctor", "nurse"})

PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/metrics"),
        ("GET", "/auth/hospitals"),
        ("POST", "/auth/login"),
        # The second sign-in step: carries its own short-lived challenge token in the body.
        ("POST", "/auth/mfa/verify"),
        ("POST", "/auth/refresh"),
        ("POST", "/auth/logout"),
        ("POST", "/kiosks/activate"),
        ("POST", "/kiosks/token"),
        ("GET", "/status/{token}"),
        ("GET", "/status/{token}/stream"),
        ("POST", "/status/{token}/ai-consent/withdraw"),
        ("POST", "/status/{token}/sms/stop"),
        ("GET", "/intake/checkin/{code}"),
        ("POST", "/intake/checkin/{code}/sessions"),
        ("GET", "/openapi.json"),
        ("HEAD", "/openapi.json"),
        ("GET", "/docs"),
        ("HEAD", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("HEAD", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
        ("HEAD", "/redoc"),
    }
)


def _unauthenticated() -> ApiProblemError:
    return ApiProblemError(
        status=401,
        code="UNAUTHENTICATED",
        title="Authentication required",
        detail="Sign in to continue.",
    )


# What a restricted staff session may still do: finish the required step, or leave.
_RESTRICTED_ROUTES: dict[str, frozenset[tuple[str, str]]] = {
    Restriction.PASSWORD_CHANGE.value: frozenset(
        {("POST", "/auth/password"), ("GET", "/auth/me"), ("POST", "/auth/logout")}
    ),
    Restriction.MFA_SETUP.value: frozenset(
        {
            ("POST", "/auth/mfa/setup"),
            ("POST", "/auth/mfa/enable"),
            ("POST", "/auth/password"),
            ("GET", "/auth/me"),
            ("POST", "/auth/logout"),
        }
    ),
}
_RESTRICTION_PROBLEMS = {
    Restriction.PASSWORD_CHANGE.value: (
        "PASSWORD_CHANGE_REQUIRED",
        "Choose a new password",
        "Your password was reset. Choose a new password to continue.",
    ),
    Restriction.MFA_SETUP.value: (
        "MFA_SETUP_REQUIRED",
        "Set up two-step sign-in",
        "Your role needs two-step sign-in. Set it up to continue.",
    ),
}


def _request_token(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise _unauthenticated()
    try:
        settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
        claims = decode_access_token(token, settings=settings, allow_restricted=True)
    except AuthenticationTokenError as exc:
        raise _unauthenticated() from exc
    for restriction in claims.get("rst") or ():
        allowed = _RESTRICTED_ROUTES.get(restriction, frozenset())
        if (request.method.upper(), request.url.path) not in allowed:
            code, title, detail = _RESTRICTION_PROBLEMS.get(
                restriction, ("FORBIDDEN", "Permission denied", "Finish signing in first.")
            )
            raise ApiProblemError(status=403, code=code, title=title, detail=detail)
    request.state.principal = claims
    return claims


def staff_revocation_key(app_env: str, user_id: UUID | str) -> str:
    return f"osa:{app_env}:staff-sessions-ended:{UUID(str(user_id)).hex}"


async def end_staff_sessions(request: Request, user_id: UUID) -> None:
    """Make every access token this person already holds stop working now.

    Refresh tokens are revoked in the database by the caller; this covers the access tokens
    (up to 10 minutes old) that would otherwise keep working until they expire.
    """
    settings: Settings = request.app.state.settings
    try:
        await request.app.state.redis_state.set(
            staff_revocation_key(settings.app_env, user_id),
            str(int(time.time() * 1000)),
            ex=settings.access_token_minutes * 60 + 120,
        )
    except RedisError:
        logger.warning("staff_session_end_not_recorded", user_id=str(user_id))


async def _ensure_session_not_ended(request: Request, claims: dict[str, Any]) -> None:
    if claims.get("role") not in STAFF_ROLES:
        return
    settings: Settings = request.app.state.settings
    try:
        ended = await request.app.state.redis_state.get(
            staff_revocation_key(settings.app_env, claims["sub"])
        )
    except (RedisError, AttributeError):
        # Fail open: the token still expires within 10 minutes, and refresh is checked
        # against the database.
        return
    issued_ms = int(claims.get("iatm") or int(claims["iat"]) * 1000)
    if ended is not None and issued_ms < int(ended):
        raise ApiProblemError(
            status=401,
            code="UNAUTHENTICATED",
            title="Signed out",
            detail="This session was ended. Sign in again.",
        )


def current_principal(request: Request) -> dict[str, Any]:
    """Return the verified request principal, decoding the bearer token once."""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, dict):
        return cast(dict[str, Any], principal)
    return _request_token(request)


# Public routes that carry an opaque 256-bit code in the path (method, exact path shape).
PUBLIC_CODE_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"/status/[A-Za-z0-9_-]{43}(?:/stream)?")),
    # The patient's status link can stop (never start) online AI help for their visit.
    ("POST", re.compile(r"/status/[A-Za-z0-9_-]{43}/ai-consent/withdraw")),
    # ...and stop (never start) queue messages by SMS.
    ("POST", re.compile(r"/status/[A-Za-z0-9_-]{43}/sms/stop")),
    # A hospital's phone check-in QR: look it up, then start a patient session from it.
    ("GET", re.compile(r"/intake/checkin/[A-Za-z0-9_-]{43}")),
    ("POST", re.compile(r"/intake/checkin/[A-Za-z0-9_-]{43}/sessions")),
)


async def authentication_gate(request: Request) -> None:
    """Reject anonymous access to every route outside the public allow-list."""
    if request.scope.get("type") == "websocket":
        return
    path = request.url.path
    method = request.method.upper()
    if path == "/metrics" and getattr(request.app.state.settings, "metrics_enabled", False):
        return
    if (method, path) in PUBLIC_ROUTES or any(
        method == allowed and pattern.fullmatch(path) is not None
        for allowed, pattern in PUBLIC_CODE_ROUTES
    ):
        return
    await _ensure_session_not_ended(request, _request_token(request))


class AuthenticationGateMiddleware(BaseHTTPMiddleware):
    """Apply the public-route allow-list to HTTP only; WebSockets authenticate in-frame."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            await authentication_gate(request)
        except ApiProblemError as exc:
            return await api_problem_exception_handler(request, exc)
        return await call_next(request)


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Build a dependency that enforces an explicit set of roles."""
    if not roles:
        raise ValueError("at least one role is required")

    async def role_dependency(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        if principal["role"] not in roles:
            raise ApiProblemError(
                status=403,
                code="FORBIDDEN",
                title="Permission denied",
                detail="Your account does not have access to this resource.",
            )
        return principal

    return role_dependency


def require_kiosk() -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    return require_role("kiosk")


def require_patient() -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    return require_role("patient")
