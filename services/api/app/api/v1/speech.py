"""Patient-session AI helpers: transcribe an answer, speak prompts, read a prescription."""

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.ai.prescription import read_prescription
from app.ai.providers.llm.base import LLMUnavailableError
from app.ai.providers.llm.gemini import GeminiProvider
from app.ai.readback import build_readback, build_readback_from_answers
from app.ai.speech import SpeechService, SpeechUnavailableError
from app.core.auth import require_patient
from app.core.errors import ApiProblemError
from app.core.ratelimit import check_rate_limit
from app.db.repositories import CaseSheetRepository
from app.db.session import set_hospital_scope
from app.domain.interview.bank import QUESTIONS, Question
from app.domain.interview.consent import get_consent_copy
from app.domain.interview.engine import InterviewState

router = APIRouter(prefix="/intake", tags=["speech"])
_MAX_AUDIO_BYTES = 2 * 1024 * 1024
_AUDIO_TYPES = ("audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg")


def _error(status: int, code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=status, code=code, title=title, detail=detail)


def _speech(request: Request) -> SpeechService:
    return request.app.state.speech  # type: ignore[no-any-return]


async def _session_state(
    request: Request, visit_id: UUID, principal: dict[str, Any]
) -> tuple[UUID, dict[str, Any]]:
    if principal.get("role") != "patient" or principal.get("sub") != str(visit_id):
        raise _error(404, "NOT_FOUND", "Session not found", "This intake session is unavailable.")
    hospital_id = UUID(str(principal["hospital_id"]))
    raw = await request.app.state.redis_state.get(f"osa:intake:{hospital_id}:{visit_id}")
    try:
        state = json.loads(raw) if raw else None
    except (TypeError, json.JSONDecodeError):
        state = None
    if not isinstance(state, dict) or state.get("visit_id") != str(visit_id):
        raise _error(410, "SESSION_EXPIRED", "Session expired", "Please ask staff for help.")
    return hospital_id, state


def _session_question(state: dict[str, Any], question_id: str) -> Question | None:
    """A bank question or one of this session's AI follow-up questions."""
    if question_id in QUESTIONS:
        return QUESTIONS[question_id]
    interview = state.get("interview")
    if not isinstance(interview, dict):
        return None
    return InterviewState.model_validate(interview).extra_questions.get(question_id)


@router.get("/capabilities")
async def speech_capabilities(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Tell the kiosk which speech features the server provides for this deployment."""
    del principal
    speech = _speech(request)
    return {
        "stt": speech.stt_mode,
        "tts": speech.tts_mode,
        "ai": getattr(request.app.state.llm_provider, "name", "mock") == "gemini",
    }


@router.post("/sessions/{visit_id}/transcribe")
async def transcribe_answer(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
    question_id: str = "",
) -> dict[str, Any]:
    """Transcribe one recorded answer; the audio is processed in memory and never stored."""
    hospital_id, state = await _session_state(request, visit_id, principal)
    if state.get("phase") not in {"interview", "summary"}:
        raise _error(409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Finish the step.")
    speech = _speech(request)
    if not speech.server_stt:
        raise _error(503, "STT_UNAVAILABLE", "Voice unavailable", "Please type or tap.")
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith(_AUDIO_TYPES):
        raise _error(415, "UNSUPPORTED_AUDIO", "Unsupported audio", "Record the answer again.")
    audio = await request.body()
    if not audio or len(audio) > _MAX_AUDIO_BYTES:
        raise _error(413, "AUDIO_TOO_LARGE", "Recording too long", "Please give a shorter answer.")
    settings = request.app.state.settings
    limit = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{settings.app_env}:rl:stt-clip:{visit_id}",
        limit=40,
        window_seconds=600,
        fail_closed=False,
    )
    if limit is not None and not limit.allowed:
        raise _error(429, "RATE_LIMITED", "Too many recordings", "Please type your answer.")
    language = str(state.get("language", "en"))
    question = _session_question(state, question_id)
    try:
        result = await speech.transcribe(
            audio,
            mime_type=content_type,
            language=language,
            context=question.prompt[language] if question else "",  # type: ignore[index]
            session_id=str(visit_id),
        )
    except SpeechUnavailableError as exc:
        raise _error(503, "STT_UNAVAILABLE", "Voice unavailable", "Please type or tap.") from exc
    del hospital_id
    return {"text": result.text[:500], "provider": result.provider}


class SpeakPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["question", "consent", "readback"]
    question_id: str = Field(default="", max_length=80)


@router.post("/sessions/{visit_id}/speak")
async def speak_prompt(
    visit_id: UUID,
    payload: SpeakPayload,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> Response:
    """Speak approved text only: bank questions, the consent notice, or this read-back."""
    hospital_id, state = await _session_state(request, visit_id, principal)
    speech = _speech(request)
    if not speech.server_tts:
        raise _error(503, "TTS_UNAVAILABLE", "Audio unavailable", "Text remains on screen.")
    language = str(state.get("language", "en"))
    cache = request.app.state.redis_cache
    if payload.kind == "question":
        question = _session_question(state, payload.question_id)
        if question is None:
            raise _error(404, "NOT_FOUND", "Unknown question", "Question not found.")
        spoken = question.prompt[language]  # type: ignore[index]
    elif payload.kind == "consent":
        spoken = str(get_consent_copy(language)["text"])  # type: ignore[arg-type]
    else:
        # Patient-specific read-back is generated on demand and never cached.
        cache = None
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            saved = await CaseSheetRepository(connection, request.app.state.settings).get(
                hospital_id=hospital_id, visit_id=visit_id
            )
            status = await connection.scalar(
                text("SELECT status FROM visits WHERE hospital_id = :h AND id = :v"),
                {"h": hospital_id, "v": visit_id},
            )
        if saved is None or status == "cancelled":
            raise _error(409, "SUMMARY_NOT_READY", "Summary not ready", "Finish the questions.")
        interview = state.get("interview")
        interview_state = (
            InterviewState.model_validate(interview) if isinstance(interview, dict) else None
        )
        spoken = (
            build_readback_from_answers(
                language, interview_state.answers, questions=interview_state.extra_questions
            )
            if interview_state is not None and interview_state.answers
            else build_readback(saved["data"])
        )
    try:
        clip = await speech.synthesize(spoken, language=language, cache=cache)
    except SpeechUnavailableError as exc:
        raise _error(
            503, "TTS_UNAVAILABLE", "Audio unavailable", "Text remains on screen."
        ) from exc
    return Response(
        content=clip.content,
        media_type=clip.media_type,
        headers={"Cache-Control": "private, no-store"},
    )


_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
# The kiosk sends a ~1280 px JPEG (about 200 kB); the API refuses any request over 1 MiB.
MAX_IMAGE_BYTES = 1_000_000


def _looks_like(content_type: str, image: bytes) -> bool:
    """Whether the file starts with the signature of the image type it claims to be."""
    if content_type == "image/jpeg":
        return image.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return image.startswith(b"\x89PNG\r\n\x1a\n")
    return image[:4] == b"RIFF" and image[8:12] == b"WEBP"


@router.post("/sessions/{visit_id}/prescription-photo")
async def read_prescription_photo(
    visit_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(require_patient())],
) -> dict[str, Any]:
    """Read medicine names from an old prescription photo; the image is never stored.

    Only a JPEG, PNG or WebP photo whose contents match its declared type is read, so a PDF
    or any other file renamed as a photo is refused before it reaches the model.
    """
    _, state = await _session_state(request, visit_id, principal)
    if state.get("phase") not in {"interview", "summary"}:
        raise _error(409, "FLOW_OUT_OF_ORDER", "Please continue in order", "Finish the step.")
    content_type = request.headers.get("content-type", "").lower().split(";")[0].strip()
    if content_type not in _IMAGE_TYPES:
        raise _error(
            415, "UNSUPPORTED_IMAGE", "Unsupported photo", "Take a photo of the prescription."
        )
    image = await request.body()
    if not image:
        raise _error(400, "EMPTY_IMAGE", "No photo", "Take the photo again.")
    if len(image) > MAX_IMAGE_BYTES:
        raise _error(413, "IMAGE_TOO_LARGE", "Photo too large", "Take the photo again.")
    if not _looks_like(content_type, image):
        raise _error(
            415, "UNSUPPORTED_IMAGE", "Not a photo", "This file is not a photo. Take a photo."
        )
    provider = request.app.state.llm_provider
    if not isinstance(provider, GeminiProvider):
        raise _error(503, "AI_UNAVAILABLE", "Scanning unavailable", "Please say or type it.")
    settings = request.app.state.settings
    limit = await check_rate_limit(
        request.app.state.redis_state,
        request.app.state.memory_limiter,
        key=f"osa:{settings.app_env}:rl:rx-photo:{visit_id}",
        limit=5,
        window_seconds=600,
        fail_closed=False,
    )
    if limit is not None and not limit.allowed:
        raise _error(429, "RATE_LIMITED", "Too many photos", "Please say or type it.")
    try:
        medicines = await read_prescription(
            provider, image, mime_type=content_type, session_id=str(visit_id)
        )
    except LLMUnavailableError as exc:
        raise _error(503, "AI_UNAVAILABLE", "Could not read", "Please say or type it.") from exc
    return {"medicines": medicines}
