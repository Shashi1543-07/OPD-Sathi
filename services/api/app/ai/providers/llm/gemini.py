"""Async Gemini JSON generation with bounded retries and a privacy-safe circuit."""

import asyncio
import hashlib
import io
import json
import random
import wave
from collections.abc import Callable, Mapping
from time import monotonic
from typing import Any, Protocol, cast

import structlog

from app.ai.output_guard import validate_safe_output
from app.ai.providers.llm.base import (
    LLMMetrics,
    LLMUnavailableError,
    SessionCallBudget,
)
from app.ai.providers.llm.redaction import redact_pii
from app.core import metrics as prometheus

# Interactive callers pass their own small budgets; background summaries may wait longer.
MAX_TIMEOUT_SECONDS = 20.0
MAX_OUTPUT_TOKENS = 1024
SESSION_TTL_SECONDS = 2 * 60 * 60
_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "mr": "Marathi"}
_TRANSCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"transcript": {"type": "string", "maxLength": 1000}},
    "required": ["transcript"],
    "additionalProperties": False,
}

Guard = Callable[[Any], None]
_LOGGER = structlog.get_logger(__name__)


class _AsyncModels(Protocol):
    async def generate_content(self, *, model: str, contents: Any, config: object) -> object: ...


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
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
        metrics: LLMMetrics | None = None,
        client: _AsyncClient | None = None,
        tts_model: str = "gemini-3.8-flash-tts",
        tts_voice: str = "Kore",
        thinking_level: str | None = "minimal",
    ) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if not model.strip() or not fallback_model.strip():
            raise ValueError("LLM_MODEL and LLM_FALLBACK_MODEL must be configured")
        if not 1 <= max_calls_per_session <= 30:
            raise ValueError("per-session call cap must be between 1 and 30")
        if not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError("max output tokens must be between 1 and 1024")
        self.model = model
        self.fallback_model = fallback_model
        self.session_budget = session_budget
        self.max_calls_per_session = max_calls_per_session
        self.max_output_tokens = max_output_tokens
        self.metrics = metrics or LLMMetrics()
        self.tts_model = tts_model
        self.tts_voice = tts_voice
        self.thinking_level = thinking_level or None
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

    def _thinking(self) -> Any:
        from google.genai import types

        if not self.thinking_level:
            return None
        return types.ThinkingConfig(thinking_level=self.thinking_level.upper())

    async def _generate(
        self, prompt: str | list[Any], schema: Mapping[str, Any], model: str
    ) -> object:
        from google.genai import types

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=dict(schema),
            max_output_tokens=self.max_output_tokens,
            thinking_config=self._thinking(),
        )
        contents = redact_pii(prompt) if isinstance(prompt, str) else prompt
        try:
            return await self._client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as exc:
            status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if self.thinking_level and status == 400 and "think" in str(exc).lower():
                # Some models reject "minimal"; step down to "low" (still fast) before giving
                # up on the limit entirely, which can make calls take tens of seconds.
                self.thinking_level = "low" if self.thinking_level == "minimal" else None
                config.thinking_config = self._thinking()
                return await self._client.models.generate_content(
                    model=model, contents=contents, config=config
                )
            raise

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
        prompt: str | list[Any],
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
        guard: Guard | None = validate_safe_output,
    ) -> dict[str, Any]:
        """Return schema-valid JSON; ``guard`` re-checks content (None only for transcripts)."""
        self.metrics.requests += 1
        started = monotonic()
        timeout = min(timeout, MAX_TIMEOUT_SECONDS)
        if timeout <= 0:
            self._record_failure()
            raise LLMUnavailableError("language model is unavailable") from None
        failure_recorded = False
        outcome = "failed"
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
                    delay = min(random.SystemRandom().uniform(0.1, 0.3), max(0.0, remaining / 5))
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
                    if guard is not None:
                        guard(value)
                    self.metrics.prompt_tokens += prompt_tokens
                    self.metrics.response_tokens += response_tokens
                    self._record_success()
                    outcome = "succeeded"
                    return value
                except Exception as exc:
                    # Log only the error class and status; never prompt or output content.
                    _LOGGER.warning(
                        "llm_call_failed",
                        model=candidate_model,
                        error_type=type(exc).__name__,
                        status=getattr(exc, "code", None) or getattr(exc, "status_code", None),
                    )
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
            elapsed = monotonic() - started
            self.metrics.latencies_ms.append(elapsed * 1000)
            prometheus.AI_CALLS.labels("gemini", outcome).inc()
            prometheus.AI_DURATION.labels("gemini").observe(elapsed)

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str,
        language: str,
        context: str = "",
        timeout: float = 12.0,
        session_id: str | None = None,
    ) -> str:
        """Transcribe one short patient clip verbatim in the patient's language and script."""
        from google.genai import types

        language_name = _LANGUAGE_NAMES.get(language, "English")
        script = "Devanagari script" if language in {"hi", "mr"} else "Latin script"
        instructions = (
            "You are a medical-intake speech transcriber at an Indian government hospital. "
            f"The patient is answering in {language_name} (they may mix in Hindi or English "
            f"words). Write exactly what was said, in {script}, without translating, "
            "summarising, correcting medical facts, or adding anything. If the clip has no "
            "intelligible speech, return an empty transcript."
        )
        if context:
            instructions += f"\nThe question the patient heard was: {context[:300]}"
        parts = [types.Part.from_bytes(data=audio, mime_type=mime_type), instructions]
        result = await self.generate_json(
            parts, _TRANSCRIPT_SCHEMA, timeout=timeout, session_id=session_id, guard=None
        )
        return str(result.get("transcript", "")).strip()

    async def synthesize_speech(self, text: str, *, language: str, timeout: float = 15.0) -> bytes:
        """Return WAV audio of ``text`` read in a calm voice, or raise LLMUnavailableError."""
        from google.genai import types

        language_name = _LANGUAGE_NAMES.get(language, "English")
        prompt = (
            f"Read the following {language_name} text aloud for a patient in a hospital, "
            f"in a warm, calm, unhurried voice:\n{text}"
        )
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.tts_voice)
                )
            ),
        )
        # Speech has its own failure handling so a TTS outage never opens the JSON circuit.
        try:
            response = await asyncio.wait_for(
                self._client.models.generate_content(
                    model=self.tts_model, contents=prompt, config=config
                ),
                timeout=min(timeout, MAX_TIMEOUT_SECONDS),
            )
            pcm = _first_inline_audio(response)
        except Exception:
            raise LLMUnavailableError("speech synthesis is unavailable") from None
        return _pcm_to_wav(pcm)


def _first_inline_audio(response: object) -> bytes:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if isinstance(data, (bytes, bytearray)) and data:
                return bytes(data)
    raise ValueError("no audio in response")


def _pcm_to_wav(pcm: bytes, *, sample_rate: int = 24_000) -> bytes:
    """Gemini TTS returns raw 16-bit mono PCM; browsers need a WAV container."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()
