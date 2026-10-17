"""Tenant AI settings and the patient's AI consent are isolated and fail closed."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.ai.providers.llm.gemini import MAX_OUTPUT_TOKENS, GeminiProvider
from app.api.v1.ai_gate import ai_consented, llm_allowed
from app.api.v1.ai_gate import hospital_llm_enabled as _hospital_llm_enabled
from app.config import Settings


class FakeConnection:
    def __init__(self, enabled: bool | str | None) -> None:
        self.enabled = enabled
        self.calls = 0

    async def execute(self, *_args: object, **_kwargs: object) -> None:
        self.calls += 1

    async def scalar(self, *_args: object, **_kwargs: object) -> bool | str | None:
        self.calls += 1
        return self.enabled


class FakeContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


def _request(
    enabled: bool | str | None, *, global_enabled: bool = True
) -> tuple[Request, FakeConnection]:
    connection = FakeConnection(enabled)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, llm_enabled=global_enabled),
            engine=SimpleNamespace(begin=lambda: FakeContext(connection)),
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/intake",
            "headers": [],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )
    return request, connection


@pytest.mark.asyncio
async def test_hospital_switch_is_scoped_and_cached_only_for_the_request() -> None:
    enabled_request, enabled_connection = _request(True)
    disabled_request, disabled_connection = _request(False)
    enabled_hospital, other_hospital = uuid4(), uuid4()

    assert await _hospital_llm_enabled(enabled_request, enabled_hospital)
    calls_after_lookup = enabled_connection.calls
    assert await _hospital_llm_enabled(enabled_request, enabled_hospital)
    assert enabled_connection.calls == calls_after_lookup
    assert not await _hospital_llm_enabled(disabled_request, other_hospital)
    assert enabled_connection.calls == calls_after_lookup
    assert enabled_connection.calls == 2
    assert disabled_connection.calls == 2


@pytest.mark.asyncio
async def test_global_kill_switch_wins_and_missing_setting_follows_operator() -> None:
    killed_request, killed_connection = _request(True, global_enabled=False)
    missing_request, missing_connection = _request(None)
    opted_out_request, _ = _request(False)
    assert not await _hospital_llm_enabled(killed_request, uuid4())
    # No saved preference: the operator's configured key decides; an explicit "off" wins.
    assert await _hospital_llm_enabled(missing_request, uuid4())
    assert not await _hospital_llm_enabled(opted_out_request, uuid4())
    assert killed_connection.calls == 0
    assert missing_connection.calls == 2


@pytest.mark.asyncio
async def test_unreadable_setting_store_fails_closed() -> None:
    request, connection = _request(True)

    async def broken(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("store unavailable")

    connection.scalar = broken  # type: ignore[method-assign]
    assert not await _hospital_llm_enabled(request, uuid4())


@pytest.mark.asyncio
async def test_only_a_standing_yes_lets_ai_work_on_a_visit() -> None:
    for choice, allowed in (
        ("granted", True),
        ("declined", False),
        ("withdrawn", False),
        (None, False),  # Never asked: the visit runs on the server alone.
    ):
        request, _ = _request(choice)
        assert await ai_consented(request, uuid4(), uuid4()) is allowed, choice

    # The answer is read once per request and for that visit only.
    request, connection = _request("granted")
    visit = uuid4()
    assert await ai_consented(request, uuid4(), visit)
    calls = connection.calls
    assert await ai_consented(request, uuid4(), visit)
    assert connection.calls == calls

    broken_request, broken_connection = _request("granted")

    async def broken(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("store unavailable")

    broken_connection.scalar = broken  # type: ignore[method-assign]
    assert not await ai_consented(broken_request, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_the_model_needs_both_the_hospital_switch_and_the_patients_yes() -> None:
    request, _ = _request("granted", global_enabled=False)
    assert not await llm_allowed(request, uuid4(), uuid4())
    hospital, visit = uuid4(), uuid4()
    request, _ = _request(None)
    request.state.llm_enablement = (hospital, True)
    request.state.ai_consent = (visit, False)
    assert not await llm_allowed(request, hospital, visit)
    request.state.ai_consent = (visit, True)
    assert await llm_allowed(request, hospital, visit)


def test_token_limit_is_configurable_but_hard_capped() -> None:
    assert Settings(_env_file=None, llm_max_output_tokens=64).llm_max_output_tokens == 64
    with pytest.raises(ValueError, match="LLM_MAX_OUTPUT_TOKENS"):
        Settings(_env_file=None, llm_max_output_tokens=MAX_OUTPUT_TOKENS + 1)
    with pytest.raises(ValueError, match="LLM_MAX_CALLS_PER_SESSION"):
        Settings(_env_file=None, llm_max_calls_per_session=31)
    with pytest.raises(ValueError, match="between 1 and 1024"):
        GeminiProvider(
            api_key="synthetic-key",
            model="primary-test",
            fallback_model="fallback-test",
            max_output_tokens=MAX_OUTPUT_TOKENS + 1,
            client=SimpleNamespace(),  # type: ignore[arg-type]
        )


def test_per_session_call_limit_cannot_be_raised_above_thirty() -> None:
    with pytest.raises(ValueError, match="between 1 and 30"):
        GeminiProvider(
            api_key="synthetic-key",
            model="primary-test",
            fallback_model="fallback-test",
            max_calls_per_session=31,
            client=SimpleNamespace(),  # type: ignore[arg-type]
        )
