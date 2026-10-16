"""RFC 7807 problem responses with stable codes and request identifiers."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiProblemError(Exception):
    """An expected API failure with a stable RFC 7807 response code."""

    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        # Machine-readable details a client needs to act, e.g. which patient is in the way.
        self.extra = extra or {}


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: Any | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        **(extra or {}),
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(
        body,
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


async def api_problem_exception_handler(request: Request, exc: ApiProblemError) -> JSONResponse:
    return problem(
        request,
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        headers=exc.headers,
        extra=exc.extra,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = (
        str(exc.detail) if isinstance(exc.detail, str) else "The request could not be completed."
    )
    return problem(
        request,
        status=exc.status_code,
        code=f"HTTP_{exc.status_code}",
        title="Request failed",
        detail=detail,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    safe_errors = [
        {key: error[key] for key in ("loc", "msg", "type") if key in error}
        for error in exc.errors()
    ]
    return problem(
        request,
        status=422,
        code="VALIDATION_FAILED",
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        errors=safe_errors,
    )
