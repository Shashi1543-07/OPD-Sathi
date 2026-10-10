"""Tap-to-answer interview, patient-language read-back, AI summary guard, providers."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.clinical_summary import (
    UnsafeSummaryError,
    apply_summary,
    guard_doctor_summary,
    summarize_interview,
)
from app.ai.providers.llm.base import LLMUnavailableError
from app.ai.providers.llm.gemini import GeminiProvider, _pcm_to_wav
from app.ai.readback import build_readback_from_answers
from app.ai.speech import SpeechService
from app.config import Settings
from app.domain.interview.bank import QUESTIONS
from app.domain.interview.engine import (
    apply_answer,
    build_case_sheet,
    next_question,
    question_total,
    start,
)
from app.workers.main import close_previous_days


def _interview(language: str, answers: dict[str, Any]) -> Any:
    state = start(language)  # type: ignore[arg-type]
    while (question := next_question(state)) is not None:
        value = answers.get(question.id)
        state = apply_answer(state, question.id, value, skip=value is None)
    return state


HINDI_FEVER = {
    "chief_concern": "बुखार, खाँसी",
    "duration": "4 से 7 दिन",
    "severity": 6,
    "fever": "तेज़ बुखार, कँपकँपी के साथ",
    "fever_associated": "इनमें से कुछ नहीं",
    "respiratory": "सूखी खाँसी",
    "past_history": "शुगर (डायबिटीज़)",
    "medications": "शुगर की दवा, metformin",
    "allergies": "कोई एलर्जी नहीं",
    "family_history": "इनमें से कुछ नहीं",
    "general_context": "नहीं - बस इतना ही",
}


def test_tapped_hindi_answers_give_the_doctor_english_history() -> None:
    sheet = build_case_sheet(_interview("hi", HINDI_FEVER))
    assert sheet["chief_complaint"] == {
        "text_original": "बुखार, खाँसी",
        "text_en": "Fever, Cough",
        "confidence": 0.7,
    }
    assert sheet["hpi"]["onset"]["text_en"] == "4 to 7 days"
    assert sheet["hpi"]["character"]["text_en"] == "Very high fever, With shivering or chills"
    assert sheet["past_history"]["conditions"] == ["Diabetes (sugar)"]
    # Free text the patient typed stays as typed.
    assert [item["name"] for item in sheet["medications"]] == ["Medicine for sugar", "metformin"]
    negatives = [item["text_en"] for item in sheet["hpi"]["pertinent_negatives"]]
    assert "No known drug or food allergy" in negatives
    assert any(text.startswith("Denies body ache") for text in negatives)
    assert any(text.startswith("No family history of diabetes") for text in negatives)
    assert sheet["allergies"] == []


def test_fever_and_cough_trigger_targeted_follow_up_questions() -> None:
    state = start("en", sex="male", age=40)
    state = apply_answer(state, "chief_concern", "Fever, Cough")
    assert state.queued[:7] == [
        "chief_concern",
        "duration",
        "severity",
        "fever",
        "fever_associated",
        "respiratory",
        "respiratory_history",
    ]
    # The pregnancy question is planned but never shown to this patient.
    assert "pregnancy" in state.queued
    assert question_total(state) <= 12


def test_every_option_label_maps_back_to_english() -> None:
    for question in QUESTIONS.values():
        for option in question.options:
            for language in ("en", "hi", "mr"):
                assert question.english(option.label[language]) == option.label["en"]


def test_read_back_uses_the_patients_own_language_and_skips_denials() -> None:
    text = build_readback_from_answers("hi", _interview("hi", HINDI_FEVER).answers)
    assert "बुखार, खाँसी" in text
    assert "शुगर की दवा" in text
    assert "Diabetes" not in text  # No English doctor fields in the patient's read-back.
    assert "कोई एलर्जी नहीं" not in text


def _summary(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "doctor_brief_en": "34-year-old female reports fever for 4-7 days with dry cough.",
        "chief_complaint_en": "Fever and cough",
        "onset_en": "4 to 7 days",
        "associated_symptoms_en": ["Dry cough", "Chills"],
        "pertinent_negatives_en": ["No body ache"],
        "past_conditions_en": ["Diabetes"],
        "medications": [{"name": "Metformin", "dose": "500 mg"}],
        "allergies_en": [],
        "family_history_en": [],
        "answers_en": [{"question_id": "chief_concern", "answer_en": "Fever, cough"}],
    }
    value.update(overrides)
    return value


def test_summary_guard_blocks_diagnosis_and_advice() -> None:
    guard_doctor_summary(_summary())
    with pytest.raises(UnsafeSummaryError):
        guard_doctor_summary(_summary(doctor_brief_en="Symptoms are likely viral fever."))
    with pytest.raises(UnsafeSummaryError):
        guard_doctor_summary(_summary(chief_complaint_en="Fever, rule out dengue"))
    with pytest.raises(UnsafeSummaryError):
        guard_doctor_summary(_summary(doctor_brief_en="Patient should start antibiotics."))
    # A translated patient answer may hedge ("probably") without being rejected.
    guard_doctor_summary(_summary(onset_en="probably three days"))


def test_apply_summary_adds_english_and_keeps_patient_words() -> None:
    baseline = build_case_sheet(_interview("hi", HINDI_FEVER))
    merged = apply_summary(baseline, _summary(), model="gemini-test")
    assert merged["chief_complaint"]["text_original"] == "बुखार, खाँसी"
    assert merged["chief_complaint"]["text_en"] == "Fever and cough"
    assert merged["ai_summary"]["brief_en"].startswith("34-year-old female")
    assert merged["ai_summary"]["answers_en"] == {"chief_concern": "Fever, cough"}
    assert merged["medications"] == [
        {"name": "Metformin", "dose": "500 mg", "source": "patient_stated"}
    ]
    assert merged["field_meta"]["/chief_complaint"]["ai_assisted"] is True


class _Provider:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.model = "gemini-test"
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, prompt: Any, schema: Any, **kwargs: Any) -> Any:
        self.calls.append({"prompt": prompt, "schema": schema, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_summarize_interview_falls_back_quietly() -> None:
    answers = [{"question_id": "chief_concern", "question_en": "Main problem?", "answer": "x"}]
    ok = _Provider(_summary())
    assert await summarize_interview(
        ok, language="hi", age=34, sex="female", answers=answers, danger_signs=[], session_id="s"
    ) == _summary()
    assert ok.calls[0]["guard"] is guard_doctor_summary
    assert "UNTRUSTED_DATA_JSON" in ok.calls[0]["prompt"]
    for failing in (
        _Provider(LLMUnavailableError("down")),
        _Provider(_summary(doctor_brief_en="Consistent with pneumonia.")),
    ):
        assert (
            await summarize_interview(
                failing,
                language="hi",
                age=34,
                sex="female",
                answers=answers,
                danger_signs=[],
                session_id="s",
            )
            is None
        )


def test_providers_follow_the_configured_keys() -> None:
    none = Settings(_env_file=None)
    assert (none.resolved_llm_provider, none.resolved_stt_provider) == ("mock", "browser")
    gemini = Settings(_env_file=None, gemini_api_key="synthetic-key")
    assert gemini.resolved_llm_provider == "gemini"
    assert (gemini.resolved_stt_provider, gemini.resolved_tts_provider) == ("gemini", "gemini")
    both = Settings(_env_file=None, gemini_api_key="k", sarvam_api_key="k")
    assert (both.resolved_stt_provider, both.resolved_tts_provider) == ("sarvam", "sarvam")


def test_speech_service_modes() -> None:
    gemini = GeminiProvider(
        api_key="synthetic-key",
        model="m",
        fallback_model="f",
        client=SimpleNamespace(),  # type: ignore[arg-type]
    )
    service = SpeechService(Settings(_env_file=None, gemini_api_key="k"), gemini)
    assert (service.stt_mode, service.tts_mode) == ("gemini", "gemini")
    assert SpeechService(Settings(_env_file=None), object()).stt_mode == "browser"
    demo = Settings(_env_file=None, gemini_api_key="k", provider_mode="demo-safe")
    assert SpeechService(demo, gemini).tts_mode == "browser"


def test_gemini_pcm_is_wrapped_as_wav() -> None:
    wav = _pcm_to_wav(b"\x00\x00" * 2400)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


class _CloseOutConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        self.statements.append((str(statement), params))
        return SimpleNamespace(rowcount=2)


@pytest.mark.asyncio
async def test_previous_days_are_closed_so_todays_queue_starts_empty() -> None:
    connection = _CloseOutConnection()
    assert await close_previous_days(connection) == 6  # type: ignore[arg-type]
    targets = {params["target"]: params["statuses"] for _, params in connection.statements}
    assert targets == {
        "no_show": ["queued", "called"],
        "done": ["in_consult"],
        "cancelled": ["registering", "awaiting_confirmation"],
    }
    assert all("service_date <" in sql for sql, _ in connection.statements)
