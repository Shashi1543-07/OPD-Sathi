"""AI follow-up questions are safe, slot in before history questions, and never block."""

from typing import Any

import pytest

from app.ai.followup_questions import draft_followups, parse_followups
from app.ai.providers.llm.base import LLMUnavailableError
from app.ai.readback import build_readback_from_answers
from app.domain.interview.bank import QUESTIONS
from app.domain.interview.engine import (
    MAX_QUESTIONS,
    add_followups,
    apply_answer,
    build_case_sheet,
    next_question,
    question_for,
    skip_followups,
    start,
    with_language,
)


def _draft(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "text_en": "What colour is the phlegm?",
        "text_hi": "बलगम का रंग कैसा है?",
        "text_mr": "कफाचा रंग कसा आहे?",
        "multi": False,
        "options": [
            {"en": "White", "hi": "सफ़ेद", "mr": "पांढरा", "none": False},
            {"en": "Yellow, green", "hi": "पीला", "mr": "पिवळा", "none": False},
            {"en": "No phlegm", "hi": "बलगम नहीं", "mr": "कफ नाही", "none": True},
        ],
    }
    item.update(overrides)
    return item


def test_safe_followups_are_kept_and_unsafe_ones_dropped() -> None:
    output = {
        "questions": [
            _draft(),
            _draft(text_en="Could this be dengue?"),
            _draft(text_en="You should drink more water?"),
            _draft(text_hi="What colour is it?"),  # not Devanagari
        ]
    }
    questions = parse_followups(output, [])
    assert [question.prompt["en"] for question in questions] == ["What colour is the phlegm?"]
    question = questions[0]
    assert question.id == "ai_1" and question.module == "ai_followup"
    # Commas inside option labels would break comma-joined answers, so they are replaced.
    assert question.options[1].label["en"] == "Yellow / green"
    assert question.english("पिवळा") == "Yellow / green"


def test_reworded_repeats_of_planned_questions_are_dropped() -> None:
    # Seen live: the model re-asked the bank's stomach-site question in other words.
    reworded = _draft(
        text_en="Where in the stomach do you feel the pain?",
        text_hi="पेट में दर्द कहाँ है?",
        text_mr="पोटात वेदना कुठे आहे?",
        options=[
            {"en": "Upper stomach", "hi": "ऊपरी पेट", "mr": "वरचे पोट", "none": False},
            {"en": "Whole stomach", "hi": "पूरा पेट", "mr": "पूर्ण पोट", "none": False},
            {"en": "None of these", "hi": "कुछ नहीं", "mr": "काहीही नाही", "none": True},
        ],
    )
    planned = [QUESTIONS["digestive_site"]]
    assert parse_followups({"questions": [reworded, _draft()]}, planned) == parse_followups(
        {"questions": [_draft()]}, planned
    )
    # Two drafts that repeat each other keep only the first.
    assert len(parse_followups({"questions": [_draft(), _draft()]}, [])) == 1


def _after_complaint(complaint: str = "Fever, Cough") -> Any:
    state = start("en")
    return apply_answer(state, "chief_concern", complaint)


def test_followups_slot_in_before_history_questions() -> None:
    state = _after_complaint()
    merged = add_followups(state, parse_followups({"questions": [_draft()]}, []))
    first_history = next(i for i, q in enumerate(merged.queued) if q == "past_history")
    assert merged.queued[first_history - 1] == "ai_1"
    assert merged.ai_followups == "merged"
    assert len(merged.queued) <= MAX_QUESTIONS
    # Answer straight through; the AI question is asked and lands in the case sheet.
    answered = merged
    while (question := next_question(answered)) is not None:
        value: str | int = (
            3 if question.input == "scale" else ("पिवळा" if question.id == "ai_1" else "no")
        )
        answered = apply_answer(answered, question.id, value)
    sheet = build_case_sheet(answered)
    assert sheet["followup_questions"] == {"ai_1": "What colour is the phlegm?"}
    assert any(item["text_en"] == "Yellow / green" for item in sheet["hpi"]["associated_symptoms"])


def test_missing_followups_never_block_the_interview() -> None:
    state = skip_followups(_after_complaint())
    assert state.ai_followups == "skipped"
    assert add_followups(state, parse_followups({"questions": [_draft()]}, [])) == state


@pytest.mark.asyncio
async def test_provider_failure_returns_no_followups() -> None:
    class Down:
        async def generate_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise LLMUnavailableError("down")

    assert (
        await draft_followups(
            Down(),
            complaint="fever",
            language="hi",
            age=40,
            sex="male",
            planned_questions=[],
            danger_signs=[],
            session_id="s",
        )
        == []
    )


def test_language_switch_relabels_taps_but_keeps_spoken_words() -> None:
    state = apply_answer(start("mr"), "chief_concern", "ताप, खोकला, रात्री जास्त")
    switched = with_language(state, "en")
    assert next_question(switched) is not None
    question = question_for(switched, "chief_concern")
    assert question.localized(switched.answers["chief_concern"], "en") == "Fever, Cough, रात्री जास्त"
    assert QUESTIONS["chief_concern"].prompt["en"] == question.prompt[switched.language]
    readback = build_readback_from_answers("hi", switched.answers)
    assert "बुखार, खाँसी" in readback
