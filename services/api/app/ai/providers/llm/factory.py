"""Build the selected LLM provider while keeping Mock the safe default."""

from typing import Any

from app.ai.providers.llm.base import LLMProvider
from app.ai.providers.llm.gemini import GeminiProvider, RedisSessionCallBudget
from app.ai.providers.llm.mock import MockLLM
from app.config import Settings


def build_llm_provider(
    settings: Settings,
    *,
    redis: object | None = None,
    client: Any | None = None,
) -> LLMProvider:
    """Return a fixture-only Mock unless Gemini is explicitly enabled."""
    if settings.llm_provider == "mock":
        return MockLLM()
    if not settings.gemini_api_key.strip():
        raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
    budget = (
        RedisSessionCallBudget(redis, environment=settings.app_env) if redis is not None else None
    )
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.llm_model,
        fallback_model=settings.llm_fallback_model,
        session_budget=budget,
        max_calls_per_session=settings.llm_max_calls_per_session,
        client=client,
    )
