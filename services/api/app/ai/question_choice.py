"""Bounded selection among configured interview question IDs."""

import asyncio
from collections.abc import Sequence

from app.ai.prompts.builders import QuestionCandidate, build_question_prompt
from app.ai.providers.llm.base import LLMProvider

MAX_DECISION_SECONDS = 0.6


async def select_question_id(
    provider: LLMProvider,
    *,
    transcript: str,
    language: str,
    candidates: Sequence[QuestionCandidate],
    fallback_id: str,
    session_id: str,
) -> str:
    """Choose a candidate ID within the decision budget or return the deterministic ID."""
    allowed_ids = {candidate.id for candidate in candidates}
    if fallback_id not in allowed_ids or not candidates or len(candidates) > 5:
        return fallback_id
    if len(candidates) == 1:
        return fallback_id
    prompt = build_question_prompt(transcript, language, tuple(candidates))
    schema = {
        "type": "object",
        "properties": {"question_id": {"type": "string", "enum": sorted(allowed_ids)}},
        "required": ["question_id"],
        "additionalProperties": False,
    }
    try:
        result = await asyncio.wait_for(
            provider.generate_json(
                prompt.text,
                schema,
                timeout=MAX_DECISION_SECONDS - 0.10,
                session_id=session_id,
            ),
            timeout=MAX_DECISION_SECONDS - 0.05,
        )
    except Exception:
        return fallback_id
    selected = result.get("question_id")
    return selected if isinstance(selected, str) and selected in allowed_ids else fallback_id
