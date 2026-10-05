"""RFC 7807 problem responses with stable codes and request identifiers."""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    errors: Any | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": getattr(request.state, "request_id", "unknown"),
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


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
    return problem(
        request,
        status=422,
        code="VALIDATION_FAILED",
        title="Request validation failed",
        detail="One or more request fields are invalid.",
        errors=exc.errors(),
    )
