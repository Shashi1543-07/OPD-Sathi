"""The doctor's own notes for a visit: versioned, audited, and never written by AI (7.11.1)."""

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.v1.visit_access import care_date, visit_in_care
from app.api.v1.visit_access import check_visit_department as _check_visit_department
from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.repositories import DoctorNoteRepository
from app.db.session import set_hospital_scope

router = APIRouter(tags=["case sheets"])

NoteText = Annotated[str, Field(max_length=4000)]


class DoctorNotesPayload(BaseModel):
    """What the doctor writes; at least one part must have text."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    findings: NoteText = ""
    assessment: NoteText = ""
    plan: NoteText = ""
    follow_up_on: date | None = None

    @model_validator(mode="after")
    def not_empty(self) -> "DoctorNotesPayload":
        if not (self.findings or self.assessment or self.plan):
            raise ValueError("Write findings, an assessment or a plan before saving.")
        return self


class DoctorNotesVersion(BaseModel):
    version: int
    findings: str
    assessment: str
    plan: str
    follow_up_on: date | None = None
    by_name: str
    at: datetime


class DoctorNotesResponse(BaseModel):
    visit_id: UUID
    # 0 until the first save; send it back in If-Match to save the next version.
    version: int
    # Whether the notes can still be changed (the patient is under care today).
    editable: bool
    # Newest first; the first entry is the current wording.
    versions: list[DoctorNotesVersion] = Field(default_factory=list)


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


def _expected_version(value: str | None) -> int:
    if value is None:
        raise _problem(
            428,
            "PRECONDITION_REQUIRED",
            "Version required",
            "Send the current notes version in If-Match (0 before the first save).",
        )
    candidate = value.strip().strip('"')
    if not re.fullmatch(r"0|[1-9][0-9]*", candidate):
        raise _problem(
            400, "INVALID_IF_MATCH", "Invalid version", "If-Match must contain one version."
        )
    return int(candidate)


async def _load(
    connection: Any, request: Request, *, hospital_id: UUID, visit_id: UUID
) -> DoctorNotesResponse:
    versions = await DoctorNoteRepository(connection, request.app.state.settings).versions(
        hospital_id=hospital_id, visit_id=visit_id
    )
    _patient_id, in_care = await visit_in_care(
        connection, hospital_id=hospital_id, visit_id=visit_id
    )
    return DoctorNotesResponse(
        visit_id=visit_id,
        version=versions[0]["version"] if versions else 0,
        editable=in_care,
        versions=[DoctorNotesVersion(**version) for version in versions],
    )


@router.get("/visits/{visit_id}/doctor-notes", response_model=DoctorNotesResponse)
async def get_doctor_notes(
    visit_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> DoctorNotesResponse:
    """The doctor's notes and every earlier version, for staff of the visit's department."""
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _check_visit_department(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            actor_id=actor_id,
            role=principal.get("role"),
        )
        notes = await _load(connection, request, hospital_id=hospital_id, visit_id=visit_id)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="doctor_notes.read",
            resource_type="visit",
            resource_id=visit_id,
            meta={"version": notes.version},
        )
    response.headers["ETag"] = f'"{notes.version}"'
    return notes


@router.put("/visits/{visit_id}/doctor-notes", response_model=DoctorNotesResponse)
async def save_doctor_notes(
    visit_id: UUID,
    payload: DoctorNotesPayload,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor"))],
    if_match: Annotated[str | None, Header()] = None,
) -> DoctorNotesResponse:
    """Save the next version of the notes, starting from the version the doctor last saw.

    Allowed to doctors of the visit's department while the patient is under care (the visit
    is open or today's), so earlier records are not rewritten later. Every save is audited
    with the parts that changed, never their text.
    """
    expected = _expected_version(if_match)
    today = care_date()
    if payload.follow_up_on is not None and not (
        today <= payload.follow_up_on <= today + timedelta(days=366)
    ):
        raise _problem(
            422,
            "INVALID_FOLLOW_UP",
            "Check the follow-up date",
            "The follow-up date must be between today and one year from now.",
        )
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id
        )
        _patient_id, in_care = await visit_in_care(
            connection, hospital_id=hospital_id, visit_id=visit_id
        )
        if not in_care:
            raise _problem(
                409,
                "NOTES_CLOSED",
                "Notes can no longer be changed",
                "Notes can be changed only while the patient is under your care today.",
            )
        repository = DoctorNoteRepository(connection, request.app.state.settings)
        earlier = await repository.versions(hospital_id=hospital_id, visit_id=visit_id)
        new_version = await repository.add_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=expected,
            body={
                "findings": payload.findings,
                "assessment": payload.assessment,
                "plan": payload.plan,
            },
            follow_up_on=payload.follow_up_on,
            author_id=actor_id,
        )
        if new_version is None:
            raise _problem(
                409,
                "NOTES_CONFLICT",
                "Notes changed",
                "Someone saved these notes meanwhile. Reload them before saving yours.",
            )
        previous = earlier[0] if earlier else {}
        changed = [
            part
            for part in ("findings", "assessment", "plan", "follow_up_on")
            if getattr(payload, part) != previous.get(part, "" if part != "follow_up_on" else None)
        ]
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="doctor_notes.saved",
            resource_type="visit",
            resource_id=visit_id,
            meta={"version_before": expected, "version_after": new_version, "changed": changed},
        )
        notes = await _load(connection, request, hospital_id=hospital_id, visit_id=visit_id)
    response.headers["ETag"] = f'"{notes.version}"'
    return notes
