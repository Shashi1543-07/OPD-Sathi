"""Configured-label department classification with nurse-confirmation on disagreement."""

import asyncio
from collections.abc import Mapping
from typing import Any

from app.ai.prompts.builders import build_department_prompt
from app.ai.providers.llm.base import LLMProvider
from app.domain.routing.engine import Route

MAX_DEPARTMENT_SECONDS = 0.6


def department_json_schema(codes: tuple[str, ...]) -> dict[str, Any]:
    """Return a closed schema whose enum contains only active configured departments."""
    return {
        "type": "object",
        "properties": {"department_code": {"type": "string", "enum": list(codes)}},
        "required": ["department_code"],
        "additionalProperties": False,
    }


async def classify_department(
    provider: LLMProvider,
    *,
    transcript: str,
    language: str,
    department_labels: Mapping[str, str],
    session_id: str,
    timeout: float = MAX_DEPARTMENT_SECONDS,
) -> str | None:
    """Return an allowed department code or None when the model is unavailable/invalid."""
    codes = tuple(sorted(department_labels))
    if not transcript.strip() or not codes:
        return None
    prompt = build_department_prompt(
        transcript,
        language,
        codes,
        department_labels=department_labels,
    )
    bounded_timeout = min(max(timeout, 0.01), MAX_DEPARTMENT_SECONDS)
    try:
        result = await asyncio.wait_for(
            provider.generate_json(
                prompt.text,
                department_json_schema(codes),
                timeout=bounded_timeout - 0.10,
                session_id=session_id,
            ),
            timeout=bounded_timeout - 0.05,
        )
    except Exception:
        return None
    code = result.get("department_code")
    return code if isinstance(code, str) and code in department_labels else None


def combine_department_classification(deterministic: Route, model_code: str | None) -> Route:
    """Raise confidence on agreement and send disagreement to mandatory nurse review."""
    if model_code is None:
        return deterministic
    if model_code == deterministic.department_code:
        return Route(
            department_code=deterministic.department_code,
            confidence=max(deterministic.confidence, 0.90),
            reason_en=deterministic.reason_en,
            needs_nurse_confirmation=False,
            matched_modules=deterministic.matched_modules,
        )
    return Route(
        department_code="NURSE_TRIAGE",
        confidence=min(deterministic.confidence, 0.50),
        reason_en="Staff confirmation is required.",
        needs_nurse_confirmation=True,
        matched_modules=deterministic.matched_modules,
    )
