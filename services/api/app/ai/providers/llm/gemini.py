"""Async Gemini JSON generation with bounded retries and a privacy-safe circuit."""

import asyncio
import hashlib
import json
import random
from collections.abc import Mapping
from time import monotonic
from typing import Any, Protocol, cast

from app.ai.providers.llm.base import (
    LLMMetrics,
    LLMUnavailableError,
    SessionCallBudget,
)

MAX_TIMEOUT_SECONDS = 2.5
MAX_OUTPUT_TOKENS = 1024
SESSION_TTL_SECONDS = 2 * 60 * 60


class _AsyncModels(Protocol):
    async def generate_content(self, *, model: str, contents: str, config: object) -> object: ...


class _AsyncClient(Protocol):
    models: _AsyncModels

    async def aclose(self) -> None: ...


class RedisSessionCallBudget:
    """Use Redis to enforce the 30-call cap consistently across API workers."""

    def __init__(self, redis: object, *, environment: str) -> None:
        self._redis = redis
        self._environment = environment

    async def consume(self, session_id: str, limit: int) -> bool:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        key = f"osa:{self._environment}:llm:budget:{digest}"
        client = cast(Any, self._redis)
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, SESSION_TTL_SECONDS)
        return bool(count <= limit)


class GeminiProvider:
    """Call Gemini with JSON Schema constraints and re-check every returned value."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback_model: str,
        session_budget: SessionCallBudget | None = None,
        max_calls_per_session: int = 30,
        metrics: LLMMetrics | None = None,
        client: _AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if not model.strip() or not fallback_model.strip():
            raise ValueError("LLM_MODEL and LLM_FALLBACK_MODEL must be configured")
        if max_calls_per_session < 1:
            raise ValueError("per-session call cap must be positive")
        self.model = model
        self.fallback_model = fallback_model
        self.session_budget = session_budget
        self.max_calls_per_session = max_calls_per_session
        self.metrics = metrics or LLMMetrics()
        self._client = client or self._create_client(api_key)
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_active = False
        self._circuit_lock = asyncio.Lock()

    @staticmethod
    def _create_client(api_key: str) -> _AsyncClient:
        from google import genai

        return cast(_AsyncClient, genai.Client(api_key=api_key).aio)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _check_circuit(self) -> None:
        async with self._circuit_lock:
            if self._opened_at is None:
                return
            if monotonic() - self._opened_at < 20.0:
                self.metrics.circuit_open += 1
                raise LLMUnavailableError("language model is unavailable") from None
            if self._probe_active:
                self.metrics.circuit_open += 1
                raise LLMUnavailableError("language model is unavailable") from None
            # Permit exactly one probe after the cool-down.
            self._probe_active = True

    def _record_failure(self) -> None:
        self.metrics.failures += 1
        self._failures += 1
        self._probe_active = False
        if self._failures >= 5:
            self._opened_at = monotonic()

    def _record_success(self) -> None:
        self.metrics.successes += 1
        self._failures = 0
        self._opened_at = None
        self._probe_active = False

    @staticmethod
    def _retryable(error: Exception) -> bool:
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return True
        status = getattr(error, "code", None) or getattr(error, "status_code", None)
        return isinstance(status, int) and (status in {408, 429} or status >= 500)

    async def _generate(self, prompt: str, schema: Mapping[str, Any], model: str) -> object:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=dict(schema),
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        return await self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

    @staticmethod
    def _decode_response(response: object) -> tuple[dict[str, Any], int, int]:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, Mapping):
            result = dict(parsed)
        else:
            raw = getattr(response, "text", None)
            if not isinstance(raw, str) or not raw:
                raise ValueError("empty response")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("response root is not an object")
            result = value
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        response_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return result, prompt_tokens, response_tokens

    @staticmethod
    def _validate_schema(value: dict[str, Any], schema: Mapping[str, Any]) -> None:
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(dict(schema))
        validator.check_schema(dict(schema))
        validator.validate(value)

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = MAX_TIMEOUT_SECONDS,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self.metrics.requests += 1
        started = monotonic()
        timeout = min(timeout, MAX_TIMEOUT_SECONDS)
        if timeout <= 0:
            self._record_failure()
            raise LLMUnavailableError("language model is unavailable") from None
        failure_recorded = False
        try:
            from jsonschema import Draft202012Validator

            Draft202012Validator.check_schema(dict(schema))
            await self._check_circuit()
            if session_id and self.session_budget is not None:
                try:
                    allowed = await self.session_budget.consume(
                        session_id, self.max_calls_per_session
                    )
                except Exception:
                    raise LLMUnavailableError("language model is unavailable") from None
                if not allowed:
                    self.metrics.call_limit_rejections += 1
                    raise LLMUnavailableError("language model call limit reached") from None

            deadline = started + timeout
            models = [model or self.model, self.fallback_model]
            for attempt, candidate_model in enumerate(models):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                if attempt:
                    self.metrics.retries += 1
                    delay = min(random.uniform(0.1, 0.3), max(0.0, remaining / 5))
                    await asyncio.sleep(delay)
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        break
                attempt_timeout = remaining if attempt else remaining * 0.65
                try:
                    response = await asyncio.wait_for(
                        self._generate(prompt, schema, candidate_model), timeout=attempt_timeout
                    )
                    value, prompt_tokens, response_tokens = self._decode_response(response)
                    self._validate_schema(value, schema)
                    self.metrics.prompt_tokens += prompt_tokens
                    self.metrics.response_tokens += response_tokens
                    self._record_success()
                    return value
                except Exception as exc:
                    if attempt or not self._retryable(exc):
                        break
            self._record_failure()
            failure_recorded = True
            raise LLMUnavailableError("language model is unavailable") from None
        except LLMUnavailableError:
            if not failure_recorded:
                self.metrics.failures += 1
            raise
        finally:
            self.metrics.latencies_ms.append((monotonic() - started) * 1000)
