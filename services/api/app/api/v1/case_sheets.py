"""Audited case sheet reads and optimistic-concurrency doctor edits."""

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.repositories import CaseSheetRepository, InterviewTurnRepository
from app.db.session import set_hospital_scope
from app.domain.case_sheet import CaseSheet
from app.domain.case_sheet_patch import CaseSheetPatchError, apply_case_sheet_patch

router = APIRouter(tags=["case sheets"])


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


class CaseSheetResponse(BaseModel):
    visit_id: UUID
    version: int
    case_sheet: CaseSheet


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
) -> None:
    visit_department = await connection.scalar(
        text(
            "SELECT department_id FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {"hospital_id": hospital_id, "visit_id": visit_id},
    )
    if not isinstance(visit_department, UUID):
        raise _problem(404, "NOT_FOUND", "Visit not found", "The requested visit is unavailable.")
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
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse"))],
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
    return CaseSheetResponse(visit_id=visit_id, version=version, case_sheet=saved["data"])


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
    return CaseSheetResponse(visit_id=visit_id, version=new_version, case_sheet=updated)


@router.get("/visits/{visit_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor"))],
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
