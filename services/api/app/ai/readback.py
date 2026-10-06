"""Deterministic patient read-back templates; never ask a model to summarize clinical data."""

from collections.abc import Mapping, Sequence
from typing import Any

_LABELS = {
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


def _items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("text_original") or item.get("name")
                if isinstance(text, str) and text.strip():
                    result.append(text.strip())
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
        return result
    return []


def build_readback(case_sheet: Mapping[str, Any]) -> str:
    """Build a bounded read-back only from supplied case-sheet values and fixed copy."""
    language = str(case_sheet.get("language", "en"))
    labels = _LABELS.get(language, _LABELS["en"])
    complaint = case_sheet.get("chief_complaint", {})
    complaint_text = complaint.get("text_original", "") if isinstance(complaint, Mapping) else ""
    sentences = [f"{labels['opening']}: {complaint_text}." if complaint_text else ""]

    hpi = case_sheet.get("hpi", {})
    if isinstance(hpi, Mapping):
        onset = hpi.get("onset")
        if isinstance(onset, Mapping) and onset.get("text_original"):
            sentences.append(f"{labels['duration']} {onset['text_original']}.")
        severity = hpi.get("severity_0_10")
        if isinstance(severity, int) and not isinstance(severity, bool):
            sentences.append(f"{labels['severity']} {severity} {labels['out_of']}.")
        symptoms = _items(hpi.get("associated_symptoms"))
        if symptoms:
            sentences.append(f"{labels['symptoms']}: {', '.join(symptoms)}.")

    history = case_sheet.get("past_history", {})
    conditions = _items(history.get("conditions")) if isinstance(history, Mapping) else []
    medications = _items(case_sheet.get("medications"))
    allergies = _items(case_sheet.get("allergies"))
    for key, values in (
        ("conditions", conditions),
        ("medications", medications),
        ("allergies", allergies),
    ):
        if values:
            sentences.append(f"{labels[key]}: {', '.join(values)}.")
    sentences.append(labels["closing"])

    words = " ".join(sentence for sentence in sentences if sentence).split()
    return " ".join(words[:90])
