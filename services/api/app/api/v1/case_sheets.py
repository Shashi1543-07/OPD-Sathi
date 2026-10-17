"""Audited case sheet reads and optimistic-concurrency doctor edits."""

import copy
import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from starlette.responses import Response as StarletteResponse

from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.repositories import (
    CaseSheetRepository,
    IntakeRepository,
    InterviewTurnRepository,
    PatientRepository,
)
from app.db.session import set_hospital_scope
from app.domain.case_sheet import CaseSheet
from app.domain.case_sheet_patch import CaseSheetPatchError, apply_case_sheet_patch
from app.domain.redflags.engine import RULES
from app.pdf import render_case_sheet_pdf

router = APIRouter(tags=["case sheets"])
_RULE_REASONS = {rule.id: rule.reason_en for rule in RULES}


def _with_flag_reasons(data: dict[str, Any]) -> dict[str, Any]:
    """Give every red flag an English reason so doctors never see only the raw evidence."""
    flags = data.get("red_flags")
    if not isinstance(flags, list):
        return data
    filled = [
        {**flag, "reason_en": _RULE_REASONS.get(str(flag.get("id")))}
        if isinstance(flag, dict) and not flag.get("reason_en") and flag.get("id") in _RULE_REASONS
        else flag
        for flag in flags
    ]
    return {**data, "red_flags": filled}


class PatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AddOrReplace(PatchModel):
    op: Literal["add", "replace"]
    path: str = Field(min_length=2, max_length=500)
    value: Any


class Remove(PatchModel):
    op: Literal["remove"]
    path: str = Field(min_length=2, max_length=500)


PatchOperation = Annotated[AddOrReplace | Remove, Field(discriminator="op")]


class PatientContext(BaseModel):
    """Non-identifying visit context the doctor needs next to the history."""

    age_years: int | None = None
    sex: str | None = None
    language: str | None = None
    token: str | None = None
    urgency: str | None = None
    status: str | None = None
    department_name: str | None = None
    # Staff-only triage score and the reason for every point (never shown to patients).
    triage: dict[str, Any] | None = None
    queued_at: datetime | None = None
    # The patient's choice about online AI help: "on", "off" (said no, or never asked), or
    # "withdrawn" (said yes, then stopped it; the AI draft is no longer shown).
    ai_help: Literal["on", "off", "withdrawn"] | None = None


class CaseSheetResponse(BaseModel):
    visit_id: UUID
    version: int
    case_sheet: CaseSheet
    confirmed_by_patient: bool = False
    confirmed_at: datetime | None = None
    patient: PatientContext | None = None


class TranscriptTurn(BaseModel):
    turn_index: int
    question_id: str
    input_mode: Literal["voice", "touch", "text", "nurse"]
    text_original: str | None
    text_english: str | None
    stt_confidence: float | None


class TranscriptResponse(BaseModel):
    visit_id: UUID
    turns: list[TranscriptTurn]


def _problem(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


def _parse_if_match(value: str | None) -> int:
    if value is None:
        raise _problem(
            428,
            "PRECONDITION_REQUIRED",
            "Version required",
            "Send the current case sheet version in If-Match.",
        )
    candidate = value.strip()
    if candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1]
    if not re.fullmatch(r"[1-9][0-9]*", candidate):
        raise _problem(
            400,
            "INVALID_IF_MATCH",
            "Invalid version",
            "If-Match must contain one positive case sheet version.",
        )
    return int(candidate)


async def _check_visit_department(
    connection: Any,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    actor_id: UUID,
    role: str | None = None,
) -> None:
    visit_department = await connection.scalar(
        text(
            "SELECT department_id FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {"hospital_id": hospital_id, "visit_id": visit_id},
    )
    if not isinstance(visit_department, UUID):
        raise _problem(404, "NOT_FOUND", "Visit not found", "The requested visit is unavailable.")
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
        raise _problem(403, "FORBIDDEN", "Permission denied", "No staff department is assigned.")
    if assigned_department != visit_department:
        raise _problem(404, "NOT_FOUND", "Visit not found", "The requested visit is unavailable.")


@router.get("/visits/{visit_id}/case-sheet", response_model=CaseSheetResponse)
async def get_case_sheet(
    visit_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> CaseSheetResponse:
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
        context = (
            (
                await connection.execute(
                    text(
                        "SELECT v.age_years, p.sex, v.language, v.token_label AS token, "
                        "v.urgency, v.status, d.name_en AS department_name, "
                        "v.acuity AS triage, v.queued_at, "
                        "CASE WHEN a.withdrawn_at IS NOT NULL THEN 'withdrawn' "
                        "WHEN a.granted THEN 'on' ELSE 'off' END AS ai_help FROM visits v "
                        "JOIN patients p ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                        "LEFT JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id "
                        "LEFT JOIN consents a ON a.hospital_id = v.hospital_id "
                        "AND a.visit_id = v.id AND a.purpose = 'ai' "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        saved = await CaseSheetRepository(connection, request.app.state.settings).get(
            hospital_id=hospital_id, visit_id=visit_id
        )
        if saved is None:
            raise _problem(
                404, "NOT_FOUND", "Case sheet not found", "The case sheet is unavailable."
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="case_sheet.read",
            resource_type="case_sheet",
            resource_id=saved["id"],
            meta={"version": saved["version"]},
        )
    version = int(saved["version"])
    response.headers["ETag"] = f'"{version}"'
    withdrawn = bool(context and context["ai_help"] == "withdrawn")
    return CaseSheetResponse(
        visit_id=visit_id,
        version=version,
        case_sheet=_with_flag_reasons(_without_ai_draft(saved["data"], withdrawn)),
        confirmed_by_patient=saved.get("confirmed_by_patient") is True,
        confirmed_at=saved.get("confirmed_at"),
        patient=PatientContext(**dict(context)) if context else None,
    )


def _without_ai_draft(data: dict[str, Any], withdrawn: bool) -> dict[str, Any]:
    """The sheet as shown once the patient stopped AI help: the AI draft is left out."""
    return {**data, "ai_summary": None} if withdrawn else data


async def _ai_withdrawn(
    connection: Any, request: Request, *, hospital_id: UUID, visit_id: UUID
) -> bool:
    choice = await IntakeRepository(connection, request.app.state.settings).ai_consent(
        hospital_id=hospital_id, visit_id=visit_id
    )
    return choice == "withdrawn"


@router.get("/visits/{visit_id}/case-sheet.pdf", response_class=StarletteResponse)
async def export_case_sheet_pdf(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> StarletteResponse:
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
        saved = await CaseSheetRepository(connection, request.app.state.settings).get(
            hospital_id=hospital_id, visit_id=visit_id
        )
        if saved is None:
            raise _problem(
                404, "NOT_FOUND", "Case sheet not found", "The case sheet is unavailable."
            )
        withdrawn = await _ai_withdrawn(
            connection, request, hospital_id=hospital_id, visit_id=visit_id
        )
        visit = (
            (
                await connection.execute(
                    text(
                        "SELECT v.token_label, v.urgency_reasons, v.age_years, p.sex "
                        "FROM visits v JOIN patients p ON p.hospital_id = v.hospital_id "
                        "AND p.id = v.patient_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if visit is None:
            raise _problem(
                404,
                "NOT_FOUND",
                "Visit not found",
                "The requested visit is unavailable.",
            )
        reasons = [
            str(
                item.get("reason_en")
                or _RULE_REASONS.get(str(item.get("id")))
                or item.get("evidence")
                or ""
            )
            for item in saved["data"].get("red_flags", [])
            if isinstance(item, dict)
        ]
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="case_sheet.pdf_exported",
            resource_type="case_sheet",
            resource_id=saved["id"],
            meta={"version": int(saved["version"]), "red_flag_count": len(reasons)},
        )
    sex = {"female": "Female", "male": "Male", "other": "Other"}.get(str(visit.get("sex")), "")
    age = visit.get("age_years")
    patient_line = " · ".join(
        part for part in (f"{age} years" if isinstance(age, int) else "", sex) if part
    )
    pdf = render_case_sheet_pdf(
        _without_ai_draft(saved["data"], withdrawn),
        queue_number=visit["token_label"] or "",
        attention_reasons=reasons,
        patient_line=patient_line,
    )
    return StarletteResponse(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="case-sheet-{visit_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.patch("/visits/{visit_id}/case-sheet", response_model=CaseSheetResponse)
async def patch_case_sheet(
    visit_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor"))],
    operations: Annotated[
        list[PatchOperation],
        Body(media_type="application/json-patch+json", min_length=1, max_length=100),
    ],
    if_match: Annotated[str | None, Header()] = None,
) -> CaseSheetResponse:
    expected_version = _parse_if_match(if_match)
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _check_visit_department(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            actor_id=actor_id,
        )
        repository = CaseSheetRepository(connection, request.app.state.settings)
        saved = await repository.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
        if saved is None:
            raise _problem(
                404, "NOT_FOUND", "Case sheet not found", "The case sheet is unavailable."
            )
        withdrawn = await _ai_withdrawn(
            connection, request, hospital_id=hospital_id, visit_id=visit_id
        )
        if int(saved["version"]) != expected_version:
            raise _problem(
                409,
                "CASE_SHEET_CONFLICT",
                "Case sheet changed",
                "Reload the latest case sheet before applying your edits.",
            )
        try:
            updated, changed_paths = apply_case_sheet_patch(
                saved["data"], [operation.model_dump() for operation in operations]
            )
        except CaseSheetPatchError as exc:
            raise _problem(
                422, "INVALID_CASE_SHEET_PATCH", "Invalid case sheet edit", str(exc)
            ) from exc
        new_version = await repository.replace_if_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=expected_version,
            data=updated,
            editor_id=actor_id,
        )
        if new_version is None:
            raise _problem(
                409,
                "CASE_SHEET_CONFLICT",
                "Case sheet changed",
                "Reload the latest case sheet before applying your edits.",
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="case_sheet.updated",
            resource_type="case_sheet",
            resource_id=saved["id"],
            meta={
                "version_before": expected_version,
                "version_after": new_version,
                "changes": [operation.op + ":" + operation.path for operation in operations],
            },
        )
    response.headers["ETag"] = f'"{new_version}"'
    return CaseSheetResponse(
        visit_id=visit_id,
        version=new_version,
        case_sheet=_with_flag_reasons(_without_ai_draft(updated, withdrawn)),
        confirmed_by_patient=saved.get("confirmed_by_patient") is True,
        confirmed_at=saved.get("confirmed_at"),
    )


class SummaryReviewPayload(PatchModel):
    decision: Literal["accepted", "edited", "rejected"]
    # The doctor's own wording of the brief; required when the decision is "edited".
    brief_en: str | None = Field(default=None, min_length=1, max_length=600)


@router.post("/visits/{visit_id}/ai-summary/review", response_model=CaseSheetResponse)
async def review_ai_summary(
    visit_id: UUID,
    payload: SummaryReviewPayload,
    request: Request,
    response: Response,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor"))],
    if_match: Annotated[str | None, Header()] = None,
) -> CaseSheetResponse:
    """Record a doctor's decision on the AI draft: accept it, rewrite it, or reject it.

    Until then the brief is shown as an unreviewed AI draft. A rewrite keeps the model's
    wording alongside the doctor's, and every decision is audited.
    """
    if payload.decision == "edited" and not payload.brief_en:
        raise _problem(422, "VALIDATION_FAILED", "Brief required", "Write the brief to save it.")
    expected_version = _parse_if_match(if_match)
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id
        )
        repository = CaseSheetRepository(connection, request.app.state.settings)
        saved = await repository.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
        if saved is None:
            raise _problem(
                404, "NOT_FOUND", "Case sheet not found", "The case sheet is unavailable."
            )
        if await _ai_withdrawn(connection, request, hospital_id=hospital_id, visit_id=visit_id):
            raise _problem(
                409,
                "AI_HELP_WITHDRAWN",
                "AI help stopped",
                "The patient stopped AI help for this visit; the AI draft is not used.",
            )
        if int(saved["version"]) != expected_version:
            raise _problem(
                409,
                "CASE_SHEET_CONFLICT",
                "Case sheet changed",
                "Reload the latest case sheet before reviewing the AI draft.",
            )
        data = copy.deepcopy(saved["data"])
        summary = data.get("ai_summary")
        if not isinstance(summary, dict) or not summary.get("brief_en"):
            raise _problem(
                409, "NO_AI_SUMMARY", "No AI draft", "This case sheet has no AI draft to review."
            )
        reviewer = await connection.scalar(
            text("SELECT name FROM users WHERE hospital_id = :hospital_id AND id = :actor_id"),
            {"hospital_id": hospital_id, "actor_id": actor_id},
        )
        earlier = summary.get("review") or {}
        review: dict[str, Any] = {
            "status": payload.decision,
            "by_name": str(reviewer or "")[:200],
            "at": datetime.now(UTC).isoformat(),
        }
        if payload.decision == "edited":
            review["original_brief_en"] = earlier.get("original_brief_en") or summary["brief_en"]
            summary["brief_en"] = payload.brief_en
            summary["statements"] = []  # The doctor's own words; no answers are cited.
        elif earlier.get("original_brief_en"):
            review["original_brief_en"] = earlier["original_brief_en"]
        summary["review"] = review
        try:
            updated = CaseSheet.model_validate(data).model_dump(mode="json")
        except ValidationError as exc:
            raise _problem(
                422, "INVALID_CASE_SHEET_PATCH", "Invalid review", "The review could not be saved."
            ) from exc
        new_version = await repository.replace_if_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=expected_version,
            data=updated,
            editor_id=actor_id,
        )
        if new_version is None:
            raise _problem(
                409,
                "CASE_SHEET_CONFLICT",
                "Case sheet changed",
                "Reload the latest case sheet before reviewing the AI draft.",
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="case_sheet.ai_summary_reviewed",
            resource_type="case_sheet",
            resource_id=saved["id"],
            meta={
                "decision": payload.decision,
                "version_before": expected_version,
                "version_after": new_version,
            },
        )
    response.headers["ETag"] = f'"{new_version}"'
    return CaseSheetResponse(
        visit_id=visit_id,
        version=new_version,
        case_sheet=_with_flag_reasons(updated),
        confirmed_by_patient=saved.get("confirmed_by_patient") is True,
        confirmed_at=saved.get("confirmed_at"),
    )


@router.post("/visits/{visit_id}/ai-consent/withdraw")
async def withdraw_ai_help_at_desk(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> dict[str, Any]:
    """Stop online AI help when a patient asks staff to (for example without a phone).

    The same as the patient's own button on their status page: nothing more about the visit
    goes to AI services, the doctor no longer sees the AI draft, and the staff member who
    recorded it is in the audit log.
    """
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
        withdrawn = await IntakeRepository(
            connection, request.app.state.settings
        ).withdraw_ai_consent(hospital_id=hospital_id, visit_id=visit_id, by="staff")
        if withdrawn:
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action="consent.ai_withdrawn",
                resource_type="visit",
                resource_id=visit_id,
                meta={"by": "staff"},
            )
    if not withdrawn:
        raise _problem(
            409, "AI_HELP_NOT_ON", "AI help is not on", "AI help is not on for this visit."
        )
    return {"visit_id": str(visit_id), "ai_help": "withdrawn"}


class PreviousVisit(BaseModel):
    """One earlier registered visit, as recorded then. Never edited from here."""

    visit_id: UUID
    service_date: date
    department: str
    status: str
    token: str | None = None
    urgency: str
    chief_complaint: str | None = None
    # The language model's draft brief for that visit (labelled AI in the interface).
    ai_brief: str | None = None
    # A doctor or nurse edited that visit's case sheet.
    staff_edited: bool = False
    red_flags: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    no_known_allergies: bool = False


class KnownItem(BaseModel):
    """Something the patient reported at one or more earlier visits (patient-reported)."""

    text: str
    last_reported: date
    visits: int


class PatientHistoryResponse(BaseModel):
    visit_id: UUID
    opd_number: str | None = None
    # How today's visit was joined to the record: "card" (patient's OPD card) or "staff".
    linked_by: Literal["card", "staff"] | None = None
    follow_up_of: UUID | None = None
    previous_visits: list[PreviousVisit] = Field(default_factory=list)
    known_allergies: list[KnownItem] = Field(default_factory=list)
    known_medications: list[KnownItem] = Field(default_factory=list)
    known_conditions: list[KnownItem] = Field(default_factory=list)
    # Date of the most recent visit at which the patient said they have no known allergy.
    no_known_allergies_reported: date | None = None


def _texts(items: Any, *keys: str) -> list[str]:
    values: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
        elif isinstance(item, dict):
            for key in keys:
                text_value = item.get(key)
                if isinstance(text_value, str) and text_value.strip():
                    values.append(text_value.strip())
                    break
    return values


def _previous_visit(row: dict[str, Any]) -> PreviousVisit:
    sheet = row.get("case_sheet_data") or {}
    complaint = sheet.get("chief_complaint") or {}
    medications = [
        f"{item['name']} {item['dose']}".strip() if item.get("dose") else str(item["name"])
        for item in sheet.get("medications") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    negatives = _texts((sheet.get("hpi") or {}).get("pertinent_negatives"), "text_en")
    flags = [
        str(flag.get("reason_en") or _RULE_REASONS.get(str(flag.get("id"))) or flag.get("id"))
        for flag in sheet.get("red_flags") or []
        if isinstance(flag, dict)
    ]
    # A visit whose patient stopped AI help shows no AI draft, here as on its own sheet.
    summary = {} if row.get("ai_withdrawn") else sheet.get("ai_summary") or {}
    return PreviousVisit(
        visit_id=row["visit_id"],
        service_date=row["service_date"],
        department=row["department"],
        status=row["status"],
        token=row.get("token_label"),
        urgency=row["urgency"],
        chief_complaint=complaint.get("text_en") or complaint.get("text_original"),
        ai_brief=summary.get("brief_en") if isinstance(summary, dict) else None,
        staff_edited=bool(row.get("staff_edited")),
        red_flags=flags[:10],
        conditions=_texts((sheet.get("past_history") or {}).get("conditions"))[:15],
        medications=medications[:15],
        allergies=_texts(sheet.get("allergies"), "text_en", "text_original")[:15],
        no_known_allergies=any(
            denial.startswith("No known drug or food allergy") for denial in negatives
        ),
    )


def _known(visits: list[PreviousVisit], field: str) -> list[KnownItem]:
    """Merge what earlier visits recorded, newest first, without guessing synonyms."""
    merged: dict[str, KnownItem] = {}
    for visit in visits:  # newest first
        for text_value in getattr(visit, field):
            key = text_value.casefold()
            if key in merged:
                merged[key].visits += 1
            else:
                merged[key] = KnownItem(
                    text=text_value, last_reported=visit.service_date, visits=1
                )
    return list(merged.values())[:20]


@router.get("/visits/{visit_id}/history", response_model=PatientHistoryResponse)
async def get_patient_history(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> PatientHistoryResponse:
    """Earlier visits of today's patient, for the clinician looking after today's visit.

    Allowed to staff of the department treating the visit while it is open or still today's
    (and to administrators), so a doctor sees a patient's earlier visits in other departments
    only while caring for them. Every read is audited. A first visit has no earlier visits.
    """
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
        visit = (
            (
                await connection.execute(
                    text(
                        "SELECT v.patient_id, v.patient_link, v.previous_visit_id, p.mrn, "
                        "(v.status IN ('registering', 'awaiting_confirmation', 'queued', "
                        "'called', 'in_consult', 'skipped') OR v.service_date = :today) "
                        "AS in_care "
                        "FROM visits v JOIN patients p ON p.hospital_id = v.hospital_id "
                        "AND p.id = v.patient_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                        "today": datetime.now(ZoneInfo("Asia/Kolkata")).date(),
                    },
                )
            )
            .mappings()
            .one()
        )
        if not visit["in_care"] and principal.get("role") != "admin":
            raise _problem(
                403,
                "HISTORY_CLOSED",
                "Earlier visits unavailable",
                "Earlier visits are shown only while the patient is under your care today.",
            )
        rows = await PatientRepository(connection, request.app.state.settings).visit_history(
            hospital_id=hospital_id,
            patient_id=visit["patient_id"],
            exclude_visit_id=visit_id,
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="patient_history.read",
            resource_type="visit",
            resource_id=visit_id,
            meta={"previous_visits": len(rows)},
        )
    previous = [_previous_visit(row) for row in rows]
    denial = next((visit_.service_date for visit_ in previous if visit_.no_known_allergies), None)
    return PatientHistoryResponse(
        visit_id=visit_id,
        opd_number=visit["mrn"],
        linked_by=visit["patient_link"],
        follow_up_of=visit["previous_visit_id"],
        previous_visits=previous,
        known_allergies=_known(previous, "allergies"),
        known_medications=_known(previous, "medications"),
        known_conditions=_known(previous, "conditions"),
        no_known_allergies_reported=denial,
    )


@router.get("/visits/{visit_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "admin"))],
) -> TranscriptResponse:
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
        turns = await InterviewTurnRepository(
            connection, request.app.state.settings
        ).list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="transcript.read",
            resource_type="visit_transcript",
            resource_id=visit_id,
            meta={"turn_count": len(turns)},
        )
    return TranscriptResponse(visit_id=visit_id, turns=turns)
