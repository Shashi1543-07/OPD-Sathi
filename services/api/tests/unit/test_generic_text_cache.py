"""The text cache accepts only fixed generic copy, never caller-provided content."""

import pytest

from app.ai.generic_text_cache import (
    GENERIC_TEXT_TTL_SECONDS,
    get_generic_text,
)


class RedisStub:
    def __init__(self, value: str | None = None, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.key = ""
        self.ttl = 0
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("synthetic redis failure")
        self.key = key
        return self.value

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        if self.fail:
            raise RuntimeError("synthetic redis failure")
        self.key = key
        self.value = value
        self.ttl = ex
        return True

    async def delete(self, key: str) -> int:
        self.deleted.append(key)
        self.value = None
        return 1


@pytest.mark.asyncio
async def test_cache_stores_only_allowlisted_fixed_generic_text() -> None:
    redis = RedisStub()
    text = await get_generic_text(redis, environment="test", key="ai_assisted_label", language="en")
    assert text == "AI-assisted suggestion; staff review required."
    assert redis.value == text
    assert redis.key == "osa:test:llm:generic-text:v1:ai_assisted_label:en"
    assert redis.ttl == GENERIC_TEXT_TTL_SECONDS


@pytest.mark.asyncio
async def test_cache_repairs_unrecognized_values_instead_of_returning_them() -> None:
    redis = RedisStub("patient-specific response")
    text = await get_generic_text(
        redis, environment="test", key="history_disclaimer", language="en"
    )
    assert text == "This summary records patient-stated history. It is not a diagnosis."
    assert redis.deleted == ["osa:test:llm:generic-text:v1:history_disclaimer:en"]
    assert redis.value == text


@pytest.mark.asyncio
async def test_redis_failure_returns_static_copy_and_invalid_language_is_rejected() -> None:
    redis = RedisStub(fail=True)
    assert (
        await get_generic_text(redis, environment="test", key="ai_assisted_label", language="hi")
        == "AI की सहायता से बना सुझाव; कर्मचारी की समीक्षा आवश्यक है।"
    )
    with pytest.raises(ValueError, match="unsupported generic-text language"):
        await get_generic_text(redis, environment="test", key="ai_assisted_label", language="fr")
