"""Old papers the patient brings: photos of prescriptions, test reports and discharge papers.

The patient adds photos while checking in and can remove them until they confirm; a doctor
or nurse can add more while the patient is under care. Photos are kept encrypted with the
visit and are shown only to staff who may open that visit. With the patient's consent to
online AI help, AI reads each paper in the background into an unconfirmed draft, which the
doctor accepts or rejects beside the photo; without that consent the photo is kept unread.
A test result is flagged only against the reference range printed on the same paper.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai.document_reading import read_document
from app.api.v1.ai_gate import llm_allowed
from app.api.v1.visit_access import care_date, check_visit_department, visit_in_care
from app.core.audit import audit
from app.core.auth import require_patient, require_role
from app.core.errors import ApiProblemError
from app.core.ratelimit import check_rate_limit
from app.db.repositories import DocumentRepository, IntakeRepository
from app.db.session import set_hospital_scope
from app.domain.documents import (
    IMAGE_TYPES,
    MAX_DOCUMENTS_PER_VISIT,
    MAX_IMAGE_BYTES,
    MAX_PATIENT_DOCUMENTS,
    DocumentKind,
    document_date,
    looks_like,
    printed_flag,
    range_flag,
)

router = APIRouter(tags=["documents"])
_LOGGER = structlog.get_logger(__name__)
_REGISTRATION_STATES = frozenset({"registering", "awaiting_confirmation"})
# One reading gets 25 seconds. A reading still pending after two minutes was interrupted
# (for example by a restart) and is shown as not read; the photo itself is unaffected.
_READING_TIMEOUT_SECONDS = 25.0
_STALE_READING = timedelta(minutes=2)
# Photo uploads per visit (patient) or per staff member, in ten minutes, retakes included.
_PATIENT_UPLOADS = 10
_STAFF_UPLOADS = 40
_UPLOAD_WINDOW_SECONDS = 600


def _error(status_code: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status_code, code=code, title=title, detail=detail)


class Medicine(BaseModel):
    name: str
    dose: str = ""
    how_often: str = ""
    duration: str = ""


class LabResult(BaseModel):
    test: str
    value: str
    unit: str = ""
    reference_range: str = ""
    # The high or low mark the laboratory printed beside the result, if any.
    printed_flag: Literal["high", "low"] | None = None
    # The value compared with the reference range printed beside it (None: not comparable).
    range_flag: Literal["above", "below", "within"] | None = None


class Reading(BaseModel):
    """What AI read from the paper: an unconfirmed draft until a doctor accepts it."""

    readable: bool
    document_type: str
    # The date as printed (the parsed date is the document's ``document_date``).
    document_date: str = ""
    facility: str = ""
    diagnoses: list[str] = Field(default_factory=list)
    medicines: list[Medicine] = Field(default_factory=list)
    lab_results: list[LabResult] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)


class StaffDocument(BaseModel):
    id: UUID
    visit_id: UUID
    service_date: date
    kind: DocumentKind
    source: Literal["patient", "staff"]
    # The staff member who added it (a patient's own photo has none).
    added_by: str | None = None
    added_at: datetime
    size: int
    # The date printed on the paper, when AI read one that is a real past date.
    document_date: date | None = None
    reading_status: Literal["pending", "complete", "failed", "skipped"]
    reading: Reading | None = None
    # The patient stopped AI help for that visit, so what AI read is not shown.
    ai_withdrawn: bool = False
    review_status: Literal["unreviewed", "accepted", "rejected"]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class VisitDocumentsResponse(BaseModel):
    visit_id: UUID
    documents: list[StaffDocument]
    # Papers from the patient's earlier visits: shown only while the patient is under care.
    earlier: list[StaffDocument]
    earlier_shown: bool
    limit: int = MAX_DOCUMENTS_PER_VISIT


class PatientDocument(BaseModel):
    id: UUID
    kind: DocumentKind
    size: int
    added_at: datetime


class PatientDocumentsResponse(BaseModel):
    documents: list[PatientDocument]
    limit: int = MAX_PATIENT_DOCUMENTS


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected"]


def _reading(stored: dict[str, Any] | None) -> Reading | None:
    if not isinstance(stored, dict):
        return None
    results = []
    for item in stored.get("lab_results") or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", ""))
        printed_range = str(item.get("reference_range", ""))
        results.append(
            LabResult(
                test=str(item.get("test", "")),
                value=value,
                unit=str(item.get("unit", "")),
                reference_range=printed_range,
                printed_flag=printed_flag(str(item.get("flag", ""))),
                range_flag=range_flag(value, printed_range),
            )
        )
    return Reading(
        readable=stored.get("readable") is True,
        document_type=str(stored.get("document_type", "other")),
        document_date=str(stored.get("document_date", "")),
        facility=str(stored.get("facility", "")),
        diagnoses=[str(item) for item in stored.get("diagnoses") or []],
        medicines=[
            Medicine(**item) for item in stored.get("medicines") or [] if isinstance(item, dict)
        ],
        lab_results=results,
        procedures=[str(item) for item in stored.get("procedures") or []],
    )


def _staff_document(row: dict[str, Any], *, now: datetime) -> StaffDocument:
    withdrawn = bool(row.get("ai_withdrawn"))
    reading_status = row["ocr_status"]
    if reading_status == "pending" and now - row["created_at"] > _STALE_READING:
        reading_status = "failed"
    return StaffDocument(
        id=row["id"],
        visit_id=row["visit_id"],
        service_date=row["service_date"],
        kind=row["kind"],
        source=row["source"],
        added_by=row.get("uploaded_by_name"),
        added_at=row["created_at"],
        size=int(row["size"]),
        document_date=row.get("document_date"),
        reading_status=reading_status,
        reading=None if withdrawn else _reading(row.get("reading")),
        ai_withdrawn=withdrawn,
        review_status=row["review_status"],
        reviewed_by=row.get("reviewed_by_name"),
        reviewed_at=row.get("reviewed_at"),
    )


def _patient_document(row: dict[str, Any]) -> PatientDocument:
    return PatientDocument(
        id=row["id"], kind=row["kind"], size=int(row["size"]), added_at=row["created_at"]
    )


async def _photo(request: Request) -> tuple[str, bytes]:
    """The uploaded photo, if it is a JPEG, PNG or WebP whose contents match its type."""
    content_type = request.headers.get("content-type", "").lower().split(";")[0].strip()
    if content_type not in IMAGE_TYPES:
        raise _error(415, "UNSUPPORTED_IMAGE", "Unsupported photo", "Take a photo of the paper.")
    image = await request.body()
    if not image:
        raise _error(400, "EMPTY_IMAGE", "No photo", "Take the photo again.")
    if len(image) > MAX_IMAGE_BYTES:
        raise _error(413, "IMAGE_TOO_LARGE", "Photo too large", "Take the photo again.")
    if not looks_like(content_type, image):
        raise _error(
            415, "UNSUPPORTED_IMAGE", "Not a photo", "This file is not a photo. Take a photo."
        )
    return content_type, image


async def _limit_uploads(request: Request, *, key: str, limit: int) -> None:
    result = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{request.app.state.settings.app_env}:rl:{key}",
        limit=limit,
        window_seconds=_UPLOAD_WINDOW_SECONDS,
        fail_closed=False,
    )
    if result is not None and not result.allowed:
        raise ApiProblemError(
            status=429,
            code="RATE_LIMITED",
            title="Too many photos",
            detail="Please wait a few minutes before adding more photos.",
            headers={"Retry-After": str(result.retry_after)},
        )


async def _will_read(request: Request, hospital_id: UUID, visit_id: UUID) -> bool:
    """AI reads the paper only with the hospital's AI switch and the patient's consent."""
    provider = request.app.state.llm_provider
    return getattr(provider, "name", "mock") == "gemini" and await llm_allowed(
        request, hospital_id, visit_id
    )


def _schedule_reading(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    document_id: UUID,
    mime: str,
    image: bytes,
) -> None:
    task = asyncio.create_task(
        _read_in_background(
            request.app,
            request.app.state.llm_provider,
            hospital_id=hospital_id,
            visit_id=visit_id,
            document_id=document_id,
            mime=mime,
            image=image,
        )
    )
    tasks: set[asyncio.Task[None]] = request.app.state.llm_background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _read_in_background(
    app: Any,
    provider: Any,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    document_id: UUID,
    mime: str,
    image: bytes,
) -> None:
    """Read one paper and store the draft, unless the patient stopped AI help meanwhile."""
    settings = app.state.settings
    reading: dict[str, Any] | None = None
    reading_status = "failed"
    try:
        reading = await asyncio.wait_for(
            read_document(provider, image, mime_type=mime, session_id=str(visit_id)),
            timeout=_READING_TIMEOUT_SECONDS,
        )
        reading_status = "complete"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Unavailable, timed out or unreadable output: the doctor reads the photo instead.
        _LOGGER.warning("document_reading_failed", error_type=type(exc).__name__)
    try:
        async with app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            choice = await IntakeRepository(connection, settings).ai_consent(
                hospital_id=hospital_id, visit_id=visit_id
            )
            if choice != "granted":
                reading, reading_status = None, "skipped"
            printed_date = (
                document_date(str(reading.get("document_date", "")), today=care_date())
                if reading
                else None
            )
            await DocumentRepository(connection, settings).save_reading(
                hospital_id=hospital_id,
                document_id=document_id,
                reading=reading,
                status=reading_status,
                document_date=printed_date,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _LOGGER.warning("document_reading_not_saved", error_type=type(exc).__name__)


async def _registering_patient(
    connection: Any, request: Request, *, hospital_id: UUID, visit_id: UUID
) -> None:
    """The patient is checking in: consent given, summary not yet confirmed."""
    context = await IntakeRepository(connection, request.app.state.settings).session_context(
        hospital_id=hospital_id, visit_id=visit_id
    )
    if context is None:
        raise _error(404, "NOT_FOUND", "Session not found", "This intake session is unavailable.")
    if context["status"] == "cancelled":
        raise _error(410, "SESSION_CLOSED", "Session ended", "Please ask a staff member for help.")
    if context["consent_granted"] is not True:
        raise _error(
            409,
            "CONSENT_REQUIRED",
            "Consent required",
            "Please review and accept the information notice before continuing.",
        )
    if context["status"] not in _REGISTRATION_STATES:
        raise _error(
            409,
            "SESSION_COMPLETE",
            "Already checked in",
            "Your token has been issued. Please show your papers to the doctor.",
        )


def _patient_identity(visit_id: UUID, principal: dict[str, Any]) -> UUID:
    if principal.get("role") != "patient" or principal.get("sub") != str(visit_id):
        raise _error(404, "NOT_FOUND", "Session not found", "This intake session is unavailable.")
    return UUID(str(principal["hospital_id"]))


@router.post(
    "/intake/sessions/{visit_id}/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientDocument,
)
async def add_patient_document(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
    kind: Annotated[DocumentKind, Query()] = "other",
) -> PatientDocument:
    """Keep a photo of one old paper with this visit, for the doctor to see.

    Up to five photos while checking in. The photo is encrypted and kept with the visit;
    with the patient's consent to online AI help it is also read for the doctor.
    """
    hospital_id = _patient_identity(visit_id, principal)
    mime, image = await _photo(request)
    await _limit_uploads(request, key=f"doc-upload:{visit_id}", limit=_PATIENT_UPLOADS)
    will_read = await _will_read(request, hospital_id, visit_id)
    settings = request.app.state.settings
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _registering_patient(
            connection, request, hospital_id=hospital_id, visit_id=visit_id
        )
        repository = DocumentRepository(connection, settings)
        visit_status = await repository.lock_visit(hospital_id=hospital_id, visit_id=visit_id)
        if visit_status not in _REGISTRATION_STATES:
            raise _error(409, "SESSION_COMPLETE", "Already checked in", "Show the doctor.")
        papers = await repository.count_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        if (
            papers.get("patient", 0) >= MAX_PATIENT_DOCUMENTS
            or sum(papers.values()) >= MAX_DOCUMENTS_PER_VISIT
        ):
            raise _error(
                409,
                "TOO_MANY_DOCUMENTS",
                "Enough photos",
                "You have added the most photos allowed. Show any other papers to the doctor.",
            )
        document_id = await repository.add(
            hospital_id=hospital_id,
            visit_id=visit_id,
            kind=kind,
            mime=mime,
            content=image,
            source="patient",
            uploaded_by=None,
            ocr_status="pending" if will_read else "skipped",
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="document.added",
            resource_type="document",
            resource_id=document_id,
            meta={"kind": kind, "source": "patient", "size": len(image), "read": will_read},
        )
        rows = await repository.list_for_patient_session(
            hospital_id=hospital_id, visit_id=visit_id
        )
    if will_read:
        _schedule_reading(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            document_id=document_id,
            mime=mime,
            image=image,
        )
    added = next(row for row in rows if row["id"] == document_id)
    return _patient_document(added)


@router.get("/intake/sessions/{visit_id}/documents", response_model=PatientDocumentsResponse)
async def list_patient_documents(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> PatientDocumentsResponse:
    """The photos the patient added while checking in (what AI read stays for the doctor)."""
    hospital_id = _patient_identity(visit_id, principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _registering_patient(
            connection, request, hospital_id=hospital_id, visit_id=visit_id
        )
        rows = await DocumentRepository(
            connection, request.app.state.settings
        ).list_for_patient_session(hospital_id=hospital_id, visit_id=visit_id)
    return PatientDocumentsResponse(documents=[_patient_document(row) for row in rows])


@router.delete(
    "/intake/sessions/{visit_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_patient_document(
    visit_id: UUID,
    document_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> Response:
    """Take back a photo before confirming: the photo and anything read from it are deleted."""
    hospital_id = _patient_identity(visit_id, principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _registering_patient(
            connection, request, hospital_id=hospital_id, visit_id=visit_id
        )
        repository = DocumentRepository(connection, request.app.state.settings)
        visit_status = await repository.lock_visit(hospital_id=hospital_id, visit_id=visit_id)
        if visit_status not in _REGISTRATION_STATES:
            raise _error(409, "SESSION_COMPLETE", "Already checked in", "Show the doctor.")
        removed = await repository.remove(
            hospital_id=hospital_id, visit_id=visit_id, document_id=document_id
        )
        if not removed:
            raise _error(404, "NOT_FOUND", "Photo not found", "This photo is not available.")
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="document.removed",
            resource_type="document",
            resource_id=document_id,
            meta={"source": "patient"},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/visits/{visit_id}/documents", response_model=VisitDocumentsResponse)
async def list_visit_documents(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> VisitDocumentsResponse:
    """The visit's papers and, while the patient is under care, papers from earlier visits.

    Each paper comes with what AI read from it (an unconfirmed draft until a doctor accepts
    it) and its results compared with the reference ranges printed on it. Audited.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    role = principal.get("role")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id, role=role
        )
        repository = DocumentRepository(connection, request.app.state.settings)
        documents = await repository.list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        patient_id, in_care = await visit_in_care(
            connection, hospital_id=hospital_id, visit_id=visit_id
        )
        earlier_shown = in_care or role == "admin"
        earlier = (
            await repository.list_earlier(
                hospital_id=hospital_id, patient_id=patient_id, exclude_visit_id=visit_id
            )
            if earlier_shown
            else []
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="documents.read",
            resource_type="visit",
            resource_id=visit_id,
            meta={"documents": len(documents), "earlier": len(earlier)},
        )
    now = datetime.now(UTC)
    return VisitDocumentsResponse(
        visit_id=visit_id,
        documents=[_staff_document(row, now=now) for row in documents],
        earlier=[_staff_document(row, now=now) for row in earlier],
        earlier_shown=earlier_shown,
    )


@router.get("/visits/{visit_id}/documents/{document_id}/content", response_class=Response)
async def get_document_photo(
    visit_id: UUID,
    document_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse", "admin"))],
) -> Response:
    """The photo itself, for staff who may open the visit (an earlier visit's paper only while
    the patient is under care). Never cached; every view is audited.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    role = principal.get("role")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id, role=role
        )
        repository = DocumentRepository(connection, request.app.state.settings)
        found = await repository.find(hospital_id=hospital_id, document_id=document_id)
        if found is None:
            raise _error(404, "NOT_FOUND", "Paper not found", "This paper is not available.")
        if found["visit_id"] != visit_id:
            patient_id, in_care = await visit_in_care(
                connection, hospital_id=hospital_id, visit_id=visit_id
            )
            if (
                found["patient_id"] != patient_id
                or not found["registered"]
                or not (in_care or role == "admin")
            ):
                raise _error(404, "NOT_FOUND", "Paper not found", "This paper is not available.")
        photo = await repository.content(hospital_id=hospital_id, document_id=document_id)
        if photo is None:
            raise _error(404, "NOT_FOUND", "Paper not found", "This paper is not available.")
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="document.photo_viewed",
            resource_type="document",
            resource_id=document_id,
            meta={"visit_id": str(visit_id), "earlier": found["visit_id"] != visit_id},
        )
    mime, content = photo
    return Response(
        content=content,
        media_type=mime,
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )


@router.post(
    "/visits/{visit_id}/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=StaffDocument,
)
async def add_staff_document(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor", "nurse"))],
    kind: Annotated[DocumentKind, Query()] = "other",
) -> StaffDocument:
    """A doctor or nurse photographs a paper the patient shows at the desk or in the room.

    Allowed while the patient is under care, up to ten papers per visit in all.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    mime, image = await _photo(request)
    await _limit_uploads(request, key=f"doc-upload-staff:{actor_id}", limit=_STAFF_UPLOADS)
    will_read = await _will_read(request, hospital_id, visit_id)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id
        )
        repository = DocumentRepository(connection, request.app.state.settings)
        visit_status = await repository.lock_visit(hospital_id=hospital_id, visit_id=visit_id)
        _, in_care = await visit_in_care(connection, hospital_id=hospital_id, visit_id=visit_id)
        if visit_status == "cancelled" or not in_care:
            raise _error(
                409,
                "VISIT_CLOSED",
                "Visit closed",
                "Papers can be added only while the patient is under care.",
            )
        papers = await repository.count_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        if sum(papers.values()) >= MAX_DOCUMENTS_PER_VISIT:
            raise _error(
                409,
                "TOO_MANY_DOCUMENTS",
                "Enough papers",
                "This visit already has the most papers allowed.",
            )
        document_id = await repository.add(
            hospital_id=hospital_id,
            visit_id=visit_id,
            kind=kind,
            mime=mime,
            content=image,
            source="staff",
            uploaded_by=actor_id,
            ocr_status="pending" if will_read else "skipped",
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="document.added",
            resource_type="document",
            resource_id=document_id,
            meta={"kind": kind, "source": "staff", "size": len(image), "read": will_read},
        )
        rows = await repository.list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
    if will_read:
        _schedule_reading(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            document_id=document_id,
            mime=mime,
            image=image,
        )
    added = next(row for row in rows if row["id"] == document_id)
    return _staff_document(added, now=datetime.now(UTC))


@router.post(
    "/visits/{visit_id}/documents/{document_id}/review", response_model=StaffDocument
)
async def review_document_reading(
    visit_id: UUID,
    document_id: UUID,
    payload: ReviewPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("doctor"))],
) -> StaffDocument:
    """The doctor accepts or rejects what AI read, after comparing it with the photo.

    Until then the reading is shown as an unconfirmed AI draft. A decision can be changed;
    each one is audited.
    """
    hospital_id = UUID(principal["hospital_id"])
    actor_id = UUID(principal["sub"])
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await check_visit_department(
            connection, hospital_id=hospital_id, visit_id=visit_id, actor_id=actor_id
        )
        repository = DocumentRepository(connection, request.app.state.settings)
        documents = await repository.list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        current = next((row for row in documents if row["id"] == document_id), None)
        if current is None:
            raise _error(404, "NOT_FOUND", "Paper not found", "This paper is not available.")
        if current["ai_withdrawn"]:
            raise _error(
                409,
                "AI_HELP_WITHDRAWN",
                "AI help stopped",
                "The patient stopped AI help for this visit; the AI reading is not used.",
            )
        reading = current.get("reading")
        if not isinstance(reading, dict) or reading.get("readable") is not True:
            raise _error(
                409, "NO_READING", "Nothing to review", "AI has not read anything from this paper."
            )
        reviewed = await repository.review(
            hospital_id=hospital_id,
            document_id=document_id,
            decision=payload.decision,
            reviewer_id=actor_id,
        )
        if not reviewed:
            raise _error(
                409, "NO_READING", "Nothing to review", "AI has not read anything from this paper."
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="document.reading_reviewed",
            resource_type="document",
            resource_id=document_id,
            meta={"decision": payload.decision, "before": current["review_status"]},
        )
        rows = await repository.list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
    updated = next(row for row in rows if row["id"] == document_id)
    return _staff_document(updated, now=datetime.now(UTC))
