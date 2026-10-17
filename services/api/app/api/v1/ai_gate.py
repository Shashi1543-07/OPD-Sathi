"""Whether online AI may work on a visit: the hospital's AI switch and the patient's own consent.

Language-model features (follow-up questions, translation, the doctor's draft, second opinions,
reading photos) need both. Online speech recognition and speaking the patient's own words
need the patient's consent. Without it a visit runs on this server alone: the rules engine,
tapping and typing, and recorded question audio.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db.repositories import read_ai_consent
from app.db.session import set_hospital_scope


async def hospital_llm_enabled(request: Any, hospital_id: UUID) -> bool:
    """Combine the global kill switch with this tenant's explicit AI opt-in."""
    if not request.app.state.settings.llm_enabled:
        return False
    cached = getattr(request.state, "llm_enablement", None)
    if isinstance(cached, tuple) and cached[0] == hospital_id:
        return cached[1] is True
    enabled = False
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            configured = await connection.scalar(
                text(
                    "SELECT llm_enabled FROM hospital_ai_settings WHERE hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
            # A hospital without a saved preference uses AI when the operator configured a
            # key; an administrator can still switch it off explicitly.
            enabled = configured is not False
    except Exception:
        # An unavailable or unmigrated setting store must leave the deterministic path active.
        enabled = False
    request.state.llm_enablement = (hospital_id, enabled)
    return enabled


async def ai_consented(request: Any, hospital_id: UUID, visit_id: UUID) -> bool:
    """Whether the patient allowed online AI help for this visit and has not withdrawn it."""
    cached = getattr(request.state, "ai_consent", None)
    if isinstance(cached, tuple) and cached[0] == visit_id:
        return cached[1] is True
    try:
        choice = await read_ai_consent(
            request.app.state.engine,
            request.app.state.settings,
            hospital_id=hospital_id,
            visit_id=visit_id,
        )
    except Exception:
        choice = None  # A consent that cannot be read is not a consent.
    allowed = choice == "granted"
    request.state.ai_consent = (visit_id, allowed)
    return allowed


async def llm_allowed(request: Any, hospital_id: UUID, visit_id: UUID) -> bool:
    """The language model may work on this visit: the hospital's switch and the patient's yes."""
    return await hospital_llm_enabled(request, hospital_id) and await ai_consented(
        request, hospital_id, visit_id
    )
