"""Extraction tests use synthetic transcripts and fixture-only provider outputs."""

import copy
from collections.abc import Mapping
from typing import Any

import pytest

from app.ai.extraction import (
    ExtractionValidationError,
    apply_extraction_patch,
    extract_case_sheet_patch,
    extraction_json_schema,
    merge_enriched_case_sheet,
)
from app.ai.providers.llm.base import LLMUnavailableError
from app.domain.interview.engine import InterviewState, build_case_sheet


def _sheet() -> dict[str, Any]:
    return build_case_sheet(
        InterviewState(
            language="en",
            answers={"chief_concern": "cough", "duration": "2 days"},
        )
    )


def _finding(
    path: str = "/hpi/location", quote: str = "right side", confidence: float = 0.91
) -> dict[str, Any]:
    return {
        "path": path,
        "text_original": quote,
        "evidence": quote,
        "confidence": confidence,
    }


def test_extraction_schema_is_strict_and_only_contains_allowlisted_paths() -> None:
    schema = extraction_json_schema()
    finding = schema["$defs"]["ExtractedFact"]
    assert finding["additionalProperties"] is False
    assert set(finding["properties"]["path"]["enum"]) == {
        "/hpi/onset",
        "/hpi/location",
        "/hpi/character",
        "/hpi/radiation",
        "/hpi/timing",
        "/hpi/associated_symptoms",
        "/hpi/pertinent_negatives",
        "/hpi/aggravating",
        "/hpi/relieving",
    }


def test_applies_quote_supported_finding_and_marks_patient_voice_source() -> None:
    updated = apply_extraction_patch(
        _sheet(), "sharp pain on the right side", {"findings": [_finding()]}
    )
    assert updated["hpi"]["location"]["text_original"] == "right side"
    assert updated["field_meta"]["/hpi/location"] == {
        "source": "patient_voice",
        "confidence": 0.91,
        "confirmed": False,
    }


def test_rejects_evidence_not_present_as_an_exact_transcript_substring() -> None:
    with pytest.raises(ExtractionValidationError, match="exact transcript quote"):
        apply_extraction_patch(
            _sheet(),
            "sharp pain on the right side",
            {"findings": [_finding(quote="left side")]},
        )


def test_rejects_paraphrase_even_when_the_evidence_quote_is_supported() -> None:
    finding = _finding(quote="right side")
    finding["text_original"] = "pain on the right side"
    with pytest.raises(ExtractionValidationError, match="exact transcript quote"):
        apply_extraction_patch(_sheet(), "pain on the right side", {"findings": [finding]})


@pytest.mark.parametrize(
    "path",
    [
        "/routing/department_code",
        "/medications/0/name",
        "/chief_complaint",
        "/hpi/severity_0_10",
    ],
)
def test_rejects_fields_outside_the_history_allowlist(path: str) -> None:
    with pytest.raises(ExtractionValidationError, match="does not match the schema"):
        apply_extraction_patch(_sheet(), "right side", {"findings": [_finding(path=path)]})


def test_preserves_existing_and_touch_confirmed_values() -> None:
    sheet = _sheet()
    sheet["hpi"]["onset"] = {
        "text_original": "2 days",
        "text_en": "2 days",
        "confidence": 0.5,
    }
    sheet["field_meta"]["/hpi/associated_symptoms"] = {
        "source": "patient_touch",
        "confidence": 1.0,
        "confirmed": True,
    }
    result = apply_extraction_patch(
        sheet,
        "2 days, nausea and dizziness",
        {
            "findings": [
                _finding("/hpi/onset", "2 days"),
                _finding("/hpi/associated_symptoms", "nausea"),
            ]
        },
    )
    assert result["hpi"]["onset"]["text_original"] == "2 days"
    assert result["hpi"]["associated_symptoms"] == []
    assert result["field_meta"]["/hpi/associated_symptoms"]["source"] == "patient_touch"


def test_adds_unique_list_findings_without_replacing_existing_values() -> None:
    result = apply_extraction_patch(
        _sheet(),
        "nausea and dizziness",
        {
            "findings": [
                _finding("/hpi/associated_symptoms", "nausea"),
                _finding("/hpi/associated_symptoms", "nausea"),
                _finding("/hpi/associated_symptoms", "dizziness"),
            ]
        },
    )
    assert [item["text_original"] for item in result["hpi"]["associated_symptoms"]] == [
        "nausea",
        "dizziness",
    ]


def test_concurrent_merge_preserves_staff_edit_and_only_adds_missing_ai_values() -> None:
    baseline = _sheet()
    enriched = apply_extraction_patch(
        baseline,
        "right side and nausea",
        {
            "findings": [
                _finding(),
                _finding("/hpi/associated_symptoms", "nausea"),
            ]
        },
    )
    latest = copy.deepcopy(baseline)
    latest["hpi"]["location"] = {
        "text_original": "left side",
        "text_en": "left side",
        "confidence": 1.0,
    }
    latest["field_meta"]["/hpi/location"] = {
        "source": "doctor",
        "confidence": 1.0,
        "confirmed": True,
    }
    merged = merge_enriched_case_sheet(latest, baseline, enriched)
    assert merged["hpi"]["location"]["text_original"] == "left side"
    assert [item["text_original"] for item in merged["hpi"]["associated_symptoms"]] == ["nausea"]


class FixtureProvider:
    name = "mock"

    def __init__(self, output: dict[str, Any] | Exception) -> None:
        self.output = output
        self.prompt = ""
        self.schema: Mapping[str, Any] = {}

    async def generate_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        timeout: float = 2.5,
        model: str | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        del timeout, model
        self.prompt = prompt
        self.schema = schema
        if isinstance(self.output, Exception):
            raise self.output
        return copy.deepcopy(self.output)


@pytest.mark.asyncio
async def test_extraction_uses_bounded_schema_and_session_id() -> None:
    provider = FixtureProvider({"findings": [_finding()]})
    result = await extract_case_sheet_patch(
        provider, _sheet(), "sharp pain on the right side", "en", session_id="synthetic-session"
    )
    assert result["hpi"]["location"]["text_original"] == "right side"
    assert provider.schema == extraction_json_schema()
    assert "sharp pain on the right side" in provider.prompt


@pytest.mark.asyncio
async def test_provider_failure_returns_the_immediate_deterministic_sheet() -> None:
    original = _sheet()
    provider = FixtureProvider(LLMUnavailableError("language model is unavailable"))
    result = await extract_case_sheet_patch(
        provider, original, "sharp pain", "en", session_id="synthetic-session"
    )
    assert result == original


@pytest.mark.asyncio
async def test_empty_voice_transcript_skips_provider_call() -> None:
    provider = FixtureProvider({"findings": []})
    original = _sheet()
    assert (
        await extract_case_sheet_patch(
            provider, original, "  ", "en", session_id="synthetic-session"
        )
        == original
    )
    assert provider.prompt == ""


@pytest.mark.asyncio
async def test_slow_provider_returns_deterministic_sheet_within_task_budget() -> None:
    import asyncio

    class SlowProvider(FixtureProvider):
        async def generate_json(
            self,
            prompt: str,
            schema: Mapping[str, Any],
            timeout: float = 2.5,
            model: str | None = None,
            *,
            session_id: str | None = None,
        ) -> dict[str, Any]:
            await asyncio.sleep(0.2)
            return await super().generate_json(
                prompt, schema, timeout, model, session_id=session_id
            )

    original = _sheet()
    provider = SlowProvider({"findings": [_finding()]})
    started = asyncio.get_running_loop().time()
    result = await extract_case_sheet_patch(
        provider,
        original,
        "right side",
        "en",
        session_id="synthetic-session",
        timeout=0.01,
    )
    assert result == original
    assert asyncio.get_running_loop().time() - started < 0.15
