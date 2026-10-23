"""Short-clip speech recognition and prompt speech with provider fallback.

The kiosk records one answer at a time (a few seconds of audio), so a request/response
flow is simpler and more reliable than streaming. Providers are tried in order:
Sarvam (best for Indian languages) -> Gemini (multimodal) -> none, in which case the
kiosk browser's own speech features or typing are used.
"""

import base64
import hashlib
from collections.abc import Awaitable
from dataclasses import dataclass
from time import monotonic
from typing import Any

import structlog

from app.ai.providers.llm.gemini import GeminiProvider
from app.ai.providers.stt.sarvam import SarvamSTT
from app.ai.providers.tts.base import AudioClip, pad_with_silence
from app.ai.providers.tts.sarvam import SarvamTTS
from app.config import Settings
from app.core import metrics

_LOGGER = structlog.get_logger(__name__)
_LOCALES = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}
_PROMPT_CACHE_SECONDS = 7 * 24 * 60 * 60


class SpeechUnavailableError(RuntimeError):
    """No configured provider could complete the speech request."""


async def _measured[Result](provider: str, call: Awaitable[Result]) -> Result:
    """Count a speech provider call by outcome and time it (task 7.9.1)."""
    started = monotonic()
    try:
        result = await call
    except Exception:
        metrics.AI_CALLS.labels(provider, "failed").inc()
        raise
    finally:
        metrics.AI_DURATION.labels(provider).observe(monotonic() - started)
    metrics.AI_CALLS.labels(provider, "succeeded").inc()
    return result


@dataclass(frozen=True, slots=True)
class Transcript:
    text: str
    provider: str


class SpeechService:
    """Select configured speech providers once at startup."""

    def __init__(self, settings: Settings, llm: object) -> None:
        self._gemini = llm if isinstance(llm, GeminiProvider) else None
        demo_safe = settings.provider_mode == "demo-safe"
        stt = "browser" if demo_safe else settings.resolved_stt_provider
        tts = "browser" if demo_safe else settings.resolved_tts_provider
        self._sarvam_stt = (
            SarvamSTT(api_key=settings.sarvam_api_key, model="saaras:v4")
            if stt == "sarvam" and settings.sarvam_api_key.strip()
            else None
        )
        self._sarvam_tts = (
            SarvamTTS(api_key=settings.sarvam_api_key, model=settings.tts_model)
            if tts == "sarvam" and settings.sarvam_api_key.strip()
            else None
        )
        self._tts_voice = settings.tts_voice or None
        self._stt_gemini = stt in {"sarvam", "gemini"} and self._gemini is not None
        self._tts_gemini = tts in {"sarvam", "gemini"} and self._gemini is not None
        self.stt_mode = (
            "sarvam" if self._sarvam_stt else "gemini" if self._stt_gemini else "browser"
        )
        self.tts_mode = (
            "sarvam" if self._sarvam_tts else "gemini" if self._tts_gemini else "browser"
        )

    @property
    def server_stt(self) -> bool:
        return self.stt_mode != "browser"

    @property
    def server_tts(self) -> bool:
        return self.tts_mode != "browser"

    async def transcribe(
        self,
        audio: bytes,
        *,
        mime_type: str,
        language: str,
        context: str = "",
        session_id: str | None = None,
    ) -> Transcript:
        locale = _LOCALES.get(language)
        if locale is None:
            raise SpeechUnavailableError("unsupported language")
        if self._sarvam_stt is not None:
            try:
                extension = next(
                    (kind for kind in ("webm", "ogg", "mp4") if kind in mime_type), "wav"
                )
                result = await _measured(
                    "sarvam_stt",
                    self._sarvam_stt.transcribe_clip(
                        audio,
                        locale,
                        filename=f"answer.{extension}",
                        content_type=mime_type.split(";")[0],
                    ),
                )
                if result.text.strip():
                    return Transcript(result.text.strip(), "sarvam")
            except Exception as exc:
                _LOGGER.warning("stt_provider_failed", provider="sarvam", error=type(exc).__name__)
        if self._stt_gemini and self._gemini is not None:
            try:
                text = await self._gemini.transcribe(
                    audio,
                    mime_type=mime_type.split(";")[0],
                    language=language,
                    context=context,
                    session_id=session_id,
                )
                return Transcript(text, "gemini")
            except Exception as exc:
                _LOGGER.warning("stt_provider_failed", provider="gemini", error=type(exc).__name__)
        raise SpeechUnavailableError("speech recognition is unavailable")

    async def synthesize(self, text: str, *, language: str, cache: Any | None = None) -> AudioClip:
        """Speak ``text``; pass ``cache`` only for fixed, non-patient prompt text."""
        if not text.strip() or language not in _LOCALES:
            raise SpeechUnavailableError("nothing to speak")
        cache_key = None
        if cache is not None:
            digest = hashlib.sha256(
                f"{self.tts_mode}|{self._tts_voice}|{language}|{text}".encode()
            ).hexdigest()
            # v2: clips now carry lead/tail silence; v1 entries were clipped on playback.
            cache_key = f"osa:tts:v2:{digest}"
            try:
                cached = await cache.get(cache_key)
                if isinstance(cached, str) and ":" in cached:
                    media_type, encoded = cached.split(":", 1)
                    return AudioClip(base64.b64decode(encoded), media_type)
            except Exception:  # The cache is best-effort; speak without it.
                _LOGGER.debug("tts_cache_read_failed")
        clip = await self._synthesize_uncached(text[:2400], language)
        if clip.media_type == "audio/wav":
            clip = AudioClip(pad_with_silence(clip.content), clip.media_type)
        if cache is not None and cache_key is not None:
            try:
                await cache.set(
                    cache_key,
                    f"{clip.media_type}:{base64.b64encode(clip.content).decode('ascii')}",
                    ex=_PROMPT_CACHE_SECONDS,
                )
            except Exception:
                _LOGGER.debug("tts_cache_write_failed")
        return clip

    async def _synthesize_uncached(self, text: str, language: str) -> AudioClip:
        if self._sarvam_tts is not None:
            try:
                return await _measured(
                    "sarvam_tts", self._sarvam_tts.synthesize(text, language, self._tts_voice)
                )
            except Exception as exc:
                _LOGGER.warning("tts_provider_failed", provider="sarvam", error=type(exc).__name__)
        if self._tts_gemini and self._gemini is not None:
            try:
                wav = await _measured(
                    "gemini_tts", self._gemini.synthesize_speech(text, language=language)
                )
                return AudioClip(wav, "audio/wav")
            except Exception as exc:
                _LOGGER.warning("tts_provider_failed", provider="gemini", error=type(exc).__name__)
        raise SpeechUnavailableError("speech synthesis is unavailable")
