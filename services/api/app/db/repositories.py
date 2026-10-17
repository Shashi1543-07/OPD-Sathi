"""Tenant-aware async repositories; endpoint modules should not contain SQL."""

import json
from datetime import UTC, date, datetime
from math import ceil
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from redis.exceptions import RedisError
from sqlalchemy import bindparam, column, exists, func, select, table, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import Settings
from app.core.crypto import (
    decrypt_bytes,
    decrypt_field,
    encrypt_bytes,
    encrypt_field,
    lookup_digest,
)
from app.core.ids import uuid7
from app.core.pagination import CursorPosition, Page, decode_cursor, encode_cursor
from app.db.session import tenant_transaction
from app.domain.checkin import checkin_code, checkin_code_digest
from app.domain.patients import format_mrn


async def _list_page(
    connection: AsyncConnection,
    *,
    table_name: str,
    hospital_id: UUID,
    limit: int,
    cursor: str | None,
    cursor_key: bytes,
    columns: tuple[str, ...],
    exclude_archived: bool = False,
) -> Page[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    extra = ("hospital_id", "deleted_at") if exclude_archived else ("hospital_id",)
    relation = table(table_name, *[column(name) for name in (*columns, *extra)])
    statement = (
        select(*(relation.c[name] for name in columns))
        .where(relation.c.hospital_id == bindparam("hospital_id"))
        .order_by(relation.c.created_at.asc(), relation.c.id.asc())
        .limit(bindparam("fetch_limit"))
    )
    if exclude_archived:
        statement = statement.where(relation.c.deleted_at.is_(None))
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


class KioskUnavailableError(LookupError):
    """A kiosk may not start a check-in; ``reason`` says why.

    "unpaired": deleted, never set up, or replaced by another device (set it up again);
    "off": turned off by an administrator (still paired).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"kiosk is {reason}")
        self.reason = reason


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

    async def assign_mrn(
        self, *, hospital_id: UUID, patient_id: UUID, year: int
    ) -> tuple[str, int]:
        """The patient's registration number and card version, numbering them on first use."""
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT mrn, card_version FROM patients "
                        "WHERE hospital_id = :hospital_id AND id = :patient_id FOR UPDATE"
                    ),
                    {"hospital_id": hospital_id, "patient_id": patient_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise LookupError("patient not found")
        if row["mrn"]:
            return str(row["mrn"]), int(row["card_version"])
        value = await self.connection.scalar(
            text(
                "INSERT INTO patient_number_counters (hospital_id, year, last_value) "
                "VALUES (:hospital_id, :year, 1) ON CONFLICT (hospital_id, year) "
                "DO UPDATE SET last_value = patient_number_counters.last_value + 1 "
                "RETURNING last_value"
            ),
            {"hospital_id": hospital_id, "year": year},
        )
        mrn = format_mrn(year, int(value))
        await self.connection.execute(
            text(
                "UPDATE patients SET mrn = :mrn WHERE hospital_id = :hospital_id "
                "AND id = :patient_id"
            ),
            {"mrn": mrn, "hospital_id": hospital_id, "patient_id": patient_id},
        )
        return mrn, int(row["card_version"])

    async def by_mrn(self, *, hospital_id: UUID, mrn: str) -> dict[str, Any] | None:
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, mrn, card_version, year_of_birth, sex FROM patients "
                        "WHERE hospital_id = :hospital_id AND mrn = :mrn"
                    ),
                    {"hospital_id": hospital_id, "mrn": mrn},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def by_phone(self, *, hospital_id: UUID, phone: str) -> list[dict[str, Any]]:
        """Registered patients who gave this phone number: a hint for staff, never a match."""
        digest = lookup_digest(phone.strip(), key=self.settings.app_hmac_key_bytes)
        result = await self.connection.execute(
            text(
                "SELECT p.id, p.mrn, p.year_of_birth, p.sex, count(v.id) AS visit_count, "
                "max(v.service_date) AS last_visit FROM patients p LEFT JOIN visits v "
                "ON v.hospital_id = p.hospital_id AND v.patient_id = p.id "
                "AND v.token_number IS NOT NULL "
                "WHERE p.hospital_id = :hospital_id AND p.phone_hmac = :digest "
                "AND p.mrn IS NOT NULL GROUP BY p.id ORDER BY max(v.service_date) DESC "
                "NULLS LAST LIMIT 20"
            ),
            {"hospital_id": hospital_id, "digest": digest},
        )
        return [dict(row) for row in result.mappings().all()]

    async def link_visit(
        self, *, hospital_id: UUID, visit_id: UUID, patient_id: UUID, source: str
    ) -> bool:
        """Join a new visit to an existing patient; False when it already belongs to them.

        A visit starts with a placeholder patient of its own. After the link the placeholder
        is emptied (its phone and birth year cleared): today's phone number is kept on the
        patient's record only when the record has none. A placeholder numbered for this one
        visit (a patient who forgot their card and was registered as new) gives the number
        up. Joining two records that each have visits is a record merge, which is refused.
        """
        visit = (
            (
                await self.connection.execute(
                    text(
                        "SELECT patient_id, patient_link FROM visits "
                        "WHERE hospital_id = :hospital_id AND id = :visit_id FOR UPDATE"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if visit is None:
            raise LookupError("visit not found")
        if visit["patient_id"] == patient_id:
            return False
        if visit["patient_link"] is not None:
            raise ValueError("this visit is already linked to another patient")
        placeholder_id = visit["patient_id"]
        placeholder = await self.get(hospital_id=hospital_id, patient_id=placeholder_id)
        others = await self.connection.scalar(
            text(
                "SELECT count(*) FROM visits WHERE hospital_id = :hospital_id "
                "AND patient_id = :patient_id AND id <> :visit_id"
            ),
            {"hospital_id": hospital_id, "patient_id": placeholder_id, "visit_id": visit_id},
        )
        if others:
            raise ValueError("this visit belongs to a patient with other visits")
        target_phone = await self.connection.scalar(
            text("SELECT phone_hmac FROM patients WHERE hospital_id = :hospital_id AND id = :id"),
            {"hospital_id": hospital_id, "id": patient_id},
        )
        phone = placeholder.get("phone") if placeholder else None
        if target_phone is None and phone:
            await self.connection.execute(
                text(
                    "UPDATE patients SET phone_enc = :phone_enc, phone_hmac = :phone_hmac "
                    "WHERE hospital_id = :hospital_id AND id = :id"
                ),
                {
                    "phone_enc": encrypt_field(
                        phone,
                        key=self.settings.app_data_key_bytes,
                        associated_data=f"patients:phone_enc:{patient_id}",
                    ),
                    "phone_hmac": lookup_digest(phone, key=self.settings.app_hmac_key_bytes),
                    "hospital_id": hospital_id,
                    "id": patient_id,
                },
            )
        await self.connection.execute(
            text(
                "UPDATE visits SET patient_id = :patient_id, patient_link = :source "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {
                "patient_id": patient_id,
                "source": source,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
            },
        )
        await self.connection.execute(
            text(
                "UPDATE patients SET phone_enc = NULL, phone_hmac = NULL, year_of_birth = NULL, "
                "sex = 'unknown', mrn = NULL WHERE hospital_id = :hospital_id AND id = :id"
            ),
            {"hospital_id": hospital_id, "id": placeholder_id},
        )
        return True

    async def visit_history(
        self, *, hospital_id: UUID, patient_id: UUID, exclude_visit_id: UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        """The patient's earlier registered visits, newest first, with their case sheets."""
        result = await self.connection.execute(
            text(
                "SELECT v.id AS visit_id, v.service_date, v.status, v.token_label, v.urgency, "
                "v.registered_at, d.name_en AS department, cs.data AS case_sheet_data, "
                "(cs.last_edited_by IS NOT NULL) AS staff_edited, "
                "(a.withdrawn_at IS NOT NULL) AS ai_withdrawn "
                "FROM visits v JOIN departments d ON d.hospital_id = v.hospital_id "
                "AND d.id = v.department_id LEFT JOIN case_sheets cs "
                "ON cs.hospital_id = v.hospital_id AND cs.visit_id = v.id "
                "LEFT JOIN consents a ON a.hospital_id = v.hospital_id AND a.visit_id = v.id "
                "AND a.purpose = 'ai' "
                "WHERE v.hospital_id = :hospital_id AND v.patient_id = :patient_id "
                "AND v.id <> :exclude_visit_id AND v.token_number IS NOT NULL "
                "ORDER BY v.registered_at DESC, v.id DESC LIMIT :limit"
            ),
            {
                "hospital_id": hospital_id,
                "patient_id": patient_id,
                "exclude_visit_id": exclude_visit_id,
                "limit": limit,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
        for row in rows:
            row["case_sheet_data"] = decrypt_case_sheet(
                row.pop("case_sheet_data"), row["visit_id"], self.settings
            )
        return rows

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
        kiosk_id: UUID | None = None,
    ) -> UUID:
        visit_id = uuid7()
        now = datetime.now(UTC)
        await self.connection.execute(
            text(
                "INSERT INTO visits (id, hospital_id, patient_id, department_id, service_date, "
                "status, urgency, language, registered_at, kiosk_id) VALUES (:id, :hospital_id, "
                ":patient_id, :department_id, :service_date, :status, :urgency, :language, :now, "
                ":kiosk_id)"
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
                "kiosk_id": kiosk_id,
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
        # The patient confirms their own summary; the timeline starts with their token.
        await self.record_event(
            hospital_id=hospital_id,
            visit_id=visit_id,
            from_status="awaiting_confirmation",
            to_status="queued",
            actor_type="patient",
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
                        # This payload flag is not a credential or authentication token.
                        "notify_token": True,  # nosec B105
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
                    "patient_id",
                    "department_id",
                    "status",
                    "urgency",
                    "urgency_reasons",
                    "acuity_score",
                    "acuity",
                    "age_years",
                    "pregnant",
                    "token_label",
                    "token_number",
                    "queued_at",
                    "called_at",
                    "consult_started_at",
                    "consult_ended_at",
                    "assigned_doctor_id",
                    "needs_assistance",
                    "kiosk_id",
                    "called_by",
                    "skip_count",
                )
            ],
        )
        case_sheets = table(
            "case_sheets", column("hospital_id"), column("visit_id"), column("data")
        )
        patients = table("patients", column("hospital_id"), column("id"), column("sex"))
        kiosks = table("kiosks", column("hospital_id"), column("id"), column("kind"))
        callers = table("users", column("hospital_id"), column("id"), column("name"))
        red_flags = table(
            "red_flag_events",
            column("hospital_id"),
            column("visit_id"),
            column("rule_id"),
            column("acknowledged_at"),
        )
        # A danger sign the patient tapped puts them first, but a nurse confirms it in person.
        tile_unconfirmed = exists().where(
            red_flags.c.hospital_id == visits.c.hospital_id,
            red_flags.c.visit_id == visits.c.id,
            red_flags.c.rule_id == "RF_DS_TILE",
            red_flags.c.acknowledged_at.is_(None),
        )
        statement = (
            select(
                visits.c.id.label("visit_id"),
                visits.c.department_id,
                visits.c.status,
                visits.c.urgency,
                visits.c.urgency_reasons,
                visits.c.acuity_score,
                visits.c.acuity,
                visits.c.age_years,
                visits.c.pregnant,
                visits.c.token_label,
                visits.c.token_number,
                visits.c.queued_at,
                visits.c.called_at,
                visits.c.consult_started_at,
                visits.c.consult_ended_at,
                visits.c.assigned_doctor_id,
                visits.c.needs_assistance,
                visits.c.called_by,
                visits.c.skip_count,
                case_sheets.c.data.label("case_sheet_data"),
                patients.c.sex,
                kiosks.c.kind.label("checkin_kind"),
                callers.c.name.label("called_by_name"),
                tile_unconfirmed.label("tile_unconfirmed"),
            )
            .outerjoin(
                case_sheets,
                (case_sheets.c.hospital_id == visits.c.hospital_id)
                & (case_sheets.c.visit_id == visits.c.id),
            )
            .outerjoin(
                kiosks,
                (kiosks.c.hospital_id == visits.c.hospital_id) & (kiosks.c.id == visits.c.kiosk_id),
            )
            .outerjoin(
                callers,
                (callers.c.hospital_id == visits.c.hospital_id)
                & (callers.c.id == visits.c.called_by),
            )
            .join(
                patients,
                (patients.c.hospital_id == visits.c.hospital_id)
                & (patients.c.id == visits.c.patient_id),
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
        rows = [dict(row) for row in result.mappings().all()]
        for row in rows:
            row["case_sheet_data"] = decrypt_case_sheet(
                row.get("case_sheet_data"), row["visit_id"], self.settings
            )
        return rows

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

    async def call_grace_seconds(self, hospital_id: UUID) -> int:
        """Seconds after a call before staff may mark the patient absent."""
        from app.domain.queue.state import DEFAULT_CALL_GRACE_SECONDS

        value = await self.connection.scalar(
            text(
                "SELECT call_grace_seconds FROM hospital_queue_settings "
                "WHERE hospital_id = :hospital_id"
            ),
            {"hospital_id": hospital_id},
        )
        return DEFAULT_CALL_GRACE_SECONDS if value is None else int(value)

    async def lock_calls(self, *, hospital_id: UUID, department_id: UUID) -> None:
        """Serialise every call in one department, so two doctors never get the same patient.

        Lock order everywhere: department calls, then the visit row, then the doctor.
        """
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"calls:{hospital_id}:{department_id}"},
        )

    async def record_event(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        from_status: str,
        to_status: str,
        actor_type: str,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        """Add one line to the visit's timeline (the table is append-only for the app)."""
        await self.connection.execute(
            text(
                "INSERT INTO visit_events (id, hospital_id, visit_id, from_status, to_status, "
                "actor_type, actor_id, reason) VALUES (:id, :hospital_id, :visit_id, "
                ":from_status, :to_status, :actor_type, :actor_id, :reason)"
            ),
            {
                "id": uuid7(),
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "from_status": from_status,
                "to_status": to_status,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "reason": reason,
            },
        )

    async def transition_visit(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        target: str,
        actor_id: UUID,
        actor_role: str = "admin",
        department_id: UUID | None = None,
        reason: str | None = None,
        expected: str | None = None,
    ) -> dict[str, Any]:
        """Change a visit's queue state with its timeline line and outbox event, atomically.

        ``department_id`` confines the change to one department (None: hospital-wide, for
        administrators); a visit elsewhere is reported as not found. A doctor sees one patient
        at a time: they cannot call or start a patient while another is called or in
        consultation for them, nor act on a patient another doctor has called. A patient can
        be marked absent only after the hospital's grace period since the call. Recalling or
        putting a patient back keeps their token, urgency and arrival time, hence their place.
        """
        from app.domain.queue.state import ACTIVE_CLAIM, QueueRuleError, require_transition

        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT department_id, status, urgency, age_years, pregnant, token_label, "
                        "queued_at, acuity_score, assigned_doctor_id, "
                        "extract(epoch FROM now() - called_at) AS called_seconds_ago "
                        "FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id "
                        "FOR UPDATE"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None or (department_id is not None and row["department_id"] != department_id):
            raise LookupError("visit not found")
        current: dict[str, Any] = dict(row)
        waited = current.pop("called_seconds_ago")
        if expected is not None and current["status"] != expected:
            raise ValueError(f"invalid visit transition: {current['status']} -> {target}")
        require_transition(current["status"], target)
        doctor = actor_role == "doctor"
        holder = current["assigned_doctor_id"]
        if doctor and current["status"] in ACTIVE_CLAIM and holder not in (None, actor_id):
            raise QueueRuleError(
                "CLAIMED_BY_ANOTHER_DOCTOR",
                "Another doctor has called this patient.",
            )
        if doctor and target in ACTIVE_CLAIM:
            await self.connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"doctor:{hospital_id}:{actor_id}"},
            )
            busy = (
                (
                    await self.connection.execute(
                        text(
                            "SELECT id, token_label, status FROM visits "
                            "WHERE hospital_id = :hospital_id AND assigned_doctor_id = :actor_id "
                            "AND status IN ('called', 'in_consult') AND id <> :visit_id "
                            "ORDER BY called_at NULLS LAST, id LIMIT 1"
                        ),
                        {"hospital_id": hospital_id, "actor_id": actor_id, "visit_id": visit_id},
                    )
                )
                .mappings()
                .first()
            )
            if busy is not None:
                raise QueueRuleError(
                    "DOCTOR_BUSY",
                    f"You already have {busy['token_label']}. Start, finish or mark them "
                    "absent first.",
                    active_visit_id=str(busy["id"]),
                    active_token=busy["token_label"],
                    active_status=busy["status"],
                )
        if target == "skipped" and waited is not None:
            grace = await self.call_grace_seconds(hospital_id)
            if float(waited) < grace:
                remaining = max(1, ceil(grace - float(waited)))
                raise QueueRuleError(
                    "CALL_GRACE_NOT_OVER",
                    f"Give the patient time to come. You can mark them absent in "
                    f"{remaining} seconds.",
                    retry_after_seconds=remaining,
                )
        visits = table(
            "visits",
            *[
                column(name)
                for name in (
                    "hospital_id",
                    "id",
                    "status",
                    "called_at",
                    "called_by",
                    "consult_started_at",
                    "consult_ended_at",
                    "assigned_doctor_id",
                    "skipped_at",
                    "recalled_at",
                    "skip_count",
                )
            ],
        )
        values: dict[str, Any] = {"status": target}
        if target == "called":
            values.update(
                called_at=func.now(),
                called_by=actor_id,
                assigned_doctor_id=actor_id if doctor else None,
            )
        elif target == "in_consult":
            values.update(consult_started_at=func.now(), assigned_doctor_id=actor_id)
        elif target == "done":
            values["consult_ended_at"] = func.now()
        elif target == "skipped":
            values.update(
                skipped_at=func.now(),
                skip_count=visits.c.skip_count + 1,
                assigned_doctor_id=None,
            )
        elif target == "queued":
            # Back in line on the original arrival time: same token, urgency and place.
            values.update(called_at=None, called_by=None, assigned_doctor_id=None)
            if current["status"] == "skipped":
                values["recalled_at"] = func.now()
        await self.connection.execute(
            update(visits)
            .where(visits.c.hospital_id == hospital_id, visits.c.id == visit_id)
            .values(**values)
        )
        await self.record_event(
            hospital_id=hospital_id,
            visit_id=visit_id,
            from_status=current["status"],
            to_status=target,
            actor_type="user",
            actor_id=actor_id,
            reason=reason,
        )
        event = {
            "op": "upsert" if target in {"queued", "called", "in_consult"} else "remove",
            "visit_id": str(visit_id),
            "department_id": str(current["department_id"]),
        }
        await self.connection.execute(
            text(
                "INSERT INTO outbox (id, hospital_id, topic, payload) VALUES "
                "(:id, :hospital_id, 'queue.changed', CAST(:payload AS jsonb))"
            ),
            {"id": uuid7(), "hospital_id": hospital_id, "payload": json.dumps(event)},
        )
        return {
            **current,
            "visit_id": visit_id,
            "previous_status": current["status"],
            "status": target,
            "override_reason": reason,
        }

    async def call_next(
        self, *, hospital_id: UUID, department_id: UUID, actor_id: UUID, actor_role: str
    ) -> dict[str, Any] | None:
        """Call the first waiting patient in calling order; None when nobody is waiting.

        The caller holds ``lock_calls`` for the department, so two doctors never receive the
        same patient. Skipped patients wait for a recall and are never called from here.
        """
        from app.domain.queue.scoring import calling_order

        result = await self.connection.execute(
            text(
                "SELECT id, urgency, age_years, pregnant, queued_at, acuity_score "
                "FROM visits WHERE hospital_id = :hospital_id "
                "AND department_id = :department_id AND status = 'queued'"
            ),
            {"hospital_id": hospital_id, "department_id": department_id},
        )
        rows = [dict(row) for row in result.mappings().all()]
        for candidate in sorted(rows, key=lambda row: calling_order(row, self.settings)):
            try:
                return await self.transition_visit(
                    hospital_id=hospital_id,
                    visit_id=candidate["id"],
                    target="called",
                    actor_id=actor_id,
                    actor_role=actor_role,
                    department_id=department_id,
                )
            except ValueError:
                continue  # Cancelled by staff a moment ago: call the next one.
        return None

    async def missed_visits(
        self, *, hospital_id: UUID, department_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        """Patients who were called and not there, oldest miss first, waiting for a recall."""
        result = await self.connection.execute(
            text(
                "SELECT v.id AS visit_id, v.department_id, v.token_label, v.urgency, "
                "v.age_years, p.sex, v.skip_count, v.skipped_at, v.queued_at, "
                "cs.data AS case_sheet_data FROM visits v JOIN patients p "
                "ON p.hospital_id = v.hospital_id AND p.id = v.patient_id "
                "LEFT JOIN case_sheets cs ON cs.hospital_id = v.hospital_id "
                "AND cs.visit_id = v.id WHERE v.hospital_id = :hospital_id "
                "AND v.status = 'skipped' AND (CAST(:department_id AS uuid) IS NULL "
                "OR v.department_id = CAST(:department_id AS uuid)) "
                "ORDER BY v.skipped_at, v.id LIMIT 100"
            ),
            {"hospital_id": hospital_id, "department_id": department_id},
        )
        rows = [dict(row) for row in result.mappings().all()]
        for row in rows:
            row["case_sheet_data"] = decrypt_case_sheet(
                row.get("case_sheet_data"), row["visit_id"], self.settings
            )
        return rows

    async def visit_events(self, *, hospital_id: UUID, visit_id: UUID) -> list[dict[str, Any]]:
        """The visit's queue timeline, oldest first, with the staff member's name and role."""
        result = await self.connection.execute(
            text(
                "SELECT e.from_status, e.to_status, e.actor_type, e.reason, e.created_at, "
                "u.name AS actor_name, u.role AS actor_role FROM visit_events e "
                "LEFT JOIN users u ON u.hospital_id = e.hospital_id AND u.id = e.actor_id "
                "WHERE e.hospital_id = :hospital_id AND e.visit_id = :visit_id "
                "ORDER BY e.created_at, e.id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return [dict(row) for row in result.mappings().all()]


def decrypt_case_sheet(stored: Any, visit_id: UUID, settings: Settings) -> dict[str, Any] | None:
    """Decrypt a stored case sheet column value; None when absent or unreadable."""
    ciphertext = stored.get("ciphertext") if isinstance(stored, dict) else None
    if not isinstance(ciphertext, str):
        return None
    try:
        plaintext = decrypt_field(
            ciphertext,
            key=settings.app_data_key_bytes,
            associated_data=f"case_sheets:data:{visit_id}",
        )
        value = json.loads(plaintext)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


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

    async def get(
        self, *, hospital_id: UUID, visit_id: UUID, for_update: bool = False
    ) -> dict[str, Any] | None:
        statement = (
            text(
                "SELECT id, data, version, confirmed_by_patient, confirmed_at FROM case_sheets "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT id, data, version, confirmed_by_patient, confirmed_at FROM case_sheets "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
            )
        )
        row = (
            (
                await self.connection.execute(
                    statement,
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
        return {
            "id": row["id"],
            "data": json.loads(plaintext),
            "version": row["version"],
            "confirmed_by_patient": row.get("confirmed_by_patient", False),
            "confirmed_at": row.get("confirmed_at"),
        }

    async def replace_if_version(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        expected_version: int,
        data: dict[str, Any],
        editor_id: UUID | None,
    ) -> int | None:
        encrypted_data = encrypt_field(
            json.dumps(data, separators=(",", ":")),
            key=self.settings.app_data_key_bytes,
            associated_data=f"case_sheets:data:{visit_id}",
        )
        result = await self.connection.execute(
            text(
                "UPDATE case_sheets SET data = CAST(:data AS jsonb), "
                "schema_version = :schema_version, "
                "version = version + 1, last_edited_by = COALESCE(:editor_id, last_edited_by) "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                "AND version = :expected_version RETURNING version"
            ),
            {
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "data": json.dumps({"ciphertext": encrypted_data}),
                "schema_version": str(data["schema_version"]),
                "editor_id": editor_id,
                "expected_version": expected_version,
            },
        )
        updated_version = result.scalar_one_or_none()
        return int(updated_version) if updated_version is not None else None

    async def confirm(self, *, hospital_id: UUID, visit_id: UUID) -> bool:
        result = await self.connection.execute(
            text(
                "UPDATE case_sheets SET confirmed_by_patient = true, confirmed_at = now() "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                "AND confirmed_by_patient = false"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return result.rowcount == 1


class InterviewTurnRepository:
    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def list_for_visit(self, *, hospital_id: UUID, visit_id: UUID) -> list[dict[str, Any]]:
        rows = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, turn_index, question_id, input_mode, text_original, "
                        "text_english, stt_confidence FROM interview_turns "
                        "WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                        "ORDER BY turn_index"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
            )
            .mappings()
            .all()
        )
        turns: list[dict[str, Any]] = []
        for row in rows:
            turn_id = row["id"]
            original = row["text_original"]
            english = row["text_english"]
            turns.append(
                {
                    "turn_index": row["turn_index"],
                    "question_id": row["question_id"],
                    "input_mode": row["input_mode"],
                    "text_original": (
                        decrypt_field(
                            original,
                            key=self.settings.app_data_key_bytes,
                            associated_data=f"interview_turns:text_original:{turn_id}",
                        )
                        if isinstance(original, str)
                        else None
                    ),
                    "text_english": (
                        decrypt_field(
                            english,
                            key=self.settings.app_data_key_bytes,
                            associated_data=f"interview_turns:text_english:{turn_id}",
                        )
                        if isinstance(english, str)
                        else None
                    ),
                    "stt_confidence": row["stt_confidence"],
                }
            )
        return turns

    async def next_correction_index(self, *, hospital_id: UUID, visit_id: UUID, base: int) -> int:
        """The next free turn number for a correction, taken from the stored turns.

        Serialised per visit, so a retried correction (whose first attempt was saved but whose
        answer never arrived) gets a new number instead of colliding with the saved one.
        """
        await self.connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"corrections:{hospital_id}:{visit_id}"},
        )
        last = await self.connection.scalar(
            text(
                "SELECT max(turn_index) FROM interview_turns WHERE hospital_id = :hospital_id "
                "AND visit_id = :visit_id AND turn_index >= :base"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id, "base": base},
        )
        return base if last is None else int(last) + 1

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

    async def active_nurse_department(self, *, hospital_id: UUID, nurse_id: UUID) -> UUID | None:
        department_id = await self.connection.scalar(
            text(
                "SELECT d.id FROM users u JOIN departments d "
                "ON d.hospital_id = u.hospital_id AND d.id = u.department_id "
                "WHERE u.hospital_id = :hospital_id AND u.id = :nurse_id "
                "AND u.role = 'nurse' AND u.is_active = true AND d.is_active = true"
            ),
            {"hospital_id": hospital_id, "nurse_id": nurse_id},
        )
        return department_id if isinstance(department_id, UUID) else None

    async def kiosk_is_active(
        self, *, hospital_id: UUID, kiosk_id: UUID, issued_at: int | None = None
    ) -> bool:
        """True while the kiosk is in service and paired to a device.

        ``issued_at`` (the token's ``iat``) also rejects tokens issued to a device that was
        replaced since: re-pairing a kiosk with a new activation code moves ``paired_at``.
        The few seconds of slack absorb clock differences between API and database.
        """
        result = await self.connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM kiosks WHERE hospital_id = :hospital_id "
                "AND id = :kiosk_id AND is_active = true AND deleted_at IS NULL "
                "AND secret_hash IS NOT NULL "
                "AND (CAST(:issued_at AS double precision) IS NULL OR paired_at IS NULL "
                "OR paired_at <= to_timestamp(CAST(:issued_at AS double precision)) "
                "+ interval '5 seconds'))"
            ),
            {
                "hospital_id": hospital_id,
                "kiosk_id": kiosk_id,
                "issued_at": float(issued_at) if issued_at is not None else None,
            },
        )
        return bool(result)

    async def kiosk_unavailable_reason(
        self, *, hospital_id: UUID, kiosk_id: UUID, issued_at: int | None = None
    ) -> str:
        """Why a kiosk failed ``kiosk_is_active``: "off" when only switched off, else "unpaired"."""
        is_active = await self.connection.scalar(
            text(
                "SELECT is_active FROM kiosks WHERE hospital_id = :hospital_id "
                "AND id = :kiosk_id AND deleted_at IS NULL AND secret_hash IS NOT NULL "
                "AND (CAST(:issued_at AS double precision) IS NULL OR paired_at IS NULL "
                "OR paired_at <= to_timestamp(CAST(:issued_at AS double precision)) "
                "+ interval '5 seconds')"
            ),
            {
                "hospital_id": hospital_id,
                "kiosk_id": kiosk_id,
                "issued_at": float(issued_at) if issued_at is not None else None,
            },
        )
        return "off" if is_active is False else "unpaired"

    async def touch_kiosk(
        self, *, hospital_id: UUID, kiosk_id: UUID, issued_at: int | None = None
    ) -> bool:
        """Note that an active kiosk is online. Writes at most once a minute per kiosk."""
        if not await self.kiosk_is_active(
            hospital_id=hospital_id, kiosk_id=kiosk_id, issued_at=issued_at
        ):
            return False
        await self.connection.execute(
            text(
                "UPDATE kiosks SET last_seen_at = now() WHERE hospital_id = :hospital_id "
                "AND id = :kiosk_id AND (last_seen_at IS NULL "
                "OR last_seen_at < now() - interval '50 seconds')"
            ),
            {"hospital_id": hospital_id, "kiosk_id": kiosk_id},
        )
        return True

    async def create_session(
        self, *, hospital_id: UUID, language: str, kiosk_id: UUID, issued_at: int | None = None
    ) -> tuple[UUID, UUID]:
        if not await self.touch_kiosk(
            hospital_id=hospital_id, kiosk_id=kiosk_id, issued_at=issued_at
        ):
            raise KioskUnavailableError(
                await self.kiosk_unavailable_reason(
                    hospital_id=hospital_id, kiosk_id=kiosk_id, issued_at=issued_at
                )
            )
        return await self._start_visit(
            hospital_id=hospital_id, language=language, kiosk_id=kiosk_id, name="Kiosk visitor"
        )

    async def checkin_point_by_code(self, code_hash: str) -> dict[str, Any] | None:
        """Find a phone check-in point by its code digest, before the hospital is known.

        Run inside ``set_secret_lookup_scope(..., "app.kiosk_checkin_hash", code_hash)``.
        """
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, hospital_id, is_active FROM kiosks "
                        "WHERE checkin_code_hash = :code_hash AND kind = 'mobile' "
                        "AND deleted_at IS NULL LIMIT 1"
                    ),
                    {"code_hash": code_hash},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def create_mobile_session(
        self, *, hospital_id: UUID, point_id: UUID, language: str
    ) -> tuple[UUID, UUID]:
        """Start a visit from a phone check-in point; refused once the point is turned off."""
        active = await self.connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM kiosks WHERE hospital_id = :hospital_id "
                "AND id = :point_id AND kind = 'mobile' AND is_active = true "
                "AND deleted_at IS NULL)"
            ),
            {"hospital_id": hospital_id, "point_id": point_id},
        )
        if not active:
            raise KioskUnavailableError("off")
        # "Last used", written at most once a minute so simultaneous patients don't queue
        # behind one row lock.
        await self.connection.execute(
            text(
                "UPDATE kiosks SET last_seen_at = now() WHERE hospital_id = :hospital_id "
                "AND id = :point_id AND (last_seen_at IS NULL "
                "OR last_seen_at < now() - interval '50 seconds')"
            ),
            {"hospital_id": hospital_id, "point_id": point_id},
        )
        return await self._start_visit(
            hospital_id=hospital_id,
            language=language,
            kiosk_id=point_id,
            name="Phone check-in visitor",
        )

    async def _start_visit(
        self, *, hospital_id: UUID, language: str, kiosk_id: UUID, name: str
    ) -> tuple[UUID, UUID]:
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
            name=name,
            phone=None,
            preferred_language=language,
        )
        visit_id = await VisitRepository(self.connection, self.settings).create(
            hospital_id=hospital_id,
            patient_id=patient_id,
            department_id=department_id,
            service_date=datetime.now(ZoneInfo("Asia/Kolkata")).date(),
            language=language,
            kiosk_id=kiosk_id,
        )
        return visit_id, patient_id

    async def create_assisted_session(
        self, *, hospital_id: UUID, department_id: UUID, language: str
    ) -> tuple[UUID, UUID]:
        patient_id = await PatientRepository(self.connection, self.settings).create(
            hospital_id=hospital_id,
            name="Pending nurse-assisted visitor",
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
                        "v.pregnant, v.patient_link, p.sex, "
                        "c.granted AS consent_granted, "
                        "CASE WHEN a.withdrawn_at IS NOT NULL THEN 'withdrawn' "
                        "WHEN a.granted THEN 'granted' WHEN a.id IS NOT NULL THEN 'declined' "
                        "END AS ai_consent FROM visits v LEFT JOIN consents c "
                        "ON c.hospital_id = v.hospital_id AND c.visit_id = v.id "
                        "AND c.purpose = 'care' LEFT JOIN consents a "
                        "ON a.hospital_id = v.hospital_id AND a.visit_id = v.id "
                        "AND a.purpose = 'ai' "
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

    async def ai_consent(self, *, hospital_id: UUID, visit_id: UUID) -> str | None:
        """The patient's choice about online AI help: granted, declined, withdrawn or None."""
        return cast(
            str | None,
            await self.connection.scalar(
                text(
                    "SELECT CASE WHEN withdrawn_at IS NOT NULL THEN 'withdrawn' "
                    "WHEN granted THEN 'granted' ELSE 'declined' END FROM consents "
                    "WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                    "AND purpose = 'ai'"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            ),
        )

    async def record_ai_consent(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        language: str,
        version: str,
        text_sha256: str,
        granted: bool,
        channel: str,
    ) -> None:
        """Record (or, while still registering, change) the answer about online AI help."""
        await self.connection.execute(
            text(
                "INSERT INTO consents (id, hospital_id, visit_id, purpose, language, "
                "text_version, text_sha256, granted, granted_at, channel) VALUES (:id, "
                ":hospital_id, :visit_id, 'ai', :language, :version, :text_sha256, :granted, "
                ":granted_at, :channel) ON CONFLICT (hospital_id, visit_id, purpose) DO UPDATE "
                "SET language = EXCLUDED.language, text_version = EXCLUDED.text_version, "
                "text_sha256 = EXCLUDED.text_sha256, granted = EXCLUDED.granted, "
                "granted_at = EXCLUDED.granted_at, channel = EXCLUDED.channel, "
                "withdrawn_at = NULL, withdrawn_by = NULL"
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
                "channel": channel,
            },
        )

    async def withdraw_ai_consent(self, *, hospital_id: UUID, visit_id: UUID, by: str) -> bool:
        """Stop online AI help for a visit; False if it was never granted or already stopped."""
        withdrawn = await self.connection.scalar(
            text(
                "UPDATE consents SET withdrawn_at = now(), withdrawn_by = :by "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id AND purpose = 'ai' "
                "AND granted AND withdrawn_at IS NULL RETURNING id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id, "by": by},
        )
        return withdrawn is not None

    async def record_consent(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        language: str,
        version: str,
        text_sha256: str,
        granted: bool,
        channel: str = "kiosk",
    ) -> None:
        existing = await self.connection.scalar(
            text(
                "SELECT id FROM consents WHERE hospital_id = :hospital_id "
                "AND visit_id = :visit_id AND purpose = 'care'"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if existing is not None:
            raise FileExistsError("consent has already been recorded")
        await self.connection.execute(
            text(
                "INSERT INTO consents (id, hospital_id, visit_id, language, text_version, "
                "text_sha256, granted, granted_at, channel) VALUES (:id, :hospital_id, :visit_id, "
                ":language, :version, :text_sha256, :granted, :granted_at, :channel)"
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
                "channel": channel,
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
        sms_opt_in: bool = False,
        name: str | None = None,
        linked: bool = False,
    ) -> None:
        if linked:
            # A visit joined to an existing patient (OPD card or staff) never rewrites the
            # patient's record from a self-service screen; staff correct it if needed.
            await self.connection.execute(
                text(
                    "UPDATE visits SET age_years = :age, sms_opt_in = :sms_opt_in, "
                    "sms_opt_in_at = CASE WHEN :sms_opt_in THEN now() ELSE NULL END "
                    "WHERE hospital_id = :hospital_id AND id = :visit_id"
                ),
                {
                    "age": age,
                    "sms_opt_in": sms_opt_in,
                    "hospital_id": hospital_id,
                    "visit_id": visit_id,
                },
            )
            return
        name_enc = (
            encrypt_field(
                name,
                key=self.settings.app_data_key_bytes,
                associated_data=f"patients:name_enc:{patient_id}",
            )
            if name
            else None
        )
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
        if sms_opt_in and not phone:
            raise ValueError("notification opt-in requires a phone number")
        current_year = datetime.now(ZoneInfo("Asia/Kolkata")).year
        await self.connection.execute(
            text(
                "UPDATE patients SET name_enc = coalesce(:name_enc, name_enc), "
                "phone_enc = :phone_enc, phone_hmac = :phone_hmac, "
                "year_of_birth = :year_of_birth, sex = :sex, preferred_language = "
                "(SELECT language FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id) "
                "WHERE hospital_id = :hospital_id AND id = :patient_id"
            ),
            {
                "phone_enc": phone_enc,
                "phone_hmac": phone_hmac,
                "name_enc": name_enc,
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
        await self.connection.execute(
            text(
                "UPDATE visits SET sms_opt_in = :sms_opt_in, "
                "sms_opt_in_at = CASE WHEN :sms_opt_in THEN now() ELSE NULL END "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {
                "sms_opt_in": sms_opt_in and phone is not None,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
            },
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

    async def set_visit_status(
        self, *, hospital_id: UUID, visit_id: UUID, status: str
    ) -> str | None:
        """Move a visit between registration steps; returns its earlier status.

        Only a visit without a token yet (registering or awaiting confirmation) changes here.
        A confirmed visit is in the queue, where only the queue's state machine moves it, so a
        patient session can never take a patient out of the queue. None: nothing changed.
        """
        previous = await self.connection.scalar(
            text(
                "SELECT status FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id "
                "FOR UPDATE"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        if previous not in {"registering", "awaiting_confirmation"}:
            return None
        await self.connection.execute(
            text(
                "UPDATE visits SET status = :status "
                "WHERE hospital_id = :hospital_id AND id = :visit_id"
            ),
            {"status": status, "hospital_id": hospital_id, "visit_id": visit_id},
        )
        return str(previous)


class DocumentRepository:
    """A visit's old papers: their details, the encrypted photo, and what AI read from them.

    The photo lives in ``document_contents``, sealed with AES-GCM to its document, so a photo
    copied onto another record cannot be decrypted; listing documents never loads photos.
    """

    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def add(
        self,
        *,
        hospital_id: UUID,
        visit_id: UUID,
        kind: str,
        mime: str,
        content: bytes,
        source: str,
        uploaded_by: UUID | None,
        ocr_status: str,
    ) -> UUID:
        document_id = uuid7()
        await self.connection.execute(
            text(
                "INSERT INTO documents (id, hospital_id, visit_id, object_key, mime, size, "
                "ocr_status, kind, source, uploaded_by) VALUES (:id, :hospital_id, :visit_id, "
                ":object_key, :mime, :size, :ocr_status, :kind, :source, :uploaded_by)"
            ),
            {
                "id": document_id,
                "hospital_id": hospital_id,
                "visit_id": visit_id,
                "object_key": f"pg:{document_id}",
                "mime": mime,
                "size": len(content),
                "ocr_status": ocr_status,
                "kind": kind,
                "source": source,
                "uploaded_by": uploaded_by,
            },
        )
        await self.connection.execute(
            text(
                "INSERT INTO document_contents (hospital_id, document_id, ciphertext) "
                "VALUES (:hospital_id, :document_id, :ciphertext)"
            ),
            {
                "hospital_id": hospital_id,
                "document_id": document_id,
                "ciphertext": encrypt_bytes(
                    content,
                    key=self.settings.app_data_key_bytes,
                    associated_data=f"documents:content:{document_id}",
                ),
            },
        )
        return document_id

    async def lock_visit(self, *, hospital_id: UUID, visit_id: UUID) -> str | None:
        """Lock the visit for this transaction and return its status.

        Uploads to one visit then count their papers one after another, and the patient
        cannot confirm (which changes the status) halfway through an upload.
        """
        return cast(
            str | None,
            await self.connection.scalar(
                text(
                    "SELECT status FROM visits WHERE hospital_id = :hospital_id "
                    "AND id = :visit_id FOR UPDATE"
                ),
                {"hospital_id": hospital_id, "visit_id": visit_id},
            ),
        )

    async def count_for_visit(self, *, hospital_id: UUID, visit_id: UUID) -> dict[str, int]:
        """How many papers the visit has, by who added them."""
        result = await self.connection.execute(
            text(
                "SELECT source, count(*) AS papers FROM documents "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id GROUP BY source"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return {str(row.source): int(row.papers) for row in result}

    def _decrypt_reading(self, row: dict[str, Any]) -> dict[str, Any]:
        """Swap the stored ``extracted`` value for the decrypted reading (None if unreadable)."""
        stored = row.pop("extracted", None)
        reading = None
        if isinstance(stored, dict) and isinstance(stored.get("ciphertext"), str):
            try:
                value = json.loads(
                    decrypt_field(
                        stored["ciphertext"],
                        key=self.settings.app_data_key_bytes,
                        associated_data=f"documents:extracted:{row['id']}",
                    )
                )
            except Exception:
                value = None
            reading = value if isinstance(value, dict) else None
        row["reading"] = reading
        return row

    async def list_for_visit(self, *, hospital_id: UUID, visit_id: UUID) -> list[dict[str, Any]]:
        """This visit's papers, newest first, each with what AI read (if anything).

        Each row says who added and reviewed the paper, and whether the patient stopped AI
        help for its visit (then what AI read is not shown).
        """
        result = await self.connection.execute(
            text(
                "SELECT d.id, d.visit_id, d.kind, d.source, d.mime, d.size, d.ocr_status, "
                "d.extracted, d.document_date, d.review_status, d.reviewed_at, d.created_at, "
                "v.service_date, u.name AS uploaded_by_name, r.name AS reviewed_by_name, "
                "EXISTS (SELECT 1 FROM consents a WHERE a.hospital_id = d.hospital_id "
                "AND a.visit_id = d.visit_id AND a.purpose = 'ai' "
                "AND a.withdrawn_at IS NOT NULL) AS ai_withdrawn "
                "FROM documents d JOIN visits v ON v.hospital_id = d.hospital_id "
                "AND v.id = d.visit_id LEFT JOIN users u ON u.hospital_id = d.hospital_id "
                "AND u.id = d.uploaded_by LEFT JOIN users r ON r.hospital_id = d.hospital_id "
                "AND r.id = d.reviewed_by WHERE d.hospital_id = :hospital_id "
                "AND d.visit_id = :visit_id ORDER BY d.created_at DESC, d.id DESC"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return [self._decrypt_reading(dict(row)) for row in result.mappings().all()]

    async def list_earlier(
        self, *, hospital_id: UUID, patient_id: UUID, exclude_visit_id: UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Papers from the patient's earlier registered visits, newest paper first."""
        result = await self.connection.execute(
            text(
                "SELECT d.id, d.visit_id, d.kind, d.source, d.mime, d.size, d.ocr_status, "
                "d.extracted, d.document_date, d.review_status, d.reviewed_at, d.created_at, "
                "v.service_date, u.name AS uploaded_by_name, r.name AS reviewed_by_name, "
                "EXISTS (SELECT 1 FROM consents a WHERE a.hospital_id = d.hospital_id "
                "AND a.visit_id = d.visit_id AND a.purpose = 'ai' "
                "AND a.withdrawn_at IS NOT NULL) AS ai_withdrawn "
                "FROM documents d JOIN visits v ON v.hospital_id = d.hospital_id "
                "AND v.id = d.visit_id LEFT JOIN users u ON u.hospital_id = d.hospital_id "
                "AND u.id = d.uploaded_by LEFT JOIN users r ON r.hospital_id = d.hospital_id "
                "AND r.id = d.reviewed_by WHERE d.hospital_id = :hospital_id "
                "AND v.patient_id = :patient_id AND v.id <> :exclude_visit_id "
                "AND v.token_number IS NOT NULL "
                "ORDER BY coalesce(d.document_date, v.service_date) DESC, d.created_at DESC "
                "LIMIT :limit"
            ),
            {
                "hospital_id": hospital_id,
                "patient_id": patient_id,
                "exclude_visit_id": exclude_visit_id,
                "limit": limit,
            },
        )
        return [self._decrypt_reading(dict(row)) for row in result.mappings().all()]

    async def list_for_patient_session(
        self, *, hospital_id: UUID, visit_id: UUID
    ) -> list[dict[str, Any]]:
        """The photos a patient added during this check-in (never what AI read from them)."""
        result = await self.connection.execute(
            text(
                "SELECT id, kind, size, created_at FROM documents "
                "WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                "AND source = 'patient' ORDER BY created_at, id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id},
        )
        return [dict(row) for row in result.mappings().all()]

    async def find(self, *, hospital_id: UUID, document_id: UUID) -> dict[str, Any] | None:
        """Which visit and patient a paper belongs to, for authorisation."""
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT d.id, d.visit_id, d.ocr_status, d.review_status, v.patient_id, "
                        "(v.token_number IS NOT NULL) AS registered FROM documents d "
                        "JOIN visits v ON v.hospital_id = d.hospital_id AND v.id = d.visit_id "
                        "WHERE d.hospital_id = :hospital_id AND d.id = :document_id"
                    ),
                    {"hospital_id": hospital_id, "document_id": document_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def content(self, *, hospital_id: UUID, document_id: UUID) -> tuple[str, bytes] | None:
        """The photo itself, decrypted, with its media type."""
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT d.mime, c.ciphertext FROM documents d JOIN document_contents c "
                        "ON c.hospital_id = d.hospital_id AND c.document_id = d.id "
                        "WHERE d.hospital_id = :hospital_id AND d.id = :document_id"
                    ),
                    {"hospital_id": hospital_id, "document_id": document_id},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        photo = decrypt_bytes(
            bytes(row["ciphertext"]),
            key=self.settings.app_data_key_bytes,
            associated_data=f"documents:content:{document_id}",
        )
        return str(row["mime"]), photo

    async def save_reading(
        self,
        *,
        hospital_id: UUID,
        document_id: UUID,
        reading: dict[str, Any] | None,
        status: str,
        document_date: date | None = None,
    ) -> None:
        """Store what AI read (encrypted), unless a doctor has already decided on it."""
        await self.connection.execute(
            text(
                "UPDATE documents SET ocr_status = :status, "
                "extracted = CAST(:extracted AS jsonb), document_date = :document_date "
                "WHERE hospital_id = :hospital_id AND id = :document_id "
                "AND review_status = 'unreviewed'"
            ),
            {
                "status": status,
                "extracted": json.dumps(
                    {
                        "ciphertext": encrypt_field(
                            json.dumps(reading, separators=(",", ":"), ensure_ascii=False),
                            key=self.settings.app_data_key_bytes,
                            associated_data=f"documents:extracted:{document_id}",
                        )
                    }
                )
                if reading is not None
                else None,
                "document_date": document_date,
                "hospital_id": hospital_id,
                "document_id": document_id,
            },
        )

    async def review(
        self, *, hospital_id: UUID, document_id: UUID, decision: str, reviewer_id: UUID
    ) -> bool:
        """The doctor accepts or rejects what AI read from a paper."""
        reviewed = await self.connection.scalar(
            text(
                "UPDATE documents SET review_status = :decision, reviewed_by = :reviewer_id, "
                "reviewed_at = now() WHERE hospital_id = :hospital_id AND id = :document_id "
                "AND ocr_status = 'complete' RETURNING id"
            ),
            {
                "decision": decision,
                "reviewer_id": reviewer_id,
                "hospital_id": hospital_id,
                "document_id": document_id,
            },
        )
        return reviewed is not None

    async def remove(self, *, hospital_id: UUID, visit_id: UUID, document_id: UUID) -> bool:
        """A patient takes back a photo they added (staff never delete documents)."""
        removed = await self.connection.scalar(
            text(
                "DELETE FROM documents WHERE hospital_id = :hospital_id AND visit_id = :visit_id "
                "AND id = :document_id AND source = 'patient' RETURNING id"
            ),
            {"hospital_id": hospital_id, "visit_id": visit_id, "document_id": document_id},
        )
        return removed is not None


async def read_ai_consent(
    engine: AsyncEngine, settings: Settings, *, hospital_id: UUID, visit_id: UUID
) -> str | None:
    """A visit's choice about online AI help, read in its own short transaction."""
    async with tenant_transaction(engine, hospital_id) as connection:
        return await IntakeRepository(connection, settings).ai_consent(
            hospital_id=hospital_id, visit_id=visit_id
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
    """Small allow-listed CRUD surface for administrator-managed records.

    Records are never physically deleted: visits, case sheets and the audit log refer to
    them. ``archive`` marks a record deleted; archived records are inactive, left out of
    every administration list, and cannot be updated again.
    """

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
        "kiosks": ("id", "label", "kind", "is_active", "last_seen_at", "paired_at", "created_at"),
    }
    # Visits in these states are still being looked after by their department.
    _LIVE_VISIT_STATES = ("registering", "awaiting_confirmation", "queued", "called", "in_consult")

    def __init__(self, connection: AsyncConnection, settings: Settings):
        self.connection = connection
        self.settings = settings

    async def list(
        self, resource: str, *, hospital_id: UUID, limit: int, cursor: str | None
    ) -> Page[dict[str, Any]]:
        if resource not in self._LISTS:
            raise ValueError("unsupported admin resource")
        page = await _list_page(
            self.connection,
            table_name=resource,
            hospital_id=hospital_id,
            limit=limit,
            cursor=cursor,
            cursor_key=self.settings.cursor_hmac_key.encode(),
            columns=self._LISTS[resource],
            exclude_archived=True,
        )
        if resource == "kiosks" and page.items:
            await self._add_kiosk_details(hospital_id, page)
        return page

    async def _add_kiosk_details(self, hospital_id: UUID, page: Page[dict[str, Any]]) -> None:
        """Pending-code state and registration counts for one page of kiosks (one query)."""
        statement = text(
            "SELECT k.id, "
            "(k.activation_code_hash IS NOT NULL AND (k.activation_code_expires_at IS NULL "
            "OR k.activation_code_expires_at > now())) AS activation_pending, "
            "k.activation_code_expires_at, "
            "(SELECT count(*) FROM visits v WHERE v.hospital_id = k.hospital_id "
            "AND v.kiosk_id = k.id AND v.service_date = :today) AS registrations_today, "
            "(SELECT count(*) FROM visits v WHERE v.hospital_id = k.hospital_id "
            "AND v.kiosk_id = k.id) AS registrations_total "
            "FROM kiosks k WHERE k.hospital_id = :hospital_id AND k.id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        result = await self.connection.execute(
            statement,
            {
                "hospital_id": hospital_id,
                "today": datetime.now(ZoneInfo("Asia/Kolkata")).date(),
                "ids": [item["id"] for item in page.items],
            },
        )
        details = {row["id"]: row for row in result.mappings().all()}
        for item in page.items:
            row = details.get(item["id"])
            pending = bool(row and row["activation_pending"])
            item["activation_pending"] = pending
            item["activation_code_expires_at"] = (
                row["activation_code_expires_at"] if row and pending else None
            )
            item["registrations_today"] = int(row["registrations_today"]) if row else 0
            item["registrations_total"] = int(row["registrations_total"]) if row else 0

    async def _require_live_department(
        self, hospital_id: UUID, department_id: UUID | None
    ) -> None:
        if department_id is None:
            return
        live = await self.connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM departments WHERE hospital_id = :hospital_id "
                "AND id = :department_id AND deleted_at IS NULL)"
            ),
            {"hospital_id": hospital_id, "department_id": department_id},
        )
        if not live:
            raise LookupError("department")

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
            await self._require_live_department(hospital_id, values["department_id"])
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
        if resource == "kiosks" and values.get("kind") == "mobile":
            # A phone check-in point: no device to pair, only its check-in code's digest.
            code = checkin_code(
                key=self.settings.app_hmac_key_bytes,
                hospital_id=hospital_id,
                point_id=resource_id,
                version=1,
            )
            await self.connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, kind, checkin_code_hash, "
                    "checkin_code_version) VALUES (:id, :hospital_id, :label, 'mobile', "
                    ":checkin_code_hash, 1)"
                ),
                {
                    "id": resource_id,
                    "hospital_id": hospital_id,
                    "label": values["label"],
                    "checkin_code_hash": checkin_code_digest(code),
                },
            )
            return {
                "id": str(resource_id),
                "label": values["label"],
                "kind": "mobile",
                "is_active": True,
                "paired_at": None,
                "activation_pending": False,
                "activation_code_expires_at": None,
            }
        if resource == "kiosks":
            await self.connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash, "
                    "activation_code_expires_at) VALUES (:id, :hospital_id, :label, "
                    ":activation_code_hash, :activation_code_expires_at)"
                ),
                {
                    "id": resource_id,
                    "hospital_id": hospital_id,
                    "label": values["label"],
                    "activation_code_hash": values["activation_code_hash"],
                    "activation_code_expires_at": values["activation_code_expires_at"],
                },
            )
            return {
                "id": str(resource_id),
                "label": values["label"],
                "kind": "device",
                "is_active": True,
                "paired_at": None,
                "activation_pending": True,
                "activation_code_expires_at": values["activation_code_expires_at"],
            }
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
        if resource == "users" and "department_id" in values:
            await self._require_live_department(hospital_id, values["department_id"])
        relation = table(
            resource,
            *[column(name) for name in (*self._LISTS[resource], "hospital_id", "deleted_at")],
        )
        result = await self.connection.execute(
            update(relation)
            .where(
                relation.c.hospital_id == bindparam("tenant_hospital_id"),
                relation.c.id == bindparam("resource_id"),
                relation.c.deleted_at.is_(None),
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

    async def issue_kiosk_code(
        self,
        *,
        hospital_id: UUID,
        kiosk_id: UUID,
        activation_code_hash: str,
        expires_at: datetime,
    ) -> dict[str, Any] | None:
        """Give an existing kiosk a fresh one-time code; its id and history stay the same.

        The device currently paired keeps working until the new code is used, at which
        point the new device takes over and the old one is signed out.
        """
        row = (
            (
                await self.connection.execute(
                    text(
                        "UPDATE kiosks SET activation_code_hash = :activation_code_hash, "
                        "activation_code_expires_at = :expires_at "
                        "WHERE hospital_id = :hospital_id AND id = :kiosk_id "
                        "AND kind = 'device' AND deleted_at IS NULL AND is_active = true "
                        "RETURNING id, label"
                    ),
                    {
                        "activation_code_hash": activation_code_hash,
                        "expires_at": expires_at,
                        "hospital_id": hospital_id,
                        "kiosk_id": kiosk_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def checkin_point(self, *, hospital_id: UUID, point_id: UUID) -> dict[str, Any] | None:
        """A phone check-in point that has not been deleted."""
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT id, label, is_active, checkin_code_version FROM kiosks "
                        "WHERE hospital_id = :hospital_id AND id = :point_id "
                        "AND kind = 'mobile' AND deleted_at IS NULL"
                    ),
                    {"hospital_id": hospital_id, "point_id": point_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def rotate_checkin_code(
        self, *, hospital_id: UUID, point_id: UUID, current_version: int
    ) -> int | None:
        """Move a check-in point to its next code; QR posters with the old code stop working."""
        version = current_version + 1
        code = checkin_code(
            key=self.settings.app_hmac_key_bytes,
            hospital_id=hospital_id,
            point_id=point_id,
            version=version,
        )
        updated = await self.connection.scalar(
            text(
                "UPDATE kiosks SET checkin_code_version = :version, "
                "checkin_code_hash = :checkin_code_hash "
                "WHERE hospital_id = :hospital_id AND id = :point_id AND kind = 'mobile' "
                "AND deleted_at IS NULL AND checkin_code_version = :current_version "
                "RETURNING checkin_code_version"
            ),
            {
                "version": version,
                "checkin_code_hash": checkin_code_digest(code),
                "hospital_id": hospital_id,
                "point_id": point_id,
                "current_version": current_version,
            },
        )
        return int(updated) if updated is not None else None

    async def blockers(
        self, resource: str, *, hospital_id: UUID, resource_id: UUID
    ) -> dict[str, int]:
        """What still depends on a record that must be moved before it can be deleted."""
        if resource != "departments":
            return {}
        row = (
            (
                await self.connection.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM visits WHERE hospital_id = :hospital_id "
                        "AND department_id = :department_id AND status IN :live_states) "
                        "AS open_visits, "
                        "(SELECT count(*) FROM users WHERE hospital_id = :hospital_id "
                        "AND department_id = :department_id AND deleted_at IS NULL) "
                        "AS assigned_staff"
                    ).bindparams(bindparam("live_states", expanding=True)),
                    {
                        "hospital_id": hospital_id,
                        "department_id": resource_id,
                        "live_states": list(self._LIVE_VISIT_STATES),
                    },
                )
            )
            .mappings()
            .one()
        )
        return {key: int(value) for key, value in row.items() if value}

    # Kiosks also lose their device credential and any unused code, so the device stops
    # working at once; staff lose every refresh token in ``archive``.
    _ARCHIVE: dict[str, str] = {
        "departments": (
            "UPDATE departments SET deleted_at = now(), is_active = false "
            "WHERE hospital_id = :hospital_id AND id = :resource_id AND deleted_at IS NULL "
            "RETURNING id"
        ),
        "users": (
            "UPDATE users SET deleted_at = now(), is_active = false "
            "WHERE hospital_id = :hospital_id AND id = :resource_id AND deleted_at IS NULL "
            "RETURNING id"
        ),
        "kiosks": (
            "UPDATE kiosks SET deleted_at = now(), is_active = false, secret_hash = NULL, "
            "activation_code_hash = NULL, activation_code_expires_at = NULL, "
            "checkin_code_hash = NULL "
            "WHERE hospital_id = :hospital_id AND id = :resource_id AND deleted_at IS NULL "
            "RETURNING id"
        ),
    }

    async def archive(self, resource: str, *, hospital_id: UUID, resource_id: UUID) -> bool:
        """Delete a record for administrators while keeping the history that refers to it."""
        if resource not in self._ARCHIVE:
            raise ValueError("unsupported admin resource")
        archived = await self.connection.scalar(
            text(self._ARCHIVE[resource]),
            {"hospital_id": hospital_id, "resource_id": resource_id},
        )
        if archived is None:
            return False
        if resource == "users":
            await self.connection.execute(
                text(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE hospital_id = :hospital_id "
                    "AND user_id = :user_id AND revoked_at IS NULL"
                ),
                {"hospital_id": hospital_id, "user_id": resource_id},
            )
        return True
