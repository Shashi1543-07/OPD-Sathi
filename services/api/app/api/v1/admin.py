"""Audited, tenant-scoped administrator CRUD for core reference data."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.v1.account import list_sessions
from app.config import Settings
from app.core.audit import audit
from app.core.auth import end_staff_sessions, require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.passwords import password_problem
from app.core.security import create_opaque_secret, digest_secret, hash_password
from app.db.repositories import FEATURE_DEFAULTS, AdminRepository
from app.db.session import set_hospital_scope
from app.domain.checkin import checkin_code
from app.domain.queue.state import DEFAULT_CALL_GRACE_SECONDS

router = APIRouter(prefix="/admin", tags=["admin"])
_admin_principal = require_role("admin")
AdminResource = Literal["departments", "users", "kiosks"]
_ALREADY_EXISTS = {
    "departments": "A department with this code or token prefix already exists.",
    "users": "A staff account with this email already exists.",
    "kiosks": "This kiosk already exists.",
}


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DepartmentCreate(Payload):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_]+$")
    token_prefix: str = Field(min_length=1, max_length=8, pattern=r"^[A-Z0-9]+$")
    name_en: str = Field(min_length=1, max_length=200)
    name_hi: str = Field(min_length=1, max_length=200)
    name_mr: str = Field(min_length=1, max_length=200)


class UserCreate(Payload):
    role: Literal["admin", "doctor", "nurse"]
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=1024)
    department_id: UUID | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or any(character.isspace() for character in normalized):
            raise ValueError("email must be valid")
        return normalized


class KioskCreate(Payload):
    label: str = Field(min_length=1, max_length=100)
    # "device": a kiosk screen paired with a one-time activation code.
    # "mobile": a phone check-in QR that patients scan to answer on their own phone.
    kind: Literal["device", "mobile"] = "device"


class DepartmentPatch(Payload):
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_hi: str | None = Field(default=None, min_length=1, max_length=200)
    name_mr: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None

    @field_validator("name_en", "name_hi", "name_mr", "is_active", mode="before")
    @classmethod
    def reject_null_required_updates(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("this field cannot be null")
        return value


class UserPatch(Payload):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    department_id: UUID | None = None
    is_active: bool | None = None

    @field_validator("name", "email", "is_active", mode="before")
    @classmethod
    def reject_null_required_updates(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("this field cannot be null")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if "@" not in normalized or any(character.isspace() for character in normalized):
            raise ValueError("email must be valid")
        return normalized


class KioskPatch(Payload):
    label: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @field_validator("label", "is_active", mode="before")
    @classmethod
    def reject_null_required_updates(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("this field cannot be null")
        return value


class AISettingsPatch(Payload):
    llm_enabled: bool


class QueueSettingsPatch(Payload):
    # Seconds after a call before staff may mark the patient absent (0 to 15 minutes).
    call_grace_seconds: int = Field(ge=0, le=900)


class InterviewSettingsPatch(Payload):
    """The switches to change; a switch left out keeps its value."""

    # Ask the six patient-reportable Ayurveda history questions in every interview.
    ayurveda_module: bool | None = None
    # Ask the one-screen review of systems (other body systems) in every interview.
    review_of_systems_module: bool | None = None

    @model_validator(mode="after")
    def one_switch(self) -> "InterviewSettingsPatch":
        if self.ayurveda_module is None and self.review_of_systems_module is None:
            raise ValueError("Change at least one interview setting.")
        return self


_INTERVIEW_SWITCHES = ("ayurveda_module", "review_of_systems_module")


class FeatureSettingsPatch(Payload):
    """The switches to change; a switch left out keeps its value."""

    # Offer patients queue messages by SMS (each still needs the patient's own tick).
    sms_messages: bool | None = None
    # Let patients check in on their own phones from the hospital's check-in QR codes.
    phone_checkin: bool | None = None

    @model_validator(mode="after")
    def one_switch(self) -> "FeatureSettingsPatch":
        if self.sms_messages is None and self.phone_checkin is None:
            raise ValueError("Change at least one feature setting.")
        return self


_FEATURE_SWITCHES = ("sms_messages", "phone_checkin")


class PrivacySettingsPut(Payload):
    # How patients reach the grievance officer: a name, a phone number, an email, a room.
    grievance_contact: str = Field(max_length=500)


def _identity(principal: dict[str, Any]) -> tuple[UUID, UUID]:
    return UUID(principal["hospital_id"]), UUID(principal["sub"])


def _not_found() -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="NOT_FOUND",
        title="Not found",
        detail="The requested record was not found.",
    )


def _conflict(code: str, title: str, detail: str) -> ApiProblemError:
    return ApiProblemError(status=409, code=code, title=title, detail=detail)


def _already_exists(resource: str) -> ApiProblemError:
    return _conflict("ALREADY_EXISTS", "Already exists", _ALREADY_EXISTS[resource])


def _unknown_department() -> ApiProblemError:
    return ApiProblemError(
        status=422,
        code="VALIDATION_FAILED",
        title="Unknown department",
        detail="Choose a department that still exists.",
    )


def _activation_expiry(settings: Settings) -> datetime:
    return datetime.now(UTC) + timedelta(hours=settings.kiosk_activation_code_hours)


def _refuse_self(resource: str, resource_id: UUID, actor_id: UUID, action: str) -> None:
    """Administrators cannot lock themselves out, so a hospital always keeps one admin."""
    if resource == "users" and resource_id == actor_id:
        raise _conflict(
            "CANNOT_CHANGE_SELF",
            "Not allowed on your own account",
            f"You can't {action} your own account. Ask another administrator.",
        )


def _ai_settings(request: Request, saved: bool | None) -> dict[str, bool]:
    """The hospital's AI switch, whether it was ever chosen, and whether AI can run here.

    No saved choice means AI is on (as intake applies it) until an admin switches it off.
    ``available`` is false when the server has AI turned off or no AI provider configured;
    then the switch has no effect and every visit runs on this server alone.
    """
    provider = getattr(request.app.state.llm_provider, "name", "mock")
    return {
        "llm_enabled": saved is not False,
        "chosen": saved is not None,
        "available": bool(request.app.state.settings.llm_enabled) and provider != "mock",
    }


@router.get("/ai-settings")
async def get_ai_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, bool]:
    """The hospital's switch for online AI help (the patient's own consent is still needed)."""
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        saved = await connection.scalar(
            text("SELECT llm_enabled FROM hospital_ai_settings WHERE hospital_id = :hospital_id"),
            {"hospital_id": hospital_id},
        )
    return _ai_settings(request, saved)


@router.patch("/ai-settings")
async def patch_ai_settings(
    payload: AISettingsPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, bool]:
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await connection.execute(
            text(
                "INSERT INTO hospital_ai_settings (hospital_id, llm_enabled) "
                "VALUES (:hospital_id, :llm_enabled) ON CONFLICT (hospital_id) DO UPDATE "
                "SET llm_enabled = EXCLUDED.llm_enabled, updated_at = now()"
            ),
            {"hospital_id": hospital_id, "llm_enabled": payload.llm_enabled},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.ai_settings.updated",
            resource_type="hospital_ai_settings",
            resource_id=hospital_id,
            meta={"llm_enabled": payload.llm_enabled},
        )
    return _ai_settings(request, payload.llm_enabled)


async def _queue_settings(connection: Any, hospital_id: UUID) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT s.call_grace_seconds, s.updated_at, u.name AS updated_by_name "
                    "FROM hospital_queue_settings s LEFT JOIN users u "
                    "ON u.hospital_id = s.hospital_id AND u.id = s.updated_by "
                    "WHERE s.hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {
            "call_grace_seconds": DEFAULT_CALL_GRACE_SECONDS,
            "is_default": True,
            "updated_at": None,
            "updated_by_name": None,
        }
    return {
        "call_grace_seconds": int(row["call_grace_seconds"]),
        "is_default": False,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by_name": row["updated_by_name"],
    }


@router.get("/queue-settings")
async def get_queue_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """How long staff wait after calling a patient before they may mark them absent."""
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        return await _queue_settings(connection, hospital_id)


@router.patch("/queue-settings")
async def patch_queue_settings(
    payload: QueueSettingsPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        before = await _queue_settings(connection, hospital_id)
        await connection.execute(
            text(
                "INSERT INTO hospital_queue_settings (hospital_id, call_grace_seconds, "
                "updated_by) VALUES (:hospital_id, :grace, :actor_id) "
                "ON CONFLICT (hospital_id) DO UPDATE SET "
                "call_grace_seconds = EXCLUDED.call_grace_seconds, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {"hospital_id": hospital_id, "grace": payload.call_grace_seconds, "actor_id": actor_id},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.queue_settings.updated",
            resource_type="hospital_queue_settings",
            resource_id=hospital_id,
            meta={
                "call_grace_seconds": {
                    "before": before["call_grace_seconds"],
                    "after": payload.call_grace_seconds,
                }
            },
        )
        return await _queue_settings(connection, hospital_id)


async def _interview_settings(connection: Any, hospital_id: UUID) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT s.ayurveda_module, s.review_of_systems_module, s.updated_at, "
                    "u.name AS updated_by_name "
                    "FROM hospital_interview_settings s LEFT JOIN users u "
                    "ON u.hospital_id = s.hospital_id AND u.id = s.updated_by "
                    "WHERE s.hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return {
            "ayurveda_module": False,
            "review_of_systems_module": False,
            "updated_at": None,
            "updated_by_name": None,
        }
    return {
        "ayurveda_module": bool(row["ayurveda_module"]),
        "review_of_systems_module": bool(row["review_of_systems_module"]),
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by_name": row["updated_by_name"],
    }


@router.get("/interview-settings")
async def get_interview_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Optional interview modules for this hospital (review of systems, Ayurveda history)."""
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        return await _interview_settings(connection, hospital_id)


@router.patch("/interview-settings")
async def patch_interview_settings(
    payload: InterviewSettingsPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Turn optional modules on or off; changes apply to interviews that start afterwards."""
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        before = await _interview_settings(connection, hospital_id)
        after = {
            name: before[name] if getattr(payload, name) is None else getattr(payload, name)
            for name in _INTERVIEW_SWITCHES
        }
        await connection.execute(
            text(
                "INSERT INTO hospital_interview_settings (hospital_id, ayurveda_module, "
                "review_of_systems_module, updated_by) "
                "VALUES (:hospital_id, :ayurveda, :review_of_systems, :actor_id) "
                "ON CONFLICT (hospital_id) DO UPDATE SET "
                "ayurveda_module = EXCLUDED.ayurveda_module, "
                "review_of_systems_module = EXCLUDED.review_of_systems_module, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {
                "hospital_id": hospital_id,
                "ayurveda": after["ayurveda_module"],
                "review_of_systems": after["review_of_systems_module"],
                "actor_id": actor_id,
            },
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.interview_settings.updated",
            resource_type="hospital_interview_settings",
            resource_id=hospital_id,
            meta={
                name: {"before": before[name], "after": after[name]}
                for name in _INTERVIEW_SWITCHES
                if getattr(payload, name) is not None
            },
        )
        return await _interview_settings(connection, hospital_id)


async def _feature_settings(
    connection: Any, hospital_id: UUID, settings: Settings
) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT s.sms_messages, s.phone_checkin, s.updated_at, "
                    "u.name AS updated_by_name "
                    "FROM hospital_feature_settings s LEFT JOIN users u "
                    "ON u.hospital_id = s.hospital_id AND u.id = s.updated_by "
                    "WHERE s.hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
        )
        .mappings()
        .first()
    )
    switches = (
        dict(FEATURE_DEFAULTS)
        if row is None
        else {name: bool(row[name]) for name in _FEATURE_SWITCHES}
    )
    return {
        **switches,
        # Without a provider the server only writes messages to its log (NOTIFICATION_PROVIDER).
        "sms_provider": settings.notification_provider != "console",
        "updated_at": row["updated_at"].isoformat() if row and row["updated_at"] else None,
        "updated_by_name": row["updated_by_name"] if row else None,
    }


@router.get("/feature-settings")
async def get_feature_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Features this hospital switches on or off: SMS queue messages and phone check-in."""
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        return await _feature_settings(connection, hospital_id, request.app.state.settings)


@router.patch("/feature-settings")
async def patch_feature_settings(
    payload: FeatureSettingsPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Switch features on or off for the whole hospital; check-ins that start later follow."""
    hospital_id, actor_id = _identity(principal)
    settings: Settings = request.app.state.settings
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        before = await _feature_settings(connection, hospital_id, settings)
        after = {
            name: before[name] if getattr(payload, name) is None else getattr(payload, name)
            for name in _FEATURE_SWITCHES
        }
        await connection.execute(
            text(
                "INSERT INTO hospital_feature_settings (hospital_id, sms_messages, "
                "phone_checkin, updated_by) "
                "VALUES (:hospital_id, :sms_messages, :phone_checkin, :actor_id) "
                "ON CONFLICT (hospital_id) DO UPDATE SET "
                "sms_messages = EXCLUDED.sms_messages, "
                "phone_checkin = EXCLUDED.phone_checkin, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {"hospital_id": hospital_id, "actor_id": actor_id, **after},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.feature_settings.updated",
            resource_type="hospital_feature_settings",
            resource_id=hospital_id,
            meta={
                name: {"before": before[name], "after": after[name]}
                for name in _FEATURE_SWITCHES
                if getattr(payload, name) is not None
            },
        )
        return await _feature_settings(connection, hospital_id, settings)


async def _privacy_settings(
    connection: Any, hospital_id: UUID, settings: Settings
) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT s.grievance_contact, s.updated_at, u.name AS updated_by_name "
                    "FROM hospital_privacy_settings s LEFT JOIN users u "
                    "ON u.hospital_id = s.hospital_id AND u.id = s.updated_by "
                    "WHERE s.hospital_id = :hospital_id"
                ),
                {"hospital_id": hospital_id},
            )
        )
        .mappings()
        .first()
    )
    path = f"/privacy/{hospital_id}"
    public_web_url = settings.public_web_url.strip().rstrip("/")
    return {
        "grievance_contact": row["grievance_contact"] if row else "",
        "notice_path": path,
        # For the poster's QR; empty when the server does not know its own address.
        "notice_url": f"{public_web_url}{path}" if public_web_url else None,
        "updated_at": row["updated_at"].isoformat() if row and row["updated_at"] else None,
        "updated_by_name": row["updated_by_name"] if row else None,
    }


@router.get("/privacy-settings")
async def get_privacy_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """The grievance officer's contact shown with the privacy notice, and the notice's link."""
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        return await _privacy_settings(connection, hospital_id, request.app.state.settings)


@router.put("/privacy-settings")
async def put_privacy_settings(
    payload: PrivacySettingsPut,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Set how patients reach the grievance officer; the notice shows it at once."""
    hospital_id, actor_id = _identity(principal)
    contact = payload.grievance_contact.strip()
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await connection.execute(
            text(
                "INSERT INTO hospital_privacy_settings (hospital_id, grievance_contact, "
                "updated_by) VALUES (:hospital_id, :contact, :actor_id) "
                "ON CONFLICT (hospital_id) DO UPDATE SET "
                "grievance_contact = EXCLUDED.grievance_contact, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()"
            ),
            {"hospital_id": hospital_id, "contact": contact, "actor_id": actor_id},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.privacy_settings.updated",
            resource_type="hospital_privacy_settings",
            resource_id=hospital_id,
            meta={"grievance_contact_set": bool(contact)},
        )
        return await _privacy_settings(connection, hospital_id, request.app.state.settings)


@router.get("/{resource}")
async def list_resource(
    resource: AdminResource,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
) -> dict[str, Any]:
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=UUID(principal["sub"]),
            action=f"admin.{resource}.listed",
            resource_type=resource.rstrip("s"),
            meta={"limit": limit, "cursor": cursor is not None},
        )
        page = await AdminRepository(connection, request.app.state.settings).list(
            resource, hospital_id=hospital_id, limit=limit, cursor=cursor
        )
        return page.model_dump(mode="json")


async def _create(
    resource: str,
    payload: dict[str, Any],
    request: Request,
    principal: dict[str, Any],
) -> dict[str, Any]:
    hospital_id, actor_id = _identity(principal)
    settings: Settings = request.app.state.settings
    idempotency_body = dict(payload)
    device_kiosk = resource == "kiosks" and payload.get("kind", "device") == "device"
    activation_code = create_opaque_secret() if device_kiosk else None
    if resource == "users":
        problem = password_problem(
            payload["password"], personal=(payload["name"], payload["email"])
        )
        if problem:
            raise ApiProblemError(
                status=422,
                code="PASSWORD_TOO_WEAK",
                title="Choose a stronger password",
                detail=problem,
            )
        # The person chooses their own password at their first sign-in.
        payload = {
            **payload,
            "password_hash": hash_password(payload.pop("password")),
            "must_change_password": True,  # nosec B105 - a flag, not a password
        }
    if device_kiosk:
        payload = {
            **payload,
            "activation_code_hash": digest_secret(activation_code or ""),
            "activation_code_expires_at": _activation_expiry(settings),
        }

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            try:
                record = await AdminRepository(connection, settings).create(
                    resource, hospital_id=hospital_id, values=payload
                )
            except IntegrityError as exc:
                raise _already_exists(resource) from exc
            except LookupError as exc:
                raise _unknown_department() from exc
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action=f"admin.{resource}.created",
                resource_type=resource.rstrip("s"),
                resource_id=UUID(record["id"]),
            )
            if activation_code is not None:
                record["activation_code"] = activation_code
            return record

    response = await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body=idempotency_body,
        operation=operation,
        response_fields_once=frozenset({"activation_code"}),
    )
    return response


@router.post("/departments", status_code=201)
async def create_department(
    payload: DepartmentCreate,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _create("departments", payload.model_dump(), request, principal)


@router.post("/users", status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _create("users", payload.model_dump(), request, principal)


@router.post("/kiosks", status_code=201)
async def create_kiosk(
    payload: KioskCreate,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _create("kiosks", payload.model_dump(), request, principal)


async def _patch(
    resource: str,
    resource_id: UUID,
    payload: dict[str, Any],
    request: Request,
    principal: dict[str, Any],
) -> dict[str, Any]:
    if not payload:
        raise ApiProblemError(
            status=422,
            code="VALIDATION_FAILED",
            title="Empty update",
            detail="Provide at least one field to update.",
        )
    if resource == "users" and "email" in payload and payload["email"] is not None:
        payload["email"] = payload["email"].strip().lower()
    hospital_id, actor_id = _identity(principal)
    if payload.get("is_active") is False:
        _refuse_self(resource, resource_id, actor_id, "deactivate")

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            try:
                updated = await AdminRepository(connection, request.app.state.settings).patch(
                    resource,
                    hospital_id=hospital_id,
                    resource_id=resource_id,
                    values=payload,
                )
            except IntegrityError as exc:
                raise _already_exists(resource) from exc
            except LookupError as exc:
                raise _unknown_department() from exc
            if updated is None:
                raise _not_found()
            if resource == "users" and payload.get("is_active") is False:
                await _revoke_refresh_tokens(connection, hospital_id, resource_id)
                await end_staff_sessions(request, resource_id)
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action=f"admin.{resource}.updated",
                resource_type=resource.rstrip("s"),
                resource_id=resource_id,
                meta={"fields": sorted(payload)},
            )
            return updated

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body=payload,
        operation=operation,
    )


@router.patch("/departments/{resource_id}")
async def patch_department(
    resource_id: UUID,
    payload: DepartmentPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _patch(
        "departments", resource_id, payload.model_dump(exclude_unset=True), request, principal
    )


@router.patch("/users/{resource_id}")
async def patch_user(
    resource_id: UUID,
    payload: UserPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _patch(
        "users", resource_id, payload.model_dump(exclude_unset=True), request, principal
    )


@router.patch("/kiosks/{resource_id}")
async def patch_kiosk(
    resource_id: UUID,
    payload: KioskPatch,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    return await _patch(
        "kiosks", resource_id, payload.model_dump(exclude_unset=True), request, principal
    )


@router.post("/kiosks/{resource_id}/activation-code")
async def issue_kiosk_activation_code(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Issue a fresh one-time activation code for an existing kiosk.

    Codes are stored only as hashes, so an earlier code can never be shown again; instead
    an administrator can get a new one for the same kiosk whenever a device needs setting
    up. The kiosk keeps its id, label and registration history.
    """
    hospital_id, actor_id = _identity(principal)
    settings: Settings = request.app.state.settings
    activation_code = create_opaque_secret()
    expires_at = _activation_expiry(settings)

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            kiosk = await AdminRepository(connection, settings).issue_kiosk_code(
                hospital_id=hospital_id,
                kiosk_id=resource_id,
                activation_code_hash=digest_secret(activation_code),
                expires_at=expires_at,
            )
            if kiosk is None:
                kind = await connection.scalar(
                    text(
                        "SELECT kind FROM kiosks WHERE hospital_id = :hospital_id "
                        "AND id = :id AND deleted_at IS NULL"
                    ),
                    {"hospital_id": hospital_id, "id": resource_id},
                )
                if kind == "mobile":
                    raise _conflict(
                        "NOT_A_DEVICE_KIOSK",
                        "Phone check-in QR",
                        "A phone check-in QR has no device to set up. Show or replace its QR.",
                    )
                if kind is not None:
                    raise _conflict(
                        "KIOSK_INACTIVE",
                        "Kiosk is turned off",
                        "Activate this kiosk before setting up a device for it.",
                    )
                raise _not_found()
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action="admin.kiosks.activation_code_issued",
                resource_type="kiosk",
                resource_id=resource_id,
            )
            return {
                "id": str(resource_id),
                "label": kiosk["label"],
                "activation_code": activation_code,
                "activation_code_expires_at": expires_at.isoformat(),
            }

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={},
        operation=operation,
        response_fields_once=frozenset({"activation_code"}),
    )


def _checkin_link(
    settings: Settings, *, hospital_id: UUID, point: dict[str, Any], version: int
) -> dict[str, Any]:
    code = checkin_code(
        key=settings.app_hmac_key_bytes,
        hospital_id=hospital_id,
        point_id=point["id"],
        version=version,
    )
    path = f"/m/{code}"
    public_web_url = settings.public_web_url.strip().rstrip("/")
    return {
        "id": str(point["id"]),
        "label": point["label"],
        "is_active": point["is_active"],
        "version": version,
        "path": path,
        # Phones need the PC's Wi-Fi address; empty when start-demo.ps1 found no network.
        "url": f"{public_web_url}{path}" if public_web_url else None,
    }


@router.get("/kiosks/{resource_id}/checkin-link")
async def get_checkin_link(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """The phone check-in QR link for a check-in point; it can be shown again any time."""
    hospital_id, actor_id = _identity(principal)
    settings: Settings = request.app.state.settings
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        point = await AdminRepository(connection, settings).checkin_point(
            hospital_id=hospital_id, point_id=resource_id
        )
        if point is None:
            raise _not_found()
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.kiosks.checkin_link_viewed",
            resource_type="kiosk",
            resource_id=resource_id,
        )
    return _checkin_link(
        settings, hospital_id=hospital_id, point=point, version=int(point["checkin_code_version"])
    )


@router.post("/kiosks/{resource_id}/checkin-link")
async def replace_checkin_link(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Replace a check-in point's QR: posters with the previous code stop working at once."""
    hospital_id, actor_id = _identity(principal)
    settings: Settings = request.app.state.settings

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repository = AdminRepository(connection, settings)
            point = await repository.checkin_point(hospital_id=hospital_id, point_id=resource_id)
            if point is None:
                raise _not_found()
            version = await repository.rotate_checkin_code(
                hospital_id=hospital_id,
                point_id=resource_id,
                current_version=int(point["checkin_code_version"]),
            )
            if version is None:
                raise _conflict(
                    "CHECKIN_CODE_CHANGED",
                    "QR changed",
                    "Someone replaced this QR just now. Reload to see the new one.",
                )
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action="admin.kiosks.checkin_code_replaced",
                resource_type="kiosk",
                resource_id=resource_id,
                meta={"version": version},
            )
            return _checkin_link(settings, hospital_id=hospital_id, point=point, version=version)

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={},
        operation=operation,
    )


@router.delete("/{resource}/{resource_id}")
async def delete_resource(
    resource: AdminResource,
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Delete a department, staff account or kiosk for administrators.

    The record is archived, not erased: past visits, case sheets and the audit trail keep
    pointing at it. It disappears from administration, can no longer sign in or be chosen,
    and its email, code or token prefix can be used again. A department is only deleted
    once no patient visit in it is still open and no staff member is assigned to it.
    """
    hospital_id, actor_id = _identity(principal)
    _refuse_self(resource, resource_id, actor_id, "delete")

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            repository = AdminRepository(connection, request.app.state.settings)
            blockers = await repository.blockers(
                resource, hospital_id=hospital_id, resource_id=resource_id
            )
            if blockers:
                parts = []
                if blockers.get("open_visits"):
                    parts.append(f"{blockers['open_visits']} open patient visit(s)")
                if blockers.get("assigned_staff"):
                    parts.append(f"{blockers['assigned_staff']} staff member(s) assigned")
                raise _conflict(
                    "DEPARTMENT_IN_USE",
                    "Department still in use",
                    "This department has "
                    + " and ".join(parts)
                    + ". Finish those visits and move its staff to another department "
                    "first. To stop new patients right away, deactivate it instead.",
                )
            if not await repository.archive(
                resource, hospital_id=hospital_id, resource_id=resource_id
            ):
                raise _not_found()
            if resource == "users":
                await end_staff_sessions(request, resource_id)
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action=f"admin.{resource}.deleted",
                resource_type=resource.rstrip("s"),
                resource_id=resource_id,
            )
            return {"id": str(resource_id), "deleted": True}

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={},
        operation=operation,
    )


async def _revoke_refresh_tokens(connection: Any, hospital_id: UUID, user_id: UUID) -> int:
    result = await connection.execute(
        text(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE hospital_id = :hospital_id "
            "AND user_id = :user_id AND revoked_at IS NULL"
        ),
        {"hospital_id": hospital_id, "user_id": user_id},
    )
    return int(result.rowcount or 0)


async def _staff_account(connection: Any, hospital_id: UUID, user_id: UUID) -> dict[str, Any]:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT id, role, name, email, is_active, mfa_enabled_at FROM users "
                    "WHERE hospital_id = :hospital_id AND id = :user_id "
                    "AND deleted_at IS NULL FOR UPDATE"
                ),
                {"hospital_id": hospital_id, "user_id": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _not_found()
    return dict(row)


def _temporary_password() -> str:
    """Easy to read aloud and type once: four groups of four, no look-alike characters."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


@router.post("/users/{resource_id}/password-reset")
async def reset_staff_password(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Give a staff member a temporary password (shown once) for a forgotten one.

    They must choose their own at the next sign-in. Every session they have ends now, and a
    lockout is lifted. Administrators change their own password from their account page.
    """
    user_id = resource_id
    hospital_id, actor_id = _identity(principal)
    _refuse_self("users", user_id, actor_id, "reset the password of")
    temporary = _temporary_password()

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            account = await _staff_account(connection, hospital_id, user_id)
            if not account["is_active"]:
                raise _conflict(
                    "ACCOUNT_INACTIVE", "Account is turned off", "Turn the account on first."
                )
            await connection.execute(
                text(
                    "UPDATE users SET password_hash = :password_hash, "
                    "must_change_password = true, failed_logins = 0, locked_until = NULL "
                    "WHERE hospital_id = :hospital_id AND id = :user_id"
                ),
                {
                    "password_hash": hash_password(temporary),
                    "hospital_id": hospital_id,
                    "user_id": user_id,
                },
            )
            ended = await _revoke_refresh_tokens(connection, hospital_id, user_id)
            await end_staff_sessions(request, user_id)
            await audit(
                connection,
                request,
                hospital_id=hospital_id,
                actor_type="user",
                actor_id=actor_id,
                action="admin.users.password_reset",
                resource_type="user",
                resource_id=user_id,
                meta={"sessions_ended": ended},
            )
            return {"id": str(user_id), "temporary_password": temporary}

    return await IdempotencyStore(request.app.state.redis_state).execute(
        request,
        hospital_id=hospital_id,
        actor_id=actor_id,
        body={"user_id": str(user_id), "action": "password-reset"},
        operation=operation,
        response_fields_once=frozenset({"temporary_password"}),
    )


@router.post("/users/{resource_id}/unlock")
async def unlock_staff_account(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Lift a sign-in lockout before its 15 minutes are up."""
    user_id = resource_id
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _staff_account(connection, hospital_id, user_id)
        await connection.execute(
            text(
                "UPDATE users SET failed_logins = 0, locked_until = NULL "
                "WHERE hospital_id = :hospital_id AND id = :user_id"
            ),
            {"hospital_id": hospital_id, "user_id": user_id},
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.users.unlocked",
            resource_type="user",
            resource_id=user_id,
        )
    return {"id": str(user_id), "locked": False}


@router.post("/users/{resource_id}/mfa-reset")
async def reset_staff_mfa(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Remove a staff member's two-step sign-in (lost phone and recovery codes).

    Never one's own: one stolen administrator account cannot remove its second factor.
    The person sets it up again at the next sign-in if their role requires it.
    """
    user_id = resource_id
    hospital_id, actor_id = _identity(principal)
    _refuse_self("users", user_id, actor_id, "reset two-step sign-in of")
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        account = await _staff_account(connection, hospital_id, user_id)
        await connection.execute(
            text(
                "UPDATE users SET mfa_enabled_at = NULL, mfa_secret_enc = NULL, "
                "mfa_last_step = NULL, mfa_recovery_hashes = '[]'::jsonb "
                "WHERE hospital_id = :hospital_id AND id = :user_id"
            ),
            {"hospital_id": hospital_id, "user_id": user_id},
        )
        ended = await _revoke_refresh_tokens(connection, hospital_id, user_id)
        await end_staff_sessions(request, user_id)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.users.mfa_reset",
            resource_type="user",
            resource_id=user_id,
            meta={"was_enabled": account["mfa_enabled_at"] is not None, "sessions_ended": ended},
        )
    return {"id": str(user_id), "mfa_enabled": False}


@router.get("/users/{resource_id}/sessions")
async def staff_sessions(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    user_id = resource_id
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _staff_account(connection, hospital_id, user_id)
        items = await list_sessions(
            connection, hospital_id=hospital_id, user_id=user_id, current=None
        )
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.users.sessions_viewed",
            resource_type="user",
            resource_id=user_id,
        )
    return {"items": items}


@router.post("/users/{resource_id}/sessions/end")
async def end_staff_sessions_now(
    resource_id: UUID,
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, Any]:
    """Sign a staff member out everywhere, at once (for example a lost or shared device)."""
    user_id = resource_id
    hospital_id, actor_id = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _staff_account(connection, hospital_id, user_id)
        ended = await _revoke_refresh_tokens(connection, hospital_id, user_id)
        await end_staff_sessions(request, user_id)
        await audit(
            connection,
            request,
            hospital_id=hospital_id,
            actor_type="user",
            actor_id=actor_id,
            action="admin.users.sessions_ended",
            resource_type="user",
            resource_id=user_id,
            meta={"sessions_ended": ended},
        )
    return {"id": str(user_id), "ended": ended}
