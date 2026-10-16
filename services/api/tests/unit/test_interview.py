import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.case_sheet import CaseSheet
from app.domain.interview.bank import BANK_DIRECTORY, REVIEW_STATUS, validate_question_bank
from app.domain.interview.consent import get_consent_copy
from app.domain.interview.engine import (
    MAX_QUESTIONS,
    InterviewValidationError,
    apply_answer,
    build_case_sheet,
    correct_answer,
    next_question,
    start,
)


def _run(language: str, complaint: str, answers: list[str | None]) -> tuple[list[str], dict]:
    state = start(language, red_flag_ids=("chest_pain",))  # type: ignore[arg-type]
    identifiers: list[str] = []
    answer_index = 0
    while (question := next_question(state)) is not None:
        identifiers.append(question.id)
        if question.id == "chief_concern":
            value: str | int | None = complaint
        elif question.input == "scale":
            value = answer_index % 11
        else:
            value = answers[answer_index % len(answers)] if answers else None
        state = apply_answer(state, question.id, value, skip=value is None)
        answer_index += 1
        assert len(identifiers) <= MAX_QUESTIONS
    return identifiers, build_case_sheet(state)


def test_question_bank_and_consent_are_valid_draft_content() -> None:
    assert validate_question_bank() == {"questions": 32, "modules": 15}
    for language in ("en", "hi", "mr"):
        consent = get_consent_copy(language)  # type: ignore[arg-type]
        assert consent["text"]
        assert len(consent["text"].split()) <= 60
    for path in [
        BANK_DIRECTORY / "core.json",
        *sorted((BANK_DIRECTORY / "modules").glob("*.json")),
    ]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["review_status"] == REVIEW_STATUS
        for question in payload["questions"]:
            assert all("????" not in question["prompt"][lang] for lang in ("hi", "mr"))
            for option in question.get("options", []):
                assert set(option["label"]) == {"en", "hi", "mr"}
                assert all("," not in option["label"][lang] for lang in ("en", "hi", "mr"))


@pytest.mark.parametrize("broken", ["missing_translation", "bad_review_status", "bad_trigger"])
def test_question_bank_validator_rejects_broken_fixtures(broken: str) -> None:
    target = Path(__file__).parents[1] / "fixtures/question_bank" / broken
    with pytest.raises((ValueError, KeyError)):
        validate_question_bank(target)


@settings(max_examples=500, deadline=None)
@given(
    language=st.sampled_from(["en", "hi", "mr"]),
    complaint=st.text(max_size=120).map(lambda text: f"concern {text}"),
    answers=st.lists(st.one_of(st.none(), st.text(max_size=120)), max_size=12),
)
def test_random_answer_sequences_are_bounded_and_always_build_case_sheet(
    language: str, complaint: str, answers: list[str | None]
) -> None:
    identifiers, case_sheet = _run(language, complaint, answers)
    assert len(identifiers) <= MAX_QUESTIONS
    assert len(identifiers) == len(set(identifiers))
    assert CaseSheet.model_validate(case_sheet)


@pytest.mark.parametrize("language", ["en", "hi", "mr"])
def test_golden_complaint_case_sheet(language: str) -> None:
    identifiers, case_sheet = _run(language, "fever for three days", ["three days", None])
    assert identifiers[:3] == ["chief_concern", "duration", "severity"]
    assert "fever" in identifiers
    assert case_sheet["chief_complaint"]["text_original"] == "fever for three days"
    assert case_sheet["red_flags"][0]["id"] == "CHEST_PAIN"


def test_engine_cleans_controls_rejects_blank_required_and_supports_correction() -> None:
    state = start("en")
    with pytest.raises(InterviewValidationError, match="non-empty"):
        apply_answer(state, "chief_concern", " \x00 \n ")
    state = apply_answer(state, "chief_concern", "fever\x00,  3 days")
    assert state.answers["chief_concern"] == "fever , 3 days"
    state = correct_answer(state, "chief_concern", "headache")
    assert state.answers["chief_concern"] == "headache"
    question = next_question(state)
    assert question is not None and question.id == "duration"
