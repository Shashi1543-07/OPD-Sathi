"""A bounded, rules-scoped second opinion that can only preserve or raise urgency."""

import asyncio
from collections.abc import Sequence
from typing import Any

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output
from app.ai.prompts.builders import build_redflag_prompt
from app.ai.providers.llm.base import LLMProvider
from app.domain.redflags.engine import RULES, RedFlag, RuleModel, maximum_urgency

MAX_REDFLAG_SECONDS = 0.6


def redflag_json_schema(rules: Sequence[RuleModel] = RULES) -> dict[str, Any]:
    """Return a response schema restricted to configured rules and short quote evidence."""
    ids = sorted(rule.id for rule in rules)
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


def _rule_prompt_details(rules: Sequence[RuleModel]) -> tuple[dict[str, object], ...]:
    details: list[dict[str, object]] = []
    for rule in rules:
        signs: list[dict[str, object]] = []
        for group in rule.all_groups:
            signs.append({"phrases_by_language": group["any"]})
        details.append(
            {
                "id": rule.id,
                "level": rule.level,
                "warning_signs": signs,
                "conditions": rule.conditions,
            }
        )
    return tuple(details)


async def second_opinion_flags(
    provider: LLMProvider,
    *,
    transcript: str,
    language: str,
    session_id: str,
    rules: Sequence[RuleModel] = RULES,
    timeout: float = MAX_REDFLAG_SECONDS,
) -> list[RedFlag]:
    """Return only quoted, configured rule flags; any failure returns no extra flags."""
    if not transcript.strip() or not rules:
        return []
    by_id = {rule.id: rule for rule in rules}
    prompt = build_redflag_prompt(
        transcript,
        language,
        tuple(by_id),
        _rule_prompt_details(rules),
    )
    bounded_timeout = min(max(timeout, 0.01), MAX_REDFLAG_SECONDS)
    try:
        response = await asyncio.wait_for(
            provider.generate_json(
                prompt.text,
                redflag_json_schema(rules),
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
    extra: list[RedFlag] = []
    seen: set[str] = set()
    for item in raw_flags:
        if not isinstance(item, dict):
            continue
        rule_id = item.get("rule_id")
        evidence = item.get("evidence")
        if (
            not isinstance(rule_id, str)
            or rule_id not in by_id
            or rule_id in seen
            or not isinstance(evidence, str)
            or not evidence
            or len(evidence) > 240
            or evidence not in transcript
        ):
            continue
        rule = by_id[rule_id]
        extra.append(
            RedFlag(
                id=rule.id,
                level=rule.level,
                source="llm",
                evidence=evidence,
                reason_en=rule.reason_en,
                visible_to_patient=False,
            )
        )
        seen.add(rule_id)
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
