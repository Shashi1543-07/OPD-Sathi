"""Pure, deterministic intake interview functions; no AI or external calls.

AI follow-up questions, when available, arrive as plain data (``add_followups``) and are
validated like bank questions; the engine itself never calls a model.
"""

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.case_sheet import CaseSheet
from app.domain.interview.bank import QUESTIONS, TRIGGERS, Language, Question

# Core 3 + up to 2 symptom modules (4) + up to 3 AI follow-ups + 5 history questions.
MAX_QUESTIONS = 15
AI_FOLLOWUP_LIMIT = 3
AI_MODULE = "ai_followup"
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
    # Questions written for this patient by the language model (validated, trilingual).
    extra_questions: dict[str, Question] = Field(default_factory=dict)
    ai_followups: Literal["none", "merged", "skipped"] = "none"


def question_for(state: InterviewState, question_id: str) -> Question:
    """Look up a bank question or one of this session's AI follow-up questions."""
    question = QUESTIONS.get(question_id) or state.extra_questions.get(question_id)
    if question is None:
        raise InterviewValidationError("unknown question")
    return question


def start(language: Language, *, red_flag_ids: tuple[str, ...] = ()) -> InterviewState:
    """Start a new interview in a supported patient language."""
    return InterviewState(language=language, red_flag_ids=list(red_flag_ids))


def next_question(state: InterviewState) -> Question | None:
    """Return the current question, or ``None`` when complete or at the hard cap."""
    if len(state.asked) >= MAX_QUESTIONS:
        return None
    for question_id in state.queued:
        if question_id not in state.asked:
            return question_for(state, question_id)
    return None


def with_language(state: InterviewState, language: Language) -> InterviewState:
    """Switch the language used for the remaining questions; answers are kept as given."""
    return state.model_copy(update={"language": language})


def add_followups(state: InterviewState, questions: list[Question]) -> InterviewState:
    """Insert AI follow-ups before the general history questions, within the question cap."""
    if state.ai_followups != "none":
        return state
    extra = {question.id: question for question in questions[:AI_FOLLOWUP_LIMIT]}
    extra = {key: value for key, value in extra.items() if key not in state.queued}
    remaining = [item for item in state.queued if item not in state.asked]
    room = MAX_QUESTIONS - len(state.asked) - len(remaining)
    selected = list(extra)[: max(room, 0)]
    if not selected:
        return state.model_copy(update={"ai_followups": "skipped"})
    always = set(ALWAYS_MODULES)
    insert_at = next(
        (
            index
            for index, item in enumerate(state.queued)
            if item not in state.asked and question_for(state, item).module in always
        ),
        len(state.queued),
    )
    queued = [*state.queued[:insert_at], *selected, *state.queued[insert_at:]]
    return state.model_copy(
        update={
            "queued": queued,
            "extra_questions": {**state.extra_questions, **{key: extra[key] for key in selected}},
            "ai_followups": "merged",
        }
    )


def skip_followups(state: InterviewState) -> InterviewState:
    """Record that AI follow-ups were unavailable so the bank interview continues."""
    if state.ai_followups != "none":
        return state
    return state.model_copy(update={"ai_followups": "skipped"})


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
        included = {question_for(state, item).module for item in queued}
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
    return state.model_copy(update={"queued": queued, "asked": asked, "answers": answers})


_LOCATION_QUESTIONS = ("pain", "digestive_site", "skin_site")
_CHARACTER_QUESTIONS = ("pain_character", "fever")


def _pair(text_original: str, text_en: str, confidence: float = 0.7) -> dict[str, Any]:
    return {
        "text_original": text_original[:500],
        "text_en": text_en[:500],
        "confidence": confidence,
    }


def _parts(question: Question | None, answer: str) -> list[dict[str, Any]]:
    """One text pair per tapped option or typed phrase, with English for known options."""
    if question is None:
        return [_pair(answer, answer, 0.5)]
    return [
        _pair(part, option.label["en"] if option else part, 0.9 if option else 0.5)
        for part, option in question.answer_parts(answer)
        if not (option and option.none)
    ]


def _denial(question: Question, language: Language) -> dict[str, Any] | None:
    """Turn an explicit "none of these" into a pertinent negative listing what was offered."""
    question_id = question.id
    offered = [
        label if label[1:2].isupper() else label[:1].lower() + label[1:]
        for label in (option.label["en"] for option in question.options if not option.none)
    ]
    if question_id == "allergies":
        text_en = "No known drug or food allergy"
    elif not offered:
        return None
    elif question_id == "family_history":
        text_en = "No family history of " + ", ".join(offered[:8])
    else:
        text_en = "Denies " + ", ".join(offered[:8])
    none_labels = [option.label[language] for option in question.options if option.none]
    return _pair(none_labels[0] if none_labels else text_en, text_en, 0.9)


def build_case_sheet(state: InterviewState) -> dict[str, Any]:
    """Build and validate a neutral draft case sheet from patient-stated answers.

    Tapped answers carry their English label, so the doctor gets English text even when
    no language model is configured; typed or spoken words stay as the patient said them.
    """
    complaint = state.answers.get("chief_concern")
    if not isinstance(complaint, str) or not complaint:
        raise InterviewValidationError("a chief concern is required before building the summary")
    hpi: dict[str, Any] = {"associated_symptoms": [], "pertinent_negatives": []}
    duration = state.answers.get("duration")
    if isinstance(duration, str) and duration:
        hpi["onset"] = _pair(duration, QUESTIONS["duration"].english(duration))
    severity = state.answers.get("severity")
    if isinstance(severity, int):
        hpi["severity_0_10"] = severity
    medications: list[dict[str, str]] = []
    allergies: list[dict[str, Any]] = []
    past_conditions: list[str] = []
    family_history: list[dict[str, Any]] = []
    pregnant: bool | None = None
    for question_id, value in state.answers.items():
        if question_id in {*CORE_ORDER, "chief_concern"} or not isinstance(value, str) or not value:
            continue
        question = QUESTIONS.get(question_id) or state.extra_questions.get(question_id)
        if question is not None and question.is_negative(value):
            denial = _denial(question, state.language)
            if denial is not None and question_id not in {"general_context", "medications"}:
                hpi["pertinent_negatives"].append(denial)
            continue
        parts = _parts(question, value)
        if not parts:
            continue
        if question_id == "medications":
            medications.extend(
                {"name": part["text_en"], "source": "patient_stated"} for part in parts
            )
        elif question_id == "allergies":
            allergies.extend(parts)
        elif question_id == "past_history":
            for part in parts:
                if part["text_en"] == "Currently pregnant":
                    pregnant = True
                else:
                    past_conditions.append(part["text_en"])
        elif question_id == "family_history":
            family_history.extend(parts)
        elif question_id in _LOCATION_QUESTIONS and "location" not in hpi:
            joined_original = ", ".join(part["text_original"] for part in parts)
            joined_en = ", ".join(part["text_en"] for part in parts)
            hpi["location"] = _pair(joined_original, joined_en)
        elif question_id in _CHARACTER_QUESTIONS and "character" not in hpi:
            joined_original = ", ".join(part["text_original"] for part in parts)
            joined_en = ", ".join(part["text_en"] for part in parts)
            hpi["character"] = _pair(joined_original, joined_en)
        else:
            hpi["associated_symptoms"].extend(parts)
    hpi["associated_symptoms"] = hpi["associated_symptoms"][:30]
    hpi["pertinent_negatives"] = hpi["pertinent_negatives"][:30]
    data: dict[str, Any] = {
        "schema_version": "1.0",
        "language": state.language,
        "chief_complaint": _pair(complaint, QUESTIONS["chief_concern"].english(complaint)),
        "hpi": hpi,
        "past_history": {"conditions": past_conditions[:30]},
        "medications": medications[:30],
        "allergies": allergies[:30],
        "family_history": family_history[:30],
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
    if pregnant:
        data["women_health"] = {"pregnant": True}
    followups = {
        question_id: question.prompt["en"]
        for question_id, question in state.extra_questions.items()
        if question_id in state.asked
    }
    if followups:
        data["followup_questions"] = followups
    return CaseSheet.model_validate(data).model_dump(mode="json")


def correct_answer(
    state: InterviewState, question_id: str, answer: str | int | None
) -> InterviewState:
    """Replace an already answered value without changing the interview order."""
    if question_id not in state.asked or question_id not in state.answers:
        raise InterviewValidationError("only an answered question can be changed")
    question = question_for(state, question_id)
    if answer is None:
        raise InterviewValidationError("an answer is required")
    value = _validate_answer(question, answer)
    answers = {**state.answers, question_id: value}
    queued = list(state.queued)
    if question_id == "chief_concern" and isinstance(value, str):
        included = {question_for(state, item).module for item in queued}
        modules = list(dict.fromkeys([*_trigger_modules(value), *ALWAYS_MODULES]))
        for module in modules:
            if module not in included:
                queued.extend(
                    bank_question.id
                    for bank_question in QUESTIONS.values()
                    if bank_question.module == module
                )
                included.add(module)
    return state.model_copy(update={"queued": queued, "answers": answers})
