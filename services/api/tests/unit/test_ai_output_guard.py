"""Output guard adversarial cases use synthetic, non-patient strings."""

import pytest

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output

_CONDITIONS = [
    "asthma",
    "diabetes",
    "infection",
    "migraine",
    "pneumonia",
    "arthritis",
]
_UNSAFE = [
    *(f"You have {condition}." for condition in _CONDITIONS),
    *(f"You may have {condition}." for condition in _CONDITIONS),
    *(f"Likely have {condition}." for condition in _CONDITIONS),
    *(f"Diagnosis: {condition}." for condition in _CONDITIONS),
    *(f"आपको {condition} है।" for condition in _CONDITIONS),
    *(f"आपको {condition} हो सकता है।" for condition in _CONDITIONS),
    *(f"तुम्हाला {condition} आहे." for condition in _CONDITIONS),
    *(f"तुम्हाला {condition} असू शकते." for condition in _CONDITIONS),
    *(f"You should take this step {index}." for index in range(8)),
    *(f"You must do this now {index}." for index in range(8)),
    *(f"Please take action {index}." for index in range(8)),
    *(f"कृपया ऐसा करें {index}।" for index in range(8)),
    *(f"यह करें और दवा लें {index}।" for index in range(8)),
    *(f"कृपया इलाज करें {index}।" for index in range(8)),
    *(f"कृपया हे करा {index}." for index in range(8)),
    *(f"हे करा आणि औषध घ्या {index}." for index in range(8)),
    *(f"उपचार करा {index}." for index in range(8)),
    *(f"Take {index + 1} tablets." for index in range(8)),
    *(f"Use {index + 1} mg of medicine." for index in range(8)),
    *(f"दवा की खुराक {index + 1} मिलीग्राम।" for index in range(8)),
    *(f"औषधाची मात्रा {index + 1} mg घ्या." for index in range(8)),
    *(f"Visit https://clinic.example/path/{index}" for index in range(8)),
    *(f"See www.example{index}.com for details." for index in range(8)),
]


@pytest.mark.parametrize("text", _UNSAFE)
def test_rejects_unsafe_generated_text_in_multiple_languages(text: str) -> None:
    with pytest.raises(UnsafeLLMOutputError):
        validate_safe_output({"findings": [{"text_original": text}]})


@pytest.mark.parametrize(
    "value",
    [
        {"question_id": "duration"},
        {"findings": [{"text_original": "sharp pain on the right side"}]},
        ["fever for two days", "pain level 6"],
        {"history": {"onset": "started yesterday", "severity": 7}},
        "I have a cough and chest discomfort.",
        "Patient reports nausea after meals.",
    ],
)
def test_preserves_safe_structured_outputs(value: object) -> None:
    validate_safe_output(value)


def test_reports_more_than_one_hundred_adversarial_strings() -> None:
    assert len(_UNSAFE) >= 100
