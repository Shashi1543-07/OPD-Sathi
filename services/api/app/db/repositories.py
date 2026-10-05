"""Tenant-aware async repositories; endpoint modules should not contain SQL."""

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, column, select, table, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.core.crypto import decrypt_field, encrypt_field, lookup_digest
from app.core.ids import uuid7
from app.core.pagination import CursorPosition, Page, decode_cursor, encode_cursor


async def _list_page(
    connection: AsyncConnection,
    *,
    table_name: str,
    hospital_id: UUID,
    limit: int,
    cursor: str | None,
    cursor_key: bytes,
    columns: tuple[str, ...],
) -> Page[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    relation = table(table_name, *[column(name) for name in (*columns, "hospital_id")])
    statement = (
        select(*(relation.c[name] for name in columns))
        .where(relation.c.hospital_id == bindparam("hospital_id"))
        .order_by(relation.c.created_at.asc(), relation.c.id.asc())
        .limit(bindparam("fetch_limit"))
    )
    query_params: dict[str, Any] = {"hospital_id": hospital_id, "fetch_limit": limit + 1}
    if cursor:
        position = decode_cursor(cursor, key=cursor_key)
        statement = statement.where(
            tuple_(relation.c.created_at, relation.c.id)
            > tuple_(bindparam("cursor_created_at"), bindparam("cursor_id"))
        )
        query_params.update(
            cursor_created_at=position.created_at,
            cursor_id=position.id,
        )
    result = await connection.execute(statement, query_params)
    rows = [dict(row) for row in result.mappings().all()]
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = encode_cursor(
            CursorPosition(created_at=last["created_at"], id=last["id"]), key=cursor_key
        )
    return Page(items=visible, next_cursor=next_cursor, limit=limit)


class PatientRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def create(
        self,
        *,
        hospital_id: UUID,
        name: str,
        phone: str | None,
        abha: str | None = None,
        year_of_birth: int | None = None,
        sex: str = "unknown",
        preferred_language: str = "en",
    ) -> UUID:
        patient_id = uuid7()
        values: dict[str, Any] = {
            "id": patient_id,
            "hospital_id": hospital_id,
            "name_enc": encrypt_field(
                name,
                key=self.settings.app_data_key_bytes,
                associated_data=f"patients:name_enc:{patient_id}",
            ),
            "phone_enc": None,
            "phone_hmac": None,
            "abha_enc": None,
            "year_of_birth": year_of_birth,
            "sex": sex,
            "preferred_language": preferred_language,
        }
        if phone:
            normalized_phone = phone.strip()
            values["phone_enc"] = encrypt_field(
                normalized_phone,
                key=self.settings.app_data_key_bytes,
                associated_data=f"patients:phone_enc:{patient_id}",
            )
            values["phone_hmac"] = lookup_digest(
                normalized_phone, key=self.settings.app_hmac_key_bytes
            )
        if abha:
            values["abha_enc"] = encrypt_field(
                abha,
                key=self.settings.app_data_key_bytes,
                associated_data=f"patients:abha_enc:{patient_id}",
            )
        await self.connection.execute(
            text(
                "INSERT INTO patients (id, hospital_id, name_enc, phone_enc, phone_hmac, "
                "abha_enc, year_of_birth, sex, preferred_language) VALUES "
                "(:id, :hospital_id, :name_enc, :phone_enc, :phone_hmac, :abha_enc, "
                ":year_of_birth, :sex, :preferred_language)"
            ),
            values,
        )
        return patient_id

    async def get(self, *, hospital_id: UUID, patient_id: UUID) -> dict[str, Any] | None:
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, name_enc, phone_enc, abha_enc, year_of_birth, sex, "
                        "preferred_language, created_at FROM patients "
                        "WHERE hospital_id = :hospital_id AND id = :id"
                    ),
                    {"hospital_id": hospital_id, "id": patient_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        patient = dict(row)
        for encrypted_column in ("name_enc", "phone_enc", "abha_enc"):
            encrypted = patient.pop(encrypted_column)
            patient[encrypted_column.removesuffix("_enc")] = (
                decrypt_field(
                    encrypted,
                    key=self.settings.app_data_key_bytes,
                    associated_data=f"patients:{encrypted_column}:{patient_id}",
                )
                if encrypted
                else None
            )
        return patient

    async def list(
        self, *, hospital_id: UUID, limit: int, cursor: str | None
    ) -> Page[dict[str, Any]]:
        page = await _list_page(
            self.connection,
            table_name="patients",
            hospital_id=hospital_id,
            limit=limit,
            cursor=cursor,
            cursor_key=self.settings.cursor_hmac_key.encode(),
            columns=(
                "id",
                "name_enc",
                "phone_enc",
                "year_of_birth",
                "sex",
                "preferred_language",
                "created_at",
            ),
        )
        for patient in page.items:
            patient_id = patient["id"]
            for encrypted_column in ("name_enc", "phone_enc"):
                encrypted = patient.pop(encrypted_column)
                patient[encrypted_column.removesuffix("_enc")] = (
                    decrypt_field(
                        encrypted,
                        key=self.settings.app_data_key_bytes,
                        associated_data=f"patients:{encrypted_column}:{patient_id}",
                    )
                    if encrypted
                    else None
                )
        return page


class VisitRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def create(
        self,
        *,
        hospital_id: UUID,
        patient_id: UUID,
        department_id: UUID,
        service_date: date,
        status: str = "registering",
        urgency: str = "routine",
        language: str = "en",
    ) -> UUID:
        visit_id = uuid7()
        now = datetime.now(UTC)
        await self.connection.execute(
            text(
                "INSERT INTO visits (id, hospital_id, patient_id, department_id, service_date, "
                "status, urgency, language, registered_at) VALUES (:id, :hospital_id, "
                ":patient_id, :department_id, :service_date, :status, :urgency, :language, :now)"
            ),
            {
                "id": visit_id,
                "hospital_id": hospital_id,
                "patient_id": patient_id,
                "department_id": department_id,
                "service_date": service_date,
                "status": status,
                "urgency": urgency,
                "language": language,
                "now": now,
            },
        )
        return visit_id

    async def list(
        self, *, hospital_id: UUID, limit: int, cursor: str | None
    ) -> Page[dict[str, Any]]:
        return await _list_page(
            self.connection,
            table_name="visits",
            hospital_id=hospital_id,
            limit=limit,
            cursor=cursor,
            cursor_key=self.settings.cursor_hmac_key.encode(),
            columns=(
                "id",
                "patient_id",
                "department_id",
                "status",
                "urgency",
                "service_date",
                "created_at",
            ),
        )


class CaseSheetRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def save(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        data: dict[str, Any],
        editor_id: UUID | None = None,
    ) -> UUID:
        row_id = uuid7()
        await self.connection.execute(
            text(
                "INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data, "
                "last_edited_by) VALUES (:id, :hospital_id, :visit_id, :schema_version, "
                "CAST(:data AS jsonb), :editor_id) ON CONFLICT (hospital_id, visit_id) DO UPDATE "
                "SET data = EXCLUDED.data, schema_version = EXCLUDED.schema_version, "
                "version = case_sheets.version + 1, last_edited_by = EXCLUDED.last_edited_by"
            ),
            {
                "id": row_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "schema_version": str(data["schema_version"]),
                "data": json.dumps(data, separators=(",", ":")),
                "editor_id": editor_id,
            },
        )
        result = await self.connection.scalar(
            text(
                "SELECT id FROM case_sheets WHERE hospital_id = :hospital_id "
                "AND visit_id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if not isinstance(result, UUID):
            raise RuntimeError("saved case sheet was not found in the current tenant")
        return result


class InterviewTurnRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def add(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        turn_index: int,
        question_id: str,
        input_mode: str,
        text_original: str | None = None,
        text_english: str | None = None,
        stt_provider: str | None = None,
        stt_confidence: float | None = None,
    ) -> UUID:
        turn_id = uuid7()
        await self.connection.execute(
            text(
                "INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, question_id, "
                "input_mode, text_original, text_english, stt_provider, stt_confidence) VALUES "
                "(:id, :hospital_id, :visit_id, :turn_index, :question_id, :input_mode, "
                ":text_original, :text_english, :stt_provider, :stt_confidence)"
            ),
            {
                "id": turn_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "turn_index": turn_index,
                "question_id": question_id,
                "input_mode": input_mode,
                "text_original": text_original,
                "text_english": text_english,
                "stt_provider": stt_provider,
                "stt_confidence": stt_confidence,
            },
        )
        return turn_id


class DepartmentRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def list(
        self, *, hospital_id: UUID, limit: int, cursor: str | None
    ) -> Page[dict[str, Any]]:
        return await _list_page(
            self.connection,
            table_name="departments",
            hospital_id=hospital_id,
            limit=limit,
            cursor=cursor,
            cursor_key=self.settings.cursor_hmac_key.encode(),
            columns=(
                "id",
                "code",
                "token_prefix",
                "name_en",
                "name_hi",
                "name_mr",
                "is_active",
                "created_at",
            ),
        )


class AdminRepository:
    """Small allow-listed CRUD surface for administrator-managed records."""

    _LISTS: dict[str, tuple[str, ...]] = {
        "departments": (
            "id",
            "code",
            "token_prefix",
            "name_en",
            "name_hi",
            "name_mr",
            "is_active",
            "created_at",
        ),
        "users": ("id", "role", "name", "email", "department_id", "is_active", "created_at"),
        "kiosks": ("id", "label", "is_active", "last_seen_at", "created_at"),
    }

    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def list(
        self, resource: str, *, hospital_id: UUID, limit: int, cursor: str | None
    ) -> Page[dict[str, Any]]:
        if resource not in self._LISTS:
            raise ValueError("unsupported admin resource")
        return await _list_page(
            self.connection,
            table_name=resource,
            hospital_id=hospital_id,
            limit=limit,
            cursor=cursor,
            cursor_key=self.settings.cursor_hmac_key.encode(),
            columns=self._LISTS[resource],
        )

    async def create(
        self, resource: str, *, hospital_id: UUID, values: dict[str, Any]
    ) -> dict[str, Any]:
        resource_id = uuid7()
        if resource == "departments":
            await self.connection.execute(
                text(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES (:id, :hospital_id, :code, :token_prefix, "
                    ":name_en, :name_hi, :name_mr)"
                ),
                {"id": resource_id, "hospital_id": hospital_id, **values},
            )
            return {"id": str(resource_id), **values, "is_active": True}
        if resource == "users":
            await self.connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash, "
                    "department_id) VALUES (:id, :hospital_id, :role, :name, :email, "
                    ":password_hash, :department_id)"
                ),
                {"id": resource_id, "hospital_id": hospital_id, **values},
            )
            return {
                "id": str(resource_id),
                "role": values["role"],
                "name": values["name"],
                "email": values["email"],
                "department_id": values["department_id"],
                "is_active": True,
            }
        if resource == "kiosks":
            await self.connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, :label, :activation_code_hash)"
                ),
                {"id": resource_id, "hospital_id": hospital_id, **values},
            )
            return {"id": str(resource_id), "label": values["label"], "is_active": True}
        raise ValueError("unsupported admin resource")

    async def patch(
        self,
        resource: str,
        *,
        hospital_id: UUID,
        resource_id: UUID,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        if resource not in self._LISTS or not values:
            raise ValueError("unsupported or empty admin update")
        allowed = {
            "departments": {"name_en", "name_hi", "name_mr", "is_active"},
            "users": {"name", "email", "department_id", "is_active"},
            "kiosks": {"label", "is_active"},
        }[resource]
        if set(values) - allowed:
            raise ValueError("unsupported update field")
        relation = table(
            resource,
            *[column(name) for name in (*self._LISTS[resource], "hospital_id")],
        )
        result = await self.connection.execute(
            update(relation)
            .where(
                relation.c.hospital_id == bindparam("tenant_hospital_id"),
                relation.c.id == bindparam("resource_id"),
            )
            .values(**values)
            .returning(*(relation.c[name] for name in self._LISTS[resource])),
            {
                **values,
                "tenant_hospital_id": hospital_id,
                "resource_id": resource_id,
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None
