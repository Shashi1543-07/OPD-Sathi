"""Evidence-checked, allow-listed case-sheet enrichment from patient voice turns."""

import asyncio
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output
from app.ai.prompts.builders import build_extraction_prompt
from app.ai.providers.llm.base import LLMProvider, LLMUnavailableError
from app.domain.case_sheet import CaseSheet

ExtractionPath = Literal[
    "/hpi/onset",
    "/hpi/location",
    "/hpi/character",
    "/hpi/radiation",
    "/hpi/timing",
    "/hpi/associated_symptoms",
    "/hpi/pertinent_negatives",
    "/hpi/aggravating",
    "/hpi/relieving",
]

_LIST_PATHS = frozenset(
    {
        "/hpi/associated_symptoms",
        "/hpi/pertinent_negatives",
        "/hpi/aggravating",
        "/hpi/relieving",
    }
)
_ALLOWED_PATHS: tuple[str, ...] = (
    "/hpi/onset",
    "/hpi/location",
    "/hpi/character",
    "/hpi/radiation",
    "/hpi/timing",
    "/hpi/associated_symptoms",
    "/hpi/pertinent_negatives",
    "/hpi/aggravating",
    "/hpi/relieving",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractedFact(StrictModel):
    path: ExtractionPath
    text_original: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class ExtractionResponse(StrictModel):
    findings: list[ExtractedFact] = Field(max_length=30)


class ExtractionValidationError(ValueError):
    """An extraction contains unsupported, malformed, or disallowed information."""


def extraction_json_schema() -> dict[str, Any]:
    """Return the strict, allow-listed JSON schema sent with extraction prompts."""
    return ExtractionResponse.model_json_schema()


def apply_extraction_patch(
    case_sheet: dict[str, Any], transcript: str, output: dict[str, Any]
) -> dict[str, Any]:
    """Merge only exact transcript quotes into currently empty, allow-listed history fields."""
    try:
        extraction = ExtractionResponse.model_validate(output)
    except ValidationError as exc:
        raise ExtractionValidationError("extraction output does not match the schema") from exc

    updated = deepcopy(CaseSheet.model_validate(case_sheet).model_dump(mode="json"))
    metadata = updated["field_meta"]
    for fact in extraction.findings:
        if fact.evidence not in transcript or fact.text_original != fact.evidence:
            raise ExtractionValidationError(
                "every extracted value must be an exact transcript quote"
            )
        current_meta = metadata.get(fact.path)
        if current_meta and current_meta.get("confirmed") is True:
            continue
        section, field = fact.path.split("/")[1:]
        target = updated[section][field]
        if fact.path in _LIST_PATHS:
            if not isinstance(target, list):
                raise ExtractionValidationError("an extraction list target has an invalid type")
            if any(item.get("text_original") == fact.text_original for item in target):
                continue
            if len(target) >= 30:
                continue
            target.append(
                {
                    "text_original": fact.text_original,
                    "text_en": fact.text_original,
                    "confidence": fact.confidence,
                }
            )
        else:
            if target is not None:
                continue
            updated[section][field] = {
                "text_original": fact.text_original,
                "text_en": fact.text_original,
                "confidence": fact.confidence,
            }
        metadata[fact.path] = {
            "source": "patient_voice",
            "confidence": fact.confidence,
            "confirmed": False,
        }
    try:
        return CaseSheet.model_validate(updated).model_dump(mode="json")
    except ValidationError as exc:
        raise ExtractionValidationError("merged case sheet does not match its schema") from exc


def merge_enriched_case_sheet(
    current: dict[str, Any], baseline: dict[str, Any], enriched: dict[str, Any]
) -> dict[str, Any]:
    """Merge only newly extracted allow-listed values into the latest stored sheet."""
    updated = deepcopy(CaseSheet.model_validate(current).model_dump(mode="json"))
    original = CaseSheet.model_validate(baseline).model_dump(mode="json")
    proposal = CaseSheet.model_validate(enriched).model_dump(mode="json")
    changed_metadata = updated["field_meta"]
    for path in _ALLOWED_PATHS:
        section, field = path.split("/")[1:]
        before = original[section][field]
        proposed = proposal[section][field]
        target = updated[section][field]
        if proposed == before:
            continue
        existing_meta = changed_metadata.get(path)
        if existing_meta and existing_meta.get("confirmed") is True:
            continue
        changed = False
        if path in _LIST_PATHS:
            additions = [item for item in proposed if item not in before]
            for item in additions:
                if item not in target and len(target) < 30:
                    target.append(item)
                    changed = True
        elif target is None:
            updated[section][field] = proposed
            changed = True
        else:
            continue
        if changed:
            source_meta = proposal["field_meta"].get(path)
            if source_meta and source_meta.get("source") == "patient_voice":
                changed_metadata[path] = source_meta
    try:
        return CaseSheet.model_validate(updated).model_dump(mode="json")
    except ValidationError as exc:
        raise ExtractionValidationError("merged case sheet does not match its schema") from exc


async def extract_case_sheet_patch(
    provider: LLMProvider,
    case_sheet: dict[str, Any],
    transcript: str,
    language: str,
    *,
    session_id: str,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Request a bounded extraction and return the unchanged sheet on any provider failure."""
    if not transcript.strip():
        return deepcopy(case_sheet)
    bounded_timeout = min(max(timeout, 0.01), 2.5)
    prompt = build_extraction_prompt(transcript, language)
    try:
        output = await asyncio.wait_for(
            provider.generate_json(
                prompt.text,
                extraction_json_schema(),
                timeout=bounded_timeout,
                session_id=session_id,
            ),
            timeout=bounded_timeout + 0.1,
        )
        validate_safe_output(output)
        return apply_extraction_patch(case_sheet, transcript, output)
    except (LLMUnavailableError, ExtractionValidationError, UnsafeLLMOutputError):
        return deepcopy(case_sheet)
    except Exception:
        # Provider adapters are not allowed to leak provider diagnostics into patient flows.
        return deepcopy(case_sheet)
