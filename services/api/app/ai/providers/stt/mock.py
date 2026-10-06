"""Fixture-driven local STT provider for CI and safe demos."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

from app.ai.providers.stt.base import STTEvent, STTTranscript, STTUnavailableError


class MockSTT:
    """Return configured synthetic text without contacting an external service."""

    name = "mock"

    def __init__(
        self,
        *,
        transcripts: dict[str, tuple[str, ...] | list[str]] | None = None,
        delay_seconds: float = 0,
        fail: bool = False,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self.transcripts = transcripts or {}
        self.delay_seconds = delay_seconds
        self.fail = fail

    def _transcript(self, language_code: str) -> str:
        values = self.transcripts.get(language_code, ())
        if not values:
            return "Synthetic voice transcript"
        return values[0]

    def stream(self, frames: AsyncIterable[bytes], language_code: str) -> AsyncIterator[STTEvent]:
        return self._stream(frames, language_code)

    async def _stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncIterator[STTEvent]:
        received_audio = False
        async for frame in frames:
            if frame:
                received_audio = True
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise STTUnavailableError("mock provider failure")
        if not received_audio:
            raise STTUnavailableError("no audio frames received")
        transcript = self._transcript(language_code)
        midpoint = max(1, len(transcript) // 2)
        yield STTEvent(type="partial", text=transcript[:midpoint], language_code=language_code)
        yield STTEvent(type="partial", text=transcript, language_code=language_code)
        yield STTEvent(
            type="final",
            text=transcript,
            language_code=language_code,
            confidence=0.95,
        )

    async def transcribe_clip(
        self,
        audio: bytes,
        language_code: str,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
    ) -> STTTranscript:
        del filename, content_type
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise STTUnavailableError("mock provider failure")
        if not audio:
            raise STTUnavailableError("audio clip is empty")
        return STTTranscript(
            text=self._transcript(language_code), language_code=language_code, confidence=0.95
        )
