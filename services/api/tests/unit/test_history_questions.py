"""Surgical, personal, Ayurveda history and review of systems; non-answers stay open."""

from typing import Any

import pytest

from app.domain.case_sheet import CaseSheet
from app.domain.interview.bank import QUESTIONS
from app.domain.interview.engine import (
    MAX_QUESTIONS,
    InterviewValidationError,
    apply_answer,
    build_case_sheet,
    next_question,
    question_cap,
    start,
    visible_question,
)


def _interview(
    answers: dict[str, str | int], *, complaint: str = "headache", **options: Any
) -> tuple[list[str], dict[str, Any]]:
    """Answer the given questions (by id) and skip every other optional one."""
    state = start("en", **options)
    asked: list[str] = []
    while (question := next_question(state)) is not None:
        asked.append(question.id)
        value = complaint if question.id == "chief_concern" else answers.get(question.id)
        state = apply_answer(state, question.id, value, skip=value is None)
    return asked, CaseSheet.model_validate(build_case_sheet(state)).model_dump(mode="json")


def test_past_operations_are_recorded_and_never_having_one_is_a_negative() -> None:
    _, sheet = _interview({"surgeries": "Appendix, Another operation, knee in 2019"})
    assert sheet["past_history"]["surgeries"] == [
        "Appendix",
        "Another operation",
        "knee in 2019",
    ]
    _, sheet = _interview({"surgeries": "Never had an operation"})
    assert sheet["past_history"]["surgeries"] == []
    assert "No previous operations" in {
        item["text_en"] for item in sheet["hpi"]["pertinent_negatives"]
    }


@pytest.mark.parametrize(
    ("answer", "status", "text"),
    [
        ("Don't know", "unknown", "Does not know: past operations"),
        ("Prefer not to say", "declined", "Preferred not to say: past operations"),
    ],
)
def test_dont_know_and_prefer_not_to_say_are_open_questions_not_denials(
    answer: str, status: str, text: str
) -> None:
    _, sheet = _interview({"surgeries": answer})
    assert sheet["unanswered"] == [{"question_id": "surgeries", "status": status, "text_en": text}]
    assert sheet["past_history"]["surgeries"] == []
    assert not any(
        "operation" in item["text_en"].lower() for item in sheet["hpi"]["pertinent_negatives"]
    )


def test_tobacco_and_alcohol_fill_the_personal_history() -> None:
    _, sheet = _interview(
        {"habits": "Smoking (bidi or cigarette), Alcohol, only at weddings"}, age=45
    )
    social = sheet["social"]
    assert social["tobacco"]["text_en"] == "Smoking (bidi or cigarette)"
    assert social["alcohol"]["text_en"] == "Alcohol"
    assert [item["text_original"] for item in social["other"]] == ["only at weddings"]

    _, sheet = _interview({"habits": "Prefer not to say"}, age=45)
    assert sheet["unanswered"][0]["text_en"] == "Preferred not to say: tobacco and alcohol use"
    assert sheet["social"].get("tobacco") is None


def test_children_are_not_asked_about_tobacco_and_men_not_about_caesareans() -> None:
    asked, _ = _interview({}, age=8)
    assert "habits" not in asked and "surgeries" in asked
    state = start("en", sex="male", age=40)
    options = visible_question(state, QUESTIONS["surgeries"])
    assert options is not None
    assert "caesarean" not in {option.id for option in options.options}


def test_the_ayurveda_history_is_asked_only_where_the_hospital_adds_it() -> None:
    asked, sheet = _interview({})
    assert not any(question_id.startswith("ayur_") for question_id in asked)
    assert "ayush" not in sheet or sheet["ayush"] is None

    asked, sheet = _interview(
        {
            "ayur_appetite": "Less than before",
            "ayur_digestion": "Light and fine",
            "ayur_bowel": "Hard or constipated",
            "ayur_diet": "Vegetarian, Mostly spicy or fried",
        },
        extra_modules=("ayurveda",),
    )
    ayurveda = [question_id for question_id in asked if question_id.startswith("ayur_")]
    assert ayurveda == [
        "ayur_appetite",
        "ayur_digestion",
        "ayur_bowel",
        "ayur_sleep",
        "ayur_exercise",
        "ayur_diet",
    ]
    # Asked after the history and before the closing "anything else?" question.
    assert asked.index("habits") < asked.index("ayur_appetite")
    assert asked[-1] == "general_context"
    ayush = sheet["ayush"]
    assert ayush["appetite"]["text_en"] == "Less than before"
    # "Light and fine" is a finding here, not a denial.
    assert ayush["digestion"]["text_en"] == "Light and fine"
    assert ayush["diet"]["text_en"] == "Vegetarian, Mostly spicy or fried"
    assert ayush["examination"] == {}


def test_an_ayush_hospital_sheet_keeps_the_section_even_when_every_question_is_skipped() -> None:
    _, sheet = _interview({}, extra_modules=("ayurveda",))
    assert sheet["ayush"]["examination"] == {}
    assert sheet["ayush"]["appetite"] is None


def test_the_ayurveda_module_brings_its_own_question_room() -> None:
    plain = start("en")
    ayush = start("en", extra_modules=("ayurveda",))
    assert question_cap(plain) == MAX_QUESTIONS
    assert question_cap(ayush) == MAX_QUESTIONS + 6
    with pytest.raises(InterviewValidationError, match="optional modules"):
        start("en", extra_modules=("fever",))


def test_the_ayurveda_sleep_question_gives_way_to_the_sleep_module() -> None:
    asked, _ = _interview({}, complaint="trouble sleeping", extra_modules=("ayurveda",))
    assert "ayur_sleep" not in asked
    assert "ayur_appetite" in asked


def test_the_review_of_systems_is_one_screen_asked_only_where_the_hospital_adds_it() -> None:
    asked, _ = _interview({})
    assert "ros_checklist" not in asked

    asked, sheet = _interview(
        {"ros_checklist": "Trouble seeing or hearing, Joint or back pain"},
        extra_modules=("review_of_systems",),
    )
    assert asked.count("ros_checklist") == 1
    assert asked.index("habits") < asked.index("ros_checklist") < asked.index("general_context")
    symptoms = [item["text_en"] for item in sheet["hpi"]["associated_symptoms"]]
    assert "Trouble seeing or hearing" in symptoms and "Joint or back pain" in symptoms
    # One question: the interview grows by one screen, inside its cap.
    assert question_cap(start("en", extra_modules=("review_of_systems",))) == MAX_QUESTIONS + 1


def test_none_on_the_review_of_systems_denies_every_system_shown_and_no_other() -> None:
    _, sheet = _interview(
        {"ros_checklist": "None of these"},
        complaint="cough and breathing concern",
        extra_modules=("review_of_systems",),
        sex="male",
        age=40,
    )
    negatives = [item["text_en"] for item in sheet["hpi"]["pertinent_negatives"]]
    denial = next(text for text in negatives if text.startswith("Review of systems: denies"))
    assert "joint or back pain" in denial and "feeling low or worried" in denial
    # The breathing module already asked about cough; men are not asked about periods.
    assert "cough or breathlessness" not in denial
    assert "periods" not in denial


def test_the_review_of_systems_hides_systems_other_modules_cover() -> None:
    state = start("en", extra_modules=("review_of_systems",), sex="female", age=30)
    state = apply_answer(state, "chief_concern", "skin rash", skip=False)
    shown = visible_question(state, QUESTIONS["ros_checklist"])
    assert shown is not None
    labels = {option.label["en"] for option in shown.options}
    assert "Rash or itching" not in labels
    assert {"Change in periods", "None of these", "I don't know"} <= labels


def test_dont_know_on_the_review_of_systems_stays_open() -> None:
    _, sheet = _interview({"ros_checklist": "I don't know"}, extra_modules=("review_of_systems",))
    assert [item["question_id"] for item in sheet["unanswered"]] == ["ros_checklist"]
    assert not any(
        item["text_en"].startswith("Review of systems")
        for item in sheet["hpi"]["pertinent_negatives"]
    )
