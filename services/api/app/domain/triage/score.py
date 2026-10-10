"""Explainable triage score: how much of a head start a patient gets in the queue.

The score adds up points from what the patient reported, mostly from things that are hard
to exaggerate (danger signs, red-flag symptom combinations, age, pregnancy, specific tapped
symptoms) and only lightly from the patient's own 0-10 rating. Every point is recorded with
its reason so staff can see exactly why a patient is where they are. Weights live in
``weights.yaml`` and are drafts pending clinician review.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.domain.interview.bank import QUESTIONS
from app.domain.redflags.engine import RULES, RedFlag

Level = Literal["routine", "priority", "urgent"]
WEIGHTS_PATH = Path(__file__).with_name("weights.yaml")
LEVEL_ORDER: tuple[Level, ...] = ("routine", "priority", "urgent")


def _load(path: Path = WEIGHTS_PATH) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("review_status") != "draft_needs_clinician_review":
        raise ValueError("triage weights must stay marked draft_needs_clinician_review")
    rule_ids = {rule.id for rule in RULES}
    for key in [*raw["signals"], *raw["option_flags"]]:
        question_id, _, option_id = key.partition(".")
        question = QUESTIONS.get(question_id)
        if question is None or option_id not in {option.id for option in question.options}:
            raise ValueError(f"triage weight refers to an unknown answer: {key}")
    if set(raw["option_flags"].values()) - rule_ids:
        raise ValueError("triage option flags must name existing red-flag rules")
    return raw


WEIGHTS = _load()
# Danger-tile flags carry the tile's rule code as evidence; staff read the tile's words.
_TILE_LABELS = {
    "DS_CHEST": "Chest discomfort",
    "DS_BREATH": "Trouble breathing",
    "DS_BLEED": "Heavy bleeding",
    "DS_FIT": "Fainting or a seizure",
    "DS_STROKE": "Sudden weakness or difficulty speaking",
}


@dataclass(frozen=True)
class Component:
    points: int
    label: str
    kind: Literal["red_flag", "signal", "vulnerability", "onset", "self_rating"]
    source: str = ""


@dataclass(frozen=True)
class Assessment:
    score: int
    level: Level
    head_start_min: int
    components: list[Component] = field(default_factory=list)
    version: int = int(WEIGHTS["version"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "score": self.score,
            "level": self.level,
            "head_start_min": self.head_start_min,
            "components": [asdict(component) for component in self.components],
        }


def tapped(answers: dict[str, str | int]) -> set[str]:
    """``question.option`` IDs of every tapped answer, the same in every language."""
    chosen: set[str] = set()
    for question_id, value in answers.items():
        question = QUESTIONS.get(question_id)
        if question is None or not isinstance(value, str):
            continue
        chosen.update(
            f"{question_id}.{option.id}"
            for _, option in question.answer_parts(value)
            if option is not None and not option.none
        )
    return chosen


def option_flags(answers: dict[str, str | int]) -> list[RedFlag]:
    """Red flags raised by tapped answers, so a tap counts the same in every language."""
    rules = {rule.id: rule for rule in RULES}
    flags: list[RedFlag] = []
    for key in sorted(tapped(answers)):
        rule_id = WEIGHTS["option_flags"].get(key)
        if rule_id is None or any(flag.id == rule_id for flag in flags):
            continue
        rule = rules[rule_id]
        question_id, _, option_id = key.partition(".")
        option = next(o for o in QUESTIONS[question_id].options if o.id == option_id)
        flags.append(
            RedFlag(
                id=rule.id,
                level=rule.level,
                source="rule",
                evidence=f"Patient tapped: {option.label['en']}",
                reason_en=rule.reason_en,
                visible_to_patient=rule.visible_to_patient,
            )
        )
    return flags


def _counted_level(flag: RedFlag) -> Literal["priority", "urgent"]:
    """The model is a checker, not a decider: its flags count as priority at most.

    An AI "urgent" still alerts the nurse at once (with its quote), who confirms with one
    click. Live, the model matched "chest pain with sweating" from "breathless on walking".
    """
    return "priority" if flag.source == "llm" else flag.level


def head_start_minutes(level: Level, score: int) -> int:
    """Minutes taken off the arrival time; the level bounds the score's effect.

    Routine stays below the priority head start and priority stays at or above it, so a
    nurse's override of the level always wins over the points.
    """
    config = WEIGHTS["head_start"]
    per_point, maximum = float(config["minutes_per_point"]), int(config["max_minutes"])
    priority_floor = round(int(WEIGHTS["levels"]["priority"]) * per_point)
    minutes = min(maximum, round(score * per_point))
    if level == "routine":
        return min(minutes, priority_floor - 1)
    return max(minutes, priority_floor)


def assess(
    flags: list[RedFlag],
    answers: dict[str, str | int],
    *,
    age: int | None,
    pregnant: bool | None,
) -> Assessment:
    """Add up the explainable points for one patient."""
    components: list[Component] = []

    config = WEIGHTS["red_flags"]
    ordered = sorted(flags, key=lambda flag: _counted_level(flag) != "urgent")
    extra = 0
    for index, flag in enumerate(ordered):
        source = "AI second opinion" if flag.source == "llm" else flag.source
        generic = not flag.evidence or flag.evidence.startswith("Structured intake answers")
        evidence = _TILE_LABELS.get(flag.evidence, flag.evidence)
        label = flag.reason_en if generic else f"{flag.reason_en} ({evidence})"
        if flag.source == "llm":
            label = f"AI suggests, nurse to confirm: {label}"
        if index == 0:
            points = int(config[f"{_counted_level(flag)}_points"])
        else:
            points = min(int(config["additional_points"]), int(config["max_additional"]) - extra)
            extra += max(points, 0)
        if points > 0:
            components.append(Component(points, label[:240], "red_flag", source))

    chosen = tapped(answers)
    signal_points = 0
    for key, signal in WEIGHTS["signals"].items():
        if key not in chosen:
            continue
        points = min(int(signal["points"]), int(WEIGHTS["signals_max"]) - signal_points)
        if points <= 0:
            continue
        signal_points += points
        components.append(Component(points, str(signal["en"]), "signal", "patient answer"))

    for group in WEIGHTS["vulnerability"]:
        if group.get("pregnant"):
            matches = pregnant is True
        else:
            matches = age is not None and (
                age >= group.get("min_age", 0) and age <= group.get("max_age", 200)
            )
        if matches:
            components.append(Component(int(group["points"]), str(group["en"]), "vulnerability"))

    onset_ids = {key.partition(".")[2] for key in chosen if key.startswith("duration.")}
    for option_id, onset in WEIGHTS["onset"].items():
        if option_id in onset_ids:
            components.append(Component(int(onset["points"]), str(onset["en"]), "onset"))

    severity = answers.get("severity")
    rating = WEIGHTS["self_rating"]
    if isinstance(severity, int) and not onset_ids & set(rating["ignore_if_onset"]):
        points = int(rating["points"].get(severity, 0))
        if points:
            components.append(
                Component(
                    points,
                    f"Patient's own rating {severity}/10 (self-reported, counted lightly)",
                    "self_rating",
                    "patient answer",
                )
            )

    score = min(100, sum(component.points for component in components))
    levels = WEIGHTS["levels"]
    level: Level
    if any(_counted_level(flag) == "urgent" for flag in flags) or score >= int(
        levels["urgent"]
    ):
        level = "urgent"
    elif score >= int(levels["priority"]):
        level = "priority"
    else:
        level = "routine"
    return Assessment(score, level, head_start_minutes(level, score), components)


def higher(first: Level, second: Level) -> Level:
    return max(first, second, key=LEVEL_ORDER.index)
