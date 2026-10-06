"""FastAPI application factory for the OPD Saathi API."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast
from uuid import uuid4

import redis.asyncio as redis
import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.api.v1 import admin as admin_api
from app.api.v1 import audit as audit_api
from app.api.v1 import auth as auth_api
from app.api.v1 import case_sheets as case_sheets_api
from app.api.v1 import intake as intake_api
from app.api.v1 import queue as queue_api
from app.config import Settings, get_settings
from app.core.auth import authentication_gate
from app.core.errors import (
    ApiProblemError,
    api_problem_exception_handler,
    http_exception_handler,
    problem,
    validation_exception_handler,
)
from app.core.logging import configure_logging
from app.core.ratelimit import InMemorySlidingWindow
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
        response.headers.setdefault("Permissions-Policy", "microphone=(self)")
        if request.app.state.settings.app_env == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        return response


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies before parsing them into endpoint models."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        limit = request.app.state.settings.max_request_body_bytes
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                if int(raw_length) > limit:
                    return problem(
                        request,
                        status=413,
                        code="REQUEST_TOO_LARGE",
                        title="Request too large",
                        detail="The request body exceeds the configured size limit.",
                    )
            except ValueError:
                return problem(
                    request,
                    status=400,
                    code="INVALID_CONTENT_LENGTH",
                    title="Invalid request",
                    detail="The request length is invalid.",
                )
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                return problem(
                    request,
                    status=413,
                    code="REQUEST_TOO_LARGE",
                    title="Request too large",
                    detail="The request body exceeds the configured size limit.",
                )
            chunks.append(chunk)
        request._body = b"".join(chunks)
        request._stream_consumed = False
        return await call_next(request)


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
            queue_registry = f"osa:{configured.app_env}:qregistry"
            if configured.worker_database_url and not await app.state.redis_state.sismember(
                queue_registry, "ready"
            ):
                lock_key = f"osa:{configured.app_env}:qrebuild-lock"
                acquired = await app.state.redis_state.set(lock_key, "api-startup", nx=True, ex=60)
                if acquired:
                    from app.workers.main import rebuild_all

                    try:
                        worker_engine = create_async_engine(configured.worker_database_url)
                        try:
                            async with worker_engine.begin() as connection:
                                await rebuild_all(
                                    connection, app.state.redis_state, settings=configured
                                )
                        finally:
                            await worker_engine.dispose()
                    finally:
                        await app.state.redis_state.delete(lock_key)
            yield
        finally:
            await app.state.engine.dispose()
            await app.state.redis_state.aclose()

    app = FastAPI(
        title="OPD Saathi API",
        version="0.1.0",
        docs_url=configured.docs_url,
        openapi_url="/openapi.json" if configured.app_env != "production" else None,
        lifespan=lifespan,
        dependencies=[Depends(authentication_gate)],
    )
    app.state.settings = configured
    app.state.memory_limiter = InMemorySlidingWindow()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RequestBodyLimitMiddleware)
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
        expose_headers=[
            "X-Request-ID",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
        ],
    )
    handler_type = Callable[[Request, Exception], Response | Awaitable[Response]]
    app.add_exception_handler(StarletteHTTPException, cast(handler_type, http_exception_handler))
    app.add_exception_handler(
        RequestValidationError, cast(handler_type, validation_exception_handler)
    )
    app.add_exception_handler(ApiProblemError, cast(handler_type, api_problem_exception_handler))

    app.include_router(auth_api.router)
    app.include_router(audit_api.router)
    app.include_router(admin_api.router)
    app.include_router(intake_api.router)
    app.include_router(queue_api.router)
    app.include_router(case_sheets_api.router)

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
