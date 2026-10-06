"""Construct the configured STT chain from application settings."""

from app.ai.providers.stt.base import STTProvider
from app.ai.providers.stt.chain import STTProviderChain
from app.ai.providers.stt.mock import MockSTT
from app.ai.providers.stt.sarvam import SarvamSTT
from app.config import Settings


def build_stt_provider(settings: Settings) -> STTProvider:
    """Keep Mock as the safe default; require a key for explicit Sarvam use."""
    fallback = MockSTT()
    if settings.stt_provider == "mock":
        return STTProviderChain(fallback, fallback)
    if not settings.sarvam_api_key.strip():
        raise ValueError("SARVAM_API_KEY is required when STT_PROVIDER=sarvam")
    return STTProviderChain(
        SarvamSTT(api_key=settings.sarvam_api_key, model=settings.stt_model), None
    )
