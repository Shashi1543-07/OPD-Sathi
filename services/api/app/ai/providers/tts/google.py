"""Google Cloud Text-to-Speech REST adapter using an access/API key from settings."""

import asyncio
import base64
from typing import Any, Protocol, cast

import httpx

from app.ai.providers.tts.base import AudioClip, CircuitBreaker


class CredentialsLike(Protocol):
    valid: bool
    token: str | None

    def refresh(self, request: Any) -> None: ...


def _load_default_credentials() -> CredentialsLike:
    """Load Google credentials in a worker thread without exposing provider internals."""
    import google.auth

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return cast(CredentialsLike, credentials)


class GoogleTTS:
    name = "google"
    endpoint = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def __init__(
        self,
        *,
        credentials: CredentialsLike | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.credentials = credentials
        self.client = client
        self.circuit = CircuitBreaker()

    async def _access_token(self) -> str:
        credentials = self.credentials
        if credentials is None:
            credentials = await asyncio.to_thread(_load_default_credentials)
        if not credentials.valid:
            from google.auth.transport.requests import Request as GoogleAuthRequest

            await asyncio.to_thread(credentials.refresh, GoogleAuthRequest())
        if not credentials.token:
            raise RuntimeError("Google authentication did not return an access token")
        return str(credentials.token)

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> AudioClip:
        language_code = {"en": "en-IN", "hi": "hi-IN", "mr": "mr-IN"}.get(lang)
        if language_code is None:
            raise ValueError("unsupported TTS language")

        async def request() -> AudioClip:
            own_client = self.client is None
            client = self.client or httpx.AsyncClient(timeout=10.0)
            try:
                access_token = await self._access_token()
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "input": {"text": text},
                        "voice": {
                            "languageCode": language_code,
                            **({"name": voice} if voice else {}),
                        },
                        "audioConfig": {"audioEncoding": "MP3"},
                    },
                )
                response.raise_for_status()
                payload: Any = response.json()
                audio = payload.get("audioContent") if isinstance(payload, dict) else None
                if not isinstance(audio, str) or not audio:
                    raise RuntimeError("TTS provider returned no audio")
                return AudioClip(base64.b64decode(audio, validate=True), "audio/mpeg")
            finally:
                if own_client:
                    await client.aclose()

        return await self.circuit.call(request)
