"""Which visits a staff member may open, and when a patient's earlier records are visible.

A doctor or nurse opens visits of their own department; administrators oversee every
department. A patient's earlier records (other visits, old papers) are shown only while the
patient is under care: their visit is still open, or it is today's.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.errors import ApiProblemError


def _not_found() -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="NOT_FOUND",
        title="Visit not found",
        detail="The requested visit is unavailable.",
    )


def care_date() -> date:
    """Today in the hospital's time zone, for the "under care today" rule."""
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


async def check_visit_department(
    connection: Any,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    actor_id: UUID,
    role: str | None = None,
) -> None:
    """Allow staff of the visit's department (and administrators); others see 404."""
    visit_department = await connection.scalar(
        text(
            "SELECT department_id FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {"hospital_id": hospital_id, "visit_id": visit_id},
    )
    if not isinstance(visit_department, UUID):
        raise _not_found()
    if role == "admin":
        return  # Administrators oversee every department; reads are still audited.
    assigned_department = await connection.scalar(
        text(
            "SELECT department_id FROM users WHERE hospital_id = :hospital_id "
            "AND id = :actor_id AND is_active = true"
        ),
        {"hospital_id": hospital_id, "actor_id": actor_id},
    )
    if not isinstance(assigned_department, UUID):
        raise ApiProblemError(
            status=403,
            code="FORBIDDEN",
            title="Permission denied",
            detail="No staff department is assigned.",
        )
    if assigned_department != visit_department:
        raise _not_found()


async def visit_in_care(connection: Any, *, hospital_id: UUID, visit_id: UUID) -> tuple[UUID, bool]:
    """The visit's patient, and whether that patient is under care now (open, or today's)."""
    row = (
        (
            await connection.execute(
                text(
                    "SELECT patient_id, (status IN ('registering', 'awaiting_confirmation', "
                    "'queued', 'called', 'in_consult', 'skipped') OR service_date = :today) "
                    "AS in_care FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id, "today": care_date()},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    return row["patient_id"], bool(row["in_care"])
