"""Generate a short, valid WAV tone for local development and CI."""

import io
import math
import struct
import wave

from app.ai.providers.tts.base import AudioClip, CircuitBreaker


class MockTTS:
    name = "mock"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.circuit = CircuitBreaker()

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> AudioClip:
        del lang, voice
        if not text.strip():
            raise ValueError("TTS text must not be empty")

        async def produce() -> AudioClip:
            if self.fail:
                raise RuntimeError("mock TTS unavailable")
            sample_rate = 16_000
            sample_count = int(sample_rate * 0.12)
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(
                    b"".join(
                        struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
                        for i in range(sample_count)
                    )
                )
            return AudioClip(output.getvalue(), "audio/wav")

        return await self.circuit.call(produce)
