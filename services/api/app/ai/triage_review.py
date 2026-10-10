"""AI double-check of the triage score, shown to staff next to the score's own reasons.

The model reads the whole interview and says whether the system's level fits. Its verdict
never changes the queue by itself: "more urgent" raises a staff alert so a nurse looks,
"less urgent" is a note a nurse may act on. A failure leaves the deterministic score alone.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from app.ai.output_guard import UnsafeLLMOutputError, validate_safe_output
from app.ai.prompts.builders import build_triage_review_prompt
from app.ai.providers.llm.base import LLMUnavailableError

Verdict = Literal["agree", "more_urgent", "less_urgent"]
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["agree", "more_urgent", "less_urgent"]},
        "reason_en": {"type": "string", "maxLength": 240},
    },
    "required": ["verdict", "reason_en"],
}


def _guard(output: Any) -> None:
    if not isinstance(output, dict) or output.get("verdict") not in {
        "agree",
        "more_urgent",
        "less_urgent",
    }:
        raise ValueError("unexpected triage review shape")


async def review_triage(
    provider: Any,
    *,
    age: int | None,
    sex: str | None,
    pregnant: bool | None,
    answers: list[dict[str, str]],
    danger_signs: list[str],
    acuity: dict[str, Any],
    session_id: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Return ``{verdict, reason_en, model, at}`` or ``None`` when no review is available."""
    prompt = build_triage_review_prompt(
        age=age,
        sex=sex,
        pregnant=pregnant,
        answers=answers,
        danger_signs=danger_signs,
        level=str(acuity.get("level", "routine")),
        score=int(acuity.get("score", 0)),
        reasons=[
            f"+{item.get('points')} {item.get('label')}"
            for item in acuity.get("components", [])
            if isinstance(item, dict)
        ],
    )
    try:
        output = await provider.generate_json(
            prompt.text, _SCHEMA, timeout=timeout, session_id=session_id, guard=_guard
        )
    except (LLMUnavailableError, ValueError):
        return None
    reason = " ".join(str(output.get("reason_en", "")).split())[:240]
    try:
        validate_safe_output(reason)
    except UnsafeLLMOutputError:
        reason = ""  # Keep the verdict; drop wording that strays into diagnosis or advice.
    return {
        "verdict": output["verdict"],
        "reason_en": reason,
        "model": str(getattr(provider, "model", "") or getattr(provider, "name", "")),
        "at": datetime.now(UTC).isoformat(),
    }
