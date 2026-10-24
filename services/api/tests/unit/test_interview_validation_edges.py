import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.interview import bank, consent
from app.domain.interview.engine import (
    InterviewValidationError,
    apply_answer,
    build_case_sheet,
    correct_answer,
    next_question,
    start,
)


def _patch_json_file(
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    mutate: Any,
) -> None:
    original = Path.read_text

    def patched(path: Path, *args: object, **kwargs: object) -> str:
        content = original(path, *args, **kwargs)
        if path.name == filename:
            value = json.loads(content)
            updated = mutate(value)
            return json.dumps(value if updated is None else updated, ensure_ascii=False)
        return content

    monkeypatch.setattr(Path, "read_text", patched)


@pytest.mark.parametrize(
    ("filename", "mutate"),
    [
        ("fever.json", lambda value: value.update(questions=[])),
        (
            "allergies.json",
            lambda value: value["questions"][0].update(id="chief_concern"),
        ),
        (
            "core.json",
            lambda value: (value["questions"].pop(), None)[1],
        ),
        (
            "general_context.json",
            lambda value: value["questions"][0].update(module="pain"),
        ),
        ("triggers.json", lambda _value: []),
        ("triggers.json", lambda value: value.update(always="bad-shape")),
        ("triggers.json", lambda value: value.update(max_triggered_modules=6)),
        ("triggers.json", lambda value: value.update(always=[1])),
        ("triggers.json", lambda value: value.update(always=["unknown_module"])),
        (
            "triggers.json",
            lambda value: value["keywords"].update(unknown_module=["x"]),
        ),
        (
            "triggers.json",
            lambda value: value["keywords"].update(general_context=["context"]),
        ),
    ],
)
def test_question_bank_rejects_invalid_shapes(
    monkeypatch: pytest.MonkeyPatch, filename: str, mutate: Any
) -> None:
    _patch_json_file(monkeypatch, filename, mutate)
    with pytest.raises((ValueError, TypeError)):
        bank.load_question_bank()


def test_question_bank_rejects_missing_bank_directory() -> None:
    with pytest.raises(ValueError, match="exactly 16 modules"):
        bank.load_question_bank(Path("missing-interview-bank"))


@pytest.mark.parametrize(
    "content",
    [
        "consent without metadata",
        "---\nreview_status: approved\nversion: x\n---\nText",
        "---\nreview_status: draft_needs_clinician_review\nversion: x\n---\n",
        "---\nreview_status: draft_needs_clinician_review\nversion: x\n---\n" + "word " * 61,
    ],
)
def test_consent_copy_validator_rejects_invalid_versions(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    original = Path.read_text

    def patched(path: Path, *args: object, **kwargs: object) -> str:
        return content if path.name == "en.md" else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", patched)
    with pytest.raises(ValueError):
        consent.get_consent_copy("en")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(review_status="approved"),
        lambda value: value.update(tiles=[]),
        lambda value: value["tiles"].update(bad=None),
        lambda value: value["tiles"]["chest_pain"].update(mr=""),
    ],
)
def test_danger_tile_validator_rejects_invalid_content(
    monkeypatch: pytest.MonkeyPatch, mutation: Any
) -> None:
    _patch_json_file(monkeypatch, "danger_tiles.json", mutation)
    with pytest.raises((ValueError, TypeError)):
        consent.load_danger_tiles()


def test_interview_engine_rejects_invalid_answers_and_builds_history_fields() -> None:
    state = start("en")
    with pytest.raises(InterviewValidationError, match="cannot be skipped"):
        apply_answer(state, "chief_concern", None, skip=True)
    with pytest.raises(InterviewValidationError, match="required"):
        apply_answer(state, "chief_concern", None)
    with pytest.raises(InterviewValidationError, match="strings"):
        apply_answer(state, "chief_concern", 3)
    with pytest.raises(InterviewValidationError, match="500 characters"):
        apply_answer(state, "chief_concern", "x" * 501)
    with pytest.raises(InterviewValidationError, match="current question"):
        apply_answer(state, "not_a_question", "answer")
    with pytest.raises(InterviewValidationError, match="chief concern"):
        build_case_sheet(state)

    state = apply_answer(
        state, "chief_concern", "pain fever breath stomach skin rash urine walk sleep"
    )
    assert next_question(state) is not None
    state = apply_answer(state, "duration", "3 days")
    with pytest.raises(InterviewValidationError, match="0 to 10"):
        apply_answer(state, "severity", 11)
    state = apply_answer(state, "severity", 5)
    while (question := next_question(state)) is not None:
        state = apply_answer(state, question.id, f"answer for {question.id}")
    assert next_question(state) is None
    sheet = build_case_sheet(state)
    assert sheet["family_history"][0]["text_original"] == "answer for family_history"


@pytest.mark.parametrize(
    "answer",
    [
        "<script>alert('x')</script>",
        "' OR 1=1; DROP TABLE visits; --",
        "patient\x00reported\nfever",
        "pain 😀",
    ],
)
def test_interview_treats_adversarial_text_as_bounded_patient_text(answer: str) -> None:
    state = start("en")
    state = apply_answer(state, "chief_concern", answer)
    cleaned = state.answers["chief_concern"]
    assert isinstance(cleaned, str)
    assert len(cleaned) <= 500
    assert "\x00" not in cleaned
    assert "\n" not in cleaned
    assert cleaned == answer.replace("\x00", " ").replace("\n", " ").strip()


def test_engine_reports_family_history_and_rejects_skipped_corrections() -> None:
    state = start("en")
    state = apply_answer(state, "chief_concern", "general concern")
    state = apply_answer(state, "duration", "three days")
    state = apply_answer(state, "severity", 2)
    while (question := next_question(state)) is not None:
        if question.id == "family_history":
            state = apply_answer(state, question.id, "No known family history")
        else:
            state = apply_answer(state, question.id, None, skip=True)
    assert (
        build_case_sheet(state)["family_history"][0]["text_original"] == "No known family history"
    )
    with pytest.raises(InterviewValidationError, match="only an answered"):
        correct_answer(state, "allergies", "new allergy")
    with pytest.raises(InterviewValidationError, match="required"):
        correct_answer(state, "chief_concern", None)
