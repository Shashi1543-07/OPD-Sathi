"""AI translation and structuring of a finished interview for the doctor.

The patient answers in Hindi, Marathi or English. The model returns English history fields
and a short doctor's brief built only from what the patient said. Output that reads like a
diagnosis, a recommendation or a link is rejected and the deterministic sheet is kept.
The patient's original words are never replaced.
"""

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.output_guard import UnsafeLLMOutputError
from app.ai.prompts.builders import build_summary_prompt
from app.ai.providers.llm.base import LLMUnavailableError
from app.domain.case_sheet import CaseSheet

_Short: Any = Field(default="", max_length=300)
_Items: Any = Field(default_factory=list, max_length=15)

# Doctor-facing text may repeat medicines or conditions the patient named, but the model
# must never add its own clinical judgement. Every field gets the always-blocked patterns;
# the model-written brief also gets the hedging-word patterns, because translated patient
# answers can legitimately say "probably three days".
_ALWAYS_BLOCKED = (
    re.compile(
        r"\b(?:diagnos\w*|rule out|r/o|differential|impression|suggestive of|"
        r"consistent with|indicative of)\b",
        re.I,
    ),
    re.compile(r"\b(?:recommend\w*|advis\w*|prescrib\w*|start on|refer to)\b", re.I),
    re.compile(r"\b(?:https?://|www\.)\S+", re.I),
)
_BRIEF_BLOCKED = (
    re.compile(
        r"\b(?:likely|unlikely|probabl[ey]|possibl[ey]|suggests?|indicates?|suspect\w*|"
        r"should|must|needs? to|manage\w* with)\b",
        re.I,
    ),
)


class UnsafeSummaryError(UnsafeLLMOutputError):
    """The summary contains model-authored clinical judgement."""


def _check(value: Any, patterns: tuple[re.Pattern[str], ...]) -> None:
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in patterns):
            raise UnsafeSummaryError("summary contains clinical inference or advice")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _check(item, patterns)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _check(item, patterns)


def guard_doctor_summary(value: Any) -> None:
    """Reject inference or advice in the model output."""
    _check(value, _ALWAYS_BLOCKED)
    if isinstance(value, Mapping):
        _check(value.get("doctor_brief_en", ""), _ALWAYS_BLOCKED + _BRIEF_BLOCKED)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Medication(_Strict):
    name: str = Field(min_length=1, max_length=120)
    dose: str = Field(default="", max_length=120)


class _Answer(_Strict):
    question_id: str = Field(min_length=1, max_length=80)
    answer_en: str = Field(default="", max_length=500)


class SummaryOutput(_Strict):
    doctor_brief_en: str = Field(min_length=1, max_length=600)
    chief_complaint_en: str = Field(min_length=1, max_length=300)
    onset_en: str = _Short
    location_en: str = _Short
    character_en: str = _Short
    timing_en: str = _Short
    associated_symptoms_en: list[str] = _Items
    pertinent_negatives_en: list[str] = _Items
    aggravating_en: list[str] = _Items
    relieving_en: list[str] = _Items
    past_conditions_en: list[str] = _Items
    surgeries_en: list[str] = _Items
    medications: list[_Medication] = _Items
    allergies_en: list[str] = _Items
    family_history_en: list[str] = _Items
    tobacco_en: str = _Short
    alcohol_en: str = _Short
    answers_en: list[_Answer] = Field(default_factory=list, max_length=20)


def summary_json_schema() -> dict[str, Any]:
    """A plain JSON schema (no $refs) that Gemini structured output accepts."""
    text = {"type": "string", "maxLength": 300}
    items = {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 15}
    return {
        "type": "object",
        "properties": {
            "doctor_brief_en": {"type": "string", "maxLength": 600},
            "chief_complaint_en": text,
            "onset_en": text,
            "location_en": text,
            "character_en": text,
            "timing_en": text,
            "associated_symptoms_en": items,
            "pertinent_negatives_en": items,
            "aggravating_en": items,
            "relieving_en": items,
            "past_conditions_en": items,
            "surgeries_en": items,
            "medications": {
                "type": "array",
                "maxItems": 15,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 120},
                        "dose": {"type": "string", "maxLength": 120},
                    },
                    "required": ["name", "dose"],
                },
            },
            "allergies_en": items,
            "family_history_en": items,
            "tobacco_en": text,
            "alcohol_en": text,
            "answers_en": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string", "maxLength": 80},
                        "answer_en": {"type": "string", "maxLength": 500},
                    },
                    "required": ["question_id", "answer_en"],
                },
            },
        },
        "required": [
            "doctor_brief_en",
            "chief_complaint_en",
            "onset_en",
            "associated_symptoms_en",
            "pertinent_negatives_en",
            "past_conditions_en",
            "medications",
            "allergies_en",
            "family_history_en",
            "answers_en",
        ],
    }


def _pair(text_en: str, original: str | None = None) -> dict[str, Any]:
    return {"text_original": original or text_en, "text_en": text_en, "confidence": 0.8}


def _pairs(values: list[str]) -> list[dict[str, Any]]:
    seen: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned.casefold() not in {item.casefold() for item in seen}:
            seen.append(cleaned)
    return [_pair(item) for item in seen[:15]]


def apply_summary(
    case_sheet: dict[str, Any], output: dict[str, Any], *, model: str
) -> dict[str, Any]:
    """Merge validated English fields; original-language text stays as the patient said it."""
    summary = SummaryOutput.model_validate(output)
    updated = deepcopy(CaseSheet.model_validate(case_sheet).model_dump(mode="json"))
    meta = updated["field_meta"]

    def mark(path: str) -> None:
        current = meta.get(path) or {}
        if current.get("source") in {"nurse", "doctor"}:
            return
        meta[path] = {
            "source": current.get("source", "patient_text"),
            "confidence": 0.8,
            "confirmed": bool(current.get("confirmed", False)),
            "ai_assisted": True,
        }

    updated["chief_complaint"]["text_en"] = summary.chief_complaint_en
    mark("/chief_complaint")
    hpi = updated["hpi"]
    for field, value in (
        ("onset", summary.onset_en),
        ("location", summary.location_en),
        ("character", summary.character_en),
        ("timing", summary.timing_en),
    ):
        if not value:
            continue
        existing = hpi.get(field)
        hpi[field] = _pair(value, existing.get("text_original") if existing else None)
        mark(f"/hpi/{field}")
    for field, values in (
        ("associated_symptoms", summary.associated_symptoms_en),
        ("pertinent_negatives", summary.pertinent_negatives_en),
        ("aggravating", summary.aggravating_en),
        ("relieving", summary.relieving_en),
    ):
        if values:
            hpi[field] = _pairs(values)
            mark(f"/hpi/{field}")
    if summary.past_conditions_en:
        updated["past_history"]["conditions"] = [v for v in summary.past_conditions_en if v][:15]
        mark("/past_history/conditions")
    if summary.surgeries_en:
        updated["past_history"]["surgeries"] = [v for v in summary.surgeries_en if v][:15]
    if summary.medications:
        updated["medications"] = [
            {"name": item.name, "dose": item.dose or None, "source": "patient_stated"}
            for item in summary.medications
        ]
        mark("/medications")
    if summary.allergies_en:
        updated["allergies"] = _pairs(summary.allergies_en)
        mark("/allergies")
    if summary.family_history_en:
        updated["family_history"] = _pairs(summary.family_history_en)
        mark("/family_history")
    social = updated.setdefault("social", {})
    if summary.tobacco_en:
        social["tobacco"] = _pair(summary.tobacco_en)
    if summary.alcohol_en:
        social["alcohol"] = _pair(summary.alcohol_en)
    updated["ai_summary"] = {
        "brief_en": summary.doctor_brief_en,
        "answers_en": {
            item.question_id: item.answer_en for item in summary.answers_en if item.answer_en
        },
        "model": model[:80],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return CaseSheet.model_validate(updated).model_dump(mode="json")


async def summarize_interview(
    provider: Any,
    *,
    language: str,
    age: int | None,
    sex: str | None,
    answers: list[dict[str, str]],
    danger_signs: list[str],
    session_id: str,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """Return validated summary output, or None if the model is unavailable or unsafe."""
    if not answers:
        return None
    prompt = build_summary_prompt(
        language=language, age=age, sex=sex, answers=answers, danger_signs=danger_signs
    )
    try:
        output = await provider.generate_json(
            prompt.text,
            summary_json_schema(),
            timeout=timeout,
            session_id=session_id,
            guard=guard_doctor_summary,
        )
        guard_doctor_summary(output)
        SummaryOutput.model_validate(output)
        return dict(output)
    except (LLMUnavailableError, UnsafeLLMOutputError, ValidationError, ValueError):
        return None
