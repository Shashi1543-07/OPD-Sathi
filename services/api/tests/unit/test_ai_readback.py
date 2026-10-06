from collections.abc import Mapping
from typing import Any

import pytest

from app.ai.readback import build_readback

GOLDEN_LABELS = {
    "en": {
        "opening": "You told us",
        "duration": "This has been happening for",
        "severity": "You rated its effect",
        "out_of": "out of 10",
        "symptoms": "You also mentioned",
        "conditions": "You reported these health conditions",
        "medications": "You said you take",
        "allergies": "You reported these allergies",
        "closing": "Please review this summary and change anything incorrect.",
    },
    "hi": {
        "opening": "आपने हमें बताया",
        "duration": "यह परेशानी इतने समय से है",
        "severity": "आपने इसका असर बताया",
        "out_of": "10 में से",
        "symptoms": "आपने ये बातें भी बताईं",
        "conditions": "आपने ये स्वास्थ्य स्थितियाँ बताईं",
        "medications": "आपने बताया कि आप ये दवाएँ लेते हैं",
        "allergies": "आपने ये एलर्जी बताईं",
        "closing": "कृपया इस सारांश को देखें और गलत जानकारी बदलें।",
    },
    "mr": {
        "opening": "तुम्ही आम्हाला सांगितले",
        "duration": "हा त्रास इतक्या काळापासून आहे",
        "severity": "तुम्ही याचा परिणाम सांगितला",
        "out_of": "१० पैकी",
        "symptoms": "तुम्ही या गोष्टीही सांगितल्या",
        "conditions": "तुम्ही या आरोग्यविषयक स्थिती सांगितल्या",
        "medications": "तुम्ही ही औषधे घेत असल्याचे सांगितले",
        "allergies": "तुम्ही या ॲलर्जी सांगितल्या",
        "closing": "कृपया हा सारांश तपासा आणि चुकीची माहिती बदला.",
    },
}


def _golden(case: Mapping[str, Any], lang: str) -> str:
    label = GOLDEN_LABELS[lang]
    parts = [f"{label['opening']}: concern {case['case']}. "]
    if case["duration"]:
        parts.append(f"{label['duration']} {case['duration']}. ")
    if case["severity"] is not None:
        parts.append(f"{label['severity']} {case['severity']} {label['out_of']}. ")
    if case["symptoms"]:
        parts.append(f"{label['symptoms']}: {case['symptoms']}. ")
    if case["conditions"]:
        parts.append(f"{label['conditions']}: {case['conditions']}. ")
    if case["medications"]:
        parts.append(f"{label['medications']}: medicine {case['case']}. ")
    if case["allergies"]:
        parts.append(f"{label['allergies']}: allergy {case['case']}. ")
    parts.append(label["closing"])
    return " ".join(" ".join(parts).split()[:90])


@pytest.mark.parametrize("language", ("en", "hi", "mr"))
@pytest.mark.parametrize("case_number", range(10))
def test_golden_readback_output_is_bounded_and_complete(language: str, case_number: int) -> None:
    case = {
        "case": case_number,
        "duration": f"{case_number + 1} days" if case_number % 2 == 0 else "",
        "severity": case_number if case_number % 3 == 0 else None,
        "symptoms": f"symptom {case_number}" if case_number % 2 == 0 else "",
        "conditions": f"condition {case_number}" if case_number % 4 == 0 else "",
        "medications": case_number % 5 == 0,
        "allergies": f"allergy {case_number}" if case_number % 3 == 1 else "",
    }
    data = {
        "language": language,
        "chief_complaint": {"text_original": f"concern {case_number}"},
        "hpi": {
            **({"onset": {"text_original": case["duration"]}} if case["duration"] else {}),
            **({"severity_0_10": case["severity"]} if case["severity"] is not None else {}),
            **(
                {"associated_symptoms": [{"text_original": case["symptoms"]}]}
                if case["symptoms"]
                else {}
            ),
        },
        "past_history": {"conditions": [case["conditions"]] if case["conditions"] else []},
        "medications": [{"name": f"medicine {case_number}"}] if case["medications"] else [],
        "allergies": [{"text_original": case["allergies"]}] if case["allergies"] else [],
    }
    result = build_readback(data)
    assert result == _golden(case, language)
    assert len(result.split()) <= 90
    assert "{" not in result and "}" not in result
