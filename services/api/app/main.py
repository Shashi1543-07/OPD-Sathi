"""FastAPI application factory for the OPD Saathi API."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
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
from starlette.responses import PlainTextResponse, Response

from app.ai.providers.llm.factory import build_llm_provider
from app.ai.providers.llm.gemini import GeminiProvider
from app.ai.providers.llm.mock import MockLLM
from app.ai.providers.stt.factory import build_stt_provider
from app.ai.providers.tts.factory import build_tts_provider
from app.ai.speech import SpeechService
from app.api.v1 import account as account_api
from app.api.v1 import admin as admin_api
from app.api.v1 import analytics as analytics_api
from app.api.v1 import audio as audio_api
from app.api.v1 import audit as audit_api
from app.api.v1 import auth as auth_api
from app.api.v1 import case_sheets as case_sheets_api
from app.api.v1 import documents as documents_api
from app.api.v1 import intake as intake_api
from app.api.v1 import queue as queue_api
from app.api.v1 import speech as speech_api
from app.api.v1 import system as system_api
from app.config import Settings, get_settings
from app.core import metrics
from app.core.auth import AuthenticationGateMiddleware
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
from app.domain.content_status import require_approved_content
from app.pdf import warm_pdf_renderer

logger = structlog.get_logger(__name__)
_READINESS_DEPENDENCY_TIMEOUT_SECONDS = 1.0


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    """Record timings and status against route templates, never raw request paths."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = monotonic()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        labels = (request.method, route, str(response.status_code))
        metrics.HTTP_REQUESTS.labels(*labels).inc()
        metrics.HTTP_DURATION.labels(*labels).observe(monotonic() - started)
        return response


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
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
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
    engine_factory: Callable[[str], AsyncEngine] | None = None,
    redis_factory: Callable[..., redis.Redis] = redis.from_url,
) -> FastAPI:
    """Create the API with injectable connections for isolated tests."""
    configured = settings or get_settings()
    configure_logging(configured.log_level)
    if configured.clinical_content_approval_required:
        require_approved_content()

    def pooled_engine(url: str) -> AsyncEngine:
        return create_engine(url, pool_min=configured.db_pool_min, pool_max=configured.db_pool_max)

    make_engine = engine_factory or pooled_engine

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(warm_pdf_renderer)
        app.state.engine = make_engine(configured.database_url)
        # Report exports may read from a replica; everything else uses the primary.
        app.state.read_engine = (
            make_engine(configured.database_read_url)
            if configured.database_read_url
            else app.state.engine
        )
        app.state.redis_state = redis_factory(configured.redis_state_url, decode_responses=True)
        app.state.redis_cache = redis_factory(configured.redis_cache_url, decode_responses=True)
        app.state.llm_provider = build_llm_provider(configured, redis=app.state.redis_cache)
        app.state.tts_provider = build_tts_provider(configured)
        app.state.speech = SpeechService(configured, app.state.llm_provider)
        logger.info(
            "providers_selected",
            llm=getattr(app.state.llm_provider, "name", "mock"),
            stt=app.state.speech.stt_mode,
            tts=app.state.speech.tts_mode,
        )
        try:
            queue_registry = f"osa:{configured.app_env}:qregistry"
            has_worker_registry = callable(getattr(app.state.redis_state, "sismember", None))
            if configured.worker_database_url and has_worker_registry:
                from app.workers.main import close_previous_days

                try:
                    close_engine = create_engine(configured.worker_database_url)
                    try:
                        async with close_engine.begin() as connection:
                            if await close_previous_days(connection):
                                await app.state.redis_state.srem(queue_registry, "ready")
                    finally:
                        await close_engine.dispose()
                except Exception as exc:
                    logger.warning("day_close_out_failed", error_type=type(exc).__name__)
            if (
                configured.worker_database_url
                and has_worker_registry
                and not await app.state.redis_state.sismember(queue_registry, "ready")
            ):
                lock_key = f"osa:{configured.app_env}:qrebuild-lock"
                acquired = await app.state.redis_state.set(lock_key, "api-startup", nx=True, ex=60)
                if acquired:
                    from app.workers.main import rebuild_all

                    try:
                        worker_engine = create_engine(configured.worker_database_url)
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
            llm_tasks = tuple(app.state.llm_background_tasks)
            for task in llm_tasks:
                task.cancel()
            if llm_tasks:
                await asyncio.gather(*llm_tasks, return_exceptions=True)
            if isinstance(app.state.llm_provider, GeminiProvider):
                await app.state.llm_provider.aclose()
            if app.state.read_engine is not app.state.engine:
                await app.state.read_engine.dispose()
            await app.state.engine.dispose()
            directory_engine = getattr(app.state, "directory_engine", None)
            if directory_engine is not None:
                await directory_engine.dispose()
            await app.state.redis_cache.aclose()
            await app.state.redis_state.aclose()

    app = FastAPI(
        title="OPD Saathi API",
        version="0.1.0",
        docs_url=configured.docs_url,
        openapi_url="/openapi.json" if configured.app_env != "production" else None,
        lifespan=lifespan,
    )
    app.state.settings = configured
    app.state.llm_background_tasks = set()
    app.state.stt_provider = build_stt_provider(configured)
    app.state.llm_provider = MockLLM()
    app.state.tts_provider = build_tts_provider(configured)
    app.state.speech = SpeechService(configured, app.state.llm_provider)
    app.state.memory_limiter = InMemorySlidingWindow()
    app.add_middleware(AuthenticationGateMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(HttpMetricsMiddleware)
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
            "ETag",
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
    app.include_router(account_api.router)
    app.include_router(audit_api.router)
    app.include_router(system_api.router)
    app.include_router(admin_api.router)
    app.include_router(analytics_api.router)
    app.include_router(intake_api.router)
    app.include_router(audio_api.router)
    app.include_router(queue_api.router)
    app.include_router(case_sheets_api.router)
    app.include_router(documents_api.router)
    app.include_router(speech_api.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": configured.app_version}

    @app.get("/readyz", include_in_schema=False, response_model=None)
    async def readyz(request: Request) -> Response:
        # Both dependencies are checked at once, so the answer comes within one timeout.
        results = await asyncio.gather(
            asyncio.wait_for(
                ping_database(request.app.state.engine),
                timeout=_READINESS_DEPENDENCY_TIMEOUT_SECONDS,
            ),
            asyncio.wait_for(
                request.app.state.redis_state.ping(),
                timeout=_READINESS_DEPENDENCY_TIMEOUT_SECONDS,
            ),
            return_exceptions=True,
        )
        failures = [
            name
            for name, result in zip(("postgres", "redis"), results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures:
            return problem(
                request,
                status=503,
                code="DEPENDENCY_UNAVAILABLE",
                title="Service not ready",
                detail=f"Unavailable dependency: {', '.join(failures)}",
            )
        return JSONResponse({"status": "ready"})

    @app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
    async def metrics_page() -> Response:
        """Metrics for Prometheus on the private network (nginx never forwards /metrics)."""
        if not configured.metrics_enabled:
            return PlainTextResponse("Not found", status_code=404)
        body, content_type = metrics.render()
        return Response(body, media_type=content_type)

    return app


app = create_app()
