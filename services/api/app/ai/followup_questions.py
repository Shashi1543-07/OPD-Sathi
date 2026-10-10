"""AI-written follow-up questions tailored to the patient's main complaint.

After the patient names their main problem, the model drafts up to three extra
history-taking questions (in English, Hindi and Marathi, with tap-to-answer options)
while the patient keeps answering the bank questions. Every question is checked here;
anything unsafe or malformed is dropped and the clinician-reviewed bank is used instead.
"""

import re
from typing import Any

from pydantic import ValidationError

from app.ai.prompts.builders import build_followup_prompt
from app.ai.providers.llm.base import LLMUnavailableError
from app.domain.interview.bank import AnswerOption, Question
from app.domain.interview.engine import AI_FOLLOWUP_LIMIT, AI_MODULE

_TEXT = {"type": "string", "maxLength": 160}
_LABEL = {"type": "string", "maxLength": 50}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "maxItems": AI_FOLLOWUP_LIMIT,
            "items": {
                "type": "object",
                "properties": {
                    "text_en": _TEXT,
                    "text_hi": _TEXT,
                    "text_mr": _TEXT,
                    "multi": {"type": "boolean"},
                    "options": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {
                            "type": "object",
                            "properties": {
                                "en": _LABEL,
                                "hi": _LABEL,
                                "mr": _LABEL,
                                "none": {"type": "boolean"},
                            },
                            "required": ["en", "hi", "mr", "none"],
                        },
                    },
                },
                "required": ["text_en", "text_hi", "text_mr", "multi", "options"],
            },
        }
    },
    "required": ["questions"],
}

# A follow-up may ask about symptoms; it must never guess a disease or tell the patient
# what to do. Patient-facing text is held to a stricter standard than the doctor's summary.
_BLOCKED = re.compile(
    r"\b(?:diagnos\w*|could be|may be|might be|sign of|symptom of|suggest\w*|likely|"
    r"should|must|recommend\w*|advis\w*|prescri\w*|dose|dosage|\d+\s?mg|tablet\w*|"
    r"dengue|malaria|typhoid|covid|corona|pneumonia|tuberculosis|cancer|heart attack|"
    r"stroke|appendicitis|hepatitis|infection|https?://|www\.)",
    re.I,
)
_BLOCKED_INDIC = (
    "निदान",
    "चाहिए",
    "कीजिए",
    "करें",
    "लीजिए",
    "पाहिजे",
    "करावे",
    "घ्यावे",
    "घ्या ",
    "डेंगू",
    "मलेरिया",
    "टाइफाइड",
    "कोरोना",
    "निमोनिया",
    "कैंसर",
    "कर्करोग",
    "हार्ट अटैक",
    "हो सकता है",
    "हो सकती है",
    "असू शकतो",
    "असू शकते",
)
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Questions that only make sense for one sex (or not for young children), checked in every
# language on the question and its options. The prompt asks for this too; this enforces it.
_FEMALE_ONLY = re.compile(
    r"\b(?:periods?|menstru\w*|menses|pregnan\w*|lmp|vagina\w*|white discharge|"
    r"breast ?feed\w*)\b|माहवारी|मासिक|पीरियड|गर्भ|स्तनपान|पाळी|योनी",
    re.I,
)
_MALE_ONLY = re.compile(r"\b(?:testic\w*|testis|scrot\w*|penis)\b|अंडकोष|वृषण", re.I)


def _fits_patient(question: Question, *, sex: str | None, age: int | None) -> bool:
    texts = [*question.prompt.values()]
    texts += [label for option in question.options for label in option.label.values()]
    joined = " ".join(texts)
    if sex == "male" and _FEMALE_ONLY.search(joined):
        return False
    if sex == "female" and _MALE_ONLY.search(joined):
        return False
    return not (age is not None and age < 10 and _FEMALE_ONLY.search(joined))


_STOPWORDS = {
    "you",
    "your",
    "the",
    "and",
    "any",
    "are",
    "did",
    "does",
    "have",
    "how",
    "what",
    "when",
    "where",
    "which",
    "with",
    "this",
    "that",
    "for",
    "feel",
    "notice",
    "there",
    "been",
    "about",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.casefold()) if len(w) > 2 and w not in _STOPWORDS}


def _overlaps(prompt: str, options: list[str], planned: list[tuple[str, set[str]]]) -> bool:
    """True when a draft repeats a planned question in other words or with the same options.

    A draft also repeats a planned question when it asks about one of that question's
    answer choices: "Do you bring up any phlegm?" repeats "Cough with phlegm" from "Tell us
    about the cough". Words the planned question already uses ("cough") do not count.
    """
    words = _words(prompt)
    chosen = {label.casefold() for label in options}
    for planned_prompt, planned_options in planned:
        other = _words(planned_prompt)
        if words and other and len(words & other) / len(words | other) >= 0.5:
            return True
        if len(chosen & planned_options) >= 2:
            return True
        for planned_label in planned_options:
            distinctive = _words(planned_label) - other
            if distinctive and distinctive <= words:
                return True
    return False


class UnsafeFollowupError(ValueError):
    """A drafted follow-up question failed validation."""


def _clean(text: object, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value or len(value) > limit:
        raise UnsafeFollowupError("text is empty or too long")
    if _BLOCKED.search(value) or any(word in value for word in _BLOCKED_INDIC):
        raise UnsafeFollowupError("text guesses a condition or gives advice")
    return value


def _label(text: object) -> str:
    # Answers are stored comma-separated, so option labels may not contain commas.
    return _clean(str(text or "").replace(",", " /").replace("،", " /"), 50)


def _question(index: int, item: dict[str, Any], planned: list[tuple[str, set[str]]]) -> Question:
    prompt = {
        "en": _clean(item.get("text_en"), 160),
        "hi": _clean(item.get("text_hi"), 160),
        "mr": _clean(item.get("text_mr"), 160),
    }
    if not prompt["en"].endswith("?"):
        raise UnsafeFollowupError("the English text is not a question")
    if not (_DEVANAGARI.search(prompt["hi"]) and _DEVANAGARI.search(prompt["mr"])):
        raise UnsafeFollowupError("Hindi and Marathi text must be in Devanagari")
    options: list[AnswerOption] = []
    for number, raw in enumerate(item.get("options") or [], start=1):
        options.append(
            AnswerOption(
                id=f"opt_{number}",
                label={
                    "en": _label(raw.get("en")),
                    "hi": _label(raw.get("hi")),
                    "mr": _label(raw.get("mr")),
                },
                none=bool(raw.get("none")),
            )
        )
    if len(options) < 2:
        raise UnsafeFollowupError("a follow-up needs at least two answer options")
    if _overlaps(
        prompt["en"], [option.label["en"] for option in options if not option.none], planned
    ):
        raise UnsafeFollowupError("repeats a planned question")
    try:
        return Question(
            id=f"ai_{index}",
            module=AI_MODULE,
            input="text",
            optional=True,
            prompt=prompt,
            options=options,
            multi=bool(item.get("multi", True)),
        )
    except ValidationError as exc:
        raise UnsafeFollowupError("follow-up does not match the question schema") from exc


def parse_followups(
    output: dict[str, Any],
    planned: list[Question],
    *,
    sex: str | None = None,
    age: int | None = None,
) -> list[Question]:
    """Keep only the drafted questions that pass every check (may return an empty list).

    Each accepted draft also counts as planned, so two drafts cannot repeat each other, and
    a question that cannot apply to this patient's sex or age is dropped.
    """
    # The main-problem choices name what the patient already said ("Cough"); a follow-up
    # about it is the point, so only the other planned questions' choices count as asked.
    seen = [
        (
            question.prompt["en"],
            {o.label["en"].casefold() for o in question.options if not o.none}
            if question.id != "chief_concern"
            else set(),
        )
        for question in planned
    ]
    questions: list[Question] = []
    for item in (output.get("questions") or [])[:AI_FOLLOWUP_LIMIT]:
        if not isinstance(item, dict):
            continue
        try:
            question = _question(len(questions) + 1, item, seen)
        except UnsafeFollowupError:
            continue
        if not _fits_patient(question, sex=sex, age=age):
            continue
        questions.append(question)
        seen.append(
            (
                question.prompt["en"],
                {o.label["en"].casefold() for o in question.options if not o.none},
            )
        )
    return questions


def _guard(output: Any) -> None:
    if not isinstance(output, dict) or not isinstance(output.get("questions"), list):
        raise UnsafeFollowupError("unexpected follow-up shape")


async def draft_followups(
    provider: Any,
    *,
    complaint: str,
    language: str,
    age: int | None,
    sex: str | None,
    planned_questions: list[Question],
    danger_signs: list[str],
    session_id: str,
    duration: str = "",
    timeout: float = 10.0,
) -> list[Question]:
    """Ask the model for follow-ups; any failure returns an empty list (bank fallback)."""
    prompt = build_followup_prompt(
        complaint=complaint,
        duration=duration,
        language=language,
        age=age,
        sex=sex,
        # The answer choices too, so the model does not ask about them again.
        planned_questions=[
            question.prompt["en"]
            + (
                " (choices: " + ", ".join(o.label["en"] for o in question.options) + ")"
                if question.options
                else ""
            )
            for question in planned_questions
        ],
        danger_signs=danger_signs,
    )
    try:
        output = await provider.generate_json(
            prompt.text, _SCHEMA, timeout=timeout, session_id=session_id, guard=_guard
        )
    except (LLMUnavailableError, UnsafeFollowupError, ValueError):
        return []
    return parse_followups(output, planned_questions, sex=sex, age=age)
