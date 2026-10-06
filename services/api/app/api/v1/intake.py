"""Consent-gated, touch/text intake endpoints for the kiosk and patient token."""

import asyncio
import json
from datetime import timedelta
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from redis.exceptions import RedisError
from sqlalchemy import text

from app.ai.department_classifier import classify_department, combine_department_classification
from app.ai.extraction import extract_case_sheet_patch, merge_enriched_case_sheet
from app.ai.prompts.builders import QuestionCandidate
from app.ai.question_choice import select_question_id
from app.ai.readback import build_readback
from app.ai.redflag_opinion import combine_redflag_opinions, second_opinion_flags
from app.core.audit import audit
from app.core.auth import require_kiosk, require_patient, require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.ids import uuid7
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
from app.domain.redflags.engine import PatientFacts, RedFlag, evaluate, maximum_urgency
from app.domain.routing.engine import Route
from app.domain.routing.engine import route as route_department

router = APIRouter(prefix="/intake", tags=["intake"])
_AI_LOGGER = structlog.get_logger(__name__)
_SESSION_TTL_FACTOR = 60 * 60
MAX_ANSWER_CHARACTERS = 500
_DANGER_TILE_RULE_IDS = {
    "chest_pain": "DS_CHEST",
    "breathing_difficulty": "DS_BREATH",
    "heavy_bleeding": "DS_BLEED",
    "fainting": "DS_FIT",
    "seizure": "DS_FIT",
    "sudden_weakness": "DS_STROKE",
}


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateSessionPayload(StrictPayload):
    language: Language


class CreateAssistedSessionPayload(StrictPayload):
    language: Language


class ConsentPayload(StrictPayload):
    version: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    granted: bool


class IdentityPayload(StrictPayload):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    age: StrictInt = Field(ge=0, le=120)
    sex: Literal["female", "male", "other", "unknown"]
    phone: str | None = Field(default=None, pattern=r"^[6-9][0-9]{9}$")
    sms_opt_in: bool = False

    @model_validator(mode="after")
    def require_phone_for_sms(self) -> "IdentityPayload":
        if self.sms_opt_in and not self.phone:
            raise ValueError("a phone number is required to receive queue updates")
        return self


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


async def _hospital_llm_enabled(request: Request, hospital_id: UUID) -> bool:
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
            enabled = configured is True
    except Exception:
        # An unavailable or unmigrated setting store must leave the deterministic path active.
        enabled = False
    request.state.llm_enablement = (hospital_id, enabled)
    return enabled


async def _evaluate_visit(
    connection: Any,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    answers: dict[str, str | int],
    tile_ids: list[str],
    request: Request | None = None,
    include_department: bool = False,
) -> tuple[list[RedFlag], Route, list[dict[str, Any]]]:
    """Persist new rule events and monotonically raise urgency inside the visit transaction."""
    row = (
        (
            await connection.execute(
                text(
                    "SELECT v.age_years, v.pregnant, v.department_id, v.status, v.urgency, "
                    "v.token_label, v.language, "
                    "v.urgency_reasons, p.sex FROM visits v JOIN patients p "
                    "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                    "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return [], route_department(""), []
    text_answer = " ".join(str(value) for value in answers.values() if isinstance(value, str))
    severity_value = answers.get("severity")
    facts = PatientFacts(
        age=row["age_years"],
        sex=row["sex"],
        pregnant=row["pregnant"],
        severity=severity_value if isinstance(severity_value, int) else None,
        tile_ids=tuple(_DANGER_TILE_RULE_IDS.get(tile_id, tile_id) for tile_id in tile_ids),
    )
    provider = request.app.state.llm_provider if request is not None else None
    tenant_llm_enabled = (
        await _hospital_llm_enabled(request, hospital_id)
        if request is not None
        and request.app.state.settings.llm_enabled
        and getattr(provider, "name", "mock") == "gemini"
        else False
    )
    if (
        request is not None
        and tenant_llm_enabled
        and provider is not None
        and getattr(provider, "name", "mock") == "gemini"
        and text_answer.strip()
    ):
        rules_task = asyncio.create_task(asyncio.to_thread(evaluate, text_answer, facts=facts))
        route_task = asyncio.create_task(
            asyncio.to_thread(
                route_department,
                text_answer,
                age=row["age_years"],
                sex=row["sex"],
                pregnant=row["pregnant"],
            )
        )
        opinion_task = asyncio.create_task(
            second_opinion_flags(
                provider,
                transcript=text_answer,
                language=str(row.get("language", "en")),
                session_id=str(visit_id),
            )
        )
        if include_department:
            departments = (
                (
                    await connection.execute(
                        text(
                            "SELECT code, name_en FROM departments "
                            "WHERE hospital_id = :hospital_id AND is_active = true"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .mappings()
                .all()
            )
            labels = {
                str(item["code"]): str(item["name_en"])
                for item in departments
                if isinstance(item.get("code"), str) and isinstance(item.get("name_en"), str)
            }
            if labels:
                department_task = asyncio.create_task(
                    classify_department(
                        provider,
                        transcript=text_answer,
                        language=str(row.get("language", "en")),
                        department_labels=labels,
                        session_id=str(visit_id),
                    )
                )
                flags, opinion, deterministic_route, model_code = await asyncio.gather(
                    rules_task, opinion_task, route_task, department_task
                )
                route_result = combine_department_classification(deterministic_route, model_code)
            else:
                flags, opinion, route_result = await asyncio.gather(
                    rules_task, opinion_task, route_task
                )
        else:
            flags, opinion, route_result = await asyncio.gather(
                rules_task, opinion_task, route_task
            )
        flags = combine_redflag_opinions(flags, opinion)
    else:
        flags = evaluate(text_answer, facts=facts)
        route_result = route_department(
            text_answer,
            age=row["age_years"],
            sex=row["sex"],
            pregnant=row["pregnant"],
        )
    existing = set(
        (
            await connection.execute(
                text(
                    "SELECT rule_id FROM red_flag_events WHERE hospital_id = :hospital_id "
                    "AND visit_id = :visit_id"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            )
        )
        .scalars()
        .all()
    )
    alerts: list[dict[str, Any]] = []
    current_reasons = row["urgency_reasons"] or []
    reason_ids = set(current_reasons if isinstance(current_reasons, list) else [])
    for flag in flags:
        reason_ids.add(flag.id)
        if flag.id in existing:
            continue
        event_id = uuid7()
        await connection.execute(
            text(
                "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, level, "
                "evidence) VALUES (:id, :hospital_id, :visit_id, :source, :rule_id, :level, "
                ":evidence)"
            ),
            {
                "id": event_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "source": flag.source,
                "rule_id": flag.id,
                "level": flag.level,
                "evidence": flag.evidence,
            },
        )
        if flag.level == "urgent":
            alerts.append(
                {
                    "type": "alert.urgent",
                    "id": str(event_id),
                    "visit_id": str(visit_id),
                    "rule_id": flag.id,
                    "level": flag.level,
                    "reason_en": flag.reason_en,
                    "evidence": flag.evidence,
                    "token_label": row["token_label"],
                }
            )
    urgency = maximum_urgency(flags)
    urgency = max((row["urgency"], urgency), key=("routine", "priority", "urgent").index)
    await connection.execute(
        text(
            "UPDATE visits SET urgency = :urgency, urgency_reasons = CAST(:reasons AS jsonb) "
            "WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {
            "urgency": urgency,
            "reasons": json.dumps(sorted(reason_ids)),
            "hospital_id": hospital_id,
            "visit_id": visit_id,
        },
    )
    if row["status"] == "queued" and urgency != row["urgency"]:
        await connection.execute(
            text(
                "INSERT INTO outbox (id, hospital_id, topic, payload) VALUES "
                "(:id, :hospital_id, 'queue.changed', CAST(:payload AS jsonb))"
            ),
            {
                "id": uuid7(),
                "hospital_id": hospital_id,
                "payload": json.dumps(
                    {
                        "op": "upsert",
                        "visit_id": str(visit_id),
                        "department_id": str(row["department_id"]),
                    }
                ),
            },
        )
    for alert in alerts:
        alert["department_id"] = str(row["department_id"])
    return flags, route_result, alerts


def _patient_case_sheet(
    data: dict[str, Any], flags: list[RedFlag], route_result: Route
) -> dict[str, Any]:
    """Attach current explainable results while excluding staff-only flag details."""
    data["red_flags"] = [
        {
            "id": flag.id,
            "level": flag.level,
            "source": flag.source,
            "evidence": flag.evidence,
        }
        for flag in flags
        if flag.visible_to_patient
    ]
    data["routing"] = {
        "department_code": route_result.department_code,
        "confidence": route_result.confidence,
        "reason_en": route_result.reason_en,
        "needs_nurse_confirmation": route_result.needs_nurse_confirmation,
    }
    return data


async def _publish_alerts(
    redis: Any,
    *,
    app_env: str,
    hospital_id: UUID,
    alerts: list[dict[str, Any]],
) -> None:
    for alert in alerts:
        department_id = alert.pop("department_id")
        channel = f"osa:{app_env}:{hospital_id}:alerts:{department_id}"
        try:
            await redis.publish(channel, json.dumps(alert, separators=(",", ":")))
        except RedisError:
            # GET /alerts reads the durable event table when a transient notification is missed.
            continue


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
    return build_readback(data)


async def _finish_interview(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    state_data: dict[str, Any],
    interview_state: InterviewState,
) -> dict[str, Any]:
    data = build_case_sheet(interview_state)
    alerts: list[dict[str, Any]] = []
    voice_transcript = ""
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        flags, route_result, alerts = await _evaluate_visit(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            answers=interview_state.answers,
            tile_ids=list(interview_state.red_flag_ids),
            request=request,
            include_department=True,
        )
        data = _patient_case_sheet(data, flags, route_result)
        case_sheet_id = await CaseSheetRepository(connection, request.app.state.settings).save(
            hospital_id=hospital_id, visit_id=visit_id, data=data
        )
        await IntakeRepository(connection, request.app.state.settings).set_visit_status(
            hospital_id=hospital_id, visit_id=visit_id, status="awaiting_confirmation"
        )
        turns = await InterviewTurnRepository(
            connection, request.app.state.settings
        ).list_for_visit(hospital_id=hospital_id, visit_id=visit_id)
        voice_transcript = "\n".join(
            str(turn["text_original"])
            for turn in turns
            if turn["input_mode"] == "voice" and isinstance(turn["text_original"], str)
        )
    await _publish_alerts(
        request.app.state.redis_state,
        app_env=request.app.state.settings.app_env,
        hospital_id=hospital_id,
        alerts=alerts,
    )
    state_data["phase"] = "summary"
    state_data["case_sheet_id"] = str(case_sheet_id)
    key = _session_key(hospital_id, visit_id)
    await request.app.state.redis_state.set(
        key,
        json.dumps(state_data, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    _schedule_case_sheet_enrichment(
        request,
        hospital_id=hospital_id,
        visit_id=visit_id,
        baseline=data,
        transcript=voice_transcript,
        language=interview_state.language,
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


def _schedule_case_sheet_enrichment(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    baseline: dict[str, Any],
    transcript: str,
    language: Language,
) -> None:
    """Start bounded best-effort enrichment after returning a deterministic summary."""
    if not transcript.strip() or getattr(request.state, "llm_enablement", None) != (
        hospital_id,
        True,
    ):
        return
    provider = request.app.state.llm_provider
    if getattr(provider, "name", "mock") != "gemini":
        return
    task = asyncio.create_task(
        _run_bounded_case_sheet_enrichment(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            baseline=baseline,
            transcript=transcript,
            language=language,
        )
    )
    tasks: set[asyncio.Task[None]] = request.app.state.llm_background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _run_bounded_case_sheet_enrichment(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    baseline: dict[str, Any],
    transcript: str,
    language: Language,
) -> None:
    try:
        await asyncio.wait_for(
            _persist_case_sheet_enrichment(
                request,
                hospital_id=hospital_id,
                visit_id=visit_id,
                baseline=baseline,
                transcript=transcript,
                language=language,
            ),
            timeout=5.0,
        )
    except TimeoutError:
        _AI_LOGGER.warning("llm_case_sheet_enrichment_fallback", reason="background_timeout")


async def _persist_case_sheet_enrichment(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    baseline: dict[str, Any],
    transcript: str,
    language: Language,
) -> None:
    """Enrich in the background, merging safely against the latest unconfirmed sheet."""
    try:
        provider = request.app.state.llm_provider
        enriched = await extract_case_sheet_patch(
            provider,
            baseline,
            transcript,
            language,
            session_id=str(visit_id),
        )
        if enriched == baseline:
            return
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repository = CaseSheetRepository(connection, request.app.state.settings)
            saved = await repository.get(
                hospital_id=hospital_id, visit_id=visit_id, for_update=True
            )
            if saved is None:
                return
            confirmed_at = await connection.scalar(
                text(
                    "SELECT confirmed_at FROM case_sheets "
                    "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            )
            if confirmed_at is not None:
                return
            current = saved["data"]
            merged = merge_enriched_case_sheet(current, baseline, enriched)
            if merged == current:
                return
            version = await repository.replace_if_version(
                hospital_id=hospital_id,
                visit_id=visit_id,
                expected_version=int(saved["version"]),
                data=merged,
                editor_id=None,
            )
            if version is not None:
                field_count = sum(
                    1
                    for path, metadata in merged["field_meta"].items()
                    if metadata.get("source") == "patient_voice"
                    and baseline["field_meta"].get(path) != metadata
                )
                _AI_LOGGER.info("llm_case_sheet_enrichment_applied", field_count=field_count)
    except asyncio.CancelledError:
        raise
    except Exception:
        _AI_LOGGER.warning("llm_case_sheet_enrichment_fallback", reason="unavailable_or_invalid")


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


@router.post("/assisted-sessions", status_code=status.HTTP_201_CREATED)
async def create_assisted_session(
    payload: CreateAssistedSessionPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("nurse"))],
) -> dict[str, Any]:
    hospital_id, nurse_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        department_id = await IntakeRepository(
            connection, request.app.state.settings
        ).active_nurse_department(
            hospital_id=hospital_id,
            nurse_id=nurse_id,
        )
        if department_id is None:
            raise _error(
                403,
                "FORBIDDEN",
                "Permission denied",
                "An active department assignment is required for assisted registration.",
            )
        visit_id, patient_id = await IntakeRepository(
            connection, request.app.state.settings
        ).create_assisted_session(
            hospital_id=hospital_id,
            department_id=department_id,
            language=payload.language,
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=nurse_id,
            action="intake.assisted_started",
            resource_type="visit",
            resource_id=visit_id,
            meta={"channel": "nurse", "language": payload.language},
        )
    consent = get_consent_copy(payload.language)
    session_state: dict[str, Any] = {
        "visit_id": str(visit_id),
        "patient_id": str(patient_id),
        "kiosk_id": None,
        "nurse_id": str(nurse_id),
        "channel": "nurse",
        "assisted": True,
        "language": payload.language,
        "phase": "consent",
        "danger_ids": [],
        "corrections": 0,
    }
    try:
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(session_state, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
    except RedisError as exc:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await IntakeRepository(connection, request.app.state.settings).set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
            )
        raise _error(
            503,
            "DEPENDENCY_UNAVAILABLE",
            "Session unavailable",
            "Please ask staff to start registration again.",
        ) from exc
    token = issue_access_token(
        subject=visit_id,
        role="patient",
        hospital_id=hospital_id,
        settings=request.app.state.settings,
        lifetime=timedelta(hours=request.app.state.settings.patient_session_hours),
    )
    return {
        "visit_id": str(visit_id),
        "patient_access_token": token,
        "expires_in": request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        "consent": {**consent, "language": payload.language},
        "assisted": True,
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
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    assisted = state.get("channel") == "nurse" and state.get("assisted") is True
    nurse_id = UUID(state["nurse_id"]) if assisted else None
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
            channel="nurse" if assisted else "kiosk",
        )
        if not payload.granted:
            await repository.set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
            )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user" if nurse_id else "patient",
            actor_id=nurse_id or visit_id,
            action="intake.consent.granted" if payload.granted else "intake.consent.declined",
            resource_type="visit",
            resource_id=visit_id,
            meta={"language": language, "version": copy["version"]},
        )
    if payload.granted:
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
    if state.get("assisted") is True and not payload.name:
        raise _error(
            422,
            "VALIDATION_FAILED",
            "Name required",
            "Enter the patient's name for nurse-assisted registration.",
        )
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
                sms_opt_in=payload.sms_opt_in,
                name=payload.name if state.get("assisted") is True else None,
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
    alerts: list[dict[str, Any]] = []
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await IntakeRepository(connection, request.app.state.settings).record_danger_tiles(
            hospital_id=hospital_id, visit_id=visit_id, tile_ids=payload.tile_ids
        )
        _, _, alerts = await _evaluate_visit(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            answers={},
            tile_ids=payload.tile_ids,
            request=request,
        )
    await _publish_alerts(
        request.app.state.redis_state,
        app_env=request.app.state.settings.app_env,
        hospital_id=hospital_id,
        alerts=alerts,
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
    pending_id = state.get("ai_selected_question")
    if (
        isinstance(pending_id, str)
        and pending_id in QUESTIONS
        and pending_id not in interview_state.asked
    ):
        question = QUESTIONS[pending_id]
    else:
        state.pop("ai_selected_question", None)
        if (
            request.app.state.settings.llm_enabled
            and getattr(request.app.state.llm_provider, "name", "mock") == "gemini"
            and await _hospital_llm_enabled(request, hospital_id)
        ):
            candidate_ids = [
                question_id
                for question_id in interview_state.queued
                if question_id in QUESTIONS and question_id not in interview_state.asked
            ][:5]
            candidates = tuple(
                QuestionCandidate(
                    question_id, QUESTIONS[question_id].prompt[interview_state.language]
                )
                for question_id in candidate_ids
            )
            transcript = "\n".join(
                str(answer)
                for answer in interview_state.answers.values()
                if isinstance(answer, str)
            )
            selected_id = await select_question_id(
                request.app.state.llm_provider,
                transcript=transcript,
                language=interview_state.language,
                candidates=candidates,
                fallback_id=question.id,
                session_id=str(visit_id),
            )
            if selected_id != question.id:
                reordered = [selected_id] + [
                    question_id
                    for question_id in interview_state.queued
                    if question_id != selected_id
                ]
                interview_state = interview_state.model_copy(update={"queued": reordered})
                state["interview"] = interview_state.model_dump(mode="json")
                state["ai_selected_question"] = selected_id
                await request.app.state.redis_state.set(
                    _session_key(hospital_id, visit_id),
                    json.dumps(state, separators=(",", ":")),
                    ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
                )
                question = QUESTIONS[selected_id]
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
        alerts: list[dict[str, Any]] = []
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
            _, _, alerts = await _evaluate_visit(
                connection,
                hospital_id=hospital_id,
                visit_id=visit_id,
                answers=next_state.answers,
                tile_ids=next_state.red_flag_ids,
                request=request,
            )
        state["interview"] = next_state.model_dump(mode="json")
        state.pop("ai_selected_question", None)
        await request.app.state.redis_state.set(
            key,
            json.dumps(state, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
        await _publish_alerts(
            request.app.state.redis_state,
            app_env=request.app.state.settings.app_env,
            hospital_id=hospital_id,
            alerts=alerts,
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
    alerts: list[dict[str, Any]] = []
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
        flags, route_result, alerts = await _evaluate_visit(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            answers=updated.answers,
            tile_ids=updated.red_flag_ids,
            request=request,
            include_department=state.get("phase") == "summary",
        )
        if state.get("phase") == "summary":
            data = _patient_case_sheet(data, flags, route_result)
            corrections = list(state.get("corrected_question_ids", []))
            if payload.question_id not in corrections:
                corrections.append(payload.question_id)
            confirmation = data.setdefault("patient_confirmation", {})
            confirmation["corrections"] = corrections
            await CaseSheetRepository(connection, request.app.state.settings).save(
                hospital_id=hospital_id, visit_id=visit_id, data=data
            )
    await _publish_alerts(
        request.app.state.redis_state,
        app_env=request.app.state.settings.app_env,
        hospital_id=hospital_id,
        alerts=alerts,
    )
    state["interview"] = updated.model_dump(mode="json")
    state["corrections"] = int(state.get("corrections", 0)) + 1
    corrected_question_ids = list(state.get("corrected_question_ids", []))
    if payload.question_id not in corrected_question_ids:
        corrected_question_ids.append(payload.question_id)
    state["corrected_question_ids"] = corrected_question_ids
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    if state.get("phase") == "summary":
        _schedule_case_sheet_enrichment(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            baseline=data,
            transcript=str(payload.answer),
            language=updated.language,
        )
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


@router.get("/sessions/{visit_id}/readback-audio")
async def generate_readback_audio(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> Response:
    """Generate patient-specific read-back speech in memory and never cache or persist it."""
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    if state.get("phase") != "summary":
        raise _error(409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first.")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        saved = await CaseSheetRepository(connection, request.app.state.settings).get(
            hospital_id=hospital_id, visit_id=visit_id
        )
        if saved is None:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            )
        copy = build_readback(saved["data"])
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="intake.readback_audio.generated",
            resource_type="case_sheet",
            resource_id=saved["id"],
            meta={"provider": request.app.state.tts_provider.name},
        )
    try:
        clip = await request.app.state.tts_provider.synthesize(
            copy, str(saved["data"].get("language", "en"))
        )
    except Exception as exc:
        raise _error(
            503,
            "TTS_UNAVAILABLE",
            "Read-back audio unavailable",
            "Read-back text remains available.",
        ) from exc
    return Response(
        content=clip.content,
        media_type=clip.media_type,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )


@router.post("/sessions/{visit_id}/needs-assistance")
async def flag_patient_needs_assistance(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, bool]:
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        repository = CaseSheetRepository(connection, request.app.state.settings)
        saved = await repository.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
        if saved is None:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            )
        data = saved["data"]
        confirmation = data.setdefault("patient_confirmation", {})
        confirmation["needs_assistance"] = True
        await repository.save(hospital_id=hospital_id, visit_id=visit_id, data=data)
        await connection.execute(
            text(
                "UPDATE visits SET needs_assistance = true "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="intake.patient_needs_assistance",
            resource_type="visit",
            resource_id=visit_id,
        )
    return {"needs_assistance": True}


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
    alerts: list[dict[str, Any]] = []
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        repository = CaseSheetRepository(connection, request.app.state.settings)
        saved = await repository.get(hospital_id=hospital_id, visit_id=visit_id)
        if saved is None:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            )
        interview_state = InterviewState.model_validate(state["interview"])
        flags, route_result, alerts = await _evaluate_visit(
            connection,
            hospital_id=hospital_id,
            visit_id=visit_id,
            answers=interview_state.answers,
            tile_ids=interview_state.red_flag_ids,
            request=request,
            include_department=True,
        )
        case_data = _patient_case_sheet(saved["data"], flags, route_result)
        confirmation = case_data.setdefault("patient_confirmation", {})
        confirmation["confirmed_by_patient"] = True
        confirmation["corrections"] = list(state.get("corrected_question_ids", []))
        for metadata in case_data.get("field_meta", {}).values():
            if isinstance(metadata, dict):
                metadata["confirmed"] = True
        if route_result.department_code != "NURSE_TRIAGE":
            routed = (
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM departments WHERE hospital_id = :hospital_id "
                            "AND code = :code AND is_active = true LIMIT 1"
                        ),
                        {"hospital_id": hospital_id, "code": route_result.department_code},
                    )
                )
                .mappings()
                .first()
            )
            if routed is not None:
                await connection.execute(
                    text(
                        "UPDATE visits SET department_id = :department_id "
                        "WHERE hospital_id = :hospital_id AND id = :visit_id"
                    ),
                    {
                        "department_id": routed["id"],
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                    },
                )
            else:
                case_data["routing"] = {
                    "department_code": "NURSE_TRIAGE",
                    "confidence": 0.0,
                    "reason_en": (
                        "The suggested department is unavailable; nurse confirmation is required."
                    ),
                    "needs_nurse_confirmation": True,
                }
        await repository.save(hospital_id=hospital_id, visit_id=visit_id, data=case_data)
        confirmed = await repository.confirm(hospital_id=hospital_id, visit_id=visit_id)
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
    await _publish_alerts(
        request.app.state.redis_state,
        app_env=request.app.state.settings.app_env,
        hospital_id=hospital_id,
        alerts=alerts,
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
