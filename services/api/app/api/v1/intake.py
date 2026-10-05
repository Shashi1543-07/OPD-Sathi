"""Consent-gated, touch/text intake endpoints for the kiosk and patient token."""

import json
from datetime import timedelta
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from redis.exceptions import RedisError
from sqlalchemy import text

from app.core.audit import audit
from app.core.auth import require_kiosk, require_patient, require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.security import issue_access_token
from app.db.repositories import (
    CaseSheetRepository,
    IntakeRepository,
    InterviewTurnRepository,
    QueueRepository,
)
from app.db.session import set_hospital_scope
from app.domain.interview.bank import QUESTIONS, Language
from app.domain.interview.consent import DANGER_TILES, get_consent_copy
from app.domain.interview.engine import (
    InterviewState,
    InterviewValidationError,
    apply_answer,
    build_case_sheet,
    correct_answer,
    next_question,
    start,
)
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import queue_score
from app.domain.queue.tokens import create_status_token, status_token_digest

router = APIRouter(prefix="/intake", tags=["intake"])
_SESSION_TTL_FACTOR = 60 * 60
MAX_ANSWER_CHARACTERS = 500


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateSessionPayload(StrictPayload):
    language: Language


class ConsentPayload(StrictPayload):
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    granted: bool


class IdentityPayload(StrictPayload):
    age: StrictInt = Field(ge=0, le=120)
    sex: Literal["female", "male", "other", "unknown"]
    phone: str | None = Field(default=None, pattern=r"^[6-9][0-9]{9}$")


class DangerSignsPayload(StrictPayload):
    tile_ids: list[str] = Field(max_length=20)


class AnswerPayload(StrictPayload):
    question_id: str = Field(min_length=2, max_length=80)
    answer: StrictStr | StrictInt | None = Field(default=None)
    skip: bool = False
    input_mode: Literal["touch", "text", "voice", "nurse"] = "touch"


def _error(status_code: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status_code, code=code, title=title, detail=detail)


def _validate_answer_size(answer: str | int | None) -> None:
    if isinstance(answer, str) and len(answer) > MAX_ANSWER_CHARACTERS:
        raise _error(
            400,
            "VALIDATION_FAILED",
            "Answer too long",
            "Please shorten the answer and try again.",
        )


def _identity(principal: dict[str, Any]) -> tuple[UUID, UUID]:
    try:
        return UUID(principal["hospital_id"]), UUID(principal["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _error(401, "UNAUTHENTICATED", "Sign-in required", "The session is invalid.") from exc


def _session_key(hospital_id: UUID, visit_id: UUID) -> str:
    return f"osa:intake:{hospital_id}:{visit_id}"


async def _authorized_visit(
    request: Request,
    visit_id: UUID,
    principal: dict[str, Any],
    *,
    consent_required: bool,
) -> tuple[UUID, dict[str, Any]]:
    hospital_id, subject_id = _identity(principal)
    if principal.get("role") != "patient" or subject_id != visit_id:
        raise _error(404, "NOT_FOUND", "Session not found", "This intake session is unavailable.")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        context = await IntakeRepository(connection, request.app.state.settings).session_context(
            hospital_id=hospital_id, visit_id=visit_id
        )
    if context is None:
        raise _error(404, "NOT_FOUND", "Session not found", "This intake session is unavailable.")
    if context["status"] == "cancelled":
        raise _error(410, "SESSION_CLOSED", "Session ended", "Please ask a staff member for help.")
    if consent_required and context["consent_granted"] is not True:
        raise _error(
            409,
            "CONSENT_REQUIRED",
            "Consent required",
            "Please review and accept the information notice before continuing.",
        )
    return hospital_id, context


def _read_state(raw: str | None) -> dict[str, Any]:
    if raw is None:
        raise _error(
            410,
            "SESSION_EXPIRED",
            "Session expired",
            "Please ask a staff member to help you start again.",
        )
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _error(
            503, "SESSION_UNAVAILABLE", "Session unavailable", "Please try again."
        ) from exc
    if not isinstance(state, dict):
        raise _error(503, "SESSION_UNAVAILABLE", "Session unavailable", "Please try again.")
    return state


def _patient_message(language: Language) -> str:
    return {
        "en": "Please inform the staff at the desk now.",
        "hi": "कृपया अभी डेस्क पर मौजूद कर्मचारी को बताएं।",
        "mr": "कृपया आत्ताच डेस्कवरील कर्मचाऱ्यांना सांगा.",
    }[language]


def _question_payload(question_id: str, language: Language) -> dict[str, Any]:
    question = QUESTIONS[question_id]
    return {
        "id": question.id,
        "input": question.input,
        "optional": question.optional,
        "prompt": question.prompt[language],
    }


def _readback(data: dict[str, Any]) -> str:
    language = data["language"]
    complaint = data["chief_complaint"]["text_original"]
    templates = {
        "en": (
            f"You told us: {complaint}. Please review this summary and change anything "
            "that is incorrect."
        ),
        "hi": f"आपने हमें बताया: {complaint}। कृपया इस सारांश को देखें और गलत जानकारी बदलें।",
        "mr": f"तुम्ही आम्हाला सांगितले: {complaint}. कृपया हा सारांश तपासा आणि चुकीची माहिती बदला.",
    }
    return templates[language]


async def _finish_interview(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    state_data: dict[str, Any],
    interview_state: InterviewState,
) -> dict[str, Any]:
    data = build_case_sheet(interview_state)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        case_sheet_id = await CaseSheetRepository(connection, request.app.state.settings).save(
            hospital_id=hospital_id, visit_id=visit_id, data=data
        )
        await IntakeRepository(connection, request.app.state.settings).set_visit_status(
            hospital_id=hospital_id, visit_id=visit_id, status="awaiting_confirmation"
        )
    state_data["phase"] = "summary"
    state_data["case_sheet_id"] = str(case_sheet_id)
    key = _session_key(hospital_id, visit_id)
    await request.app.state.redis_state.set(
        key,
        json.dumps(state_data, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return {
        "complete": True,
        "case_sheet": data,
        "readback": _readback(data),
        "answered": [
            {
                **_question_payload(answered_id, interview_state.language),
                "answer": interview_state.answers[answered_id],
            }
            for answered_id in interview_state.asked
            if answered_id in interview_state.answers
        ],
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_kiosk())],
) -> dict[str, Any]:
    hospital_id, kiosk_id = _identity(principal)
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            visit_id, patient_id = await IntakeRepository(
                connection, request.app.state.settings
            ).create_session(hospital_id=hospital_id, language=payload.language, kiosk_id=kiosk_id)
    except LookupError as exc:
        raise _error(
            409, "KIOSK_NOT_READY", "Kiosk unavailable", "Ask staff to check this kiosk."
        ) from exc
    copy = get_consent_copy(payload.language)
    state_data = {
        "visit_id": str(visit_id),
        "patient_id": str(patient_id),
        "kiosk_id": str(kiosk_id),
        "language": payload.language,
        "phase": "consent",
        "danger_ids": [],
        "corrections": 0,
    }
    try:
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(state_data, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
    except Exception as exc:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await IntakeRepository(connection, request.app.state.settings).set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
            )
        raise _error(
            503, "DEPENDENCY_UNAVAILABLE", "Session unavailable", "Please ask staff to try again."
        ) from exc
    patient_token = issue_access_token(
        subject=visit_id,
        role="patient",
        hospital_id=hospital_id,
        settings=request.app.state.settings,
        lifetime=timedelta(hours=request.app.state.settings.patient_session_hours),
    )
    return {
        "visit_id": str(visit_id),
        "patient_access_token": patient_token,
        "expires_in": request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        "consent": copy,
    }


@router.post("/sessions/{visit_id}/consent")
async def record_consent(
    visit_id: UUID,
    payload: ConsentPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=False
    )
    language: Language = context["language"]
    copy = get_consent_copy(language)
    if payload.version != copy["version"] or payload.sha256 != copy["sha256"]:
        raise _error(
            409,
            "CONSENT_COPY_MISMATCH",
            "Consent copy changed",
            "Reload the information notice and review it again.",
        )
    if context["consent_granted"] is True:
        return {"granted": True, "next": "identity"}
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        repository = IntakeRepository(connection, request.app.state.settings)
        await repository.record_consent(
            hospital_id=hospital_id,
            visit_id=visit_id,
            language=language,
            version=copy["version"],
            text_sha256=copy["sha256"],
            granted=payload.granted,
        )
        if not payload.granted:
            await repository.set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="intake.consent.granted" if payload.granted else "intake.consent.declined",
            resource_type="visit",
            resource_id=visit_id,
            meta={"language": language, "version": copy["version"]},
        )
    if payload.granted:
        state = _read_state(
            await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
        )
        state["phase"] = "identity"
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(state, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
        return {"granted": True, "next": "identity"}
    await request.app.state.redis_state.delete(_session_key(hospital_id, visit_id))
    return {"granted": False, "next": "staff_help", "message": _patient_message(language)}


@router.post("/sessions/{visit_id}/identity")
async def save_identity(
    visit_id: UUID,
    payload: IdentityPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, str]:
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if state.get("phase") != "identity":
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await IntakeRepository(connection, request.app.state.settings).update_identity(
                hospital_id=hospital_id,
                visit_id=visit_id,
                patient_id=context["patient_id"],
                age=payload.age,
                sex=payload.sex,
                phone=payload.phone,
            )
    except Exception as exc:
        if "uq_patients_hospital_phone_hmac" in str(exc):
            raise _error(
                409,
                "PHONE_ALREADY_REGISTERED",
                "Contact already registered",
                "Ask staff to help complete this intake.",
            ) from exc
        raise
    state["phase"] = "danger_signs"
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return {"next": "danger_signs"}


@router.post("/sessions/{visit_id}/danger-signs")
async def save_danger_signs(
    visit_id: UUID,
    payload: DangerSignsPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    unknown = set(payload.tile_ids) - set(DANGER_TILES)
    if unknown or len(payload.tile_ids) != len(set(payload.tile_ids)):
        raise _error(
            422, "VALIDATION_FAILED", "Invalid selection", "Choose from the displayed options."
        )
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if state.get("phase") != "danger_signs":
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await IntakeRepository(connection, request.app.state.settings).record_danger_tiles(
            hospital_id=hospital_id, visit_id=visit_id, tile_ids=payload.tile_ids
        )
    state["danger_ids"] = payload.tile_ids
    interview_state = start(context["language"], red_flag_ids=tuple(payload.tile_ids))
    state["interview"] = interview_state.model_dump(mode="json")
    state["phase"] = "interview"
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return {
        "next": "interview",
        "staff_help_required": bool(payload.tile_ids),
        "patient_message": _patient_message(context["language"]) if payload.tile_ids else None,
    }


async def _question_response(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> dict[str, Any]:
    interview_state = InterviewState.model_validate(state["interview"])
    question = next_question(interview_state)
    if question is None:
        return await _finish_interview(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            state_data=state,
            interview_state=interview_state,
        )
    return {
        "complete": False,
        "question": _question_payload(question.id, interview_state.language),
        "answered": [
            {
                **_question_payload(answered_id, interview_state.language),
                "answer": interview_state.answers.get(answered_id),
            }
            for answered_id in interview_state.asked
            if answered_id in interview_state.answers
        ],
    }


@router.get("/sessions/{visit_id}/danger-tiles")
async def get_danger_tiles(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    _, context = await _authorized_visit(request, visit_id, principal, consent_required=True)
    language: Language = context["language"]
    return {
        "tiles": [
            {"id": tile_id, "label": labels[language]} for tile_id, labels in DANGER_TILES.items()
        ]
    }


@router.get("/sessions/{visit_id}/interview/next-question")
async def get_next_question(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    if state.get("phase") == "summary":
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            result = await CaseSheetRepository(connection, request.app.state.settings).get(
                hospital_id=hospital_id, visit_id=visit_id
            )
        data = result["data"] if result else None
        return {
            "complete": True,
            "case_sheet": data,
            "readback": _readback(data) if data else None,
            "answered": [
                {
                    **_question_payload(answered_id, state["interview"]["language"]),
                    "answer": answer,
                }
                for answered_id, answer in state["interview"]["answers"].items()
            ],
        }
    if state.get("phase") != "interview":
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    return await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )


@router.post("/sessions/{visit_id}/interview/answers")
async def submit_answer(
    visit_id: UUID,
    payload: AnswerPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    _validate_answer_size(payload.answer)
    idempotency_body = payload.model_dump()

    async def operation() -> dict[str, Any]:
        key = _session_key(hospital_id, visit_id)
        state = _read_state(await request.app.state.redis_state.get(key))
        if state.get("phase") != "interview":
            raise _error(
                409,
                "FLOW_OUT_OF_ORDER",
                "Please continue in order",
                "Return to the current screen.",
            )
        previous_state = InterviewState.model_validate(state["interview"])
        try:
            next_state = apply_answer(
                previous_state,
                payload.question_id,
                payload.answer,
                skip=payload.skip,
            )
        except InterviewValidationError as exc:
            raise _error(422, "VALIDATION_FAILED", "Answer not accepted", str(exc)) from exc
        raw_answer = None if payload.skip or payload.answer is None else str(payload.answer)
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await InterviewTurnRepository(connection, request.app.state.settings).add(
                hospital_id=hospital_id,
                visit_id=visit_id,
                turn_index=len(next_state.asked) - 1,
                question_id=payload.question_id,
                input_mode=payload.input_mode,
                text_original=raw_answer,
            )
        state["interview"] = next_state.model_dump(mode="json")
        await request.app.state.redis_state.set(
            key,
            json.dumps(state, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
        return await _question_response(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=visit_id,
        body=idempotency_body,
        operation=operation,
    )


@router.patch("/sessions/{visit_id}/interview/answers")
async def correct_interview_answer(
    visit_id: UUID,
    payload: AnswerPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    if payload.skip or payload.answer is None:
        raise _error(
            422, "VALIDATION_FAILED", "Answer not accepted", "Enter an answer to continue."
        )
    _validate_answer_size(payload.answer)
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if state.get("phase") not in {"interview", "summary"}:
        raise _error(409, "SUMMARY_NOT_READY", "Summary not ready", "Finish the questions first.")
    previous = InterviewState.model_validate(state["interview"])
    try:
        updated = correct_answer(previous, payload.question_id, payload.answer)
        data = build_case_sheet(updated)
    except InterviewValidationError as exc:
        raise _error(422, "VALIDATION_FAILED", "Answer not accepted", str(exc)) from exc
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await InterviewTurnRepository(connection, request.app.state.settings).add(
            hospital_id=hospital_id,
            visit_id=visit_id,
            turn_index=12 + int(state.get("corrections", 0)),
            question_id=payload.question_id,
            input_mode=payload.input_mode,
            text_original=str(payload.answer),
        )
        if state.get("phase") == "summary":
            await CaseSheetRepository(connection, request.app.state.settings).save(
                hospital_id=hospital_id, visit_id=visit_id, data=data
            )
    state["interview"] = updated.model_dump(mode="json")
    state["corrections"] = int(state.get("corrections", 0)) + 1
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    if state.get("phase") == "summary":
        return {
            "complete": True,
            "case_sheet": data,
            "readback": _readback(data),
            "answered": [
                {
                    **_question_payload(answered_id, updated.language),
                    "answer": updated.answers[answered_id],
                }
                for answered_id in updated.asked
                if answered_id in updated.answers
            ],
        }
    return await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )


@router.post("/sessions/{visit_id}/confirm")
async def confirm_summary(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    if state.get("phase") == "confirmed" and isinstance(state.get("queue"), dict):
        return cast(dict[str, Any], state["queue"])
    if state.get("phase") != "summary":
        raise _error(409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first.")
    status_token = create_status_token(
        key=request.app.state.settings.app_hmac_key_bytes,
        hospital_id=hospital_id,
        visit_id=visit_id,
    )
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        confirmed = await CaseSheetRepository(connection, request.app.state.settings).confirm(
            hospital_id=hospital_id, visit_id=visit_id
        )
        if not confirmed:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            )
        queue_record = await QueueRepository(connection, request.app.state.settings).confirm_visit(
            redis=request.app.state.redis_state,
            hospital_id=hospital_id,
            visit_id=visit_id,
            status_hash=status_token_digest(status_token),
        )
        details = (
            (
                await connection.execute(
                    text(
                        "SELECT urgency, urgency_reasons, age_years, pregnant, queued_at "
                        "FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .one()
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="intake.summary.confirmed",
            resource_type="visit",
            resource_id=visit_id,
        )
    score = queue_score(
        int(details["queued_at"].timestamp() * 1000),
        details["urgency"],
        details["age_years"],
        bool(details["pregnant"]),
        urgent_bonus_min=request.app.state.settings.urgent_bonus_min,
        priority_bonus_min=request.app.state.settings.priority_bonus_min,
        vulnerable_bonus_min=request.app.state.settings.vulnerable_bonus_min,
    )
    try:
        await QueueRedis(
            request.app.state.redis_state, app_env=request.app.state.settings.app_env
        ).enqueue(
            hospital_id=hospital_id,
            department_id=queue_record["department_id"],
            visit_id=visit_id,
            score=score,
            item={
                "visit_id": str(visit_id),
                "token": queue_record["token_label"],
                "urgency": details["urgency"],
                "status": "queued",
            },
        )
    except RedisError:
        # The committed outbox row is retried by the relay.
        pass
    position: int | None
    try:
        position = await QueueRedis(
            request.app.state.redis_state, app_env=request.app.state.settings.app_env
        ).position(
            hospital_id=hospital_id,
            department_id=queue_record["department_id"],
            visit_id=visit_id,
        )
    except RedisError:
        position = None
    if position is None:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            rows = await QueueRepository(connection, request.app.state.settings).queued_visits(
                hospital_id=hospital_id, department_id=queue_record["department_id"]
            )
        rows.sort(
            key=lambda item: (
                queue_score(
                    int((item["queued_at"] or details["queued_at"]).timestamp() * 1000),
                    item["urgency"],
                    item["age_years"],
                    bool(item["pregnant"]),
                    urgent_bonus_min=request.app.state.settings.urgent_bonus_min,
                    priority_bonus_min=request.app.state.settings.priority_bonus_min,
                    vulnerable_bonus_min=request.app.state.settings.vulnerable_bonus_min,
                ),
                str(item["visit_id"]),
            )
        )
        position = next(
            (index + 1 for index, item in enumerate(rows) if item["visit_id"] == visit_id), None
        )
    queue_response = {
        "confirmed": True,
        "status": "queued",
        "token": queue_record["token_label"],
        "status_token": status_token,
        "status_url": f"/s/{status_token}",
        "position": position,
    }
    state["phase"] = "confirmed"
    state["queue"] = queue_response
    await request.app.state.redis_state.set(
        _session_key(hospital_id, visit_id),
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return queue_response


@router.delete("/sessions/{visit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_session(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("patient", "kiosk"))],
) -> Response:
    hospital_id, subject_id = _identity(principal)
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    role = principal.get("role")
    if role == "patient":
        if subject_id != visit_id:
            raise _error(
                404, "NOT_FOUND", "Session not found", "This intake session is unavailable."
            )
    elif role == "kiosk":
        if state.get("kiosk_id") != str(subject_id):
            raise _error(
                404, "NOT_FOUND", "Session not found", "This intake session is unavailable."
            )
    else:
        raise _error(403, "FORBIDDEN", "Permission denied", "This session cannot be ended here.")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await IntakeRepository(connection, request.app.state.settings).set_visit_status(
            hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
        )
    await request.app.state.redis_state.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
