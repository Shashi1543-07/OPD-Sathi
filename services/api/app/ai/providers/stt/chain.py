"""Provider selection, a small circuit breaker, and privacy-safe counters."""

from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from time import monotonic

from app.ai.providers.stt.base import STTEvent, STTProvider, STTTranscript, STTUnavailableError


@dataclass(slots=True)
class STTMetrics:
    """Aggregate counters only; never store audio, transcript, or patient identifiers."""

    requests: int = 0
    successes: int = 0
    failures: int = 0
    fallbacks: int = 0
    circuit_open: int = 0


@dataclass(slots=True)
class _Circuit:
    failures: int = 0
    opened_at: float | None = None


class STTProviderChain:
    """Use the configured provider and fall back safely when it is unavailable."""

    name = "chain"

    def __init__(
        self,
        primary: STTProvider,
        fallback: STTProvider | None,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        metrics: STTMetrics | None = None,
    ) -> None:
        if failure_threshold < 1 or reset_seconds <= 0:
            raise ValueError("circuit breaker limits must be positive")
        self.primary = primary
        self.fallback = fallback
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.metrics = metrics or STTMetrics()
        self._circuit = _Circuit()

    def _primary_available(self) -> bool:
        opened_at = self._circuit.opened_at
        if opened_at is None:
            return True
        if monotonic() - opened_at < self.reset_seconds:
            self.metrics.circuit_open += 1
            return False
        # Permit a single probe after the cool-down.
        self._circuit.opened_at = None
        return True

    def _record_success(self) -> None:
        self._circuit.failures = 0
        self._circuit.opened_at = None
        self.metrics.successes += 1

    def _record_failure(self) -> None:
        self.metrics.failures += 1
        self._circuit.failures += 1
        if self._circuit.failures >= self.failure_threshold:
            self._circuit.opened_at = monotonic()

    def stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncGenerator[STTEvent, None]:
        return self._stream(frames, language_code)

    async def _stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncGenerator[STTEvent, None]:
        self.metrics.requests += 1
        # Live audio cannot be replayed safely after a provider consumed it. On
        # failure, signal the client to offer touch/typing rather than fabricate.
        if not self._primary_available():
            self.metrics.fallbacks += 1
            yield STTEvent(type="unavailable", code="provider_unavailable")
            return
        try:
            async for event in self.primary.stream(frames, language_code):
                if event.type == "final":
                    self._record_success()
                yield event
        except STTUnavailableError:
            self._record_failure()
            self.metrics.fallbacks += 1
            yield STTEvent(type="unavailable", code="provider_unavailable")

    async def transcribe_clip(
        self,
        audio: bytes,
        language_code: str,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
    ) -> STTTranscript:
        self.metrics.requests += 1
        if self._primary_available():
            try:
                result = await self.primary.transcribe_clip(
                    audio,
                    language_code,
                    filename=filename,
                    content_type=content_type,
                )
                self._record_success()
                return result
            except STTUnavailableError:
                self._record_failure()
        self.metrics.fallbacks += 1
        if self.fallback is None:
            raise STTUnavailableError("speech transcription is unavailable") from None
        try:
            result = await self.fallback.transcribe_clip(audio, language_code)
        except STTUnavailableError:
            raise STTUnavailableError("speech transcription is unavailable") from None
        self.metrics.successes += 1
        return result


@dataclass(slots=True)
class STTMetricsRegistry:
    """Metrics keyed only by provider name, suitable for basic health reporting."""

    providers: dict[str, STTMetrics] = field(default_factory=dict)
