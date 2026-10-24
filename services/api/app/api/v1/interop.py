"""Records for the hospital's own systems: one visit as FHIR R4 (Phase 7, task 7.10.1)."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from starlette.responses import Response

from app.api.v1.documents import investigations_timeline
from app.api.v1.visit_access import check_visit_department
from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.db.repositories import CaseSheetRepository, DocumentRepository, InterviewTurnRepository
from app.db.session import set_hospital_scope
from app.domain.fhir_export import build_visit_bundle
from app.domain.interview.bank import QUESTIONS

router = APIRouter(tags=["interoperability"])


@router.get("/visits/{visit_id}/fhir", response_class=Response)
async def export_visit_fhir(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "admin"))],
) -> Response:
    """The visit as a FHIR R4 bundle: patient, encounter, what the patient reported, accepted
    results from their papers, the interview and the consents. Not ABDM-certified (tagged).

    For doctors of the visit's department and administrators. Audited, without contents.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    settings = request.app.state.settings
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await check_visit_department(
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
                        "SELECT v.id AS visit_id, v.hospital_id, v.patient_id, p.mrn, p.sex, "
                        "p.year_of_birth, v.status, v.registered_at, v.consult_ended_at, "
                        "d.code AS department_code, d.name_en AS department_name, v.language, "
                        "v.answered_by FROM visits v JOIN patients p "
                        "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                        "JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if visit is None:
            raise ApiProblemError(
                status=404,
                code="NOT_FOUND",
                title="Visit not found",
                detail="The requested visit is unavailable.",
            )
        saved = await CaseSheetRepository(connection, settings).get(
            hospital_id=hospital_id, visit_id=visit_id
        )
        turns = await InterviewTurnRepository(connection, settings).list_for_visit(
            hospital_id=hospital_id, visit_id=visit_id
        )
        rows = await DocumentRepository(connection, settings).list_for_visit(
            hospital_id=hospital_id, visit_id=visit_id
        )
        consents = (
            (
                await connection.execute(
                    text(
                        "SELECT purpose, granted, granted_at, withdrawn_at, text_version "
                        "FROM consents WHERE hospital_id = :hospital_id "
                        "AND visit_id = :visit_id ORDER BY purpose"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .all()
        )
        # Only papers a doctor accepted, never readings awaiting review (as in the timeline).
        accepted, _tests, _awaiting = investigations_timeline(rows)
        by_id = {row["id"]: row for row in rows}
        papers = [
            {
                **by_id[paper.document_id],
                "reading": {"lab_results": [r.model_dump() for r in paper.lab_results]},
            }
            for paper in accepted
        ]
        bundle = build_visit_bundle(
            visit=dict(visit),
            case_sheet=saved["data"] if saved else None,
            turns=turns,
            papers=papers,
            consents=[dict(row) for row in consents],
            questions={key: question.prompt["en"] for key, question in QUESTIONS.items()},
            now=datetime.now(UTC),
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="fhir.exported",
            resource_type="visit",
            resource_id=visit_id,
            meta={"resources": len(bundle["entry"])},
        )
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, separators=(",", ":")),
        media_type="application/fhir+json",
        headers={
            "Content-Disposition": f'attachment; filename="visit-{visit_id}.fhir.json"',
            "Cache-Control": "no-store",
        },
    )
