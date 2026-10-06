"""Shared STT protocol and event types; transcripts are never logged here."""

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol

STTEventType = Literal["partial", "final", "speech_start", "speech_end", "unavailable"]


@dataclass(frozen=True, slots=True)
class STTEvent:
    """One provider-neutral speech or transcription event."""

    type: STTEventType
    text: str | None = None
    language_code: str | None = None
    confidence: float | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class STTTranscript:
    """Final one-shot transcription result."""

    text: str
    language_code: str
    confidence: float | None


class STTUnavailableError(RuntimeError):
    """Provider is unavailable or returned an invalid response."""


class STTProvider(Protocol):
    """Streaming and short-clip speech transcription contract."""

    name: str

    def stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncIterator[STTEvent]: ...

    async def transcribe_clip(
        self,
        audio: bytes,
        language_code: str,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
    ) -> STTTranscript: ...
