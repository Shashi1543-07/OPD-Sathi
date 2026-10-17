"""Consent-gated, touch/text intake endpoints for the kiosk and patient token."""

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator
from redis.exceptions import RedisError
from sqlalchemy import text

from app.ai.clinical_summary import apply_summary, summarize_interview
from app.ai.department_classifier import classify_department, combine_department_classification
from app.ai.followup_questions import draft_followups
from app.ai.prompts.builders import QuestionCandidate
from app.ai.providers.tts.base import pad_with_silence
from app.ai.question_choice import select_question_id
from app.ai.readback import build_readback, build_readback_from_answers
from app.ai.redflag_opinion import combine_redflag_opinions, second_opinion_flags
from app.ai.triage_review import review_triage
from app.api.v1.ai_gate import ai_consented as _ai_consented
from app.api.v1.ai_gate import llm_allowed as _llm_allowed
from app.core.audit import audit
from app.core.auth import require_kiosk, require_patient, require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.ids import uuid7
from app.core.ratelimit import check_rate_limit
from app.core.security import issue_access_token
from app.db.repositories import (
    CaseSheetRepository,
    IntakeRepository,
    InterviewTurnRepository,
    KioskUnavailableError,
    PatientRepository,
    QueueRepository,
)
from app.db.session import set_hospital_scope, set_secret_lookup_scope
from app.domain.checkin import checkin_code_digest, is_checkin_code
from app.domain.interview.bank import AYURVEDA_MODULE, QUESTIONS, Language, Question
from app.domain.interview.consent import DANGER_TILES, get_consent_copy
from app.domain.interview.engine import (
    ALWAYS_MODULES,
    InterviewState,
    InterviewValidationError,
    add_followups,
    apply_answer,
    build_case_sheet,
    correct_answer,
    go_back,
    next_question,
    pending,
    pregnancy_answer,
    question_cap,
    question_for,
    question_total,
    skip_followups,
    start,
    visible_question,
    with_language,
)
from app.domain.patients import card_pin, demographics_consistent, normalize_mrn, pin_matches
from app.domain.queue.redis_queue import QueueRedis
from app.domain.queue.scoring import queue_score
from app.domain.queue.tokens import create_status_token, status_token_digest
from app.domain.redflags.engine import PatientFacts, RedFlag, evaluate
from app.domain.routing.engine import Route
from app.domain.routing.engine import route as route_department
from app.domain.triage.score import assess, head_start_minutes, higher, option_flags

router = APIRouter(prefix="/intake", tags=["intake"])
_AI_LOGGER = structlog.get_logger(__name__)
_SESSION_TTL_FACTOR = 60 * 60
MAX_ANSWER_CHARACTERS = 500
# Corrections are stored after every possible interview turn so turn numbers never collide.
_CORRECTION_TURN_BASE = 100
# Each "Back" re-asks a question, so answers get fresh turn numbers below the correction base.
_MAX_INTERVIEW_TURNS = _CORRECTION_TURN_BASE
_FOLLOWUP_WAIT_SECONDS = 1.5
# AI follow-ups are drafted once the patient has said what the problem is and since when,
# so the model sees an overview while the patient answers the next few bank questions.
_FOLLOWUP_TRIGGER = "duration"
# Parts of a visit's triage record that a re-score must not erase.
_ACUITY_KEPT = ("override", "ai_review")
_FOLLOWUP_BASIS = ("chief_concern", "duration")
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


class OfflineSubmissionPayload(StrictPayload):
    idempotency_key: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    language: Language
    consent_granted: bool
    consent_version: str = Field(min_length=1, max_length=64)
    consent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    age: StrictInt = Field(ge=0, le=120)
    sex: Literal["female", "male", "other", "unknown"]
    chief_complaint: str = Field(min_length=1, max_length=500)
    duration: str = Field(default="", max_length=500)
    severity: StrictInt = Field(ge=0, le=10)
    conditions: str = Field(default="", max_length=500)
    medications: str = Field(default="", max_length=500)
    allergies: str = Field(default="", max_length=500)
    danger_tile_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def normalize_text(self) -> "OfflineSubmissionPayload":
        self.chief_complaint = self.chief_complaint.strip()
        if not self.chief_complaint:
            raise ValueError("a chief concern is required")
        return self


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


# A patient session changes its visit only before the token is issued.
_REGISTRATION_STATES = frozenset({"registering", "awaiting_confirmation"})
# What a session keeps after confirmation (R12): enough to end it, ask for help or rebuild the
# receipt from the database, but none of the answers.
_CONFIRMED_SESSION_KEYS = ("visit_id", "kiosk_id", "channel", "language", "assisted", "nurse_id")
_SESSION_LOCK_SECONDS = 20
_SESSION_LOCK_WAIT_SECONDS = 8.0
_RELEASE_SESSION_LOCK = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end "
    "return 0"
)


def _require_registering(context: dict[str, Any]) -> None:
    """Answers are fixed once the patient confirms the summary: the visit is in the queue."""
    if context.get("status") not in _REGISTRATION_STATES:
        raise _error(
            409,
            "SESSION_COMPLETE",
            "Already checked in",
            "Your token has been issued. Please tell the desk or the doctor about any change.",
        )


@asynccontextmanager
async def _session_lock(request: Request, hospital_id: UUID, visit_id: UUID) -> AsyncIterator[None]:
    """One interview change at a time per session.

    A second tap, or a retry on a slow network, waits for the first request and then sees its
    result, instead of applying the same answer twice.
    """
    redis = request.app.state.redis_state
    key = f"{_session_key(hospital_id, visit_id)}:lock"
    owner = secrets.token_urlsafe(16)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SESSION_LOCK_WAIT_SECONDS
    while not await redis.set(key, owner, nx=True, ex=_SESSION_LOCK_SECONDS):
        if loop.time() >= deadline:
            raise _error(409, "SESSION_BUSY", "Still saving", "Please wait a moment and try again.")
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        try:
            await redis.eval(_RELEASE_SESSION_LOCK, 1, key, owner)
        except RedisError:
            pass  # The lock expires by itself.


async def _hospital_ayurveda_enabled(request: Request, hospital_id: UUID) -> bool:
    """Whether this hospital adds the Ayurveda history questions (an AYUSH hospital)."""
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        enabled = await connection.scalar(
            text(
                "SELECT ayurveda_module FROM hospital_interview_settings "
                "WHERE hospital_id = :hospital_id"
            ),
            {"hospital_id": hospital_id},
        )
    return enabled is True


async def _evaluate_visit(
    connection: Any,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    answers: dict[str, str | int],
    tile_ids: list[str],
    request: Request | None = None,
    include_department: bool = False,
    use_llm: bool = False,
) -> tuple[list[RedFlag], Route, list[dict[str, Any]]]:
    """Persist new rule events and monotonically raise urgency inside the visit transaction."""
    row = (
        (
            await connection.execute(
                text(
                    "SELECT v.age_years, v.pregnant, v.department_id, v.status, v.urgency, "
                    "v.token_label, v.language, v.acuity_score, v.acuity, "
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
    # Rules run on every answer instantly; the model's second opinion runs once, at
    # confirmation, where urgency and department are decided.
    tenant_llm_enabled = (
        await _llm_allowed(request, hospital_id, visit_id)
        if request is not None
        and use_llm
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
                facts=facts,
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
    # A tapped answer that matches a red-flag rule counts the same in every language.
    known = {flag.id for flag in flags}
    flags = [*flags, *(flag for flag in option_flags(answers) if flag.id not in known)]
    assessment = assess(flags, answers, age=row["age_years"], pregnant=row["pregnant"])
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
        inserted = await connection.scalar(
            text(
                "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, level, "
                "evidence) VALUES (:id, :hospital_id, :visit_id, :source, :rule_id, :level, "
                ":evidence) ON CONFLICT (hospital_id, visit_id, rule_id) "
                "WHERE rule_id IS NOT NULL DO NOTHING RETURNING id"
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
        if inserted is None:
            continue  # A simultaneous request for this visit recorded (and alerted) it first.
        if flag.level == "urgent":
            alerts.append(
                {
                    "type": "alert.urgent",
                    "id": str(event_id),
                    "visit_id": str(visit_id),
                    "rule_id": flag.id,
                    "level": flag.level,
                    "reason_en": (
                        f"AI suggests (please confirm): {flag.reason_en}"
                        if flag.source == "llm"
                        else flag.reason_en
                    ),
                    "evidence": flag.evidence,
                    "token_label": row["token_label"],
                    "source": flag.source,
                }
            )
    # Neither the level nor the score falls on its own once raised (safety first); staff
    # can lower them with a recorded reason.
    urgency = higher(row["urgency"], assessment.level)
    previous_score = row.get("acuity_score")
    acuity_score = previous_score
    acuity = row.get("acuity") if isinstance(row.get("acuity"), dict) else None
    if previous_score is None or assessment.score >= previous_score:
        acuity_score = assessment.score
        kept = {key: value for key, value in (acuity or {}).items() if key in _ACUITY_KEPT}
        acuity = {**assessment.as_dict(), **kept}
    if acuity is not None and acuity_score is not None:
        acuity["level"] = urgency
        # Urgent goes to the front; otherwise the head start shown is the one applied.
        acuity["head_start_min"] = (
            None if urgency == "urgent" else head_start_minutes(urgency, acuity_score)
        )
    await connection.execute(
        text(
            "UPDATE visits SET urgency = :urgency, urgency_reasons = CAST(:reasons AS jsonb), "
            "acuity_score = :acuity_score, acuity = CAST(:acuity AS jsonb) "
            "WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {
            "urgency": urgency,
            "reasons": json.dumps(sorted(reason_ids)),
            "acuity_score": acuity_score,
            "acuity": json.dumps(acuity) if acuity is not None else None,
            "hospital_id": hospital_id,
            "visit_id": visit_id,
        },
    )
    if row["status"] == "queued" and (
        urgency != row["urgency"] or acuity_score != previous_score
    ):
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


def _question_payload(state: InterviewState, question_id: str) -> dict[str, Any]:
    raw = question_for(state, question_id)
    question = visible_question(state, raw) or raw
    language = state.language
    return {
        "id": question.id,
        "input": question.input,
        "optional": question.optional,
        "prompt": question.prompt[language],
        "multi": question.multi,
        "options": [
            {
                "id": option.id,
                "label": option.label[language],
                # "Don't know" and "Prefer not to say" stand alone on screen like "None".
                "none": option.non_answer,
                "excludes": option.excludes,
            }
            for option in question.options
        ],
    }


async def _sync_pregnancy(
    connection: Any, *, hospital_id: UUID, visit_id: UUID, interview_state: InterviewState
) -> None:
    """Keep the visit's pregnancy flag (queue priority, routing, safety rules) in step."""
    if "pregnancy" not in interview_state.queued:
        return
    await connection.execute(
        text(
            "UPDATE visits SET pregnant = :pregnant "
            "WHERE hospital_id = :hospital_id AND id = :visit_id"
        ),
        {
            "pregnant": pregnancy_answer(interview_state),
            "hospital_id": hospital_id,
            "visit_id": visit_id,
        },
    )


def _interview_not_started(state: dict[str, Any]) -> bool:
    """On the first question with nothing answered: Back may still change earlier steps."""
    interview = state.get("interview")
    return (
        state.get("phase") == "interview"
        and isinstance(interview, dict)
        and not interview.get("asked")
    )


def _next_turn_index(state: dict[str, Any], interview_state: InterviewState) -> int:
    """A fresh turn number per answer, so re-answering after "Back" never collides."""
    index = max(int(state.get("turns", 0)), len(interview_state.asked) - 1)
    if index >= _MAX_INTERVIEW_TURNS:
        raise _error(
            409, "TOO_MANY_CHANGES", "Too many changes", "Please ask staff to help you finish."
        )
    state["turns"] = index + 1
    return index


def _answered(state: InterviewState) -> list[dict[str, Any]]:
    """Answered questions, with tapped options shown in the current language."""
    return [
        {
            **_question_payload(state, question_id),
            "answer": question_for(state, question_id).localized(
                state.answers[question_id], state.language
            ),
        }
        for question_id in state.asked
        if question_id in state.answers
    ]


def _readback(data: dict[str, Any], state: InterviewState | None = None) -> str:
    """Patient read-back in the patient's own words when the interview answers are known."""
    if state is not None and state.answers:
        return build_readback_from_answers(
            state.language, state.answers, questions=state.extra_questions
        )
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
    await _schedule_case_sheet_enrichment(
        request,
        hospital_id=hospital_id,
        visit_id=visit_id,
        interview_state=interview_state,
    )
    return {
        "complete": True,
        "case_sheet": data,
        "readback": _readback(data, interview_state),
        "answered": _answered(interview_state),
    }


async def _schedule_case_sheet_enrichment(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    interview_state: InterviewState,
) -> None:
    """Start the doctor-facing AI summary after returning the deterministic summary."""
    if not await _llm_allowed(request, hospital_id, visit_id):
        return
    provider = request.app.state.llm_provider
    if getattr(provider, "name", "mock") != "gemini":
        return
    task = asyncio.create_task(
        _run_bounded_case_sheet_enrichment(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            interview_state=interview_state,
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
    interview_state: InterviewState,
) -> None:
    try:
        await asyncio.wait_for(
            _persist_case_sheet_enrichment(
                request,
                hospital_id=hospital_id,
                visit_id=visit_id,
                interview_state=interview_state,
            ),
            timeout=25.0,
        )
    except TimeoutError:
        _AI_LOGGER.warning("llm_case_sheet_enrichment_fallback", reason="background_timeout")


def _summary_answers(interview_state: InterviewState) -> list[dict[str, str]]:
    return [
        {
            "question_id": question_id,
            "question_en": question_for(interview_state, question_id).prompt["en"],
            "answer": str(interview_state.answers[question_id]),
        }
        for question_id in interview_state.asked
        if question_id in interview_state.answers
    ]


async def _persist_case_sheet_enrichment(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    interview_state: InterviewState,
) -> None:
    """Translate and structure the interview, then merge onto the latest stored sheet."""
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            patient = (
                (
                    await connection.execute(
                        text(
                            "SELECT v.age_years, p.sex FROM visits v JOIN patients p "
                            "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                            "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                        ),
                        {"hospital_id": hospital_id, "visit_id": visit_id},
                    )
                )
                .mappings()
                .first()
            )
        provider = request.app.state.llm_provider
        output = await summarize_interview(
            provider,
            language=interview_state.language,
            age=patient["age_years"] if patient else None,
            sex=patient["sex"] if patient else None,
            answers=_summary_answers(interview_state),
            danger_signs=[
                DANGER_TILES[tile]["en"]
                for tile in interview_state.red_flag_ids
                if tile in DANGER_TILES
            ],
            session_id=str(visit_id),
        )
        if output is None:
            _AI_LOGGER.warning("llm_case_sheet_enrichment_fallback", reason="unavailable_or_unsafe")
            return
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repository = CaseSheetRepository(connection, request.app.state.settings)
            saved = await repository.get(
                hospital_id=hospital_id, visit_id=visit_id, for_update=True
            )
            if saved is None:
                return
            choice = await IntakeRepository(connection, request.app.state.settings).ai_consent(
                hospital_id=hospital_id, visit_id=visit_id
            )
            if choice != "granted":
                return  # The patient stopped AI help while the draft was being written.
            current = saved["data"]
            if any(
                isinstance(meta, dict) and meta.get("source") in {"nurse", "doctor"}
                for meta in current.get("field_meta", {}).values()
            ) or (current.get("ai_summary") or {}).get("review"):
                return  # Staff have edited or reviewed this sheet; never overwrite their work.
            merged = apply_summary(
                current,
                output,
                model=str(getattr(provider, "model", "")),
                asked=interview_state.asked,
            )
            version = await repository.replace_if_version(
                hospital_id=hospital_id,
                visit_id=visit_id,
                expected_version=int(saved["version"]),
                data=merged,
                editor_id=None,
            )
            if version is not None:
                _AI_LOGGER.info("llm_case_sheet_enrichment_applied")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _AI_LOGGER.warning(
            "llm_case_sheet_enrichment_fallback",
            reason="unavailable_or_invalid",
            error_type=type(exc).__name__,
        )


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_kiosk())],
) -> dict[str, Any]:
    hospital_id, kiosk_id = _identity(principal)
    issued_at = principal.get("iat")
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            visit_id, patient_id = await IntakeRepository(
                connection, request.app.state.settings
            ).create_session(
                hospital_id=hospital_id,
                language=payload.language,
                kiosk_id=kiosk_id,
                issued_at=issued_at if isinstance(issued_at, int) else None,
            )
    except KioskUnavailableError as exc:
        if exc.reason == "off":
            raise _error(
                409,
                "KIOSK_TURNED_OFF",
                "Kiosk turned off",
                "This kiosk is turned off. Ask staff to turn it on.",
            ) from exc
        # Deleted or replaced: 401 sends the device back to its activation screen.
        raise _error(
            401,
            "KIOSK_NOT_PAIRED",
            "Kiosk needs setting up",
            "This kiosk was removed or set up on another device. "
            "Ask staff for a new activation code.",
        ) from exc
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
        "ai_consent": get_consent_copy(payload.language, "ai"),
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
        "ai_consent": get_consent_copy(payload.language, "ai"),
        "assisted": True,
    }


class CheckinSessionPayload(StrictPayload):
    language: Language


_CHECKIN_WINDOW_SECONDS = 600
# Check-ins one phone (client address) may start per check-in point in ten minutes: enough for
# a family registering several members, low enough to stop a script flooding the queue.
_CHECKIN_SESSIONS_PER_CLIENT = 6
# All phones together, per check-in point, in ten minutes.
_CHECKIN_SESSIONS_PER_POINT = 150
# "Call staff" presses per visit in ten minutes; each one rings the staff board.
_HELP_REQUESTS_PER_VISIT = 5
_HELP_WINDOW_SECONDS = 600


async def _rate_limit(
    request: Request, *, key: str, limit: int, window_seconds: int, detail: str
) -> None:
    result = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=key,
        limit=limit,
        window_seconds=window_seconds,
        fail_closed=False,
    )
    if result is not None and not result.allowed:
        raise ApiProblemError(
            status=429,
            code="RATE_LIMITED",
            title="Too many requests",
            detail=detail,
            headers={"Retry-After": str(result.retry_after)},
        )


def _client_address(request: Request) -> str:
    # Behind the web container's /api proxy this is the phone's address (X-Forwarded-For).
    return request.client.host if request.client else "unknown"


async def _checkin_point(request: Request, code: str) -> dict[str, Any]:
    """The active phone check-in point behind a QR code, with its hospital's name."""
    unavailable = _error(
        404,
        "CHECKIN_UNAVAILABLE",
        "Check-in link not valid",
        "This check-in QR is not valid. Please use the kiosk or ask at the registration desk.",
    )
    if not is_checkin_code(code):
        raise unavailable
    digest = checkin_code_digest(code)
    async with request.app.state.engine.begin() as connection:
        await set_secret_lookup_scope(
            connection, setting="app.kiosk_checkin_hash", secret_hash=digest
        )
        point = await IntakeRepository(
            connection, request.app.state.settings
        ).checkin_point_by_code(digest)
        if point is None:
            raise unavailable
        await set_hospital_scope(connection, point["hospital_id"])
        hospital_name = await connection.scalar(
            text("SELECT name FROM hospitals WHERE id = :hospital_id"),
            {"hospital_id": point["hospital_id"]},
        )
    if not point["is_active"]:
        raise _error(
            409,
            "CHECKIN_TURNED_OFF",
            "Phone check-in paused",
            "Phone check-in is paused. Please use the kiosk or ask at the registration desk.",
        )
    return {**point, "hospital_name": hospital_name}


@router.get("/checkin/{code}")
async def checkin_details(code: str, request: Request) -> dict[str, Any]:
    """What a phone shows before starting: the hospital's name, or why it cannot start."""
    settings = request.app.state.settings
    await _rate_limit(
        request,
        key=f"osa:{settings.app_env}:rl:checkin-lookup:{_client_address(request)}",
        limit=60,
        window_seconds=60,
        detail="Please wait a moment and try again.",
    )
    point = await _checkin_point(request, code)
    return {"hospital_name": point["hospital_name"], "languages": ["hi", "mr", "en"]}


@router.post("/checkin/{code}/sessions", status_code=status.HTTP_201_CREATED)
async def create_checkin_session(
    code: str, payload: CheckinSessionPayload, request: Request
) -> dict[str, Any]:
    """Start the interview on a patient's own phone from the hospital's check-in QR.

    The phone gets the same patient session a kiosk would, so consent, the interview, the
    safety rules and the token are exactly the kiosk's. Starting is limited per phone and
    per check-in point; a session only reaches the queue once the patient confirms it.
    """
    settings = request.app.state.settings
    point = await _checkin_point(request, code)
    hospital_id, point_id = point["hospital_id"], point["id"]
    await _rate_limit(
        request,
        key=f"osa:{settings.app_env}:rl:checkin-start:{point_id}:{_client_address(request)}",
        limit=_CHECKIN_SESSIONS_PER_CLIENT,
        window_seconds=_CHECKIN_WINDOW_SECONDS,
        detail="Too many check-ins from this phone. Please ask at the registration desk.",
    )
    await _rate_limit(
        request,
        key=f"osa:{settings.app_env}:rl:checkin-point:{point_id}",
        limit=_CHECKIN_SESSIONS_PER_POINT,
        window_seconds=_CHECKIN_WINDOW_SECONDS,
        detail="Phone check-in is very busy. Please use the kiosk or ask at the desk.",
    )
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            visit_id, patient_id = await IntakeRepository(
                connection, settings
            ).create_mobile_session(
                hospital_id=hospital_id, point_id=point_id, language=payload.language
            )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="kiosk",
                actor_id=point_id,
                action="intake.mobile_started",
                resource_type="visit",
                resource_id=visit_id,
                meta={"channel": "mobile", "language": payload.language},
            )
    except KioskUnavailableError as exc:
        raise _error(
            409,
            "CHECKIN_TURNED_OFF",
            "Phone check-in paused",
            "Phone check-in is paused. Please use the kiosk or ask at the registration desk.",
        ) from exc
    except LookupError as exc:
        raise _error(
            409, "CHECKIN_NOT_READY", "Check-in unavailable", "Please ask at the registration desk."
        ) from exc
    state_data = {
        "visit_id": str(visit_id),
        "patient_id": str(patient_id),
        "kiosk_id": str(point_id),
        "channel": "mobile",
        "language": payload.language,
        "phase": "consent",
        "danger_ids": [],
        "corrections": 0,
    }
    try:
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(state_data, separators=(",", ":")),
            ex=settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
    except Exception as exc:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await IntakeRepository(connection, settings).set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
            )
        raise _error(
            503, "DEPENDENCY_UNAVAILABLE", "Check-in unavailable", "Please try again in a moment."
        ) from exc
    patient_token = issue_access_token(
        subject=visit_id,
        role="patient",
        hospital_id=hospital_id,
        settings=settings,
        lifetime=timedelta(hours=settings.patient_session_hours),
    )
    return {
        "visit_id": str(visit_id),
        "patient_access_token": patient_token,
        "expires_in": settings.patient_session_hours * _SESSION_TTL_FACTOR,
        "consent": get_consent_copy(payload.language),
        "ai_consent": get_consent_copy(payload.language, "ai"),
        "channel": "mobile",
        "hospital_name": point["hospital_name"],
    }


class LanguagePayload(StrictPayload):
    language: Language


@router.post("/sessions/{visit_id}/language")
async def change_language(
    visit_id: UUID,
    payload: LanguagePayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Switch the patient's language mid-interview; earlier answers are kept as given."""
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=False)
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if state.get("phase") == "confirmed":
        raise _error(409, "SESSION_COMPLETE", "Already checked in", "The token has been issued.")
    state["language"] = payload.language
    if isinstance(state.get("interview"), dict):
        interview_state = with_language(
            InterviewState.model_validate(state["interview"]), payload.language
        )
        state["interview"] = interview_state.model_dump(mode="json")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await connection.execute(
            text(
                "UPDATE visits SET language = :language "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {"language": payload.language, "hospital_id": hospital_id, "visit_id": visit_id},
        )
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return {
        "language": payload.language,
        "consent": get_consent_copy(payload.language),
        "ai_consent": get_consent_copy(payload.language, "ai"),
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
            channel=(
                "nurse"
                if assisted
                else "mobile"
                if state.get("channel") == "mobile"
                else "kiosk"
            ),
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


@router.post("/sessions/{visit_id}/consent/ai")
async def record_ai_consent(
    visit_id: UUID,
    payload: ConsentPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """The patient's separate, optional answer about online AI help; nothing is pre-selected.

    Asked after the care consent, from its own notice (read aloud like the first). Saying no
    changes nothing about the visit except that it runs on this server alone. The answer can
    be changed until the summary is confirmed, and a "yes" can be withdrawn later from the
    patient's status page or at the desk.
    """
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    _require_registering(context)
    language: Language = context["language"]
    copy = get_consent_copy(language, "ai")
    if payload.version != copy["version"] or payload.sha256 != copy["sha256"]:
        raise _error(
            409,
            "CONSENT_COPY_MISMATCH",
            "Consent copy changed",
            "Reload the information notice and review it again.",
        )
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    assisted = state.get("channel") == "nurse" and state.get("assisted") is True
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await IntakeRepository(connection, request.app.state.settings).record_ai_consent(
            hospital_id=hospital_id,
            visit_id=visit_id,
            language=language,
            version=copy["version"],
            text_sha256=copy["sha256"],
            granted=payload.granted,
            channel=(
                "nurse" if assisted else "mobile" if state.get("channel") == "mobile" else "kiosk"
            ),
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user" if assisted else "patient",
            actor_id=UUID(state["nurse_id"]) if assisted else visit_id,
            action="intake.ai_consent.granted" if payload.granted else "intake.ai_consent.declined",
            resource_type="visit",
            resource_id=visit_id,
            meta={"language": language, "version": copy["version"]},
        )
    return {"granted": payload.granted}


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
    # A patient who went Back from the safety screen (or from the first question, before
    # answering anything) may change their details.
    if state.get("phase") not in {"identity", "danger_signs"} and not _interview_not_started(
        state
    ):
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    # Phone numbers are not unique: families share one phone and returning patients give
    # theirs again. A number is a contact detail, never an identity.
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
            linked=context.get("patient_link") is not None,
        )
    state["phase"] = "danger_signs"
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return {"next": "danger_signs"}


class OpdCardPayload(StrictPayload):
    number: str = Field(min_length=6, max_length=24)
    pin: str = Field(min_length=4, max_length=12)


class FollowUpPayload(StrictPayload):
    previous_visit_id: UUID | None = None


@router.post("/sessions/{visit_id}/opd-card")
async def link_opd_card(
    visit_id: UUID,
    payload: OpdCardPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """A returning patient joins today's visit to their record with their OPD card.

    Nothing from the earlier record is shown here (a kiosk screen is shared); the doctor sees
    it. The card links only when today's age and sex fit the record, attempts are limited per
    visit and per card, and every attempt is audited.
    """
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    settings = request.app.state.settings
    if context["status"] != "registering":
        raise _error(
            409, "SESSION_COMPLETE", "Too late to change", "Please ask at the registration desk."
        )
    if context.get("age_years") is None:
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Enter your age first."
        )
    too_many = "Too many attempts. Please ask at the registration desk."
    await _rate_limit(
        request,
        key=f"osa:{settings.app_env}:rl:opd-card:visit:{visit_id}",
        limit=5,
        window_seconds=900,
        detail=too_many,
    )
    number = normalize_mrn(payload.number)
    if number is not None:
        await _rate_limit(
            request,
            key=f"osa:{settings.app_env}:rl:opd-card:number:{hospital_id}:{number}",
            limit=10,
            window_seconds=3600,
            detail=too_many,
        )
    outcome = "linked"
    response: dict[str, Any] = {}
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        patients = PatientRepository(connection, settings)
        record = await patients.by_mrn(hospital_id=hospital_id, mrn=number) if number else None
        # Derive a PIN even for an unknown number, so both answers take the same time.
        expected = card_pin(
            key=settings.app_hmac_key_bytes,
            hospital_id=hospital_id,
            patient_id=record["id"] if record else visit_id,
            version=int(record["card_version"]) if record else 1,
        )
        if record is None or not pin_matches(payload.pin, expected):
            outcome = "not_recognised"
        elif not demographics_consistent(
            stored_birth_year=record["year_of_birth"],
            stored_sex=record["sex"],
            age=context.get("age_years"),
            sex=context.get("sex"),
            current_year=datetime.now(UTC).year,
        ):
            outcome = "identity_mismatch"
        else:
            try:
                await patients.link_visit(
                    hospital_id=hospital_id,
                    visit_id=visit_id,
                    patient_id=record["id"],
                    source="card",
                )
            except ValueError:
                outcome = "already_linked"
            if outcome == "linked":
                earlier = await patients.visit_history(
                    hospital_id=hospital_id,
                    patient_id=record["id"],
                    exclude_visit_id=visit_id,
                    limit=1,
                )
                visit_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM visits WHERE hospital_id = :hospital_id "
                        "AND patient_id = :patient_id AND id <> :visit_id "
                        "AND token_number IS NOT NULL"
                    ),
                    {
                        "hospital_id": hospital_id,
                        "patient_id": record["id"],
                        "visit_id": visit_id,
                    },
                )
                response = {
                    "linked": True,
                    "opd_number": record["mrn"],
                    "visit_count": int(visit_count or 0),
                    # Only the date: the kiosk never shows what an earlier visit was about.
                    "previous_visit": (
                        {
                            "id": str(earlier[0]["visit_id"]),
                            "date": earlier[0]["service_date"].isoformat(),
                        }
                        if earlier
                        else None
                    ),
                }
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="patient",
            actor_id=visit_id,
            action="intake.opd_card.linked" if outcome == "linked" else "intake.opd_card.refused",
            resource_type="visit",
            resource_id=visit_id,
            meta={"outcome": outcome},
        )
    if outcome == "identity_mismatch":
        raise _error(
            409,
            "CARD_IDENTITY_MISMATCH",
            "Details do not match",
            "These details do not match the card. Please ask at the registration desk.",
        )
    if outcome == "already_linked":
        raise _error(
            409,
            "ALREADY_LINKED",
            "Already linked",
            "This visit is already linked to a record. Please ask at the registration desk.",
        )
    if outcome != "linked":
        raise _error(
            422,
            "CARD_NOT_RECOGNISED",
            "Card not recognised",
            "We could not match this OPD card. Check the number and PIN, "
            "or ask at the registration desk.",
        )
    return response


@router.post("/sessions/{visit_id}/follow-up")
async def mark_follow_up(
    visit_id: UUID,
    payload: FollowUpPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Say whether today's visit continues an earlier one; the earlier visit is not changed."""
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    if context["status"] != "registering":
        raise _error(
            409, "SESSION_COMPLETE", "Too late to change", "Please ask at the registration desk."
        )
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        if payload.previous_visit_id is not None:
            earlier = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM visits WHERE hospital_id = :hospital_id "
                    "AND id = :previous_id AND patient_id = :patient_id AND id <> :visit_id "
                    "AND token_number IS NOT NULL)"
                ),
                {
                    "hospital_id": hospital_id,
                    "previous_id": payload.previous_visit_id,
                    "patient_id": context["patient_id"],
                    "visit_id": visit_id,
                },
            )
            if not earlier:
                raise _error(
                    422,
                    "VALIDATION_FAILED",
                    "Earlier visit not found",
                    "Choose one of your own earlier visits.",
                )
        await connection.execute(
            text(
                "UPDATE visits SET previous_visit_id = :previous_id "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {
                "previous_id": payload.previous_visit_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
            },
        )
    return {
        "previous_visit_id": str(payload.previous_visit_id) if payload.previous_visit_id else None
    }


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
    # Back from the first question (nothing answered yet) returns here to change the tiles.
    if state.get("phase") != "danger_signs" and not _interview_not_started(state):
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
    interview_state = start(
        context["language"],
        red_flag_ids=tuple(payload.tile_ids),
        sex=context.get("sex"),
        age=context.get("age_years"),
        extra_modules=(
            (AYURVEDA_MODULE,) if await _hospital_ayurveda_enabled(request, hospital_id) else ()
        ),
    )
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


def _followup_key(hospital_id: UUID, visit_id: UUID) -> str:
    return f"{_session_key(hospital_id, visit_id)}:followups"


def _schedule_triage_review(
    request: Request, *, hospital_id: UUID, visit_id: UUID, interview_state: InterviewState
) -> None:
    """Have the model double-check the triage level after check-in, off the patient's path."""
    task = asyncio.create_task(
        _run_triage_review(
            request, hospital_id=hospital_id, visit_id=visit_id, interview_state=interview_state
        )
    )
    tasks: set[asyncio.Task[None]] = request.app.state.llm_background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _run_triage_review(
    request: Request, *, hospital_id: UUID, visit_id: UUID, interview_state: InterviewState
) -> None:
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT v.acuity, v.age_years, v.pregnant, v.department_id, "
                            "v.token_label, p.sex FROM visits v JOIN patients p "
                            "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                            "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                        ),
                        {"hospital_id": hospital_id, "visit_id": visit_id},
                    )
                )
                .mappings()
                .first()
            )
        if row is None or not isinstance(row["acuity"], dict):
            return
        answers = []
        for question_id in interview_state.asked:
            value = interview_state.answers.get(question_id)
            if value is None:
                continue
            question = question_for(interview_state, question_id)
            answer = question.english(value) if isinstance(value, str) else f"{value}/10"
            answers.append({"question": question.prompt["en"], "answer": answer})
        review = await review_triage(
            request.app.state.llm_provider,
            age=row["age_years"],
            sex=row["sex"],
            pregnant=row["pregnant"],
            answers=answers,
            danger_signs=[
                DANGER_TILES[tile]["en"]
                for tile in interview_state.red_flag_ids
                if tile in DANGER_TILES
            ],
            acuity=row["acuity"],
            session_id=str(visit_id),
        )
        if review is None:
            return
        alerts: list[dict[str, Any]] = []
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            choice = await IntakeRepository(connection, request.app.state.settings).ai_consent(
                hospital_id=hospital_id, visit_id=visit_id
            )
            if choice != "granted":
                return  # The patient stopped AI help while the check was running.
            await connection.execute(
                text(
                    "UPDATE visits SET acuity = coalesce(acuity, '{}'::jsonb) || "
                    "jsonb_build_object('ai_review', CAST(:review AS jsonb)) "
                    "WHERE hospital_id = :hospital_id AND id = :visit_id"
                ),
                {"review": json.dumps(review), "hospital_id": hospital_id, "visit_id": visit_id},
            )
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
            if review["verdict"] == "more_urgent":
                # A nurse decides; the AI never reorders the queue by itself.
                event_id = uuid7()
                evidence = review["reason_en"] or "AI review suggests checking this patient."
                inserted = await connection.scalar(
                    text(
                        "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, "
                        "rule_id, level, evidence) VALUES (:id, :hospital_id, :visit_id, "
                        "'llm', 'AI_TRIAGE_REVIEW', 'priority', :evidence) "
                        "ON CONFLICT (hospital_id, visit_id, rule_id) WHERE rule_id IS NOT NULL "
                        "DO NOTHING RETURNING id"
                    ),
                    {
                        "id": event_id,
                        "hospital_id": hospital_id,
                        "visit_id": visit_id,
                        "evidence": evidence[:240],
                    },
                )
                if inserted is not None:
                    alerts.append(
                        {
                            "type": "alert.urgent",
                            "id": str(event_id),
                            "visit_id": str(visit_id),
                            "rule_id": "AI_TRIAGE_REVIEW",
                            "level": "priority",
                            "reason_en": "AI review suggests this patient may be more urgent.",
                            "evidence": evidence[:240],
                            "token_label": row["token_label"],
                            "department_id": str(row["department_id"]),
                        }
                    )
        await _publish_alerts(
            request.app.state.redis_state,
            app_env=request.app.state.settings.app_env,
            hospital_id=hospital_id,
            alerts=alerts,
        )
        _AI_LOGGER.info("ai_triage_review_ready", verdict=review["verdict"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _AI_LOGGER.warning("ai_triage_review_failed", error_type=type(exc).__name__)


def _followup_basis(interview_state: InterviewState) -> str:
    """The answers a draft was written from; a draft for older answers is never used."""
    return json.dumps(
        [interview_state.answers.get(question_id) for question_id in _FOLLOWUP_BASIS],
        ensure_ascii=False,
    )


async def _forget_followups(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> None:
    """Allow a fresh draft after the patient changed the answers it would be based on."""
    state.pop("followups_requested", None)
    state.pop("followups_waited", None)
    try:
        await request.app.state.redis_state.delete(_followup_key(hospital_id, visit_id))
    except RedisError:
        return


async def _followups_enabled(request: Request, hospital_id: UUID, visit_id: UUID) -> bool:
    settings = request.app.state.settings
    provider = getattr(request.app.state, "llm_provider", None)
    return bool(
        getattr(settings, "ai_followup_questions", False)
        and getattr(provider, "name", "mock") == "gemini"
        and await _llm_allowed(request, hospital_id, visit_id)
    )


def _schedule_followups(
    request: Request, *, hospital_id: UUID, visit_id: UUID, interview_state: InterviewState
) -> None:
    """Draft AI follow-ups in the background while the patient answers the next questions."""
    task = asyncio.create_task(
        _draft_and_store_followups(
            request, hospital_id=hospital_id, visit_id=visit_id, interview_state=interview_state
        )
    )
    tasks: set[asyncio.Task[None]] = request.app.state.llm_background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _draft_and_store_followups(
    request: Request, *, hospital_id: UUID, visit_id: UUID, interview_state: InterviewState
) -> None:
    questions: list[Question] = []
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            patient = (
                (
                    await connection.execute(
                        text(
                            "SELECT v.age_years, p.sex FROM visits v JOIN patients p "
                            "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                            "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                        ),
                        {"hospital_id": hospital_id, "visit_id": visit_id},
                    )
                )
                .mappings()
                .first()
            )
        duration = interview_state.answers.get("duration")
        questions = await asyncio.wait_for(
            draft_followups(
                request.app.state.llm_provider,
                complaint=str(interview_state.answers.get("chief_concern", "")),
                duration=(
                    QUESTIONS["duration"].english(duration) if isinstance(duration, str) else ""
                ),
                language=interview_state.language,
                age=patient["age_years"] if patient else None,
                sex=patient["sex"] if patient else None,
                planned_questions=[
                    question_for(interview_state, item) for item in interview_state.queued
                ],
                danger_signs=[
                    DANGER_TILES[tile]["en"]
                    for tile in interview_state.red_flag_ids
                    if tile in DANGER_TILES
                ],
                session_id=str(visit_id),
            ),
            timeout=12.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _AI_LOGGER.warning("ai_followups_failed", error_type=type(exc).__name__)
    _AI_LOGGER.info("ai_followups_ready", count=len(questions))
    try:
        await request.app.state.redis_state.set(
            _followup_key(hospital_id, visit_id),
            json.dumps(
                {
                    "basis": _followup_basis(interview_state),
                    "questions": [question.model_dump(mode="json") for question in questions],
                }
            ),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
    except RedisError:
        return


async def _merge_followups(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    state: dict[str, Any],
    interview_state: InterviewState,
) -> InterviewState:
    """Slot ready AI follow-ups in before the history questions; never block the patient.

    The draft normally finishes while the patient answers duration, severity and the symptom
    questions. If it is still missing when the history questions start, wait once for at
    most 1.5 seconds, then carry on with the clinician-reviewed bank; a draft that arrives
    later is still slotted in among the remaining history questions.
    """
    if interview_state.ai_followups != "none" or not state.get("followups_requested"):
        return interview_state
    if not await _llm_allowed(request, hospital_id, visit_id):
        return interview_state  # AI help was stopped after the draft was requested.
    upcoming = next_question(interview_state)
    if upcoming is None:
        return interview_state  # Interview finished; nothing left to slot in before.
    redis = request.app.state.redis_state
    key = _followup_key(hospital_id, visit_id)
    raw = await redis.get(key)
    if raw is None and upcoming.module in ALWAYS_MODULES and not state.get("followups_waited"):
        state["followups_waited"] = True
        waited = 0.0
        while raw is None and waited < _FOLLOWUP_WAIT_SECONDS:
            await asyncio.sleep(0.25)
            waited += 0.25
            raw = await redis.get(key)
        if raw is None:
            await redis.set(
                _session_key(hospital_id, visit_id),
                json.dumps(state, separators=(",", ":")),
                ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
            )
    if raw is None:
        return interview_state
    questions: list[Question] = []
    try:
        stored = json.loads(raw)
        if isinstance(stored, dict):
            if stored.get("basis") != _followup_basis(interview_state):
                return interview_state  # Written for answers the patient has since changed.
            stored = stored.get("questions")
        questions = [Question.model_validate(item) for item in stored]
    except (TypeError, ValueError):
        questions = []
    merged = (
        add_followups(interview_state, questions) if questions else skip_followups(interview_state)
    )
    state["interview"] = merged.model_dump(mode="json")
    await redis.set(
        _session_key(hospital_id, visit_id),
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    return merged


async def _question_response(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> dict[str, Any]:
    interview_state = await _merge_followups(
        request,
        hospital_id=hospital_id,
        visit_id=visit_id,
        state=state,
        interview_state=InterviewState.model_validate(state["interview"]),
    )
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
    still_to_ask = pending(interview_state)
    if isinstance(pending_id, str) and pending_id in QUESTIONS and pending_id in still_to_ask:
        question = visible_question(interview_state, QUESTIONS[pending_id]) or question
    else:
        state.pop("ai_selected_question", None)
        if (
            request.app.state.settings.llm_enabled
            and request.app.state.settings.ai_question_choice
            and getattr(request.app.state.llm_provider, "name", "mock") == "gemini"
            and await _llm_allowed(request, hospital_id, visit_id)
        ):
            candidate_ids = [
                question_id for question_id in still_to_ask if question_id in QUESTIONS
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
        "question": _question_payload(interview_state, question.id),
        "progress": {
            "current": len(interview_state.asked) + 1,
            # Before the main concern is known, assume a typical interview length.
            "total": min(
                question_cap(interview_state),
                max(question_total(interview_state), 10 if not interview_state.asked else 0),
            ),
        },
        "answered": _answered(interview_state),
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
    async with _session_lock(request, hospital_id, visit_id):
        state = _read_state(
            await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
        )
        return await _current_interview(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )


async def _current_interview(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> dict[str, Any]:
    """Where the interview stands: the next question, or the summary once all are answered."""
    if state.get("phase") == "summary":
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            result = await CaseSheetRepository(connection, request.app.state.settings).get(
                hospital_id=hospital_id, visit_id=visit_id
            )
        data = result["data"] if result else None
        summary_state = InterviewState.model_validate(state["interview"])
        return {
            "complete": True,
            "case_sheet": data,
            "readback": _readback(data, summary_state) if data else None,
            "answered": _answered(summary_state),
        }
    if state.get("phase") != "interview":
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    return await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )


def _already_answered(state: dict[str, Any], question_id: str) -> bool:
    """Whether this question was answered and the interview has already moved past it."""
    interview = state.get("interview")
    if state.get("phase") not in {"interview", "summary"} or not isinstance(interview, dict):
        return False
    current = InterviewState.model_validate(interview)
    if question_id not in current.asked:
        return False
    upcoming = next_question(current)
    return upcoming is None or upcoming.id != question_id


@router.post("/sessions/{visit_id}/interview/answers")
async def submit_answer(
    visit_id: UUID,
    payload: AnswerPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    _require_registering(context)
    _validate_answer_size(payload.answer)
    idempotency_body = payload.model_dump()

    async def operation() -> dict[str, Any]:
        async with _session_lock(request, hospital_id, visit_id):
            return await _save_answer(
                request, hospital_id=hospital_id, visit_id=visit_id, payload=payload
            )

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=visit_id,
        body=idempotency_body,
        operation=operation,
    )


async def _save_answer(
    request: Request, *, hospital_id: UUID, visit_id: UUID, payload: AnswerPayload
) -> dict[str, Any]:
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if _already_answered(state, payload.question_id):
        # Saved by an earlier request whose response never arrived (the connection dropped)
        # or by a second tap: show where the interview stands now instead of an error. The
        # patient can still change the answer on the review screen.
        return await _current_interview(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )
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
    turn_index = _next_turn_index(state, next_state)
    alerts: list[dict[str, Any]] = []
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await InterviewTurnRepository(connection, request.app.state.settings).add(
            hospital_id=hospital_id,
            visit_id=visit_id,
            turn_index=turn_index,
            question_id=payload.question_id,
            input_mode=payload.input_mode,
            text_original=raw_answer,
        )
        await _sync_pregnancy(
            connection, hospital_id=hospital_id, visit_id=visit_id, interview_state=next_state
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
    draft_followups_now = (
        payload.question_id == _FOLLOWUP_TRIGGER
        and isinstance(next_state.answers.get("chief_concern"), str)
        and not state.get("followups_requested")
        and await _followups_enabled(request, hospital_id, visit_id)
    )
    if draft_followups_now:
        state["followups_requested"] = True
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    if draft_followups_now:
        _schedule_followups(
            request, hospital_id=hospital_id, visit_id=visit_id, interview_state=next_state
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


@router.patch("/sessions/{visit_id}/interview/answers")
async def correct_interview_answer(
    visit_id: UUID,
    payload: AnswerPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    # The database decides, not the session: a confirmation whose session update was lost
    # must not reopen the answers of a patient who is already in the queue.
    _require_registering(context)
    if payload.skip or payload.answer is None:
        raise _error(
            422, "VALIDATION_FAILED", "Answer not accepted", "Enter an answer to continue."
        )
    _validate_answer_size(payload.answer)
    async with _session_lock(request, hospital_id, visit_id):
        return await _apply_correction(
            request, hospital_id=hospital_id, visit_id=visit_id, payload=payload
        )


async def _apply_correction(
    request: Request, *, hospital_id: UUID, visit_id: UUID, payload: AnswerPayload
) -> dict[str, Any]:
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
    # A new main problem on the review screen can need questions not yet asked.
    reopen = state.get("phase") == "summary" and next_question(updated) is not None
    alerts: list[dict[str, Any]] = []
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        turns = InterviewTurnRepository(connection, request.app.state.settings)
        await turns.add(
            hospital_id=hospital_id,
            visit_id=visit_id,
            # From the stored turns, not the session: a retry must never reuse a number.
            turn_index=await turns.next_correction_index(
                hospital_id=hospital_id, visit_id=visit_id, base=_CORRECTION_TURN_BASE
            ),
            question_id=payload.question_id,
            input_mode=payload.input_mode,
            text_original=str(payload.answer),
        )
        await _sync_pregnancy(
            connection, hospital_id=hospital_id, visit_id=visit_id, interview_state=updated
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
        if reopen:
            await IntakeRepository(connection, request.app.state.settings).set_visit_status(
                hospital_id=hospital_id, visit_id=visit_id, status="registering"
            )
            state["phase"] = "interview"
        elif state.get("phase") == "summary":
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
        await _schedule_case_sheet_enrichment(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            interview_state=updated,
        )
        return {
            "complete": True,
            "case_sheet": data,
            "readback": _readback(data, updated),
            "answered": _answered(updated),
        }
    return await _question_response(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )


@router.post("/sessions/{visit_id}/interview/back")
async def go_back_one_question(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Undo the last answer and ask that question again, with the old answer filled in.

    Answers after it are not affected; going back to the main problem re-plans the
    symptom questions once it is answered again.
    """
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=True
    )
    _require_registering(context)
    async with _session_lock(request, hospital_id, visit_id):
        return await _undo_last_answer(request, hospital_id=hospital_id, visit_id=visit_id)


async def _undo_last_answer(
    request: Request, *, hospital_id: UUID, visit_id: UUID
) -> dict[str, Any]:
    key = _session_key(hospital_id, visit_id)
    state = _read_state(await request.app.state.redis_state.get(key))
    if state.get("phase") != "interview":
        raise _error(
            409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Return to the current screen."
        )
    previous = InterviewState.model_validate(state["interview"])
    try:
        updated, undone = go_back(previous)
    except InterviewValidationError as exc:
        raise _error(409, "NOTHING_TO_UNDO", "Nothing to go back to", str(exc)) from exc
    undone_id = previous.asked[-1]
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _sync_pregnancy(
            connection, hospital_id=hospital_id, visit_id=visit_id, interview_state=updated
        )
    state["interview"] = updated.model_dump(mode="json")
    state.pop("ai_selected_question", None)
    if undone_id in _FOLLOWUP_BASIS and updated.ai_followups == "none":
        await _forget_followups(request, hospital_id=hospital_id, visit_id=visit_id, state=state)
    await request.app.state.redis_state.set(
        key,
        json.dumps(state, separators=(",", ":")),
        ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
    )
    current = next_question(updated)
    if current is None or current.id != undone_id:
        return await _question_response(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )
    return {
        "complete": False,
        "question": _question_payload(updated, undone_id),
        "progress": {
            "current": len(updated.asked) + 1,
            "total": max(question_total(updated), 10 if not updated.asked else 0),
        },
        "answered": _answered(updated),
        "draft": (
            None if undone is None else question_for(updated, undone_id).localized(
                undone, updated.language
            )
        ),
    }


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
    # The read-back is the patient's own words: an online voice service needs their consent.
    if getattr(request.app.state.tts_provider, "name", "mock") != "mock" and not (
        await _ai_consented(request, hospital_id, visit_id)
    ):
        raise _error(
            503,
            "TTS_UNAVAILABLE",
            "Read-back audio unavailable",
            "Read-back text remains available.",
        )
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        saved = await CaseSheetRepository(connection, request.app.state.settings).get(
            hospital_id=hospital_id, visit_id=visit_id
        )
        if saved is None:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            )
        copy = _readback(
            saved["data"],
            InterviewState.model_validate(state["interview"]) if state.get("interview") else None,
        )
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
    content = pad_with_silence(clip.content) if clip.media_type == "audio/wav" else clip.content
    return Response(
        content=content,
        media_type=clip.media_type,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )


@router.post("/sessions/{visit_id}/help")
async def request_staff_help(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, bool]:
    """The patient pressed "Call staff": raise an acknowledgeable alert on the staff board.

    Each press re-opens the visit's one help alert and rings the board, so presses are limited
    per visit (a script holding a phone session cannot keep the board ringing), and a visit
    that has finished cannot call staff any more.
    """
    hospital_id, context = await _authorized_visit(
        request, visit_id, principal, consent_required=False
    )
    if context.get("status") in {"done", "no_show"}:
        raise _error(
            409, "SESSION_COMPLETE", "Visit finished", "Please ask at the registration desk."
        )
    settings = request.app.state.settings
    await _rate_limit(
        request,
        key=f"osa:{settings.app_env}:rl:patient-help:{visit_id}",
        limit=_HELP_REQUESTS_PER_VISIT,
        window_seconds=_HELP_WINDOW_SECONDS,
        detail="Staff have been asked to come to you. Please wait, or go to the desk.",
    )
    try:
        raw_state = await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
        channel = json.loads(raw_state).get("channel") if raw_state else None
    except (RedisError, ValueError, AttributeError):
        channel = None
    # Staff need to know where to look: at the kiosk, or for someone holding a phone.
    evidence = (
        "Patient asked for help from their own phone."
        if channel == "mobile"
        else "Patient pressed Call staff on the kiosk."
    )
    event_id = uuid7()
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        visit = (
            (
                await connection.execute(
                    text(
                        "SELECT department_id, token_label FROM visits "
                        "WHERE hospital_id = :hospital_id AND id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if visit is None:
            raise _error(404, "NOT_FOUND", "Session not found", "Please ask staff for help.")
        inserted = await connection.scalar(
            text(
                "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, level, "
                "evidence) VALUES (:id, :hospital_id, :visit_id, 'tile', 'PATIENT_HELP', "
                "'priority', :evidence) "
                "ON CONFLICT (hospital_id, visit_id, rule_id) WHERE rule_id IS NOT NULL "
                "DO UPDATE SET acknowledged_at = NULL, acknowledged_by = NULL, created_at = now(), "
                "evidence = EXCLUDED.evidence RETURNING id"
            ),
            {
                "id": event_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "evidence": evidence,
            },
        )
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
            action="intake.patient_called_staff",
            resource_type="visit",
            resource_id=visit_id,
        )
    await _publish_alerts(
        request.app.state.redis_state,
        app_env=request.app.state.settings.app_env,
        hospital_id=hospital_id,
        alerts=[
            {
                "type": "alert.urgent",
                "id": str(inserted or event_id),
                "visit_id": str(visit_id),
                "rule_id": "PATIENT_HELP",
                "level": "priority",
                "reason_en": "Patient asked for help",
                "evidence": evidence,
                "token_label": visit["token_label"],
                "department_id": str(visit["department_id"]),
            }
        ],
    )
    return {"requested": True}


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


class _AlreadyConfirmedError(Exception):
    """Another request confirmed this visit while this one was running."""


async def _queue_receipt(
    request: Request,
    *,
    hospital_id: UUID,
    visit_id: UUID,
    state: dict[str, Any],
    token_label: str,
    department_id: UUID,
    visit_status: str,
    status_token: str,
    opd_card: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The patient's token receipt, also kept in the session so a repeat request gets it."""
    settings = request.app.state.settings
    position: int | None = None
    if visit_status == "queued":
        try:
            position = await QueueRedis(
                request.app.state.redis_state, app_env=settings.app_env
            ).position(hospital_id=hospital_id, department_id=department_id, visit_id=visit_id)
        except RedisError:
            position = None
        if position is None:
            async with request.app.state.engine.begin() as connection:
                await set_hospital_scope(connection, hospital_id)
                rows = await QueueRepository(connection, settings).queued_visits(
                    hospital_id=hospital_id, department_id=department_id
                )
            now = datetime.now(UTC)
            rows.sort(
                key=lambda item: (
                    queue_score(
                        int((item["queued_at"] or now).timestamp() * 1000),
                        item["urgency"],
                        item["age_years"],
                        bool(item["pregnant"]),
                        urgent_bonus_min=settings.urgent_bonus_min,
                        priority_bonus_min=settings.priority_bonus_min,
                        vulnerable_bonus_min=settings.vulnerable_bonus_min,
                        acuity_score=item.get("acuity_score"),
                    ),
                    str(item["visit_id"]),
                )
            )
            position = next(
                (index + 1 for index, item in enumerate(rows) if item["visit_id"] == visit_id),
                None,
            )
    language = str(state.get("language", "en"))
    department_name = None
    try:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            department = (
                (
                    await connection.execute(
                        text(
                            "SELECT code, name_en, name_hi, name_mr FROM departments "
                            "WHERE hospital_id = :hospital_id AND id = :department_id"
                        ),
                        {"hospital_id": hospital_id, "department_id": department_id},
                    )
                )
                .mappings()
                .first()
            )
        if department is not None:
            department_name = department.get(f"name_{language}") or department["name_en"]
    except Exception:
        department_name = None
    consult_minutes = settings.default_consult_min
    public_web_url = settings.public_web_url.strip().rstrip("/")
    receipt = {
        "confirmed": True,
        "status": visit_status,
        "token": token_label,
        "status_token": status_token,
        "status_url": f"/s/{status_token}",
        # The QR code needs an address a patient's phone can open (not localhost).
        "public_status_url": f"{public_web_url}/s/{status_token}" if public_web_url else None,
        "position": position,
        "department_name": department_name,
        "wait_minutes": round(max(position - 1, 0) * consult_minutes) if position else None,
        # The OPD card (registration number and PIN) to bring to the next visit.
        "opd_card": opd_card,
    }
    # The session keeps only what ending it or calling staff needs. The answers, the status
    # link and the card PIN leave Redis: the database holds them, and a repeated confirmation
    # rebuilds this receipt from it.
    confirmed = {key: state[key] for key in _CONFIRMED_SESSION_KEYS if key in state}
    confirmed["phase"] = "confirmed"
    try:
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(confirmed, separators=(",", ":")),
            ex=settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
        await request.app.state.redis_state.delete(_followup_key(hospital_id, visit_id))
    except RedisError:
        pass  # The database holds the confirmation; a repeat request rebuilds this receipt.
    return receipt


async def _confirmed_receipt(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> dict[str, Any] | None:
    """The receipt of a visit that already has a token, rebuilt from the database."""
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT v.status, v.token_label, v.status_token_hash, v.department_id, "
                        "v.patient_id, p.mrn, p.card_version FROM visits v JOIN patients p "
                        "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id "
                        "AND v.token_number IS NOT NULL AND v.status_token_hash IS NOT NULL"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    status_token = create_status_token(
        key=request.app.state.settings.app_hmac_key_bytes,
        hospital_id=hospital_id,
        visit_id=visit_id,
    )
    if status_token_digest(status_token) != row["status_token_hash"]:
        return None  # Issued under another key, so the link cannot be rebuilt.
    return await _queue_receipt(
        request,
        hospital_id=hospital_id,
        visit_id=visit_id,
        state=state,
        token_label=str(row["token_label"]),
        department_id=row["department_id"],
        visit_status=str(row["status"]),
        status_token=status_token,
        opd_card=(
            _opd_card(request, hospital_id, row["patient_id"], str(row["mrn"]), row["card_version"])
            if row["mrn"]
            else None
        ),
    )


def _opd_card(
    request: Request, hospital_id: UUID, patient_id: UUID, number: str, version: int
) -> dict[str, str]:
    return {
        "number": number,
        "pin": card_pin(
            key=request.app.state.settings.app_hmac_key_bytes,
            hospital_id=hospital_id,
            patient_id=patient_id,
            version=int(version),
        ),
    }


@router.post("/sessions/{visit_id}/confirm")
async def confirm_summary(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Issue the visit's token once; repeated or simultaneous requests get that same token."""
    hospital_id, _ = await _authorized_visit(request, visit_id, principal, consent_required=True)
    state = _read_state(
        await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
    )
    # The database, not the session, records a confirmation: a repeated request, or one whose
    # earlier attempt committed while its session update was lost, gets the token it issued.
    existing = await _confirmed_receipt(
        request, hospital_id=hospital_id, visit_id=visit_id, state=state
    )
    if existing is not None:
        return existing
    if state.get("phase") != "summary":
        raise _error(409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first.")
    try:
        return await _confirm_new_summary(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )
    except _AlreadyConfirmedError:
        # A simultaneous request issued the token first and this one was rolled back.
        existing = await _confirmed_receipt(
            request, hospital_id=hospital_id, visit_id=visit_id, state=state
        )
        if existing is None:
            raise _error(
                409, "SUMMARY_NOT_READY", "Summary not ready", "Complete the questions first."
            ) from None
        return existing


async def _confirm_new_summary(
    request: Request, *, hospital_id: UUID, visit_id: UUID, state: dict[str, Any]
) -> dict[str, Any]:
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
            use_llm=True,
        )
        # The AI summary may have been saved while the checks above ran; build on the
        # latest version so confirmation never overwrites it.
        saved = (
            await repository.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
            or saved
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
            # Leaving the block rolls back this request's writes; the caller answers with
            # the token the first request issued.
            raise _AlreadyConfirmedError
        queue_record = await QueueRepository(connection, request.app.state.settings).confirm_visit(
            redis=request.app.state.redis_state,
            hospital_id=hospital_id,
            visit_id=visit_id,
            status_hash=status_token_digest(status_token),
        )
        # A first visit becomes a registered patient with an OPD card; a returning patient
        # (joined by card or by staff) keeps their number. The visit's current patient is
        # read here because a card link moves the visit off its placeholder.
        patient_id = await connection.scalar(
            text("SELECT patient_id FROM visits WHERE hospital_id = :hospital_id AND id = :id"),
            {"hospital_id": hospital_id, "id": visit_id},
        )
        mrn, card_version = await PatientRepository(
            connection, request.app.state.settings
        ).assign_mrn(
            hospital_id=hospital_id,
            patient_id=patient_id,
            year=queue_record["service_date"].year,
        )
        opd_card = _opd_card(request, hospital_id, patient_id, mrn, card_version)
        details = (
            (
                await connection.execute(
                    text(
                        "SELECT urgency, urgency_reasons, age_years, pregnant, queued_at, "
                        "acuity_score FROM visits "
                        "WHERE hospital_id = :hospital_id AND id = :visit_id"
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
        acuity_score=details["acuity_score"],
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
    queue_response = await _queue_receipt(
        request,
        hospital_id=hospital_id,
        visit_id=visit_id,
        state=state,
        token_label=queue_record["token_label"],
        department_id=queue_record["department_id"],
        visit_status="queued",
        status_token=status_token,
        opd_card=opd_card,
    )
    if getattr(request.app.state.llm_provider, "name", "mock") == "gemini" and (
        await _llm_allowed(request, hospital_id, visit_id)
    ):
        _schedule_triage_review(
            request, hospital_id=hospital_id, visit_id=visit_id, interview_state=interview_state
        )
    return queue_response


@router.post("/offline-submissions", status_code=status.HTTP_201_CREATED)
async def replay_offline_submission(
    payload: OfflineSubmissionPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_kiosk())],
) -> dict[str, Any]:
    """Replay one encrypted local kiosk submission through the regular intake engine."""
    hospital_id, kiosk_id = _identity(principal)
    if request.headers.get("Idempotency-Key") != payload.idempotency_key:
        raise _error(
            400,
            "VALIDATION_FAILED",
            "Idempotency key mismatch",
            "The request header and offline submission key must match.",
        )
    if not payload.consent_granted:
        raise _error(
            422,
            "CONSENT_REQUIRED",
            "Consent required",
            "The patient must agree before the offline form can be submitted.",
        )
    consent_copy = get_consent_copy(payload.language)
    if (
        payload.consent_version != consent_copy["version"]
        or payload.consent_sha256 != consent_copy["sha256"]
    ):
        raise _error(
            409,
            "CONSENT_COPY_MISMATCH",
            "Consent copy changed",
            "The offline notice is out of date. Please ask clinic staff to help.",
        )
    body = payload.model_dump()

    async def operation() -> dict[str, Any]:
        session = await create_session(
            CreateSessionPayload(language=payload.language), request, principal
        )
        visit_id = UUID(session["visit_id"])
        patient_principal = {
            "hospital_id": str(hospital_id),
            "sub": str(visit_id),
            "role": "patient",
        }
        consent = consent_copy
        await record_consent(
            visit_id,
            ConsentPayload(version=consent["version"], sha256=consent["sha256"], granted=True),
            request,
            patient_principal,
        )
        await save_identity(
            visit_id,
            IdentityPayload(age=payload.age, sex=payload.sex),
            request,
            patient_principal,
        )
        await save_danger_signs(
            visit_id,
            DangerSignsPayload(tile_ids=payload.danger_tile_ids),
            request,
            patient_principal,
        )

        state = _read_state(
            await request.app.state.redis_state.get(_session_key(hospital_id, visit_id))
        )
        interview_state = InterviewState.model_validate(state["interview"])
        # The short form asks these safety-relevant history fields even when the
        # complaint would not otherwise activate their adaptive modules.
        queued = list(interview_state.queued)
        for question_id in ("past_history", "medications", "allergies"):
            if question_id not in queued:
                queued.append(question_id)
        interview_state = interview_state.model_copy(update={"queued": queued})
        supplied: dict[str, str | int] = {
            "chief_concern": payload.chief_complaint,
            "duration": payload.duration,
            "severity": payload.severity,
            "past_history": payload.conditions,
            "medications": payload.medications,
            "allergies": payload.allergies,
        }
        turns: list[dict[str, Any]] = []
        try:
            while (question := next_question(interview_state)) is not None:
                value = supplied.get(question.id)
                skip = value is None or (isinstance(value, str) and not value.strip())
                if not skip and question.id == "severity" and isinstance(value, int):
                    answer: str | int | None = value
                else:
                    answer = None if skip else str(value)
                interview_state = apply_answer(interview_state, question.id, answer, skip=skip)
                turns.append(
                    {
                        "question_id": question.id,
                        "answer": None if skip else str(answer),
                    }
                )
        except InterviewValidationError as exc:
            raise _error(
                422, "VALIDATION_FAILED", "Offline answers not accepted", str(exc)
            ) from exc

        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repository = InterviewTurnRepository(connection, request.app.state.settings)
            for turn_index, turn in enumerate(turns):
                await repository.add(
                    hospital_id=hospital_id,
                    visit_id=visit_id,
                    turn_index=turn_index,
                    question_id=turn["question_id"],
                    input_mode="touch",
                    text_original=turn["answer"],
                )
        state["interview"] = interview_state.model_dump(mode="json")
        await request.app.state.redis_state.set(
            _session_key(hospital_id, visit_id),
            json.dumps(state, separators=(",", ":")),
            ex=request.app.state.settings.patient_session_hours * _SESSION_TTL_FACTOR,
        )
        await _finish_interview(
            request,
            hospital_id=hospital_id,
            visit_id=visit_id,
            state_data=state,
            interview_state=interview_state,
        )
        result = await confirm_summary(visit_id, request, patient_principal)
        return {
            **result,
            "visit_id": str(visit_id),
            "provisional_token": f"L-{payload.idempotency_key[-6:].upper()}",
        }

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=kiosk_id,
        body=body,
        operation=operation,
    )


@router.delete("/sessions/{visit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_session(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_role("patient", "kiosk"))],
) -> Response:
    """End a check-in that did not finish: the patient quit, or left the kiosk idle.

    A visit that already has a token is not cancelled here. The patient is in the queue, and
    only staff take a patient out of it (with the queue's own timeline entry), so a kiosk reset
    or a stray request after confirmation never costs a patient their place.
    """
    hospital_id, subject_id = _identity(principal)
    role = principal.get("role")
    key = _session_key(hospital_id, visit_id)
    if role == "patient":
        # Who is asking is checked first, so a refusal says nothing about other visits.
        await _authorized_visit(request, visit_id, principal, consent_required=False)
    elif role == "kiosk":
        # A kiosk ends only a session it started, as its session state records.
        raw = await request.app.state.redis_state.get(key)
        if raw is None or _read_state(raw).get("kiosk_id") != str(subject_id):
            raise _error(
                404, "NOT_FOUND", "Session not found", "This intake session is unavailable."
            )
    else:
        raise _error(403, "FORBIDDEN", "Permission denied", "This session cannot be ended here.")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        previous = await IntakeRepository(connection, request.app.state.settings).set_visit_status(
            hospital_id=hospital_id, visit_id=visit_id, status="cancelled"
        )
        if previous is not None:
            await QueueRepository(connection, request.app.state.settings).record_event(
                hospital_id=hospital_id,
                visit_id=visit_id,
                from_status=previous,
                to_status="cancelled",
                actor_type="patient",
                reason="session_ended",
            )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="patient" if role == "patient" else "kiosk",
                actor_id=visit_id if role == "patient" else subject_id,
                action="intake.session.cancelled",
                resource_type="visit",
                resource_id=visit_id,
                meta={"from": previous},
            )
    if previous is None:
        raise _error(
            409,
            "SESSION_COMPLETE",
            "Already checked in",
            "This visit already has a token. Please ask at the registration desk to cancel it.",
        )
    await request.app.state.redis_state.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
