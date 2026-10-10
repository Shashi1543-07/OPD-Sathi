"""Triage score: explainable, hard to game, and fair to patients waiting in line."""

from app.domain.interview.bank import QUESTIONS
from app.domain.queue.scoring import MILLISECONDS_PER_MINUTE, queue_score
from app.domain.redflags.engine import RedFlag, evaluate
from app.domain.triage.score import (
    WEIGHTS,
    assess,
    head_start_minutes,
    option_flags,
)


def _label(question_id: str, option_id: str, language: str = "en") -> str:
    question = QUESTIONS[question_id]
    return next(o.label[language] for o in question.options if o.id == option_id)


def _flag(level: str = "urgent", rule: str = "RF_TEST") -> RedFlag:
    return RedFlag(id=rule, level=level, source="rule", evidence="", reason_en="Test flag.")  # type: ignore[arg-type]


def test_a_patient_cannot_rate_themselves_into_priority() -> None:
    result = assess([], {"chief_concern": "Cough", "severity": 10}, age=25, pregnant=False)
    assert result.level == "routine"
    rating = [c for c in result.components if c.kind == "self_rating"]
    assert len(rating) == 1 and rating[0].points == 4
    assert result.head_start_min <= 3
    assert not evaluate("", facts=None) and "RF_SEVERE_PAIN" not in {f.id for f in option_flags({})}


def test_a_long_standing_problem_rated_ten_gets_no_rating_points() -> None:
    answers = {"duration": _label("duration", "months"), "severity": 10}
    result = assess([], answers, age=40, pregnant=False)
    assert not [c for c in result.components if c.kind == "self_rating"]


def test_tapping_every_warning_sign_is_capped_and_never_urgent() -> None:
    answers: dict[str, str | int] = {}
    for key in WEIGHTS["signals"]:
        question_id, _, option_id = key.partition(".")
        previous = answers.get(question_id)
        label = _label(question_id, option_id)
        answers[question_id] = f"{previous}, {label}" if previous else label
    answers["severity"] = 10
    answers["duration"] = _label("duration", "today")
    result = assess([], answers, age=30, pregnant=False)
    signals = sum(c.points for c in result.components if c.kind == "signal")
    assert signals == WEIGHTS["signals_max"]
    assert result.level != "urgent"


def test_danger_signs_and_red_flag_rules_decide_urgent() -> None:
    assert assess([_flag("urgent")], {}, age=30, pregnant=False).level == "urgent"
    chest = evaluate("chest pain")
    older = assess(chest, {}, age=70, pregnant=False)
    assert older.level == "priority" and older.score == 30 + 8
    assert {c.kind for c in older.components} == {"red_flag", "vulnerability"}


def test_an_ai_flag_alone_never_makes_a_patient_urgent() -> None:
    ai = RedFlag(
        id="RF_CHEST_PAIN_ASSOC",
        level="urgent",
        source="llm",
        evidence="Breathless on walking",
        reason_en="Chest discomfort with another concerning symptom was reported.",
    )
    result = assess([ai], {}, age=70, pregnant=False)
    assert result.level == "priority"
    assert result.components[0].points == WEIGHTS["red_flags"]["priority_points"]
    assert result.components[0].label.startswith("AI suggests, nurse to confirm")
    # A rule or danger tile still makes it urgent.
    assert assess([ai, _flag("urgent")], {}, age=70, pregnant=False).level == "urgent"


def test_every_point_is_explained() -> None:
    answers = {
        "chief_concern": _label("chief_concern", "breathing"),
        "duration": _label("duration", "today"),
        "respiratory": _label("respiratory", "breathless"),
        "severity": 8,
    }
    result = assess([], answers, age=80, pregnant=False)
    assert result.score == sum(component.points for component in result.components)
    labels = [component.label for component in result.components]
    assert "Breathless on walking" in labels and "Aged 75 or over" in labels
    assert result.as_dict()["components"][0]["label"]


def test_a_tapped_answer_counts_the_same_in_every_language() -> None:
    for language in ("en", "hi", "mr"):
        flags = option_flags({"fever": _label("fever", "high", language)})
        assert [flag.id for flag in flags] == ["RF_HIGH_FEVER"], language
        road = option_flags({"mobility": _label("mobility", "road", language)})
        assert [flag.id for flag in road] == ["RF_TRAUMA_MAJOR"], language


def test_head_start_stays_inside_the_level_band() -> None:
    floor = round(WEIGHTS["levels"]["priority"] * WEIGHTS["head_start"]["minutes_per_point"])
    assert head_start_minutes("routine", 100) == floor - 1  # a nurse's "routine" wins
    assert head_start_minutes("priority", 0) == floor  # a nurse's "priority" wins
    assert head_start_minutes("priority", 100) == WEIGHTS["head_start"]["max_minutes"]


def test_nobody_is_overtaken_by_someone_who_came_much_later() -> None:
    first = 1_000_000_000_000
    cap = WEIGHTS["head_start"]["max_minutes"]
    waiting = queue_score(first, "routine", 30, acuity_score=0)
    later = first + (cap + 1) * MILLISECONDS_PER_MINUTE
    soon_after = first + (cap - 5) * MILLISECONDS_PER_MINUTE
    assert queue_score(later, "priority", 30, acuity_score=100) > waiting
    assert queue_score(soon_after, "priority", 30, acuity_score=100) < waiting
    # Urgent always goes first.
    assert queue_score(later, "urgent", 30, acuity_score=0) < waiting
    # Among equal scores it is plain arrival order.
    assert queue_score(first + 1, "routine", 30, acuity_score=5) > queue_score(
        first, "routine", 30, acuity_score=5
    )
