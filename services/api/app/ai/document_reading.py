"""Read an old medical paper from its photo: diagnoses, medicines, results, procedures.

Used only for visits whose patient allowed online AI help. The model copies what is clearly
written; it does not interpret, judge results, or add anything. Its reading is stored as an
unconfirmed draft beside the photo, and the doctor accepts or rejects it after comparing the
two. Abnormal values are not the model's call: the API flags a result only against the
reference range printed on the same paper.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.providers.llm.base import LLMUnavailableError

_TEXT = {"type": "string", "maxLength": 120}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean"},
        "document_type": {
            "type": "string",
            "enum": ["prescription", "lab_report", "discharge_summary", "other"],
        },
        "document_date": {"type": "string", "maxLength": 10},
        "facility": _TEXT,
        "diagnoses": {"type": "array", "maxItems": 10, "items": _TEXT},
        "medicines": {
            "type": "array",
            "maxItems": 15,
            "items": {
                "type": "object",
                "properties": {"name": _TEXT, "dose": _TEXT, "how_often": _TEXT, "duration": _TEXT},
                "required": ["name", "dose", "how_often", "duration"],
            },
        },
        "lab_results": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "properties": {
                    "test": _TEXT,
                    "value": _TEXT,
                    "unit": _TEXT,
                    "reference_range": _TEXT,
                    "flag": {"type": "string", "maxLength": 8},
                },
                "required": ["test", "value", "unit", "reference_range", "flag"],
            },
        },
        "procedures": {"type": "array", "maxItems": 10, "items": _TEXT},
    },
    "required": [
        "readable",
        "document_type",
        "document_date",
        "facility",
        "diagnoses",
        "medicines",
        "lab_results",
        "procedures",
    ],
}

_INSTRUCTIONS = (
    "This is a photo of a medical paper (a prescription, a laboratory report, a discharge "
    "summary or another record) that a patient brought to an Indian government hospital. "
    "Copy only what is clearly written: diagnoses as the earlier doctor wrote them, medicines "
    "with strength, how often and for how long, laboratory tests with value, unit, the "
    "reference range printed beside them and any H or L mark printed, and procedures. Give the "
    "paper's date as YYYY-MM-DD only if it is printed unambiguously, otherwise an empty string. "
    "Do not interpret results, do not say whether anything is normal, do not guess unreadable "
    "words, do not add anything and do not give advice. Translate handwritten words only when "
    "you are certain. If nothing is readable, set readable to false and leave the lists empty."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _Medicine(_Strict):
    name: str = Field(min_length=2, max_length=120)
    dose: str = Field(default="", max_length=120)
    how_often: str = Field(default="", max_length=120)
    duration: str = Field(default="", max_length=120)


class _LabResult(_Strict):
    test: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="", max_length=120)
    reference_range: str = Field(default="", max_length=120)
    flag: str = Field(default="", max_length=8)


class DocumentReading(_Strict):
    readable: bool
    document_type: str = Field(pattern=r"^(prescription|lab_report|discharge_summary|other)$")
    document_date: str = Field(default="", max_length=10)
    facility: str = Field(default="", max_length=120)
    diagnoses: list[str] = Field(default_factory=list, max_length=10)
    medicines: list[_Medicine] = Field(default_factory=list, max_length=15)
    lab_results: list[_LabResult] = Field(default_factory=list, max_length=30)
    procedures: list[str] = Field(default_factory=list, max_length=10)


async def read_document(
    provider: Any, image: bytes, *, mime_type: str, session_id: str
) -> dict[str, Any]:
    """The paper's contents as the model read them, or raise LLMUnavailableError."""
    from google.genai import types

    parts = [types.Part.from_bytes(data=image, mime_type=mime_type), _INSTRUCTIONS]
    try:
        # The output guard is off on purpose: an earlier doctor's written diagnosis is
        # exactly what must be copied, and the reading is shown only as a draft to check.
        output = await provider.generate_json(
            parts, _SCHEMA, timeout=20.0, session_id=session_id, guard=None
        )
        reading = DocumentReading.model_validate(output)
    except ValidationError as exc:
        raise LLMUnavailableError("document could not be read") from exc
    if not reading.readable:
        return DocumentReading(readable=False, document_type=reading.document_type).model_dump()
    return reading.model_dump()
