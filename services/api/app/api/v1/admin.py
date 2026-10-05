"""Audited, tenant-scoped administrator CRUD for core reference data."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


def _identity(principal: dict[str, Any]) -> tuple[UUID, UUID]:
    return UUID(principal["hospital_id"]), UUID(principal["sub"])


def _not_found() -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="NOT_FOUND",
        title="Not found",
        detail="The requested record was not found.",
    )


@router.get("/{resource}")
async def list_resource(
    resource: Literal["departments", "users", "kiosks"],
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
        payload = {**payload, "activation_code_hash": digest_secret(activation_code or "")}

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            record = await AdminRepository(connection, settings).create(
                resource, hospital_id=hospital_id, values=payload
            )
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

    async def operation() -> dict[str, Any]:
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            updated = await AdminRepository(connection, request.app.state.settings).patch(
                resource,
                hospital_id=hospital_id,
                resource_id=resource_id,
                values=payload,
            )
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
