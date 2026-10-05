"""FastAPI application factory for the OPD Saathi API."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

import redis.asyncio as redis
import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.config import Settings, get_settings
from app.core.errors import http_exception_handler, problem, validation_exception_handler
from app.core.logging import configure_logging
from app.db.session import create_engine, ping_database

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and emit a small, non-sensitive completion log."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", "")
        if not request_id or len(request_id) > 100 or any(ord(ch) < 32 for ch in request_id):
            request_id = str(uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply the baseline security headers to API responses."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        return response


def create_app(
    settings: Settings | None = None,
    *,
    engine_factory: Callable[[str], AsyncEngine] = create_engine,
    redis_factory: Callable[..., redis.Redis] = redis.from_url,
) -> FastAPI:
    """Create the API with injectable connections for isolated tests."""
    configured = settings or get_settings()
    configure_logging(configured.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine_factory(configured.database_url)
        app.state.redis_state = redis_factory(configured.redis_state_url, decode_responses=True)
        try:
            yield
        finally:
            await app.state.engine.dispose()
            await app.state.redis_state.aclose()

    app = FastAPI(
        title="OPD Saathi API", version="0.1.0", docs_url=configured.docs_url, lifespan=lifespan
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    handler_type = Callable[[Request, Exception], Response | Awaitable[Response]]
    app.add_exception_handler(
        StarletteHTTPException, cast(handler_type, http_exception_handler)
    )
    app.add_exception_handler(
        RequestValidationError, cast(handler_type, validation_exception_handler)
    )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    async def readyz(request: Request) -> Response:
        failures: list[str] = []
        try:
            await ping_database(request.app.state.engine)
        except Exception:
            failures.append("postgres")
        try:
            await request.app.state.redis_state.ping()
        except Exception:
            failures.append("redis")
        if failures:
            return problem(
                request,
                status=503,
                code="DEPENDENCY_UNAVAILABLE",
                title="Service not ready",
                detail=f"Unavailable dependency: {', '.join(failures)}",
            )
        return JSONResponse({"status": "ready"})

    return app


app = create_app()
