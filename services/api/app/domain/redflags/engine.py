"""Explainable rules-only red-flag engine. Rules are drafts, not clinical advice."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.domain.redflags.normalize import normalize_text, tokens

RULE_PATH = Path(__file__).with_name("rules.yaml")
EXAMPLE_PATH = Path(__file__).parents[5] / "eval" / "redflags" / "unit_examples.yaml"
REVIEW_STATUS = "draft_needs_clinician_review"
LANGUAGES = ("en", "hi", "mr", "hinglish")
NEGATIONS = {
    "no",
    "not",
    "without",
    "nahi",
    "nahin",
    "nhi",
    "nahiin",
    "नहीं",
    "नही",
    "नाही",
    "नाहि",
}
CONTRASTS = {"but", "however", "although", "पर", "लेकिन", "मगर", "पण"}
CONJUNCTIONS = {"and", "और", "आणि", "aur", "ani"}


class RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=r"^[A-Z0-9_]+$")
    level: Literal["priority", "urgent"]
    status: Literal["draft_needs_clinician_review"]
    reviewed_by: str | None
    reason_en: str = Field(min_length=1, max_length=300)
    all_groups: list[dict[str, dict[str, list[str]]]] = Field(default_factory=list)
    conditions: dict[str, Any] = Field(default_factory=dict)
    visible_to_patient: bool = True


@dataclass(frozen=True)
class PatientFacts:
    age: int | None = None
    sex: str | None = None
    pregnant: bool | None = None
    severity: int | None = None
    tile_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RedFlag:
    id: str
    level: Literal["priority", "urgent"]
    source: Literal["tile", "rule"]
    evidence: str
    reason_en: str
    visible_to_patient: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "source": self.source,
            "evidence": self.evidence,
            "reason_en": self.reason_en,
            "visible_to_patient": self.visible_to_patient,
        }


def load_rules(path: Path = RULE_PATH) -> list[RuleModel]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("red-flag rules must be a non-empty YAML list")
    rules = [RuleModel.model_validate(item) for item in raw]
    ids = [rule.id for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("red-flag rule ids must be unique")
    for rule in rules:
        for group in rule.all_groups:
            choices = group.get("any")
            if not isinstance(choices, dict) or set(choices) != set(LANGUAGES):
                raise ValueError(f"{rule.id} phrase groups need en, hi, mr, and hinglish")
            if any(
                not values or any(not phrase.strip() for phrase in values)
                for values in choices.values()
            ):
                raise ValueError(f"{rule.id} phrase groups cannot be empty")
    return rules


RULES = load_rules()


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    haystack = tokens(text)
    needle = tokens(phrase)
    if not needle or len(needle) > len(haystack):
        return []
    return [
        (start, start + len(needle) - 1)
        for start in range(len(haystack) - len(needle) + 1)
        if haystack[start : start + len(needle)] == needle
    ]


def _negated(text: str, span: tuple[int, int]) -> bool:
    words = tokens(text)
    start, end = span
    before = any(words[index] in NEGATIONS for index in range(max(0, start - 3), start))
    after = any(
        words[index] in NEGATIONS
        and not any(
            CONTRASTS.intersection(words[end + 1 : index])
            or CONJUNCTIONS.intersection(words[end + 1 : index])
        )
        for index in range(end + 1, min(len(words), end + 4))
    )
    return before or after


def _conditions_match(conditions: dict[str, Any], facts: PatientFacts) -> bool:
    if "tile_any" in conditions and not set(conditions["tile_any"]) & set(facts.tile_ids):
        return False
    if "min_age" in conditions and (facts.age is None or facts.age < conditions["min_age"]):
        return False
    if "max_age" in conditions and (facts.age is None or facts.age > conditions["max_age"]):
        return False
    if "sex_any" in conditions and facts.sex not in conditions["sex_any"]:
        return False
    if "pregnant" in conditions and facts.pregnant is not conditions["pregnant"]:
        return False
    if "severity_min" in conditions and (
        facts.severity is None or facts.severity < conditions["severity_min"]
    ):
        return False
    return True


def evaluate(
    text: str,
    *,
    facts: PatientFacts | None = None,
    rules: list[RuleModel] | None = None,
) -> list[RedFlag]:
    """Return ordered, deduplicated flags; negation suppresses text-only matches."""
    facts = facts or PatientFacts()
    evaluated: list[RedFlag] = []
    normalized_text = normalize_text(text)
    for rule in rules or RULES:
        tile_match = bool(set(rule.conditions.get("tile_any", [])) & set(facts.tile_ids))
        if not _conditions_match(rule.conditions, facts):
            continue
        evidence = next(
            (tile for tile in facts.tile_ids if tile in rule.conditions.get("tile_any", [])), ""
        )
        groups_match = True
        matched_phrase = ""
        for group in rule.all_groups:
            phrases = [phrase for language in LANGUAGES for phrase in group["any"][language]]
            matches = [
                (phrase, span)
                for phrase in phrases
                for span in _phrase_spans(normalized_text, phrase)
                if not _negated(normalized_text, span)
            ]
            if not matches:
                groups_match = False
                break
            phrase, span = min(matches, key=lambda item: item[1][0])
            matched_phrase = matched_phrase or phrase
            if not evidence:
                words = tokens(normalized_text)
                evidence = " ".join(words[max(0, span[0] - 2) : min(len(words), span[1] + 3)])
        if groups_match:
            if not evidence:
                evidence = matched_phrase or "Structured intake answers match this draft rule."
            evaluated.append(
                RedFlag(
                    id=rule.id,
                    level=rule.level,
                    source="tile" if tile_match else "rule",
                    evidence=evidence[:240],
                    reason_en=rule.reason_en,
                    visible_to_patient=rule.visible_to_patient,
                )
            )
    return evaluated


def maximum_urgency(flags: list[RedFlag]) -> Literal["routine", "priority", "urgent"]:
    if any(flag.level == "urgent" for flag in flags):
        return "urgent"
    if flags:
        return "priority"
    return "routine"
