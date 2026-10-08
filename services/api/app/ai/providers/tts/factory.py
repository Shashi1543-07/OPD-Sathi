"""Select TTS with a safe Mock fallback when a live provider is unavailable."""

from app.ai.providers.tts.base import AudioClip, TTSProvider
from app.ai.providers.tts.google import GoogleTTS
from app.ai.providers.tts.mock import MockTTS
from app.ai.providers.tts.sarvam import SarvamTTS
from app.config import Settings


class FallbackTTS:
    name = "fallback"

    def __init__(self, primary: TTSProvider, fallback: TTSProvider) -> None:
        self.primary = primary
        self.fallback = fallback

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> AudioClip:
        try:
            return await self.primary.synthesize(text, lang, voice)
        except Exception:
            return await self.fallback.synthesize(text, lang, voice)


def build_tts_provider(settings: Settings) -> TTSProvider:
    if settings.provider_mode == "demo-safe":
        return MockTTS()
    if settings.resolved_tts_provider == "sarvam":
        primary: TTSProvider = SarvamTTS(api_key=settings.sarvam_api_key, model=settings.tts_model)
        fallback = FallbackTTS(GoogleTTS(), MockTTS())
        return FallbackTTS(primary, fallback)
    if settings.resolved_tts_provider == "google":
        return FallbackTTS(GoogleTTS(), MockTTS())
    return MockTTS()
