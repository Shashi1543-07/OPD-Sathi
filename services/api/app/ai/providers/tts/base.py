"""TTS provider boundary and a small circuit breaker for transient vendor outages."""

import asyncio
import io
import time
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")

# Speech services end the audio within ~50 ms of the last syllable. Browsers on Windows
# release the audio device as soon as playback "ends", before the speakers have played
# the final buffer, and a sleeping output device swallows the first few milliseconds, so
# the start and especially the end of a sentence were cut off. Silence on both sides
# absorbs that.
LEAD_SILENCE_MS = 150
TAIL_SILENCE_MS = 500


@dataclass(frozen=True)
class AudioClip:
    content: bytes
    media_type: str


def pad_with_silence(
    wav_bytes: bytes, *, lead_ms: int = LEAD_SILENCE_MS, tail_ms: int = TAIL_SILENCE_MS
) -> bytes:
    """Add silence before and after PCM WAV audio; anything else is returned unchanged."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as source:
            params = source.getparams()
            frames = source.readframes(params.nframes)
    except (wave.Error, EOFError):
        return wav_bytes
    if params.comptype != "NONE":
        return wav_bytes
    frame_size = params.nchannels * params.sampwidth
    # 8-bit WAV is unsigned, so its silence is 0x80 rather than 0x00.
    silent = b"\x80" if params.sampwidth == 1 else b"\x00"

    def silence(milliseconds: int) -> bytes:
        return silent * (params.framerate * milliseconds // 1000) * frame_size

    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(params.nchannels)
        target.setsampwidth(params.sampwidth)
        target.setframerate(params.framerate)
        target.writeframes(silence(lead_ms) + frames + silence(tail_ms))
    return output.getvalue()


class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> AudioClip: ...


class CircuitBreaker:
    def __init__(self, *, threshold: int = 3, reset_after: float = 15.0) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = asyncio.Lock()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            if self.opened_at is not None:
                if time.monotonic() - self.opened_at < self.reset_after:
                    raise RuntimeError("TTS provider circuit is open")
                self.opened_at = None
                self.failures = 0
        try:
            result = await operation()
        except Exception:
            async with self._lock:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = time.monotonic()
            raise
        async with self._lock:
            self.failures = 0
            self.opened_at = None
        return result
