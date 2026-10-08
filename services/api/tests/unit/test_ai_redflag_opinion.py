"""Second-opinion tests use synthetic transcripts and configured draft rules."""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from app.ai.redflag_opinion import (
    MAX_REDFLAG_SECONDS,
    combine_redflag_opinions,
    redflag_json_schema,
    second_opinion_flags,
    urgency_never_decreases,
)
from app.domain.redflags.engine import RULES, RedFlag, maximum_urgency


class FixtureProvider:
    name = "mock"

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
        self.schema: Mapping[str, Any] = {}
        self.timeout = 0.0

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
        self.schema = schema
        self.timeout = timeout
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_schema_limits_model_output_to_configured_rules_and_quotes() -> None:
    schema = redflag_json_schema()
    item = schema["properties"]["flags"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["properties"]["rule_id"]["enum"]) == {rule.id for rule in RULES}
    assert item["properties"]["evidence"]["maxLength"] == 240


@pytest.mark.asyncio
async def test_adds_only_configured_flags_with_exact_quote_and_neutral_visibility() -> None:
    rule = next(rule for rule in RULES if rule.level == "urgent")
    provider = FixtureProvider({"flags": [{"rule_id": rule.id, "evidence": "severe chest pain"}]})
    flags = await second_opinion_flags(
        provider,
        transcript="patient reports severe chest pain today",
        language="en",
        session_id="synthetic-session",
    )
    assert len(flags) == 1
    assert flags[0].id == rule.id
    assert flags[0].level == "urgent"
    assert flags[0].source == "llm"
    assert flags[0].visible_to_patient is False
    assert flags[0].evidence == "severe chest pain"
    assert provider.timeout < MAX_REDFLAG_SECONDS


@pytest.mark.asyncio
async def test_unsupported_ids_and_evidence_are_dropped() -> None:
    rule = RULES[0]
    provider = FixtureProvider(
        {
            "flags": [
                {"rule_id": "INVENTED_RULE", "evidence": "synthetic text"},
                {"rule_id": rule.id, "evidence": "not in transcript"},
            ]
        }
    )
    flags = await second_opinion_flags(
        provider,
        transcript="synthetic text",
        language="en",
        session_id="synthetic-session",
    )
    assert flags == []


@pytest.mark.asyncio
async def test_prohibited_recommendation_evidence_rejects_the_opinion() -> None:
    rule = RULES[0]
    provider = FixtureProvider(
        {"flags": [{"rule_id": rule.id, "evidence": "You should take medicine."}]}
    )
    flags = await second_opinion_flags(
        provider,
        transcript="You should take medicine.",
        language="en",
        session_id="synthetic-session",
    )
    assert flags == []


@pytest.mark.asyncio
async def test_provider_failure_and_timeout_return_no_extra_flags() -> None:
    rule = RULES[0]
    failed = await second_opinion_flags(
        FixtureProvider(RuntimeError("synthetic failure")),
        transcript="synthetic complaint",
        language="en",
        session_id="synthetic-session",
    )
    assert failed == []

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
            await asyncio.sleep(MAX_REDFLAG_SECONDS + 0.3)
            return await super().generate_json(
                prompt, schema, timeout, model, session_id=session_id
            )

    started = asyncio.get_running_loop().time()
    timed_out = await second_opinion_flags(
        SlowProvider({"flags": [{"rule_id": rule.id, "evidence": "synthetic"}]}),
        transcript="synthetic",
        language="en",
        session_id="synthetic-session",
    )
    assert timed_out == []
    assert asyncio.get_running_loop().time() - started < MAX_REDFLAG_SECONDS


def test_combination_keeps_rule_flags_and_never_reduces_urgency() -> None:
    levels = {"routine": 0, "priority": 1, "urgent": 2}
    samples = [
        [],
        [RedFlag("R1", "priority", "rule", "quote", "reason")],
        [RedFlag("R2", "urgent", "rule", "quote", "reason")],
    ]
    model_flag = RedFlag("R3", "urgent", "llm", "quote", "reason", False)
    for deterministic in samples:
        combined = combine_redflag_opinions(deterministic, [model_flag, model_flag])
        expected_count = len(deterministic) + int("R3" not in {flag.id for flag in deterministic})
        assert len(combined) == expected_count
        assert levels[maximum_urgency(combined)] >= levels[maximum_urgency(deterministic)]
        assert urgency_never_decreases(deterministic, combined)
