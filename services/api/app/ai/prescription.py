"""Read medicine names from a photo of the patient's old prescription (F12).

The photo is processed in memory and never stored. The patient sees the extracted names
and confirms or edits them before they become part of their history.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.providers.llm.base import LLMUnavailableError

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean"},
        "medicines": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "dose": {"type": "string", "maxLength": 60},
                    "how_often": {"type": "string", "maxLength": 60},
                },
                "required": ["name", "dose", "how_often"],
            },
        },
    },
    "required": ["readable", "medicines"],
}

_INSTRUCTIONS = (
    "This is a photo of an Indian outpatient prescription or medicine strip that a patient "
    "brought to the hospital. List only the medicine names that are clearly written, with "
    "the strength (for example 500 mg) and how often (for example 1-0-1 or twice daily) "
    "exactly as written. Do not guess unreadable words, do not add medicines, and do not "
    "add advice. If nothing is readable, set readable to false and return no medicines."
)


class _Medicine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=80)
    dose: str = Field(default="", max_length=60)
    how_often: str = Field(default="", max_length=60)


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readable: bool
    medicines: list[_Medicine] = Field(default_factory=list, max_length=12)


async def read_prescription(
    provider: Any, image: bytes, *, mime_type: str, session_id: str
) -> list[dict[str, str]]:
    """Return [{name, dose, how_often}], or raise LLMUnavailableError."""
    from google.genai import types

    parts = [types.Part.from_bytes(data=image, mime_type=mime_type), _INSTRUCTIONS]
    try:
        output = await provider.generate_json(
            parts, _SCHEMA, timeout=15.0, session_id=session_id, guard=None
        )
        result = _Result.model_validate(output)
    except ValidationError as exc:
        raise LLMUnavailableError("prescription could not be read") from exc
    if not result.readable:
        return []
    return [item.model_dump() for item in result.medicines]
