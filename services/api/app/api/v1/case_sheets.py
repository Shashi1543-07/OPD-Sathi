"""Audited case sheet reads and optimistic-concurrency doctor edits."""

import re
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from starlette.responses import Response as StarletteResponse

from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.repositories import CaseSheetRepository, InterviewTurnRepository
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
                        "v.urgency, v.status, d.name_en AS department_name FROM visits v "
                        "JOIN patients p ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                        "LEFT JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id "
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
    return CaseSheetResponse(
        visit_id=visit_id,
        version=version,
        case_sheet=_with_flag_reasons(saved["data"]),
        confirmed_by_patient=saved.get("confirmed_by_patient") is True,
        confirmed_at=saved.get("confirmed_at"),
        patient=PatientContext(**dict(context)) if context else None,
    )


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
        saved["data"],
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
        case_sheet=_with_flag_reasons(updated),
        confirmed_by_patient=saved.get("confirmed_by_patient") is True,
        confirmed_at=saved.get("confirmed_at"),
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
