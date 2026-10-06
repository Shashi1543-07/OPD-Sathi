"""Department classification tests use synthetic labels and outputs."""

from collections.abc import Mapping
from typing import Any

import pytest

from app.ai.department_classifier import (
    classify_department,
    combine_department_classification,
    department_json_schema,
)
from app.domain.routing.engine import Route


class FixtureProvider:
    name = "mock"

    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self.result = result
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
        del prompt, timeout, model, session_id
        self.schema = schema
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_classifier_schema_contains_only_configured_hospital_labels() -> None:
    labels = {"GENMED": "General Medicine", "ORTHO": "Orthopedics"}
    provider = FixtureProvider({"department_code": "ORTHO"})
    selected = await classify_department(
        provider,
        transcript="synthetic joint pain",
        language="en",
        department_labels=labels,
        session_id="synthetic-session",
    )
    assert selected == "ORTHO"
    assert provider.schema == department_json_schema(("GENMED", "ORTHO"))


@pytest.mark.asyncio
async def test_classifier_rejects_unconfigured_codes_and_provider_failures() -> None:
    labels = {"GENMED": "General Medicine"}
    invalid = await classify_department(
        FixtureProvider({"department_code": "UNCONFIGURED"}),
        transcript="synthetic complaint",
        language="en",
        department_labels=labels,
        session_id="synthetic-session",
    )
    failed = await classify_department(
        FixtureProvider(RuntimeError("synthetic provider failure")),
        transcript="synthetic complaint",
        language="en",
        department_labels=labels,
        session_id="synthetic-session",
    )
    assert invalid is None
    assert failed is None


def test_agreement_raises_confidence_without_nurse_confirmation() -> None:
    deterministic = Route("GENMED", 0.65, "Matched two terms.", True, ("GENMED", "ENT"))
    combined = combine_department_classification(deterministic, "GENMED")
    assert combined.department_code == "GENMED"
    assert combined.confidence == 0.9
    assert combined.needs_nurse_confirmation is False
    assert combined.matched_modules == deterministic.matched_modules


def test_disagreement_routes_to_nurse_confirmation() -> None:
    deterministic = Route("GENMED", 0.9, "Matched general medicine.", False, ("GENMED",))
    combined = combine_department_classification(deterministic, "ORTHO")
    assert combined.department_code == "NURSE_TRIAGE"
    assert combined.confidence == 0.5
    assert combined.needs_nurse_confirmation is True
    assert combined.reason_en == "Staff confirmation is required."


def test_missing_model_result_preserves_deterministic_route() -> None:
    deterministic = Route("GENMED", 0.9, "Matched general medicine.", False, ("GENMED",))
    assert combine_department_classification(deterministic, None) == deterministic
