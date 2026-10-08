"""Construct the configured STT chain from application settings."""

from app.ai.providers.stt.base import STTProvider
from app.ai.providers.stt.chain import STTProviderChain
from app.ai.providers.stt.mock import MockSTT
from app.ai.providers.stt.sarvam import SarvamSTT
from app.config import Settings


def build_stt_provider(settings: Settings) -> STTProvider:
    """Keep Mock as the safe default; require a key for explicit Sarvam use."""
    if settings.provider_mode == "demo-safe":
        # WebSocket callers send locale tags; retain short tags for clip callers.
        story_transcripts = {
            "hi": (
                "\u092a\u0947\u091f \u092e\u0947\u0902 \u0926\u0930\u094d\u0926 "
                "\u0914\u0930 \u092c\u0941\u0916\u093e\u0930 "
                "\u0924\u0940\u0928 \u0926\u093f\u0928 \u0938\u0947",
            ),
            "mr": (
                "\u091b\u093e\u0924\u0940\u0924 \u0926\u0941\u0916\u0924\u0902\u092f "
                "\u0906\u0923\u093f \u0918\u093e\u092e \u092f\u0947\u0924\u094b\u092f",
            ),
            "en": ("My child has had a cough for two days",),
        }
        selected_language = {
            "hindi-fever": "hi",
            "marathi-chest-pain": "mr",
            "english-child-cough": "en",
        }[settings.demo_story]
        transcripts: dict[str, tuple[str, ...] | list[str]] = {
            "hi": (
                "\u092e\u0948\u0902 \u0920\u0940\u0915 \u0928\u0939\u0940\u0902 "
                "\u092e\u0939\u0938\u0942\u0938 \u0915\u0930 \u0930\u0939\u093e \u0939\u0942\u0901",
            ),
            "mr": (
                "\u092e\u0932\u093e \u092c\u0930\u0947 \u0935\u093e\u091f\u0924 "
                "\u0928\u093e\u0939\u0940",
            ),
            "en": ("I am not feeling well",),
        }
        transcripts[selected_language] = story_transcripts[selected_language]
        transcripts.update(
            {
                "hi-IN": transcripts["hi"],
                "mr-IN": transcripts["mr"],
                "en-IN": transcripts["en"],
            }
        )
        fixture = MockSTT(transcripts=transcripts)
        return STTProviderChain(fixture, fixture)
    fallback = MockSTT()
    if settings.resolved_stt_provider != "sarvam":
        # Gemini and browser speech use the clip endpoint, not this streaming socket.
        return STTProviderChain(fallback, fallback)
    if not settings.sarvam_api_key.strip():
        raise ValueError("SARVAM_API_KEY is required when STT_PROVIDER=sarvam")
    return STTProviderChain(
        SarvamSTT(api_key=settings.sarvam_api_key, model=settings.stt_model), None
    )
