"""Cache fixed generic UI text; this cache cannot accept prompts or patient responses."""

from collections.abc import Mapping
from typing import Literal, Protocol

GenericTextKey = Literal["ai_assisted_label", "history_disclaimer"]
GENERIC_TEXT_TTL_SECONDS = 60 * 60

_TEXT: Mapping[GenericTextKey, Mapping[str, str]] = {
    "ai_assisted_label": {
        "en": "AI-assisted suggestion; staff review required.",
        "hi": "AI की सहायता से बना सुझाव; कर्मचारी की समीक्षा आवश्यक है।",
        "mr": "AI-सहाय्यित सूचना; कर्मचाऱ्यांची तपासणी आवश्यक आहे.",
    },
    "history_disclaimer": {
        "en": "This summary records patient-stated history. It is not a diagnosis.",
        "hi": "यह सारांश मरीज द्वारा बताई गई जानकारी है। यह निदान नहीं है।",
        "mr": "हा सारांश रुग्णाने सांगितलेली माहिती नोंदवतो. हे निदान नाही.",
    },
}


class _RedisTextCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ex: int) -> object: ...

    async def delete(self, key: str) -> object: ...


async def get_generic_text(
    redis: _RedisTextCache, *, environment: str, key: GenericTextKey, language: str
) -> str:
    """Return one allow-listed static string, falling back to source when Redis is unavailable."""
    if language not in {"en", "hi", "mr"}:
        raise ValueError("unsupported generic-text language")
    fixed_text = _TEXT[key][language]
    cache_key = f"osa:{environment}:llm:generic-text:v1:{key}:{language}"
    try:
        cached = await redis.get(cache_key)
        if cached == fixed_text:
            return fixed_text
        if cached is not None:
            await redis.delete(cache_key)
        await redis.set(cache_key, fixed_text, ex=GENERIC_TEXT_TTL_SECONDS)
    except Exception:
        return fixed_text
    return fixed_text
