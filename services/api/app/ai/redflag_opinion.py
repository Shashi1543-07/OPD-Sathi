"""A bounded, rules-scoped second opinion that can only preserve or raise urgency."""

import asyncio
from collections.abc import Sequence
from typing import Any

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output
from app.ai.prompts.builders import build_redflag_prompt
from app.ai.providers.llm.base import LLMProvider
from app.domain.redflags.engine import (
    RULES,
    PatientFacts,
    RedFlag,
    RuleModel,
    conditions_match,
    group_signs,
    maximum_urgency,
)

# Runs once at confirmation, in parallel with department routing.
MAX_REDFLAG_SECONDS = 5.0


def redflag_json_schema(
    rules: Sequence[RuleModel] = RULES, ids: Sequence[str] | None = None
) -> dict[str, Any]:
    """Return a response schema restricted to configured rules and short quote evidence."""
    ids = sorted(ids if ids is not None else (rule.id for rule in rules))
    return {
        "type": "object",
        "properties": {
            "flags": {
                "type": "array",
                "maxItems": len(ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string", "enum": ids},
                        "evidence": {"type": "string", "minLength": 1, "maxLength": 240},
                    },
                    "required": ["rule_id", "evidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["flags"],
        "additionalProperties": False,
    }


def _sign_id(rule_id: str, index: int) -> str:
    """The ID under which one sign group of a several-group rule is offered to the model."""
    return f"{rule_id}__SIGN_{index + 1}"


def _offers(rules: Sequence[RuleModel]) -> dict[str, tuple[RuleModel, int | None]]:
    """What the model may return: a one-group rule whole, a several-group rule group by group.

    A rule such as "chest discomfort with another concerning symptom" needs a sign from
    every one of its groups. Offered whole, the model raised it, urgent, from "dizziness"
    alone, a sign of the second group. So the model only says which groups the patient's
    words show, and the rule is raised here, when every group is shown.
    """
    offers: dict[str, tuple[RuleModel, int | None]] = {}
    for rule in rules:
        if len(rule.all_groups) == 1:
            offers[rule.id] = (rule, None)
            continue
        for index in range(len(rule.all_groups)):
            offers[_sign_id(rule.id, index)] = (rule, index)
    return offers


def _offer_prompt_details(
    offers: dict[str, tuple[RuleModel, int | None]],
) -> tuple[dict[str, object], ...]:
    details: list[dict[str, object]] = []
    for offer_id, (rule, index) in offers.items():
        groups = rule.all_groups if index is None else [rule.all_groups[index]]
        details.append(
            {
                "id": offer_id,
                "level": rule.level,
                "warning_signs": [{"phrases_by_language": group["any"]} for group in groups],
                "conditions": rule.conditions,
            }
        )
    return tuple(details)


def _group_quote(
    group: dict[str, dict[str, list[str]]],
    offers: dict[str, tuple[RuleModel, int | None]],
    quotes: dict[str, str],
) -> str:
    """The model's quote for a sign group, under whichever offered ID lists those signs.

    "Chest pain" is a rule of its own and also the first group of "chest pain with another
    sign"; the model often quotes it under only one of the two IDs.
    """
    for offer_id, (rule, index) in offers.items():
        offered = rule.all_groups if index is None else [rule.all_groups[index]]
        if offer_id in quotes and offered == [group]:
            return quotes[offer_id]
    return ""


async def second_opinion_flags(
    provider: LLMProvider,
    *,
    transcript: str,
    language: str,
    session_id: str,
    rules: Sequence[RuleModel] = RULES,
    timeout: float = MAX_REDFLAG_SECONDS,
    facts: PatientFacts | None = None,
) -> list[RedFlag]:
    """Return only quoted, configured rule flags; any failure returns no extra flags.

    Only rules the patient's words can show are offered, and only those whose conditions
    fit this patient: the model once claimed "patient selected a danger-sign tile" from
    "breathless on walking", which made a routine patient urgent.
    """
    rules = [
        rule
        for rule in rules
        if rule.all_groups and (facts is None or conditions_match(rule.conditions, facts))
    ]
    if not transcript.strip() or not rules:
        return []
    offers = _offers(rules)
    prompt = build_redflag_prompt(
        transcript,
        language,
        tuple(offers),
        _offer_prompt_details(offers),
    )
    bounded_timeout = min(max(timeout, 0.01), MAX_REDFLAG_SECONDS)
    try:
        response = await asyncio.wait_for(
            provider.generate_json(
                prompt.text,
                redflag_json_schema(rules, tuple(offers)),
                timeout=bounded_timeout - 0.10,
                session_id=session_id,
            ),
            timeout=bounded_timeout - 0.05,
        )
    except Exception:
        return []

    raw_flags = response.get("flags")
    if not isinstance(raw_flags, list):
        return []
    try:
        validate_safe_output(raw_flags)
    except UnsafeLLMOutputError:
        return []
    # The first valid quote for each offered ID.
    quotes: dict[str, str] = {}
    for item in raw_flags:
        if not isinstance(item, dict):
            continue
        offer_id = item.get("rule_id")
        evidence = item.get("evidence")
        if (
            not isinstance(offer_id, str)
            or offer_id not in offers
            or offer_id in quotes
            or not isinstance(evidence, str)
            or not evidence
            or len(evidence) > 240
            or evidence not in transcript
        ):
            continue
        quotes[offer_id] = evidence
    extra: list[RedFlag] = []
    for rule in rules:
        if len(rule.all_groups) == 1:
            evidence = quotes.get(rule.id, "")
        else:
            # Each group is shown by the model's quote or by a listed sign in the transcript;
            # the model must show at least one (the rules already raise a rule whose every
            # group is stated in the listed words).
            stated = group_signs(rule, transcript)
            model = [_group_quote(group, offers, quotes) for group in rule.all_groups]
            parts = [quote or sign for quote, sign in zip(model, stated, strict=True)]
            evidence = "; ".join(dict.fromkeys(parts)) if any(model) and all(parts) else ""
        if not evidence:
            continue
        extra.append(
            RedFlag(
                id=rule.id,
                level=rule.level,
                source="llm",
                evidence=evidence[:240],
                reason_en=rule.reason_en,
                visible_to_patient=False,
            )
        )
    return extra


def combine_redflag_opinions(
    deterministic: list[RedFlag], second_opinion: list[RedFlag]
) -> list[RedFlag]:
    """Keep every deterministic flag and add only new model flags; urgency cannot fall."""
    combined = list(deterministic)
    seen = {flag.id for flag in deterministic}
    for flag in second_opinion:
        if flag.id not in seen:
            combined.append(flag)
            seen.add(flag.id)
    return combined


def urgency_never_decreases(deterministic: list[RedFlag], combined: list[RedFlag]) -> bool:
    levels = {"routine": 0, "priority": 1, "urgent": 2}
    return levels[maximum_urgency(combined)] >= levels[maximum_urgency(deterministic)]
