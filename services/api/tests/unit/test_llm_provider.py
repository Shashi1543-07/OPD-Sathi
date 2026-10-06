"""LLM adapter tests use synthetic prompts and never contact Gemini."""

import asyncio
from typing import Any

import pytest
from jsonschema.exceptions import SchemaError

from app.ai.providers.llm.base import LLMMetrics, LLMUnavailableError
from app.ai.providers.llm.factory import build_llm_provider
from app.ai.providers.llm.gemini import GeminiProvider, RedisSessionCallBudget
from app.ai.providers.llm.mock import MockLLM
from app.config import Settings

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class FakeResponse:
    def __init__(self, value: dict[str, Any], *, prompt_tokens: int = 7, output_tokens: int = 3):
        self.parsed = value
        self.text = None
        self.usage_metadata = type(
            "Usage",
            (),
            {"prompt_token_count": prompt_tokens, "candidates_token_count": output_tokens},
        )()


class FakeModels:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, object]] = []

    async def generate_content(self, *, model: str, contents: str, config: object) -> object:
        self.calls.append((model, contents, config))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.models = FakeModels(responses)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeBudget:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.sessions: list[tuple[str, int]] = []

    async def consume(self, session_id: str, limit: int) -> bool:
        self.sessions.append((session_id, limit))
        return self.allowed


@pytest.mark.asyncio
async def test_mock_returns_only_scripted_result_and_copies_values() -> None:
    response = {"ok": True}
    provider = MockLLM([response])
    result = await provider.generate_json("synthetic prompt", SCHEMA)
    result["ok"] = False
    assert response == {"ok": True}
    with pytest.raises(LLMUnavailableError, match="no language-model fixture"):
        await provider.generate_json("synthetic prompt", SCHEMA)


def test_factory_uses_mock_by_default_and_requires_gemini_key() -> None:
    assert isinstance(build_llm_provider(Settings(_env_file=None)), MockLLM)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_llm_provider(Settings(_env_file=None, llm_provider="gemini"))


@pytest.mark.asyncio
async def test_gemini_uses_json_schema_and_records_only_aggregate_usage() -> None:
    client = FakeClient([FakeResponse({"ok": True})])
    metrics = LLMMetrics()
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=client,
        metrics=metrics,
    )
    result = await provider.generate_json("synthetic patient text", SCHEMA)
    assert result == {"ok": True}
    assert client.models.calls[0][0] == "gemini-primary-test"
    config = client.models.calls[0][2]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == SCHEMA
    assert metrics.prompt_tokens == 7
    assert metrics.response_tokens == 3
    assert metrics.successes == 1
    assert len(metrics.latencies_ms) == 1


@pytest.mark.asyncio
async def test_gemini_rejects_json_that_does_not_match_schema() -> None:
    client = FakeClient([FakeResponse({"ok": "yes", "diagnosis": "invented"})])
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=client,
    )
    with pytest.raises(LLMUnavailableError, match="unavailable"):
        await provider.generate_json("synthetic prompt", SCHEMA)


@pytest.mark.asyncio
async def test_gemini_rejects_invalid_schema_before_sending_prompt() -> None:
    client = FakeClient([])
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=client,
    )
    with pytest.raises(SchemaError):
        await provider.generate_json("synthetic prompt", {"type": "not-a-schema-type"})
    assert client.models.calls == []


@pytest.mark.asyncio
async def test_gemini_retries_once_on_transient_error_using_fallback_model() -> None:
    transient = RuntimeError("transient")
    transient.code = 503  # type: ignore[attr-defined]
    client = FakeClient([transient, FakeResponse({"ok": True})])
    metrics = LLMMetrics()
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=client,
        metrics=metrics,
    )
    assert await provider.generate_json("synthetic prompt", SCHEMA) == {"ok": True}
    assert [call[0] for call in client.models.calls] == [
        "gemini-primary-test",
        "gemini-fallback-test",
    ]
    assert metrics.retries == 1


@pytest.mark.asyncio
async def test_gemini_opens_circuit_after_five_failures() -> None:
    client = FakeClient([ValueError("bad response")] * 5)
    metrics = LLMMetrics()
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=client,
        metrics=metrics,
    )
    for _ in range(5):
        with pytest.raises(LLMUnavailableError, match="unavailable"):
            await provider.generate_json("synthetic prompt", SCHEMA)
    with pytest.raises(LLMUnavailableError, match="unavailable"):
        await provider.generate_json("synthetic prompt", SCHEMA)
    assert len(client.models.calls) == 5
    assert metrics.failures == 6
    assert metrics.circuit_open == 1


@pytest.mark.asyncio
async def test_gemini_respects_call_deadline_and_session_budget() -> None:
    async def slow_response() -> FakeResponse:
        await asyncio.sleep(0.5)
        return FakeResponse({"ok": True})

    class SlowModels:
        async def generate_content(self, *, model: str, contents: str, config: object) -> object:
            return await slow_response()

    class SlowClient:
        models = SlowModels()

        async def aclose(self) -> None:
            return None

    limited = FakeBudget(allowed=False)
    provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        session_budget=limited,
        max_calls_per_session=1,
        client=FakeClient([FakeResponse({"ok": True})]),
    )
    with pytest.raises(LLMUnavailableError, match="call limit"):
        await provider.generate_json("synthetic prompt", SCHEMA, session_id="synthetic-session")
    assert limited.sessions == [("synthetic-session", 1)]

    slow_provider = GeminiProvider(
        api_key="synthetic-test-key",
        model="gemini-primary-test",
        fallback_model="gemini-fallback-test",
        client=SlowClient(),  # type: ignore[arg-type]
    )
    started = asyncio.get_running_loop().time()
    with pytest.raises(LLMUnavailableError, match="unavailable"):
        await slow_provider.generate_json("synthetic prompt", SCHEMA, timeout=0.05)
    assert asyncio.get_running_loop().time() - started < 0.2


@pytest.mark.asyncio
async def test_redis_budget_hashes_session_key_and_sets_two_hour_expiry() -> None:
    class RedisStub:
        key: str = ""
        ttl: int = 0

        async def incr(self, key: str) -> int:
            self.key = key
            return 1

        async def expire(self, key: str, ttl: int) -> bool:
            assert key == self.key
            self.ttl = ttl
            return True

    redis = RedisStub()
    budget = RedisSessionCallBudget(redis, environment="test")
    assert await budget.consume("synthetic-visit-id", 30)
    assert "synthetic-visit-id" not in redis.key
    assert redis.ttl == 7200
