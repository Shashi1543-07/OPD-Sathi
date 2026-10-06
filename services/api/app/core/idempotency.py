"""Redis-backed idempotency for transactional HTTP writes."""

import asyncio
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import UUID

import structlog
from fastapi import Request
from fastapi.encoders import jsonable_encoder

from app.core.errors import ApiProblemError

_TTL_SECONDS = 24 * 60 * 60
_RESERVATION_TTL_SECONDS = 60
_WAIT_SECONDS = 5.0
logger = structlog.get_logger(__name__)


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


class IdempotencyStore:
    def __init__(self, redis_client: Any):
        self.redis = redis_client

    async def execute(
        self,
        request: Request,
        *,
        hospital_id: UUID,
        actor_id: UUID,
        body: dict[str, Any],
        operation: Callable[[], Awaitable[dict[str, Any]]],
        response_fields_once: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        client_key = request.headers.get("Idempotency-Key", "")
        if not 1 <= len(client_key) <= 200 or any(ord(char) < 33 for char in client_key):
            raise _problem(
                400,
                "VALIDATION_FAILED",
                "Idempotency key required",
                "Provide an Idempotency-Key header between 1 and 200 visible characters.",
            )
        body_hash = hashlib.sha256(
            json.dumps(
                jsonable_encoder(body),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        key_hash = hashlib.sha256(client_key.encode()).hexdigest()
        redis_key = (
            f"osa:idempotency:{hospital_id}:{actor_id}:{request.method}:"
            f"{request.url.path}:{key_hash}"
        )
        owner = secrets.token_urlsafe(18)
        deadline = asyncio.get_running_loop().time() + _WAIT_SECONDS
        try:
            while True:
                cached_raw = await self.redis.get(redis_key)
                if cached_raw is not None:
                    cached = json.loads(cached_raw)
                    if cached["body_hash"] != body_hash:
                        raise _problem(
                            422,
                            "IDEMPOTENCY_KEY_REUSED",
                            "Idempotency key reused",
                            "This key was already used with a different request body.",
                        )
                    if cached.get("state") == "complete":
                        return cast(dict[str, Any], cached["response"])
                reservation = json.dumps(
                    {"body_hash": body_hash, "state": "running", "owner": owner},
                    separators=(",", ":"),
                )
                acquired = await self.redis.set(
                    redis_key, reservation, ex=_RESERVATION_TTL_SECONDS, nx=True
                )
                if acquired:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise _problem(
                        409,
                        "IDEMPOTENCY_IN_PROGRESS",
                        "Request in progress",
                        "A request with this idempotency key is still being processed.",
                    )
                await asyncio.sleep(0.05)
        except ApiProblemError:
            raise
        except Exception as exc:
            raise _problem(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Service temporarily unavailable",
                "Idempotent writes are temporarily unavailable. Try again shortly.",
            ) from exc

        try:
            response = await operation()
            cached_response = {
                key: value for key, value in response.items() if key not in response_fields_once
            }
            complete = json.dumps(
                {
                    "body_hash": body_hash,
                    "state": "complete",
                    "response": cached_response,
                },
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            # Compare ownership so a long-running expired reservation cannot overwrite a retry.
            await self.redis.eval(
                "local v=redis.call('GET',KEYS[1]); "
                "if not v then return 0 end; local d=cjson.decode(v); "
                "if d.owner ~= ARGV[1] then return 0 end; "
                "redis.call('SET',KEYS[1],ARGV[2],'EX',ARGV[3]); return 1",
                1,
                redis_key,
                owner,
                complete,
                _TTL_SECONDS,
            )
            return response
        except Exception:
            try:
                await self.redis.eval(
                    "local v=redis.call('GET',KEYS[1]); if not v then return 0 end; "
                    "local d=cjson.decode(v); if d.owner == ARGV[1] then "
                    "return redis.call('DEL',KEYS[1]) end; return 0",
                    1,
                    redis_key,
                    owner,
                )
            except Exception as cleanup_error:
                logger.warning(
                    "idempotency_reservation_cleanup_failed",
                    error_type=type(cleanup_error).__name__,
                )
            raise
