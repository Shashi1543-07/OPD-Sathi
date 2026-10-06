"""STT provider tests use synthetic audio and never contact Sarvam."""

import asyncio
import base64
import json

import httpx
import pytest

from app.ai.providers.stt import sarvam as sarvam_module
from app.ai.providers.stt.base import STTEvent, STTUnavailableError
from app.ai.providers.stt.chain import STTMetrics, STTProviderChain
from app.ai.providers.stt.factory import build_stt_provider
from app.ai.providers.stt.mock import MockSTT
from app.ai.providers.stt.sarvam import SarvamSTT
from app.config import Settings


async def _frames(*values: bytes):
    for value in values:
        yield value


@pytest.mark.asyncio
async def test_mock_streams_fixture_without_exposing_audio() -> None:
    mock = MockSTT(transcripts={"hi-IN": ["नमस्ते रोगी"]})
    events = [event async for event in mock.stream(_frames(b"synthetic"), "hi-IN")]
    assert [event.type for event in events] == ["partial", "partial", "final"]
    assert events[-1].text == "नमस्ते रोगी"
    assert events[-1].confidence == 0.95


@pytest.mark.asyncio
async def test_chain_opens_after_five_provider_failures_and_reports_unavailable() -> None:
    metrics = STTMetrics()
    primary = MockSTT(fail=True)
    chain = STTProviderChain(primary, MockSTT(), metrics=metrics)
    for _ in range(5):
        events = [event async for event in chain.stream(_frames(b"audio"), "en-IN")]
        assert events == [STTEvent(type="unavailable", code="provider_unavailable")]
    events = [event async for event in chain.stream(_frames(b"audio"), "en-IN")]
    assert events[0].type == "unavailable"
    assert metrics.failures == 5
    assert metrics.fallbacks == 6
    assert metrics.circuit_open == 1


def test_factory_keeps_mock_as_default_and_requires_sarvam_key() -> None:
    settings = Settings(_env_file=None)
    provider = build_stt_provider(settings)
    assert isinstance(provider, STTProviderChain)
    assert provider.primary is provider.fallback
    with pytest.raises(ValueError, match="SARVAM_API_KEY"):
        build_stt_provider(Settings(_env_file=None, stt_provider="sarvam"))
    live = build_stt_provider(
        Settings(_env_file=None, stt_provider="sarvam", sarvam_api_key="synthetic-key")
    )
    assert isinstance(live, STTProviderChain)
    assert live.fallback is None


@pytest.mark.asyncio
async def test_chain_never_substitutes_mock_transcript_after_live_failure() -> None:
    chain = STTProviderChain(MockSTT(fail=True), None)
    with pytest.raises(STTUnavailableError, match="unavailable"):
        await chain.transcribe_clip(b"audio", "en-IN")


@pytest.mark.asyncio
async def test_sarvam_realtime_auth_audio_framing_and_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    ended = asyncio.Event()

    class Socket:
        async def send(self, payload: str) -> None:
            sent.append(payload)
            if json.loads(payload)["event"] == "end":
                ended.set()

        def __aiter__(self):
            return self.messages()

        async def messages(self):
            await ended.wait()
            yield json.dumps({"event": "vad.speech_start"})
            yield json.dumps({"event": "transcript.partial", "text": "hello"})
            yield json.dumps(
                {"event": "transcript.final", "text": "hello there", "language_confidence": 0.9}
            )

    socket = Socket()

    class SocketContext:
        async def __aenter__(self):
            return socket

        async def __aexit__(self, *_args):
            return None

    captured: dict[str, object] = {}

    def fake_connect(url: str, **kwargs):
        captured.update(url=url, **kwargs)
        return SocketContext()

    monkeypatch.setattr(sarvam_module, "connect", fake_connect)
    provider = SarvamSTT(api_key="synthetic-key")
    events = [event async for event in provider.stream(_frames(b"\x01\x02"), "en-IN")]
    assert [event.type for event in events] == ["speech_start", "partial", "final"]
    assert events[-1].text == "hello there"
    assert "language_code=en-IN" in str(captured["url"])
    assert captured["additional_headers"] == {"Api-Subscription-Key": "synthetic-key"}
    assert json.loads(sent[0]) == {
        "event": "audio_input",
        "audio": base64.b64encode(b"\x01\x02").decode("ascii"),
    }
    assert json.loads(sent[-1]) == {"event": "end"}


@pytest.mark.asyncio
async def test_sarvam_clip_uses_multipart_and_validates_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.read()
        return httpx.Response(
            200, json={"transcript": "synthetic clip", "language_confidence": 0.8}
        )

    async_client = httpx.AsyncClient
    monkeypatch.setattr(
        sarvam_module.httpx,
        "AsyncClient",
        lambda **kwargs: async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    provider = SarvamSTT(api_key="synthetic-key")
    transcript = await provider.transcribe_clip(b"RIFFsynthetic", "mr-IN")
    assert transcript.text == "synthetic clip"
    assert transcript.confidence == 0.8
    assert captured["headers"]["api-subscription-key"] == "synthetic-key"
    assert b'name="file"' in captured["body"]


@pytest.mark.asyncio
async def test_sarvam_rejects_unsupported_language() -> None:
    provider = SarvamSTT(api_key="synthetic-key")
    with pytest.raises(STTUnavailableError, match="unsupported language"):
        [event async for event in provider.stream(_frames(b"audio"), "ta-IN")]
