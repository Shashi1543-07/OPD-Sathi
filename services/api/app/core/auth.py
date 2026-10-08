"""Bearer-token dependencies and the explicit public-route inventory."""

import re
from collections.abc import Callable, Coroutine
from typing import Any, cast

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import Settings, get_settings
from app.core.errors import ApiProblemError, api_problem_exception_handler
from app.core.security import AuthenticationTokenError, decode_access_token

PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/metrics"),
        ("GET", "/auth/hospitals"),
        ("POST", "/auth/login"),
        ("POST", "/auth/refresh"),
        ("POST", "/auth/logout"),
        ("POST", "/kiosks/activate"),
        ("GET", "/status/{token}"),
        ("GET", "/status/{token}/stream"),
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


def _request_token(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise _unauthenticated()
    try:
        settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
        claims = decode_access_token(token, settings=settings)
    except AuthenticationTokenError as exc:
        raise _unauthenticated() from exc
    request.state.principal = claims
    return claims


def current_principal(request: Request) -> dict[str, Any]:
    """Return the verified request principal, decoding the bearer token once."""
    principal = getattr(request.state, "principal", None)
    if isinstance(principal, dict):
        return cast(dict[str, Any], principal)
    return _request_token(request)


async def authentication_gate(request: Request) -> None:
    """Reject anonymous access to every route outside the public allow-list."""
    if request.scope.get("type") == "websocket":
        return
    path = request.url.path
    if path == "/metrics" and getattr(request.app.state.settings, "metrics_enabled", False):
        return
    if (request.method.upper(), path) in PUBLIC_ROUTES or (
        request.method.upper() == "GET"
        and re.fullmatch(r"/status/[A-Za-z0-9_-]{43}(?:/stream)?", path) is not None
    ):
        return
    _request_token(request)


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
