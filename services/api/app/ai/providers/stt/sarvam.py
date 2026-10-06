"""Sarvam Realtime WebSocket and short-clip REST adapters."""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, AsyncIterable
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.ai.providers.stt.base import STTEvent, STTTranscript, STTUnavailableError

_REALTIME_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"
_REST_URL = "https://api.sarvam.ai/speech-to-text"
_MAX_CLIP_BYTES = 15 * 1024 * 1024
_SUPPORTED_LANGUAGES = {"en-IN", "hi-IN", "mr-IN"}


class SarvamSTT:
    """Use Sarvam Realtime for partials and REST for short one-shot clips."""

    name = "sarvam"

    def __init__(self, *, api_key: str, model: str = "saaras:v3-realtime") -> None:
        if not api_key.strip():
            raise ValueError("SARVAM_API_KEY is required for SarvamSTT")
        if model not in {"saaras:v3-realtime", "saaras:v4"}:
            raise ValueError("unsupported Sarvam realtime STT model")
        self.api_key = api_key
        self.model = model

    def _realtime_url(self, language_code: str) -> str:
        if language_code not in _SUPPORTED_LANGUAGES:
            raise STTUnavailableError("unsupported language for the configured STT flow")
        params = urlencode(
            {
                "language_code": language_code,
                "model": self.model,
                "stream_type": "fast",
                "mode": "transcribe",
                "endpointing": "vad",
                "encoding": "linear16",
                "sample_rate": "16000",
            }
        )
        return f"{_REALTIME_URL}?{params}"

    def stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncGenerator[STTEvent, None]:
        return self._stream(frames, language_code)

    async def _stream(
        self, frames: AsyncIterable[bytes], language_code: str
    ) -> AsyncGenerator[STTEvent, None]:
        url = self._realtime_url(language_code)
        try:
            async with connect(
                url,
                additional_headers={"Api-Subscription-Key": self.api_key},
                open_timeout=5,
                close_timeout=2,
                max_size=1_048_576,
            ) as socket:

                async def send_frames() -> None:
                    async for frame in frames:
                        if frame:
                            await socket.send(
                                json.dumps(
                                    {
                                        "event": "audio_input",
                                        "audio": base64.b64encode(frame).decode("ascii"),
                                    },
                                    separators=(",", ":"),
                                )
                            )
                    await socket.send(json.dumps({"event": "end"}))

                sender = asyncio.create_task(send_frames())
                try:
                    async for raw_message in socket:
                        try:
                            message = json.loads(raw_message)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise STTUnavailableError(
                                "Sarvam returned malformed event data"
                            ) from exc
                        event = self._event(message, language_code)
                        if event is not None:
                            yield event
                            if event.type == "final":
                                break
                finally:
                    if not sender.done():
                        sender.cancel()
                    try:
                        await sender
                    except asyncio.CancelledError:
                        pass
        except STTUnavailableError:
            raise
        except (OSError, TimeoutError, WebSocketException, httpx.HTTPError) as exc:
            raise STTUnavailableError("Sarvam realtime STT is unavailable") from exc

    @staticmethod
    def _event(message: object, language_code: str) -> STTEvent | None:
        if not isinstance(message, dict):
            raise STTUnavailableError("Sarvam returned an invalid event")
        event_type = message.get("event")
        text = message.get("text")
        if event_type == "transcript.partial":
            return STTEvent(
                type="partial",
                text=text if isinstance(text, str) else "",
                language_code=language_code,
            )
        if event_type == "transcript.final":
            confidence = message.get("language_confidence")
            return STTEvent(
                type="final",
                text=text if isinstance(text, str) else "",
                language_code=language_code,
                confidence=float(confidence)
                if isinstance(confidence, (float, int)) and not isinstance(confidence, bool)
                else None,
            )
        if event_type == "vad.speech_start":
            return STTEvent(type="speech_start", language_code=language_code)
        if event_type == "vad.speech_end":
            return STTEvent(type="speech_end", language_code=language_code)
        if event_type == "error":
            raise STTUnavailableError("Sarvam realtime STT returned an error")
        return None

    async def transcribe_clip(
        self,
        audio: bytes,
        language_code: str,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
    ) -> STTTranscript:
        if language_code not in _SUPPORTED_LANGUAGES:
            raise STTUnavailableError("unsupported language for the configured STT flow")
        if not audio or len(audio) > _MAX_CLIP_BYTES:
            raise STTUnavailableError("audio clip is empty or exceeds the size limit")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                response = await client.post(
                    _REST_URL,
                    headers={"Api-Subscription-Key": self.api_key},
                    data={
                        "model": "saaras:v4",
                        "language_code": language_code,
                        "mode": "transcribe",
                    },
                    files={"file": (filename, audio, content_type)},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise STTUnavailableError("Sarvam clip STT is unavailable") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("transcript"), str):
            raise STTUnavailableError("Sarvam returned an invalid clip transcript")
        confidence = payload.get("language_confidence")
        return STTTranscript(
            text=payload["transcript"],
            language_code=language_code,
            confidence=float(confidence)
            if isinstance(confidence, (float, int)) and not isinstance(confidence, bool)
            else None,
        )
