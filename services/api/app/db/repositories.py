"""Tenant-aware async repositories; endpoint modules should not contain SQL."""

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy import bindparam, column, func, select, table, text, tuple_, update
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


class QueueRepository:
    """Tenant-scoped durable queue writes and daily token allocation."""

    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def allocate_daily_token_number(
        self,
        *,
        redis: Any,
        hospital_id: UUID,
        department_id: UUID,
        service_date: date,
    ) -> int:
        """Allocate a collision-free daily token; PostgreSQL serializes fallback and repair."""
        from app.domain.queue.tokens import daily_counter_key

        lock_key = f"{hospital_id}:{department_id}:{service_date.isoformat()}"
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        maximum = await self.connection.scalar(
            text(
                "SELECT coalesce(max(token_number), 0) FROM visits "
                "WHERE hospital_id = :hospital_id AND department_id = :department_id "
                "AND service_date = :service_date"
            ),
            {
                "hospital_id": hospital_id,
                "department_id": department_id,
                "service_date": service_date,
            },
        )
        minimum = int(maximum or 0) + 1
        key = daily_counter_key(self.settings.app_env, hospital_id, department_id, service_date)
        try:
            value = await redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "local floor=tonumber(ARGV[1]); "
                "if n < floor then n=floor; redis.call('SET',KEYS[1],n) end; "
                "if redis.call('TTL',KEYS[1]) < 0 then "
                "redis.call('EXPIRE',KEYS[1],tonumber(ARGV[2])) end; return n",
                1,
                key,
                minimum,
                36 * 60 * 60,
            )
            return int(value)
        except RedisError:
            # A Redis outage must not stop registration; the transaction lock protects MAX+1.
            return minimum

    async def confirm_visit(
        self,
        *,
        redis: Any,
        hospital_id: UUID,
        visit_id: UUID,
        status_hash: str,
    ) -> dict[str, Any]:
        """Assign the daily token and persist a queue event in the caller's transaction."""
        from app.domain.queue.tokens import token_label

        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT v.department_id, v.service_date, v.status, v.token_number, "
                        "v.token_label, v.status_token_hash, d.token_prefix FROM visits v "
                        "JOIN departments d ON d.hospital_id = v.hospital_id "
                        "AND d.id = v.department_id WHERE v.hospital_id = :hospital_id "
                        "AND v.id = :visit_id "
                        "FOR UPDATE OF v"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LookupError("visit not found")
        row_data: dict[str, Any] = dict(row)
        if row_data["token_number"] is not None and row_data["status_token_hash"] is not None:
            return {
                "department_id": row_data["department_id"],
                "service_date": row_data["service_date"],
                "token_number": row_data["token_number"],
                "token_label": row_data["token_label"],
            }
        if row_data["status"] != "awaiting_confirmation":
            raise ValueError("visit is not awaiting confirmation")
        number = await self.allocate_daily_token_number(
            redis=redis,
            hospital_id=hospital_id,
            department_id=row_data["department_id"],
            service_date=row_data["service_date"],
        )
        label = token_label(row_data["token_prefix"], number)
        await self.connection.execute(
            text(
                "UPDATE visits SET status = 'queued', token_number = :number, "
                "token_label = :label, "
                "status_token_hash = :status_hash, queued_at = now() "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {
                "number": number,
                "label": label,
                "status_hash": status_hash,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
            },
        )
        await self.connection.execute(
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
                        "department_id": str(row_data["department_id"]),
                    },
                    separators=(",", ":"),
                ),
            },
        )
        return {
            "department_id": row_data["department_id"],
            "service_date": row_data["service_date"],
            "token_number": number,
            "token_label": label,
        }

    async def queued_visits(
        self, *, hospital_id: UUID, department_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        visits = table(
            "visits",
            *[
                column(name)
                for name in (
                    "id",
                    "hospital_id",
                    "department_id",
                    "status",
                    "urgency",
                    "urgency_reasons",
                    "age_years",
                    "pregnant",
                    "token_label",
                    "token_number",
                    "queued_at",
                    "called_at",
                    "consult_started_at",
                    "consult_ended_at",
                    "assigned_doctor_id",
                )
            ],
        )
        case_sheets = table(
            "case_sheets", column("hospital_id"), column("visit_id"), column("data")
        )
        statement = (
            select(
                visits.c.id.label("visit_id"),
                visits.c.department_id,
                visits.c.status,
                visits.c.urgency,
                visits.c.urgency_reasons,
                visits.c.age_years,
                visits.c.pregnant,
                visits.c.token_label,
                visits.c.token_number,
                visits.c.queued_at,
                visits.c.called_at,
                visits.c.consult_started_at,
                visits.c.consult_ended_at,
                visits.c.assigned_doctor_id,
                case_sheets.c.data.label("case_sheet_data"),
            )
            .outerjoin(
                case_sheets,
                (case_sheets.c.hospital_id == visits.c.hospital_id)
                & (case_sheets.c.visit_id == visits.c.id),
            )
            .where(
                visits.c.hospital_id == hospital_id,
                visits.c.status.in_(("queued", "called", "in_consult")),
            )
            .order_by(visits.c.queued_at, visits.c.id)
        )
        if department_id is not None:
            statement = statement.where(visits.c.department_id == department_id)
        result = await self.connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]

    async def queue_estimate_inputs(
        self, *, hospital_id: UUID, department_id: UUID
    ) -> tuple[int, list[float]]:
        doctors = await self.connection.scalar(
            text(
                "SELECT count(*) FROM users WHERE hospital_id = :hospital_id AND role = 'doctor' "
                "AND is_active = true AND department_id = :department_id"
            ),
            {"hospital_id": hospital_id, "department_id": department_id},
        )
        result = await self.connection.execute(
            text(
                "SELECT extract(epoch FROM (consult_ended_at - consult_started_at)) / 60.0 "
                "FROM visits WHERE hospital_id = :hospital_id AND department_id = :department_id "
                "AND status = 'done' AND consult_ended_at IS NOT NULL "
                "AND consult_started_at IS NOT NULL ORDER BY consult_ended_at DESC LIMIT 20"
            ),
            {"hospital_id": hospital_id, "department_id": department_id},
        )
        return int(doctors or 0), [float(value) for value in result.scalars().all()]

    async def visit_status_by_hash(self, status_hash: str) -> dict[str, Any] | None:
        result = await self.connection.execute(
            text(
                "SELECT id AS visit_id, hospital_id, department_id, status, urgency, token_label, "
                "service_date, queued_at FROM visits WHERE status_token_hash = :status_hash LIMIT 1"
            ),
            {"status_hash": status_hash},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def transition_visit(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        target: str,
        actor_id: UUID,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Transition a visit and write its outbox event atomically."""
        from app.domain.queue.state import require_transition

        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT department_id, status, urgency, age_years, pregnant, token_label, "
                        "queued_at FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id "
                        "FOR UPDATE"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LookupError("visit not found")
        require_transition(row["status"], target)
        visits = table(
            "visits",
            column("hospital_id"),
            column("id"),
            column("status"),
            column("called_at"),
            column("consult_started_at"),
            column("consult_ended_at"),
            column("assigned_doctor_id"),
        )
        values: dict[str, Any] = {"status": target}
        timestamp_column = {
            "called": "called_at",
            "in_consult": "consult_started_at",
            "done": "consult_ended_at",
        }.get(target)
        if timestamp_column is not None:
            values[timestamp_column] = func.now()
        if target == "in_consult":
            values["assigned_doctor_id"] = actor_id
        await self.connection.execute(
            update(visits)
            .where(visits.c.hospital_id == hospital_id, visits.c.id == visit_id)
            .values(**values)
        )
        if target in {"cancelled", "no_show", "done"}:
            event = {
                "op": "remove",
                "visit_id": str(visit_id),
                "department_id": str(row["department_id"]),
            }
        else:
            event = {
                "op": "upsert",
                "visit_id": str(visit_id),
                "department_id": str(row["department_id"]),
            }
        await self.connection.execute(
            text(
                "INSERT INTO outbox (id, hospital_id, topic, payload) VALUES "
                "(:id, :hospital_id, 'queue.changed', CAST(:payload AS jsonb))"
            ),
            {"id": uuid7(), "hospital_id": hospital_id, "payload": json.dumps(event)},
        )
        return {**dict(row), "visit_id": visit_id, "status": target, "override_reason": reason}


class CaseSheetRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def save(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        data: dict[str, Any],
        editor_id: UUID | None = None,
    ) -> UUID:
        row_id = uuid7()
        encrypted_data = encrypt_field(
            json.dumps(data, separators=(",", ":")),
            key=self.settings.app_data_key_bytes,
            associated_data=f"case_sheets:data:{visit_id}",
        )
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
                "data": json.dumps({"ciphertext": encrypted_data}),
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

    async def get(self, *, hospital_id: UUID, visit_id: UUID) -> dict[str, Any] | None:
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, data FROM case_sheets "
                        "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        stored = row["data"]
        ciphertext = stored.get("ciphertext") if isinstance(stored, dict) else None
        if not isinstance(ciphertext, str):
            raise ValueError("case sheet ciphertext is missing")
        plaintext = decrypt_field(
            ciphertext,
            key=self.settings.app_data_key_bytes,
            associated_data=f"case_sheets:data:{visit_id}",
        )
        return {"id": row["id"], "data": json.loads(plaintext)}

    async def confirm(self, *, hospital_id: UUID, visit_id: UUID) -> bool:
        result = await self.connection.execute(
            text(
                "UPDATE case_sheets SET confirmed_at = now() "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return result.rowcount == 1


class InterviewTurnRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

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
        encrypted_original = (
            encrypt_field(
                text_original,
                key=self.settings.app_data_key_bytes,
                associated_data=f"interview_turns:text_original:{turn_id}",
            )
            if text_original is not None
            else None
        )
        encrypted_english = (
            encrypt_field(
                text_english,
                key=self.settings.app_data_key_bytes,
                associated_data=f"interview_turns:text_english:{turn_id}",
            )
            if text_english is not None
            else None
        )
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
                "text_original": encrypted_original,
                "text_english": encrypted_english,
                "stt_provider": stt_provider,
                "stt_confidence": stt_confidence,
            },
        )
        return turn_id


class IntakeRepository:
    """Tenant-scoped persistence for patient intake sessions and consent."""

    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def kiosk_is_active(self, *, hospital_id: UUID, kiosk_id: UUID) -> bool:
        result = await self.connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM kiosks WHERE hospital_id = :hospital_id "
                "AND id = :kiosk_id AND is_active = true AND secret_hash IS NOT NULL)"
            ),
            {"hospital_id": hospital_id, "kiosk_id": kiosk_id},
        )
        return bool(result)

    async def create_session(
        self, *, hospital_id: UUID, language: str, kiosk_id: UUID
    ) -> tuple[UUID, UUID]:
        if not await self.kiosk_is_active(hospital_id=hospital_id, kiosk_id=kiosk_id):
            raise LookupError("kiosk is inactive")
        department_id = await self.connection.scalar(
            text(
                "SELECT id FROM departments WHERE hospital_id = :hospital_id AND is_active = true "
                "ORDER BY CASE WHEN code = 'GENMED' THEN 0 ELSE 1 END, id LIMIT 1"
            ),
            {"hospital_id": hospital_id},
        )
        if not isinstance(department_id, UUID):
            raise LookupError("no active intake department is configured")
        patient_id = await PatientRepository(self.connection, self.settings).create(
            hospital_id=hospital_id,
            name="Kiosk visitor",
            phone=None,
            preferred_language=language,
        )
        visit_id = await VisitRepository(self.connection, self.settings).create(
            hospital_id=hospital_id,
            patient_id=patient_id,
            department_id=department_id,
            service_date=datetime.now(ZoneInfo("Asia/Kolkata")).date(),
            language=language,
        )
        return visit_id, patient_id

    async def session_context(self, *, hospital_id: UUID, visit_id: UUID) -> dict[str, Any] | None:
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT v.id, v.patient_id, v.language, v.status, v.age_years, "
                        "v.pregnant, p.sex, "
                        "c.granted AS consent_granted FROM visits v LEFT JOIN consents c "
                        "ON c.hospital_id = v.hospital_id AND c.visit_id = v.id "
                        "JOIN patients p ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                        "WHERE v.hospital_id = :hospital_id AND v.id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def record_consent(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        language: str,
        version: str,
        text_sha256: str,
        granted: bool,
    ) -> None:
        existing = await self.connection.scalar(
            text(
                "SELECT id FROM consents WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if existing is not None:
            raise FileExistsError("consent has already been recorded")
        await self.connection.execute(
            text(
                "INSERT INTO consents (id, hospital_id, visit_id, language, text_version, "
                "text_sha256, granted, granted_at, channel) VALUES (:id, :hospital_id, :visit_id, "
                ":language, :version, :text_sha256, :granted, :granted_at, 'kiosk')"
            ),
            {
                "id": uuid7(),
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "language": language,
                "version": version,
                "text_sha256": text_sha256,
                "granted": granted,
                "granted_at": datetime.now(UTC) if granted else None,
            },
        )

    async def update_identity(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        patient_id: UUID,
        age: int,
        sex: str,
        phone: str | None,
    ) -> None:
        phone_enc = (
            encrypt_field(
                phone,
                key=self.settings.app_data_key_bytes,
                associated_data=f"patients:phone_enc:{patient_id}",
            )
            if phone
            else None
        )
        phone_hmac = lookup_digest(phone, key=self.settings.app_hmac_key_bytes) if phone else None
        current_year = datetime.now(ZoneInfo("Asia/Kolkata")).year
        await self.connection.execute(
            text(
                "UPDATE patients SET phone_enc = :phone_enc, phone_hmac = :phone_hmac, "
                "year_of_birth = :year_of_birth, sex = :sex, preferred_language = "
                "(SELECT language FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id) "
                "WHERE hospital_id = :hospital_id AND id = :patient_id"
            ),
            {
                "phone_enc": phone_enc,
                "phone_hmac": phone_hmac,
                "year_of_birth": current_year - age,
                "sex": sex,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "patient_id": patient_id,
            },
        )
        await self.connection.execute(
            text(
                "UPDATE visits SET age_years = :age "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {"age": age, "hospital_id": hospital_id, "visit_id": visit_id},
        )

    async def record_danger_tiles(
        self, *, hospital_id: UUID, visit_id: UUID, tile_ids: list[str]
    ) -> None:
        previous = set(
            (
                await self.connection.execute(
                    text(
                        "SELECT rule_id FROM red_flag_events WHERE hospital_id = :hospital_id "
                        "AND visit_id = :visit_id AND source = 'tile'"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .scalars()
            .all()
        )
        for tile_id in tile_ids:
            if tile_id in previous:
                continue
            await self.connection.execute(
                text(
                    "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, "
                    "level, evidence) VALUES (:id, :hospital_id, :visit_id, 'tile', :tile_id, "
                    "'urgent', 'Patient selected a kiosk help tile.')"
                ),
                {
                    "id": uuid7(),
                    "hospital_id": hospital_id,
                    "visit_id": visit_id,
                    "tile_id": tile_id,
                },
            )
            previous.add(tile_id)
        if previous:
            await self.connection.execute(
                text(
                    "UPDATE visits SET urgency = 'urgent', "
                    "urgency_reasons = CAST(:reasons AS jsonb) "
                    "WHERE hospital_id = :hospital_id AND id = :visit_id"
                ),
                {
                    "reasons": json.dumps(sorted(previous)),
                    "hospital_id": hospital_id,
                    "visit_id": visit_id,
                },
            )

    async def set_visit_status(self, *, hospital_id: UUID, visit_id: UUID, status: str) -> None:
        await self.connection.execute(
            text(
                "UPDATE visits SET status = :status "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {"status": status, "hospital_id": hospital_id, "visit_id": visit_id},
        )


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
