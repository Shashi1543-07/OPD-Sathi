"""Sarvam Bulbul TTS REST adapter. Patient text is sent only at request time."""

import base64
from typing import Any

import httpx

from app.ai.providers.tts.base import AudioClip, CircuitBreaker


class SarvamTTS:
    name = "sarvam"
    endpoint = "https://api.sarvam.ai/text-to-speech"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "bulbul:v3",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("SARVAM_API_KEY is required for Sarvam TTS")
        self.api_key = api_key
        self.model = model or "bulbul:v3"
        self.client = client
        self.circuit = CircuitBreaker()

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> AudioClip:
        if len(text) > 2500:
            raise ValueError("Sarvam TTS text exceeds 2500 characters")
        language_code = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}.get(lang)
        if language_code is None:
            raise ValueError("unsupported TTS language")

        async def request() -> AudioClip:
            own_client = self.client is None
            client = self.client or httpx.AsyncClient(timeout=10.0)
            try:
                response = await client.post(
                    self.endpoint,
                    headers={"api-subscription-key": self.api_key},
                    json={
                        "text": text,
                        "language_code": language_code,
                        "model": self.model,
                        "speaker": voice or "shubh",
                    },
                )
                response.raise_for_status()
                payload: Any = response.json()
                audio_list = payload.get("audios") if isinstance(payload, dict) else None
                if not isinstance(audio_list, list) or not audio_list:
                    raise RuntimeError("TTS provider returned no audio")
                # Sarvam's REST endpoint defaults to WAV; keep the declared
                # response media type aligned with that default.
                return AudioClip(base64.b64decode(audio_list[0], validate=True), "audio/wav")
            finally:
                if own_client:
                    await client.aclose()

        return await self.circuit.call(request)
