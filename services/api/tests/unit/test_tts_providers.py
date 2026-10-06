import base64
import io
import json
import wave

import httpx
import pytest

from app.ai.providers.tts.base import AudioClip, CircuitBreaker
from app.ai.providers.tts.google import GoogleTTS
from app.ai.providers.tts.mock import MockTTS
from app.ai.providers.tts.sarvam import SarvamTTS


class StaticCredentials:
    def __init__(self) -> None:
        self.token = "synthetic-access-token"
        self.valid = True

    def refresh(self, request: object) -> None:
        raise AssertionError("valid synthetic credentials must not refresh")


@pytest.mark.asyncio
async def test_mock_tts_returns_short_wav_tone() -> None:
    clip = await MockTTS().synthesize("Synthetic text", "en")
    assert clip.media_type == "audio/wav"
    with wave.open(io.BytesIO(clip.content), "rb") as wav:
        assert wav.getnframes() / wav.getframerate() <= 0.2


@pytest.mark.asyncio
async def test_sarvam_adapter_decodes_audio_and_sends_current_request_shape() -> None:
    audio = b"synthetic-audio"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"audios": [base64.b64encode(audio).decode()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        clip = await SarvamTTS(api_key="synthetic-key", client=client).synthesize(
            "synthetic text", "hi"
        )
    assert clip == AudioClip(audio, "audio/wav")
    assert requests[0].url.path == "/text-to-speech"
    assert requests[0].headers["api-subscription-key"] == "synthetic-key"
    payload = json.loads(requests[0].content)
    assert payload["language_code"] == "hi-IN"
    assert payload["model"] == "bulbul:v3"
    assert "output_audio_codec" not in payload


@pytest.mark.asyncio
async def test_google_adapter_decodes_audio_and_uses_mp3_request() -> None:
    audio = b"synthetic-audio"
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"audioContent": base64.b64encode(audio).decode()})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        clip = await GoogleTTS(credentials=StaticCredentials(), client=client).synthesize(
            "synthetic text", "mr"
        )
    assert clip == AudioClip(audio, "audio/mpeg")
    assert requests[0].headers["authorization"] == "Bearer synthetic-access-token"
    assert json.loads(requests[0].content)["voice"]["languageCode"] == "mr-IN"
    assert json.loads(requests[0].content)["audioConfig"]["audioEncoding"] == "MP3"


@pytest.mark.asyncio
async def test_circuit_opens_after_three_provider_failures() -> None:
    circuit = CircuitBreaker(threshold=3)

    async def fail() -> bytes:
        raise RuntimeError("synthetic outage")

    for _ in range(3):
        with pytest.raises(RuntimeError, match="synthetic outage"):
            await circuit.call(fail)
    with pytest.raises(RuntimeError, match="circuit is open"):
        await circuit.call(fail)
