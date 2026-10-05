"""Load and validate the clinician-reviewable multilingual question bank."""

import json
from pathlib import Path
from typing import Literal, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["en", "hi", "mr"]
REVIEW_STATUS = "draft_needs_clinician_review"
BANK_DIRECTORY = Path(__file__).with_name("bank")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Question(StrictModel):
    id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    module: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    input: Literal["text", "scale"]
    optional: bool = True
    prompt: dict[Language, str]


class TriggerConfiguration(TypedDict):
    review_status: str
    always: list[str]
    max_triggered_modules: int
    keywords: dict[str, list[str]]


def load_question_bank(
    directory: Path = BANK_DIRECTORY,
) -> tuple[dict[str, Question], TriggerConfiguration]:
    """Read the core and thirteen module files, failing closed on invalid content."""
    files = [directory / "core.json", *sorted((directory / "modules").glob("*.json"))]
    if len(files) != 14 or not files[0].is_file():
        raise ValueError("question bank must contain core.json and exactly 13 modules")
    questions: dict[str, Question] = {}
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("review_status") != REVIEW_STATUS:
            raise ValueError(f"{path.name} must remain marked {REVIEW_STATUS}")
        entries = raw.get("questions")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{path.name} must contain at least one question")
        for entry in entries:
            question = Question.model_validate(entry)
            if set(question.prompt) != {"en", "hi", "mr"} or any(
                not question.prompt[language].strip() for language in ("en", "hi", "mr")
            ):
                raise ValueError(f"{question.id} must have non-empty en, hi, and mr prompts")
            if question.id in questions:
                raise ValueError(f"duplicate question id: {question.id}")
            questions[question.id] = question
    if len([item for item in questions.values() if item.module == "core"]) != 3:
        raise ValueError("the core interview must contain exactly three questions")
    raw_triggers = json.loads((directory / "triggers.json").read_text(encoding="utf-8"))
    if not isinstance(raw_triggers, dict):
        raise ValueError("trigger configuration must be an object")
    triggers = cast(TriggerConfiguration, raw_triggers)
    if (
        triggers.get("review_status") != REVIEW_STATUS
        or not isinstance(triggers.get("always"), list)
        or not isinstance(triggers.get("max_triggered_modules"), int)
        or not isinstance(triggers.get("keywords"), dict)
    ):
        raise ValueError("trigger configuration has an invalid shape")
    if triggers["max_triggered_modules"] < 0 or triggers["max_triggered_modules"] > 5:
        raise ValueError("trigger configuration may select at most five modules")
    if any(not isinstance(module, str) for module in triggers["always"]):
        raise ValueError("always-on modules must be strings")
    if any(
        not isinstance(module, str)
        or not isinstance(terms, list)
        or not terms
        or any(not isinstance(term, str) or not term.strip() for term in terms)
        for module, terms in triggers["keywords"].items()
    ):
        raise ValueError("trigger terms must be non-empty strings")
    module_names = {question.module for question in questions.values()} - {"core"}
    if len(module_names) != 13:
        raise ValueError("question bank must contain exactly 13 non-core modules")
    if set(triggers.get("always", [])) - module_names:
        raise ValueError("trigger configuration refers to an unknown always-on module")
    if set(triggers.get("keywords", {})) - module_names:
        raise ValueError("trigger configuration refers to an unknown module")
    if set(triggers["always"]) & set(triggers["keywords"]):
        raise ValueError("a module cannot be both always-on and keyword-triggered")
    return questions, triggers


QUESTIONS, TRIGGERS = load_question_bank()


def validate_question_bank(directory: Path = BANK_DIRECTORY) -> dict[str, int]:
    questions, _ = load_question_bank(directory)
    return {"questions": len(questions), "modules": len({q.module for q in questions.values()}) - 1}
