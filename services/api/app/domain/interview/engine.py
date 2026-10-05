"""Pure, deterministic intake interview functions; no AI or external calls."""

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.case_sheet import CaseSheet
from app.domain.interview.bank import QUESTIONS, TRIGGERS, Language, Question

MAX_QUESTIONS = 12
CORE_ORDER = ("chief_concern", "duration", "severity")
ALWAYS_MODULES: tuple[str, ...] = tuple(TRIGGERS["always"])


class InterviewValidationError(ValueError):
    """An answer does not match the current deterministic interview question."""


class InterviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language
    queued: list[str] = Field(default_factory=lambda: list(CORE_ORDER))
    asked: list[str] = Field(default_factory=list)
    answers: dict[str, str | int] = Field(default_factory=dict)
    red_flag_ids: list[str] = Field(default_factory=list)


def start(language: Language, *, red_flag_ids: tuple[str, ...] = ()) -> InterviewState:
    """Start a new interview in a supported patient language."""
    return InterviewState(language=language, red_flag_ids=list(red_flag_ids))


def next_question(state: InterviewState) -> Question | None:
    """Return the current question, or ``None`` when complete or at the hard cap."""
    if len(state.asked) >= MAX_QUESTIONS:
        return None
    for question_id in state.queued:
        if question_id not in state.asked:
            return QUESTIONS[question_id]
    return None


def _trigger_modules(complaint: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", complaint).casefold()
    keywords = TRIGGERS["keywords"]
    selected = [
        module
        for module, terms in keywords.items()
        if any(term.casefold() in normalized for term in terms)
    ]
    maximum = int(TRIGGERS["max_triggered_modules"])
    return selected[:maximum]


def _validate_answer(question: Question, answer: str | int) -> str | int:
    if question.input == "scale":
        if isinstance(answer, bool) or not isinstance(answer, int) or not 0 <= answer <= 10:
            raise InterviewValidationError("scale answers must be a whole number from 0 to 10")
        return answer
    if not isinstance(answer, str):
        raise InterviewValidationError("text answers must be strings")
    cleaned = "".join(
        " " if unicodedata.category(character) == "Cc" else character for character in answer
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > 500:
        raise InterviewValidationError("answers may contain at most 500 characters")
    if not question.optional and not cleaned:
        raise InterviewValidationError("this question requires a non-empty answer")
    return cleaned


def apply_answer(
    state: InterviewState,
    question_id: str,
    answer: str | int | None,
    *,
    skip: bool = False,
) -> InterviewState:
    """Validate the current response and return a new state without mutating input."""
    question = next_question(state)
    if question is None or question.id != question_id:
        raise InterviewValidationError("answer does not match the current question")
    if skip:
        if not question.optional:
            raise InterviewValidationError("this question cannot be skipped")
        value: str | int | None = None
    elif answer is None:
        raise InterviewValidationError("an answer is required")
    else:
        value = _validate_answer(question, answer)

    answers = dict(state.answers)
    if value is not None:
        answers[question_id] = value
    queued = list(state.queued)
    if question_id == "chief_concern" and isinstance(value, str):
        included = {QUESTIONS[item].module for item in queued}
        modules = list(dict.fromkeys([*_trigger_modules(value), *ALWAYS_MODULES]))
        for module in modules:
            if module not in included:
                queued.extend(
                    bank_question.id
                    for bank_question in QUESTIONS.values()
                    if bank_question.module == module
                )
                included.add(module)
    asked = [*state.asked, question_id]
    if len(asked) > MAX_QUESTIONS:
        raise InterviewValidationError("the interview question limit was exceeded")
    return InterviewState(
        language=state.language,
        queued=queued,
        asked=asked,
        answers=answers,
        red_flag_ids=state.red_flag_ids,
    )


def build_case_sheet(state: InterviewState) -> dict[str, Any]:
    """Build and validate a neutral draft case sheet from patient-stated answers."""
    complaint = state.answers.get("chief_concern")
    if not isinstance(complaint, str) or not complaint:
        raise InterviewValidationError("a chief concern is required before building the summary")
    hpi: dict[str, Any] = {"associated_symptoms": []}
    duration = state.answers.get("duration")
    if isinstance(duration, str) and duration:
        hpi["onset"] = {"text_original": duration, "text_en": duration, "confidence": 0.5}
    severity = state.answers.get("severity")
    if isinstance(severity, int):
        hpi["severity_0_10"] = severity
    medications: list[dict[str, str]] = []
    allergies: list[dict[str, Any]] = []
    past_conditions: list[str] = []
    family_history: list[dict[str, Any]] = []
    for question_id, value in state.answers.items():
        if question_id in {*CORE_ORDER, "chief_concern"} or not isinstance(value, str) or not value:
            continue
        if question_id == "medications":
            medications.append({"name": value, "source": "patient_stated"})
        elif question_id == "allergies":
            allergies.append({"text_original": value, "text_en": value, "confidence": 0.5})
        elif question_id == "past_history":
            past_conditions.append(value)
        elif question_id == "family_history":
            family_history.append(
                {"text_original": value, "text_en": value, "confidence": 0.5}
            )
        else:
            hpi["associated_symptoms"].append(
                {"text_original": value, "text_en": value, "confidence": 0.5}
            )
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "language": state.language,
        "chief_complaint": {
            "text_original": complaint,
            "text_en": complaint,
            "confidence": 0.5,
        },
        "hpi": hpi,
        "past_history": {"conditions": past_conditions},
        "medications": medications,
        "allergies": allergies,
        "family_history": family_history,
        "red_flags": [
            {
                "id": tile_id.upper(),
                "level": "urgent",
                "source": "tile",
                "evidence": "Patient selected this help tile.",
            }
            for tile_id in state.red_flag_ids
        ],
        "routing": {
            "department_code": "GENMED",
            "confidence": 0.25,
            "reason_en": "Default department selected; staff confirmation is required.",
            "needs_nurse_confirmation": True,
        },
        "field_meta": {
            "/chief_complaint": {"source": "patient_text", "confidence": 0.5, "confirmed": False}
        },
    }
    return CaseSheet.model_validate(data).model_dump(mode="json")


def correct_answer(
    state: InterviewState, question_id: str, answer: str | int | None
) -> InterviewState:
    """Replace an already answered value without changing the interview order."""
    if question_id not in state.asked or question_id not in state.answers:
        raise InterviewValidationError("only an answered question can be changed")
    question = QUESTIONS[question_id]
    if answer is None:
        raise InterviewValidationError("an answer is required")
    value = _validate_answer(question, answer)
    answers = {**state.answers, question_id: value}
    queued = list(state.queued)
    if question_id == "chief_concern" and isinstance(value, str):
        included = {QUESTIONS[item].module for item in queued}
        modules = list(dict.fromkeys([*_trigger_modules(value), *ALWAYS_MODULES]))
        for module in modules:
            if module not in included:
                queued.extend(
                    bank_question.id
                    for bank_question in QUESTIONS.values()
                    if bank_question.module == module
                )
                included.add(module)
    return InterviewState(
        language=state.language,
        queued=queued,
        asked=state.asked,
        answers=answers,
        red_flag_ids=state.red_flag_ids,
    )
