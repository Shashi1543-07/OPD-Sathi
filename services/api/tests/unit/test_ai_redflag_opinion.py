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
    # A rule with one sign group; rules that need several signs together are tested below.
    rule = next(rule for rule in RULES if rule.level == "urgent" and len(rule.all_groups) == 1)
    provider = FixtureProvider({"flags": [{"rule_id": rule.id, "evidence": "gasping for air"}]})
    flags = await second_opinion_flags(
        provider,
        transcript="patient is gasping for air today",
        language="en",
        session_id="synthetic-session",
    )
    assert len(flags) == 1
    assert flags[0].id == rule.id
    assert flags[0].level == "urgent"
    assert flags[0].source == "llm"
    assert flags[0].visible_to_patient is False
    assert flags[0].evidence == "gasping for air"
    assert provider.timeout < MAX_REDFLAG_SECONDS


def _two_sign_rule() -> tuple[str, str, str]:
    """A rule that needs a sign from each of two groups, and the IDs of its two groups."""
    rule = next(rule for rule in RULES if len(rule.all_groups) == 2 and not rule.conditions)
    return rule.id, f"{rule.id}__SIGN_1", f"{rule.id}__SIGN_2"


@pytest.mark.asyncio
async def test_a_rule_that_needs_two_signs_is_not_raised_from_one_of_them() -> None:
    rule_id, first, second = _two_sign_rule()
    # Seen live: "chest discomfort with another concerning symptom", urgent, from dizziness
    # alone. The whole rule is no longer offered; one of its two groups raises nothing.
    for flags_from_model in (
        [{"rule_id": rule_id, "evidence": "feeling dizzy"}],
        [{"rule_id": second, "evidence": "feeling dizzy"}],
    ):
        provider = FixtureProvider({"flags": flags_from_model})
        flags = await second_opinion_flags(
            provider,
            transcript="tired for two weeks and feeling dizzy",
            language="en",
            session_id="synthetic-session",
        )
        assert flags == []
        offered = provider.schema["properties"]["flags"]["items"]["properties"]["rule_id"]["enum"]
        assert rule_id not in offered and {first, second} <= set(offered)


@pytest.mark.asyncio
async def test_a_rule_that_needs_two_signs_is_raised_when_both_are_shown() -> None:
    rule_id, first, second = _two_sign_rule()
    # Both groups in the patient's own words, each quoted by the model.
    both = await second_opinion_flags(
        FixtureProvider(
            {
                "flags": [
                    {"rule_id": first, "evidence": "a heavy squeezing in my chest"},
                    {"rule_id": second, "evidence": "my shirt is soaked"},
                ]
            }
        ),
        transcript="a heavy squeezing in my chest and my shirt is soaked",
        language="en",
        session_id="synthetic-session",
    )
    assert [(flag.id, flag.level, flag.source) for flag in both] == [(rule_id, "urgent", "llm")]
    assert both[0].evidence == "a heavy squeezing in my chest; my shirt is soaked"

    # One group in the patient's own words, the other in the rule's listed words.
    mixed = await second_opinion_flags(
        FixtureProvider(
            {"flags": [{"rule_id": first, "evidence": "a heavy squeezing in my chest"}]}
        ),
        transcript="a heavy squeezing in my chest with sweating",
        language="en",
        session_id="synthetic-session",
    )
    assert [flag.id for flag in mixed] == [rule_id]
    assert mixed[0].evidence == "a heavy squeezing in my chest; sweating"

    # The first group quoted under the one-group rule that lists the same signs.
    same_signs = next(
        rule.id
        for rule in RULES
        if len(rule.all_groups) == 1
        and rule.all_groups[0] == next(r for r in RULES if r.id == rule_id).all_groups[0]
    )
    shared = await second_opinion_flags(
        FixtureProvider(
            {"flags": [{"rule_id": same_signs, "evidence": "a heavy squeezing in my chest"}]}
        ),
        transcript="a heavy squeezing in my chest with sweating",
        language="en",
        session_id="synthetic-session",
    )
    assert {flag.id for flag in shared} == {rule_id, same_signs}


@pytest.mark.asyncio
async def test_the_model_cannot_claim_a_tile_or_a_rule_that_does_not_fit_the_patient() -> None:
    from app.domain.redflags.engine import PatientFacts

    provider = FixtureProvider(
        {
            "flags": [
                # Seen live: "breathless on walking" reported as a danger tile.
                {"rule_id": "RF_DS_TILE", "evidence": "Breathless on walking"},
                {"rule_id": "RF_PREGNANCY_DANGER", "evidence": "Breathless on walking"},
            ]
        }
    )
    flags = await second_opinion_flags(
        provider,
        transcript="Difficulty breathing, Breathless on walking",
        language="en",
        session_id="synthetic-session",
        facts=PatientFacts(age=70, sex="male", pregnant=None),
    )
    assert flags == []
    offered = set(provider.schema["properties"]["flags"]["items"]["properties"]["rule_id"]["enum"])
    assert "RF_DS_TILE" not in offered and "RF_PREGNANCY_DANGER" not in offered


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
