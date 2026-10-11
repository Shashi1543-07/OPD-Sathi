"""Audited, tenant-scoped administrator CRUD for core reference data."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.core.audit import audit
from app.core.auth import require_role
from app.core.errors import ApiProblemError
from app.core.idempotency import IdempotencyStore
from app.core.security import create_opaque_secret, digest_secret, hash_password
from app.db.repositories import AdminRepository
from app.db.session import set_hospital_scope

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


@router.get("/ai-settings")
async def get_ai_settings(
    request: Request,
    principal: Annotated[dict[str, Any], Depends(_admin_principal)],
) -> dict[str, bool]:
    hospital_id, _ = _identity(principal)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        enabled = await connection.scalar(
            text("SELECT llm_enabled FROM hospital_ai_settings WHERE hospital_id = :hospital_id"),
            {"hospital_id": hospital_id},
        )
    # No saved choice means AI is on (as intake applies it) until an admin switches it off.
    return {"llm_enabled": enabled is not False}


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
    return {"llm_enabled": payload.llm_enabled}


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
    activation_code = create_opaque_secret() if resource == "kiosks" else None
    if resource == "users":
        payload = {**payload, "password_hash": hash_password(payload.pop("password"))}
    if resource == "kiosks":
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
                exists = await connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM kiosks WHERE hospital_id = :hospital_id "
                        "AND id = :id AND deleted_at IS NULL)"
                    ),
                    {"hospital_id": hospital_id, "id": resource_id},
                )
                if exists:
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
