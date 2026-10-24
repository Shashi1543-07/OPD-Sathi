"""The patient privacy notice, public: the waiting-area poster's QR and the patient's own
phone pages link here (Phase 7, task 7.5.2)."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import text

from app.core.errors import ApiProblemError
from app.core.ratelimit import check_rate_limit
from app.db.session import set_hospital_scope
from app.domain.interview.bank import Language
from app.domain.privacy import get_privacy_notice

router = APIRouter(tags=["privacy"])


@router.get("/privacy-notice/{hospital_id}")
async def privacy_notice(
    hospital_id: UUID,
    request: Request,
    language: Annotated[Language, Query()] = "en",
) -> dict[str, Any]:
    """The hospital's name, the full notice in one language, and whom to complain to."""
    settings = request.app.state.settings
    client = request.client.host if request.client else "unknown"
    limited = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{settings.app_env}:rl:privacy-notice:{client}",
        limit=60,
        window_seconds=60,
        fail_closed=False,
    )
    if limited is not None and not limited.allowed:
        raise ApiProblemError(
            status=429,
            code="RATE_LIMITED",
            title="Too many requests",
            detail="Please wait a moment and try again.",
            headers={"Retry-After": str(limited.retry_after)},
        )
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        hospital = await connection.scalar(
            text("SELECT name FROM hospitals WHERE id = :hospital_id"),
            {"hospital_id": hospital_id},
        )
        if hospital is None:
            raise ApiProblemError(
                status=404,
                code="NOT_FOUND",
                title="Not found",
                detail="This privacy notice link is not valid.",
            )
        contact = await connection.scalar(
            text(
                "SELECT grievance_contact FROM hospital_privacy_settings "
                "WHERE hospital_id = :hospital_id"
            ),
            {"hospital_id": hospital_id},
        )
    return {
        "hospital_name": hospital,
        "grievance_contact": (contact or "").strip() or None,
        "notice": get_privacy_notice(language),
    }
