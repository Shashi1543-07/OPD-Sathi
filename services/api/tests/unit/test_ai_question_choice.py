"""Question selection is limited to supplied IDs and uses synthetic patient answers."""

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.ai.prompts.builders import QuestionCandidate
from app.ai.question_choice import MAX_DECISION_SECONDS, select_question_id
from app.api.v1.intake import _question_response
from app.config import Settings
from app.domain.interview.engine import start


class FixtureProvider:
    name = "mock"

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.schema: Mapping[str, Any] = {}
        self.timeout = 0.0
        self.calls = 0

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del prompt, model, session_id
        self.calls += 1
        self.schema = schema
        self.timeout = timeout
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_selects_only_from_the_configured_candidate_ids() -> None:
    provider = FixtureProvider({"question_id": "duration"})
    selected = await select_question_id(
        provider,
        transcript="synthetic cough",
        language="en",
        candidates=(
            QuestionCandidate("chief_concern", "What is bothering you?"),
            QuestionCandidate("duration", "How long has this been happening?"),
        ),
        fallback_id="chief_concern",
        session_id="synthetic-session",
    )
    assert selected == "duration"
    assert provider.schema["properties"]["question_id"]["enum"] == [
        "chief_concern",
        "duration",
    ]
    assert provider.timeout < MAX_DECISION_SECONDS


@pytest.mark.asyncio
async def test_rejects_unapproved_id_and_returns_deterministic_choice() -> None:
    provider = FixtureProvider({"question_id": "invented_question"})
    selected = await select_question_id(
        provider,
        transcript="synthetic complaint",
        language="en",
        candidates=(
            QuestionCandidate("chief_concern", "What is bothering you?"),
            QuestionCandidate("duration", "How long?"),
        ),
        fallback_id="chief_concern",
        session_id="synthetic-session",
    )
    assert selected == "chief_concern"


@pytest.mark.asyncio
async def test_provider_failure_uses_deterministic_choice() -> None:
    provider = FixtureProvider(RuntimeError("synthetic provider failure"))
    selected = await select_question_id(
        provider,
        transcript="synthetic complaint",
        language="en",
        candidates=(
            QuestionCandidate("chief_concern", "What is bothering you?"),
            QuestionCandidate("duration", "How long?"),
        ),
        fallback_id="chief_concern",
        session_id="synthetic-session",
    )
    assert selected == "chief_concern"


@pytest.mark.asyncio
async def test_slow_provider_falls_back_within_the_600ms_budget() -> None:
    class SlowProvider(FixtureProvider):
        async def generate_json(
            self,
            prompt: str,
            schema: Mapping[str, Any],
            timeout: float = 2.5,
            model: str | None = None,
            *,
            session_id: str | None = None,
        ) -> dict[str, Any]:
            await asyncio.sleep(0.8)
            return await super().generate_json(
                prompt, schema, timeout, model, session_id=session_id
            )

    provider = SlowProvider({"question_id": "duration"})
    started = asyncio.get_running_loop().time()
    selected = await select_question_id(
        provider,
        transcript="synthetic complaint",
        language="en",
        candidates=(
            QuestionCandidate("chief_concern", "What is bothering you?"),
            QuestionCandidate("duration", "How long?"),
        ),
        fallback_id="chief_concern",
        session_id="synthetic-session",
    )
    elapsed = asyncio.get_running_loop().time() - started
    assert selected == "chief_concern"
    assert elapsed < MAX_DECISION_SECONDS


@pytest.mark.asyncio
async def test_one_candidate_skips_provider_call() -> None:
    provider = FixtureProvider({"question_id": "unused"})
    selected = await select_question_id(
        provider,
        transcript="synthetic complaint",
        language="en",
        candidates=(QuestionCandidate("duration", "How long?"),),
        fallback_id="duration",
        session_id="synthetic-session",
    )
    assert selected == "duration"
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_intake_persists_selected_id_and_reuses_it_on_refresh() -> None:
    provider = FixtureProvider({"question_id": "duration"})
    provider.name = "gemini"

    class RedisStub:
        writes: list[tuple[str, str, int]] = []

        async def set(self, key: str, value: str, *, ex: int) -> None:
            self.writes.append((key, value, ex))

    visit_id = uuid4()
    hospital_id = uuid4()
    redis = RedisStub()
    state = {
        "interview": start("en").model_dump(mode="json"),
        "phase": "interview",
    }
    app_state = SimpleNamespace(
        settings=Settings(_env_file=None, llm_enabled=True),
        llm_provider=provider,
        redis_state=redis,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=app_state),
        state=SimpleNamespace(llm_enablement=(hospital_id, True)),
    )

    first = await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )
    again = await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )
    assert first["question"]["id"] == "duration"
    assert again["question"]["id"] == "duration"
    assert state["ai_selected_question"] == "duration"
    assert provider.calls == 1
    assert len(redis.writes) == 1
