"""Demo-safe mode stays local even when live providers are configured."""

import asyncio

from app.ai.providers.llm.demo_safe import DemoSafeLLM
from app.ai.providers.llm.factory import build_llm_provider
from app.ai.providers.stt.chain import STTProviderChain
from app.ai.providers.stt.factory import build_stt_provider
from app.ai.providers.stt.mock import MockSTT
from app.ai.providers.tts.factory import build_tts_provider
from app.ai.providers.tts.mock import MockTTS
from app.config import Settings


def test_demo_safe_mode_forces_local_providers_and_selected_story() -> None:
    settings = Settings(
        provider_mode="demo-safe",
        demo_story="marathi-chest-pain",
        stt_provider="sarvam",
        tts_provider="google",
        llm_provider="gemini",
    )

    stt = build_stt_provider(settings)
    llm = build_llm_provider(settings)
    tts = build_tts_provider(settings)

    assert isinstance(stt, STTProviderChain)
    assert isinstance(stt.primary, MockSTT)
    assert stt.primary.transcripts["mr"] == ("छातीत दुखतंय आणि घाम येतोय",)
    assert isinstance(llm, DemoSafeLLM)
    assert isinstance(tts, MockTTS)


def test_demo_safe_story_transcript_is_replayed_without_network() -> None:
    provider = build_stt_provider(
        Settings(provider_mode="demo-safe", demo_story="english-child-cough")
    )

    async def transcript() -> str:
        assert isinstance(provider, STTProviderChain)
        result = await provider.transcribe_clip(b"synthetic audio", "en")
        return result.text

    assert asyncio.run(transcript()) == "My child has had a cough for two days"


def test_demo_safe_llm_replays_schema_bounded_saved_fixtures() -> None:
    provider = build_llm_provider(Settings(provider_mode="demo-safe", llm_provider="gemini"))

    async def response() -> dict[str, object]:
        return await provider.generate_json(
            "synthetic prompt",
            {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string", "enum": ["q1", "q2"]},
                    "flags": {"type": "array"},
                },
                "required": ["question_id", "flags"],
            },
        )

    assert asyncio.run(response()) == {"question_id": "q1", "flags": []}
