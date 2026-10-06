"""TTS provider boundary and a small circuit breaker for transient vendor outages."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class AudioClip:
    content: bytes
    media_type: str


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
