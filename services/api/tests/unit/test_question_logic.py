"""Every question and option shown must fit the patient and their stated problem."""

from typing import Any

from app.ai.followup_questions import parse_followups
from app.domain.interview.bank import QUESTIONS
from app.domain.interview.engine import (
    InterviewState,
    apply_answer,
    build_case_sheet,
    correct_answer,
    go_back,
    next_question,
    start,
)


def _walk(state: InterviewState, answers: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Answer every question (or skip it) and return the option IDs each question showed."""
    shown: dict[str, list[str]] = {}
    while (question := next_question(state)) is not None:
        shown[question.id] = [option.id for option in question.options]
        value = (answers or {}).get(question.id)
        state = apply_answer(state, question.id, value, skip=value is None)
    return shown


def _seen(
    complaint: str, *, sex: str = "male", age: int = 35, language: str = "en"
) -> dict[str, list[str]]:
    state = start(language, sex=sex, age=age)  # type: ignore[arg-type]
    return _walk(apply_answer(state, "chief_concern", complaint))


def test_pregnancy_is_asked_only_where_it_can_apply() -> None:
    assert "pregnancy" not in _seen("Fever", sex="male")
    assert "pregnancy" not in _seen("Fever", sex="female", age=8)
    assert "pregnancy" not in _seen("Fever", sex="female", age=62)
    assert "pregnancy" in _seen("Fever", sex="female", age=28)
    # It is no longer offered as a "long-term illness".
    assert "pregnant" not in _seen("Fever", sex="female", age=28)["past_history"]


def test_pregnancy_answer_reaches_the_case_sheet() -> None:
    def sheet(answer: str) -> dict[str, Any]:
        state = apply_answer(start("en", sex="female", age=26), "chief_concern", "Fever")
        while (question := next_question(state)) is not None:
            value = answer if question.id == "pregnancy" else None
            state = apply_answer(state, question.id, value, skip=value is None)
        return build_case_sheet(state)

    assert sheet("Yes")["women_health"] == {"pregnant": True, "lmp": None}
    assert sheet("No")["women_health"] == {"pregnant": False, "lmp": None}
    unsure = sheet("Not sure")
    assert unsure["women_health"] is None
    assert "Not sure whether pregnant" in unsure["past_history"]["conditions"]


def test_a_man_who_denies_everything_is_never_recorded_as_not_pregnant() -> None:
    state = apply_answer(start("en", sex="male", age=40), "chief_concern", "Fever")
    while (question := next_question(state)) is not None:
        none = [option.label["en"] for option in question.options if option.none]
        state = apply_answer(state, question.id, none[0] if none else None, skip=not none)
    negatives = build_case_sheet(state)["hpi"]["pertinent_negatives"]
    assert "pregnan" not in " ".join(item["text_en"] for item in negatives).casefold()


def test_location_is_not_asked_again_when_the_problem_names_it() -> None:
    stomach = _seen("Stomach pain")
    assert "pain" not in stomach  # "Where is the pain?" after "Stomach pain"
    assert "no_pain" not in stomach["digestive_site"]  # "No stomach pain" after "Stomach pain"
    assert "pain" not in _seen("Headache")
    body = _seen("Body or joint pain")
    assert "pain" in body and "stomach" in body["pain"]
    # Typed words could mean anything, so the location is still asked.
    assert "pain" in _seen("pain since two days")


def test_hindi_and_marathi_choices_plan_the_same_questions_as_english() -> None:
    for option in QUESTIONS["chief_concern"].options:
        english = list(_seen(option.label["en"]))
        for language in ("hi", "mr"):
            assert list(_seen(option.label[language], language=language)) == english, (
                option.id,
                language,
            )


def test_words_that_only_look_like_a_symptom_do_not_add_questions() -> None:
    assert "mobility" not in _seen("आजारी पडलो, ताप आहे", language="mr")  # "fell ill"
    assert "mobility" not in _seen("I fell sick yesterday")
    assert "pain" not in _seen("साँस लेने में तकलीफ़", language="hi")  # breathing trouble


def test_the_same_symptom_is_not_asked_twice() -> None:
    fever_urine = _seen("Fever, Burning while passing urine")
    assert "burning_urine" not in fever_urine["fever_associated"]
    assert "burning" not in fever_urine["urinary"]  # just said "burning while passing urine"
    assert "fever" not in fever_urine["urinary_associated"]
    cough_tired = _seen("Cough, Weakness or tiredness")
    assert "weight_loss" in cough_tired["respiratory_history"]
    assert "weight_loss" not in cough_tired["sleep_energy"]
    assert "asthma" not in cough_tired["past_history"]
    assert "tb" not in cough_tired["family_history"]
    fever_headache = _seen("Fever, Headache")
    assert {"headache", "body_ache"}.isdisjoint(fever_headache["fever_associated"])


def test_injury_questions_fit_how_it_happened() -> None:
    assert "no_injury" not in _seen("Injury or fall")["mobility"]
    state = apply_answer(start("en", sex="male", age=30), "chief_concern", "Injury or fall")
    bite = _walk(state, {"mobility": "Animal or dog bite"})
    assert bite["mobility_effect"] == ["swelling", "wound", "normal"]
    fall = _walk(state, {"mobility": "Fell down"})
    assert {"walk", "lift", "bend"} <= set(fall["mobility_effect"])


def test_contradicting_choices_exclude_each_other() -> None:
    fever = {option.id: set(option.excludes) for option in QUESTIONS["fever"].options}
    assert fever["continuous"] == {"comes_goes", "night"}
    assert "continuous" in fever["comes_goes"] and "continuous" in fever["night"]
    pain = {option.id: set(option.excludes) for option in QUESTIONS["pain_character"].options}
    assert pain["constant"] == {"comes_goes"} and pain["comes_goes"] == {"constant"}


def test_every_patient_is_asked_for_anything_else_at_the_end() -> None:
    for complaint in ("Fever", "Stomach pain", "something else entirely"):
        assert list(_seen(complaint))[-1] == "general_context"


def test_every_symptom_question_lets_the_patient_say_none() -> None:
    for question_id in ("digestive", "urinary", "mobility_effect"):
        assert any(option.none for option in QUESTIONS[question_id].options), question_id


def test_go_back_undoes_only_the_last_answer() -> None:
    state = apply_answer(start("en", sex="male", age=30), "chief_concern", "Fever")
    state = apply_answer(state, "duration", "Since today")
    back, undone = go_back(state)
    assert undone == "Since today" and back.asked == ["chief_concern"]
    assert next_question(back).id == "duration"  # type: ignore[union-attr]
    again, undone = go_back(back)
    assert undone == "Fever" and again.queued == ["chief_concern", "duration", "severity"]


def test_changing_the_problem_drops_answers_that_no_longer_fit() -> None:
    state = apply_answer(start("en", sex="male", age=30), "chief_concern", "Fever")
    state = apply_answer(state, "duration", None, skip=True)
    state = apply_answer(state, "severity", 4)
    state = apply_answer(state, "fever", "Very high fever")
    changed = correct_answer(state, "chief_concern", "Skin rash or itching")
    assert "fever" not in changed.answers and "fever" not in changed.queued
    assert next_question(changed).id == "skin"  # type: ignore[union-attr]
    assert changed.answers["severity"] == 4


def _draft(text_en: str, text_hi: str, text_mr: str) -> dict[str, Any]:
    return {
        "text_en": text_en,
        "text_hi": text_hi,
        "text_mr": text_mr,
        "multi": False,
        "options": [
            {"en": "Yes", "hi": "हाँ", "mr": "होय", "none": False},
            {"en": "No", "hi": "नहीं", "mr": "नाही", "none": True},
        ],
    }


def test_ai_followups_that_ask_about_a_planned_choice_are_dropped() -> None:
    output = {
        "questions": [
            _draft(
                "Do you bring up phlegm when you cough?",
                "क्या खाँसी में बलगम आता है?",
                "खोकल्यात कफ येतो का?",
            ),
            _draft(
                "Do you bring up any phlegm?",
                "क्या बलगम निकलता है?",
                "कफ बाहर पडतो का?",
            ),
            _draft(
                "When do you cough the most?",
                "खाँसी सबसे ज़्यादा कब आती है?",
                "खोकला सगळ्यात जास्त कधी येतो?",
            ),
        ]
    }
    planned = [QUESTIONS["chief_concern"], QUESTIONS["respiratory"]]
    kept = parse_followups(output, planned, sex="male", age=52)
    # Mentioning the patient's own complaint ("cough") is not a repeat of the main question.
    assert [question.prompt["en"] for question in kept] == ["When do you cough the most?"]


def test_ai_followups_that_do_not_fit_the_patient_are_dropped() -> None:
    output = {
        "questions": [
            _draft(
                "Are your periods late this month?",
                "क्या इस महीने माहवारी देर से आई?",
                "या महिन्यात पाळी उशिरा आली का?",
            ),
            _draft(
                "Does the pain wake you at night?",
                "क्या दर्द से रात में नींद खुलती है?",
                "दुखण्यामुळे रात्री जाग येते का?",
            ),
        ]
    }
    male = parse_followups(output, [], sex="male", age=30)
    assert [question.prompt["en"] for question in male] == ["Does the pain wake you at night?"]
    child = parse_followups(output, [], sex="female", age=6)
    assert len(child) == 1
    woman = parse_followups(output, [], sex="female", age=30)
    assert len(woman) == 2
