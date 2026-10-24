"""Load and validate the clinician-reviewable multilingual question bank."""

import json
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field

from app.domain.review import check_review

Language = Literal["en", "hi", "mr"]
Sex = Literal["female", "male", "other", "unknown"]
BANK_DIRECTORY = Path(__file__).with_name("bank")
# Symptom and history modules, each in its own file next to core.json.
MODULE_COUNT = 16
# Asked only where the hospital turns on Ayurveda history (an AYUSH hospital).
AYURVEDA_MODULE = "ayurveda"
# A one-screen check of the other body systems, asked where the hospital turns it on.
REVIEW_OF_SYSTEMS_MODULE = "review_of_systems"
# Modules each hospital chooses; keyword triggers never select them.
HOSPITAL_MODULES = (REVIEW_OF_SYSTEMS_MODULE, AYURVEDA_MODULE)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Applicability(StrictModel):
    """When a question or option is shown; every rule must pass (empty rules always pass).

    ``hide_with_modules`` hides it when another module in this interview already covers the
    same topic, and ``hide_with_answers`` when an earlier answer makes it irrelevant.
    """

    only_sexes: list[Sex] = Field(default_factory=list)
    min_age: int | None = Field(default=None, ge=0, le=120)
    max_age: int | None = Field(default=None, ge=0, le=120)
    hide_with_modules: list[str] = Field(default_factory=list)
    hide_with_answers: dict[str, list[str]] = Field(default_factory=dict)


class AnswerOption(Applicability):
    """A tap-to-answer choice; ``none`` marks an explicit negative such as "No allergy".

    ``unknown`` ("Don't know") and ``declined`` ("Prefer not to say") are not negatives:
    the doctor sees them as open questions, never as a denial. ``excludes`` lists options
    that contradict this one, so they cannot both be picked.
    """

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: dict[Language, str]
    none: bool = False
    unknown: bool = False
    declined: bool = False
    excludes: list[str] = Field(default_factory=list)

    @property
    def non_answer(self) -> bool:
        """A choice that is not itself a finding: "none", "don't know" or "prefer not to say"."""
        return self.none or self.unknown or self.declined


class Question(Applicability):
    id: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    module: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    input: Literal["text", "scale"]
    optional: bool = True
    prompt: dict[Language, str]
    options: list[AnswerOption] = Field(default_factory=list, max_length=16)
    multi: bool = True
    # Skip when every tapped main problem is one of these (the answer is already known).
    skip_if_only_concerns: list[str] = Field(default_factory=list)
    # What the question is about, for staff ("past operations"); defaults to the prompt.
    topic: str | None = Field(default=None, max_length=80)

    def option_for_label(self, label: str) -> AnswerOption | None:
        """Find the option whose label (in any language) matches ``label`` exactly."""
        needle = label.strip().casefold()
        for option in self.options:
            if any(text.strip().casefold() == needle for text in option.label.values()):
                return option
        return None

    def answer_parts(self, answer: str) -> list[tuple[str, AnswerOption | None]]:
        """Split a comma-joined answer into parts, matching tapped option labels."""
        parts = [part.strip() for part in answer.replace("،", ",").split(",")]
        return [(part, self.option_for_label(part)) for part in parts if part]

    def english(self, answer: str) -> str:
        """English text for tapped options; free-text parts are kept as spoken."""
        return ", ".join(
            option.label["en"] if option else part for part, option in self.answer_parts(answer)
        )

    def localized(self, answer: str | int, language: Language) -> str | int:
        """Show tapped options in ``language``; typed or spoken words stay as given."""
        if not isinstance(answer, str):
            return answer
        return ", ".join(
            option.label[language] if option else part for part, option in self.answer_parts(answer)
        )

    def is_negative(self, answer: str) -> bool:
        """True when every part of the answer is an explicit "none" option."""
        parts = self.answer_parts(answer)
        return bool(parts) and all(option is not None and option.none for _, option in parts)

    def open_status(self, answer: str) -> Literal["unknown", "declined"] | None:
        """ "unknown" or "declined" when the whole answer is "Don't know" or "Prefer not to say"."""
        parts = self.answer_parts(answer)
        if not parts or any(option is None for _, option in parts):
            return None
        if all(option is not None and option.declined for _, option in parts):
            return "declined"
        if all(option is not None and (option.unknown or option.declined) for _, option in parts):
            return "unknown"
        return None


class TriggerConfiguration(TypedDict):
    review_status: str
    review: NotRequired[dict[str, object]]
    always: list[str]
    max_triggered_modules: int
    keywords: dict[str, list[str]]
    # Phrases that contain a module keyword but mean something else ("fell ill").
    ignore_phrases: NotRequired[dict[str, list[str]]]


def _check_rules(questions: dict[str, Question], modules: set[str]) -> None:
    """Fail closed when an applicability rule refers to something that does not exist."""
    concern_ids = {option.id for option in questions["chief_concern"].options}
    for question in questions.values():
        option_ids = {option.id for option in question.options}
        items: list[tuple[str, Applicability]] = [(question.id, question)]
        items += [(f"{question.id}.{option.id}", option) for option in question.options]
        for name, item in items:
            if item.min_age is not None and item.max_age is not None:
                if item.min_age > item.max_age:
                    raise ValueError(f"{name} has min_age above max_age")
            if set(item.hide_with_modules) - modules:
                raise ValueError(f"{name} hides with an unknown module")
            for other_id, other_options in item.hide_with_answers.items():
                other = questions.get(other_id)
                if other is None or set(other_options) - {o.id for o in other.options}:
                    raise ValueError(f"{name} hides with an unknown answer")
        for option in question.options:
            if set(option.excludes) - option_ids or option.id in option.excludes:
                raise ValueError(f"{question.id}.{option.id} excludes an unknown option")
        if set(question.skip_if_only_concerns) - concern_ids:
            raise ValueError(f"{question.id} skips for an unknown main problem")


def load_question_bank(
    directory: Path = BANK_DIRECTORY,
) -> tuple[dict[str, Question], TriggerConfiguration]:
    """Read the core and module files, failing closed on invalid content."""
    files = [directory / "core.json", *sorted((directory / "modules").glob("*.json"))]
    if len(files) != MODULE_COUNT + 1 or not files[0].is_file():
        raise ValueError(f"question bank must contain core.json and exactly {MODULE_COUNT} modules")
    questions: dict[str, Question] = {}
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        check_review(f"bank/{path.relative_to(directory).as_posix()}",
                     raw.get("review_status"), raw.get("review"))
        entries = raw.get("questions")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{path.name} must contain at least one question")
        for entry in entries:
            question = Question.model_validate(entry)
            if set(question.prompt) != {"en", "hi", "mr"} or any(
                not question.prompt[language].strip() for language in ("en", "hi", "mr")
            ):
                raise ValueError(f"{question.id} must have non-empty en, hi, and mr prompts")
            for option in question.options:
                if set(option.label) != {"en", "hi", "mr"} or any(
                    not option.label[language].strip() or "," in option.label[language]
                    for language in ("en", "hi", "mr")
                ):
                    raise ValueError(f"{question.id} option {option.id} needs en, hi, mr labels")
                if option.none + option.unknown + option.declined > 1:
                    raise ValueError(
                        f"{question.id} option {option.id} can be only one of none, unknown, "
                        "declined"
                    )
            if question.id in questions:
                raise ValueError(f"duplicate question id: {question.id}")
            questions[question.id] = question
    if len([item for item in questions.values() if item.module == "core"]) != 3:
        raise ValueError("the core interview must contain exactly three questions")
    raw_triggers = json.loads((directory / "triggers.json").read_text(encoding="utf-8"))
    if not isinstance(raw_triggers, dict):
        raise ValueError("trigger configuration must be an object")
    triggers = cast(TriggerConfiguration, raw_triggers)
    check_review("bank/triggers.json", triggers.get("review_status"), raw_triggers.get("review"))
    if (
        not isinstance(triggers.get("always"), list)
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
    if len(module_names) != MODULE_COUNT or set(HOSPITAL_MODULES) - module_names:
        raise ValueError(f"question bank must contain exactly {MODULE_COUNT} non-core modules")
    if set(triggers.get("always", [])) - module_names:
        raise ValueError("trigger configuration refers to an unknown always-on module")
    if set(HOSPITAL_MODULES) & {*triggers["always"], *triggers["keywords"]}:
        raise ValueError("hospital-chosen modules are never selected by triggers")
    if set(triggers.get("keywords", {})) - module_names:
        raise ValueError("trigger configuration refers to an unknown module")
    if set(triggers["always"]) & set(triggers["keywords"]):
        raise ValueError("a module cannot be both always-on and keyword-triggered")
    ignore_phrases = triggers.get("ignore_phrases", {})
    if not isinstance(ignore_phrases, dict) or set(ignore_phrases) - set(triggers["keywords"]):
        raise ValueError("ignore phrases must belong to keyword-triggered modules")
    if any(
        not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms)
        for terms in ignore_phrases.values()
    ):
        raise ValueError("ignore phrases must be non-empty strings")
    _check_rules(questions, module_names)
    return questions, triggers


QUESTIONS, TRIGGERS = load_question_bank()


def validate_question_bank(directory: Path = BANK_DIRECTORY) -> dict[str, int]:
    questions, _ = load_question_bank(directory)
    return {"questions": len(questions), "modules": len({q.module for q in questions.values()}) - 1}
