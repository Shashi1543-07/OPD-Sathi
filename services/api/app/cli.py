"""Operator-only bootstrap commands for synthetic local staff and kiosks."""

import argparse
import asyncio
import getpass
import json
import os
import random
import sys
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.core.crypto import encrypt_field
from app.core.ids import stable_uuid7, uuid7
from app.core.passwords import password_problem
from app.core.security import create_opaque_secret, digest_secret, hash_password
from app.db.repositories import CaseSheetRepository, DocumentRepository, PatientRepository
from app.db.session import set_hospital_scope
from app.demo_papers import prescription_png
from app.domain.case_sheet import CaseSheet
from app.domain.checkin import checkin_code, checkin_code_digest
from app.domain.patients import card_pin
from app.domain.queue.tokens import token_label


async def _create_admin(
    *,
    hospital_id: UUID | None,
    hospital_name: str | None,
    hospital_code: str | None,
    name: str,
    email: str,
    password: str,
) -> UUID:
    settings = get_settings()
    selected_hospital_id = hospital_id or uuid7()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, selected_hospital_id)
            hospital = await connection.scalar(
                text("SELECT id FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": selected_hospital_id},
            )
            if hospital is None and (hospital_name is None or hospital_code is None):
                raise SystemExit(
                    "Provide an existing --hospital-id or a new hospital name and code."
                )
            if hospital is not None and (hospital_name is not None or hospital_code is not None):
                raise SystemExit(
                    "A new hospital can only be created when --hospital-id is omitted."
                )
            if hospital is None:
                try:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO hospitals (id, name, code) VALUES (:id, :name, :code)"
                            ),
                            {
                                "id": selected_hospital_id,
                                "name": hospital_name,
                                "code": hospital_code,
                            },
                        )
                except IntegrityError as exc:
                    raise SystemExit(
                        f"A hospital with the code {hospital_code!r} already exists. "
                        "Choose another code."
                    ) from exc
            user_id = uuid7()
            await connection.execute(
                text(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash) "
                    "VALUES (:id, :hospital_id, 'admin', :name, :email, :password_hash)"
                ),
                {
                    "id": user_id,
                    "hospital_id": selected_hospital_id,
                    "name": name,
                    "email": email.strip().lower(),
                    "password_hash": hash_password(password),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', "
                    "'bootstrap.admin.created', 'user', :user_id, 'operator-cli', "
                    '\'{"role":"admin"}\'::jsonb)'
                ),
                {"id": uuid7(), "hospital_id": selected_hospital_id, "user_id": user_id},
            )
    finally:
        await engine.dispose()
    return selected_hospital_id


async def _reencrypt(
    *, hospital_id: UUID | None, apply: bool, batch: int, verify: bool = False
) -> int:
    """Re-seal records still encrypted with an older data key (docs/runbooks/key-rotation.md)."""
    from app.rekey import reencrypt_hospital

    settings = get_settings()
    ring = settings.app_data_keyring
    if hospital_id is not None:
        hospital_ids = [hospital_id]
    else:
        # The worker role may list every hospital; each is then processed in its own scope.
        lister = create_async_engine(settings.worker_database_url or settings.database_url)
        try:
            async with lister.connect() as connection:
                hospital_ids = list(
                    (await connection.execute(text("SELECT id FROM hospitals ORDER BY id")))
                    .scalars()
                    .all()
                )
        finally:
            await lister.dispose()
    engine = create_async_engine(settings.migrations_database_url)
    unreadable = 0
    try:
        for current in hospital_ids:
            print(f"Hospital {current} (current key {ring.current_id}):")
            async with engine.begin() as connection:
                await set_hospital_scope(connection, current)
                reports = await reencrypt_hospital(
                    connection,
                    hospital_id=current,
                    ring=ring,
                    apply=apply,
                    batch=batch,
                    progress=print,
                    verify=verify,
                )
                unreadable += sum(report.unreadable for report in reports.values())
                if apply:
                    await connection.execute(
                        text(
                            "INSERT INTO audit_log (id, hospital_id, actor_type, action, "
                            "resource_type, request_id, meta) VALUES (:id, :hospital_id, "
                            "'system', 'key_rotation.reencrypted', 'hospital', 'operator-cli', "
                            "CAST(:meta AS jsonb))"
                        ),
                        {
                            "id": uuid7(),
                            "hospital_id": current,
                            "meta": json.dumps(
                                {
                                    "key_id": ring.current_id,
                                    "resealed": sum(r.resealed for r in reports.values()),
                                    "unreadable": sum(r.unreadable for r in reports.values()),
                                }
                            ),
                        },
                    )
    finally:
        await engine.dispose()
    return unreadable


async def _reset_mfa(*, hospital_id: UUID, email: str, operator: str) -> UUID:
    """Remove a staff member's two-step sign-in, from the server, with an audit entry.

    For when the last administrator has lost both their phone and their recovery codes; any
    other case goes through another administrator (Settings -> Users -> Reset two-step).
    """
    settings = get_settings()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            user_id = await connection.scalar(
                text(
                    "UPDATE users SET mfa_enabled_at = NULL, mfa_secret_enc = NULL, "
                    "mfa_last_step = NULL, mfa_recovery_hashes = '[]'::jsonb "
                    "WHERE hospital_id = :hospital_id AND lower(email) = :email "
                    "AND deleted_at IS NULL RETURNING id"
                ),
                {"hospital_id": hospital_id, "email": email.strip().lower()},
            )
            if user_id is None:
                raise SystemExit("No staff account with that email at that hospital.")
            await connection.execute(
                text(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE hospital_id = "
                    ":hospital_id AND user_id = :user_id AND revoked_at IS NULL"
                ),
                {"hospital_id": hospital_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', "
                    "'break_glass.mfa_reset', 'user', :user_id, 'operator-cli', "
                    "CAST(:meta AS jsonb))"
                ),
                {
                    "id": uuid7(),
                    "hospital_id": hospital_id,
                    "user_id": user_id,
                    "meta": json.dumps({"operator": operator}),
                },
            )
    finally:
        await engine.dispose()
    return UUID(str(user_id))


async def _setup_hospital(
    *,
    hospital_name: str,
    hospital_code: str,
    admin_name: str,
    admin_email: str,
    admin_password: str,
    standard_departments: bool,
) -> UUID:
    """Onboard a hospital: its record, first administrator and starting departments.

    The standard departments use the codes the routing rules know (GENMED, ORTHO, ENT,
    OBG, PAED, DERM, OPHTH, SURG), so kiosks route patients from the first day. The
    hospital's administrator can rename, deactivate or delete them and add their own.
    """
    hospital_id = await _create_admin(
        hospital_id=None,
        hospital_name=hospital_name,
        hospital_code=hospital_code,
        name=admin_name,
        email=admin_email,
        password=admin_password,
    )
    if not standard_departments:
        return hospital_id
    engine = create_async_engine(get_settings().migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            for code, prefix, en, hi, mr in _DEMO_DEPARTMENTS:
                await connection.execute(
                    text(
                        "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                        "name_hi, name_mr) VALUES (:id, :hospital_id, :code, :prefix, :en, "
                        ":hi, :mr) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": uuid7(),
                        "hospital_id": hospital_id,
                        "code": code,
                        "prefix": prefix,
                        "en": en,
                        "hi": hi,
                        "mr": mr,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', "
                    "'bootstrap.departments.created', 'hospital', :hospital_id, "
                    "'operator-cli', '{}'::jsonb)"
                ),
                {"id": uuid7(), "hospital_id": hospital_id},
            )
    finally:
        await engine.dispose()
    return hospital_id


def _read_new_password(*, from_stdin: bool = False) -> str:
    """The first line of stdin, ADMIN_PASSWORD, or an interactive, confirmed prompt.

    Scripted installs should pipe the password in (``--admin-password-stdin``): unlike an
    environment variable it never shows in ``docker inspect`` or the process list.
    """
    if from_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if problem := password_problem(password):
            raise SystemExit(f"The administrator password on stdin is refused: {problem}")
        return password
    password = os.getenv("ADMIN_PASSWORD", "")
    if password:
        if problem := password_problem(password):
            raise SystemExit(f"ADMIN_PASSWORD is refused: {problem}")
        return password
    while True:
        password = getpass.getpass("Administrator password (12+ characters): ")
        if problem := password_problem(password):
            print(problem)
            continue
        if getpass.getpass("Type it again: ") == password:
            return password
        print("The two passwords did not match. Try again.")


def _activation_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(hours=get_settings().kiosk_activation_code_hours)


async def _create_kiosk(*, hospital_id: UUID, label: str, reuse: bool = False) -> str:
    """Return a one-time activation code for a new kiosk.

    With ``reuse``, an existing kiosk with the same label gets the fresh code instead, so
    running the demo again re-pairs the same kiosk (same id and history) rather than
    piling up a new kiosk record each time.
    """
    settings = get_settings()
    code = create_opaque_secret()
    kiosk_id = uuid7()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            hospital = await connection.scalar(
                text("SELECT id FROM hospitals WHERE id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            if hospital is None:
                raise SystemExit("Hospital does not exist; create the first admin for it first.")
            existing = (
                await connection.scalar(
                    text(
                        "SELECT id FROM kiosks WHERE hospital_id = :hospital_id "
                        "AND label = :label AND kind = 'device' AND deleted_at IS NULL "
                        "ORDER BY created_at, id LIMIT 1"
                    ),
                    {"hospital_id": hospital_id, "label": label},
                )
                if reuse
                else None
            )
            if existing is not None:
                kiosk_id = existing
                await connection.execute(
                    text(
                        "UPDATE kiosks SET activation_code_hash = :activation_hash, "
                        "activation_code_expires_at = :expires_at, is_active = true "
                        "WHERE hospital_id = :hospital_id AND id = :id"
                    ),
                    {
                        "activation_hash": digest_secret(code),
                        "expires_at": _activation_expiry(),
                        "hospital_id": hospital_id,
                        "id": kiosk_id,
                    },
                )
            else:
                await connection.execute(
                    text(
                        "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash, "
                        "activation_code_expires_at) "
                        "VALUES (:id, :hospital_id, :label, :activation_hash, :expires_at)"
                    ),
                    {
                        "id": kiosk_id,
                        "hospital_id": hospital_id,
                        "label": label,
                        "activation_hash": digest_secret(code),
                        "expires_at": _activation_expiry(),
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', :action, "
                    "'kiosk', :kiosk_id, 'operator-cli', '{}'::jsonb)"
                ),
                {
                    "id": uuid7(),
                    "hospital_id": hospital_id,
                    "kiosk_id": kiosk_id,
                    "action": (
                        "kiosk.activation_code_issued"
                        if existing is not None
                        else "kiosk.provisioned"
                    ),
                },
            )
    finally:
        await engine.dispose()
    return code


async def _demo_checkin(*, hospital_id: UUID, label: str) -> str:
    """The phone check-in code for the demo hospital's QR poster, creating the point once.

    Running the demo again keeps the same point and the same QR (the code is derived, not
    stored), so a poster printed for one rehearsal still works at the next.
    """
    settings = get_settings()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            if (
                await connection.scalar(
                    text("SELECT id FROM hospitals WHERE id = :hospital_id"),
                    {"hospital_id": hospital_id},
                )
                is None
            ):
                raise SystemExit("Hospital does not exist; run seed-demo first.")
            point = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, checkin_code_version FROM kiosks "
                            "WHERE hospital_id = :hospital_id AND label = :label "
                            "AND kind = 'mobile' AND deleted_at IS NULL "
                            "ORDER BY created_at, id LIMIT 1"
                        ),
                        {"hospital_id": hospital_id, "label": label},
                    )
                )
                .mappings()
                .first()
            )
            if point is not None:
                point_id, version = point["id"], int(point["checkin_code_version"])
                await connection.execute(
                    text(
                        "UPDATE kiosks SET is_active = true "
                        "WHERE hospital_id = :hospital_id AND id = :id"
                    ),
                    {"hospital_id": hospital_id, "id": point_id},
                )
            else:
                point_id, version = uuid7(), 1
                code = checkin_code(
                    key=settings.app_hmac_key_bytes,
                    hospital_id=hospital_id,
                    point_id=point_id,
                    version=version,
                )
                await connection.execute(
                    text(
                        "INSERT INTO kiosks (id, hospital_id, label, kind, checkin_code_hash, "
                        "checkin_code_version) VALUES (:id, :hospital_id, :label, 'mobile', "
                        ":checkin_code_hash, 1)"
                    ),
                    {
                        "id": point_id,
                        "hospital_id": hospital_id,
                        "label": label,
                        "checkin_code_hash": checkin_code_digest(code),
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO audit_log "
                        "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                        "request_id, meta) VALUES (:id, :hospital_id, 'system', "
                        "'kiosk.checkin_point_created', 'kiosk', :kiosk_id, 'operator-cli', "
                        "'{}'::jsonb)"
                    ),
                    {"id": uuid7(), "hospital_id": hospital_id, "kiosk_id": point_id},
                )
    finally:
        await engine.dispose()
    return checkin_code(
        key=settings.app_hmac_key_bytes, hospital_id=hospital_id, point_id=point_id, version=version
    )


_DEMO_DEPARTMENTS = (
    ("GENMED", "MED", "General Medicine", "सामान्य चिकित्सा", "सामान्य औषधोपचार"),
    ("ORTHO", "ORT", "Orthopaedics", "अस्थिरोग", "अस्थिरोग"),
    ("ENT", "ENT", "Ear, Nose and Throat", "कान, नाक और गला", "कान, नाक आणि घसा"),
    ("OBG", "OBG", "Obstetrics and Gynaecology", "प्रसूति एवं स्त्री रोग", "प्रसूती व स्त्रीरोग"),
    ("PAED", "PED", "Paediatrics", "बाल रोग", "बालरोग"),
    ("DERM", "DER", "Dermatology", "त्वचा रोग", "त्वचारोग"),
    ("OPHTH", "OPH", "Ophthalmology", "नेत्र रोग", "नेत्ररोग"),
    ("SURG", "SUR", "General Surgery", "सामान्य शल्य चिकित्सा", "सामान्य शस्त्रक्रिया"),
)


async def _seed_demo() -> tuple[UUID, list[str]]:
    settings = get_settings()
    admin_password = os.getenv("DEMO_ADMIN_PASSWORD", "")
    staff_password = os.getenv("DEMO_STAFF_PASSWORD", "")
    admin_email = os.getenv("DEMO_ADMIN_EMAIL", "admin@demo.opdsathi.test").strip().lower()
    if len(admin_password) < 12 or len(staff_password) < 12:
        raise SystemExit(
            "Set DEMO_ADMIN_PASSWORD and DEMO_STAFF_PASSWORD (12+ chars) in the environment."
        )
    hospital_code = "demo-nagpur-government"
    hospital_id = stable_uuid7(f"opd-saathi:{hospital_code}")
    engine = create_async_engine(settings.migrations_database_url)
    new_kiosk_codes: list[str] = []
    created_admin = False
    created_staff = False
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            await connection.execute(
                text(
                    "INSERT INTO hospitals (id, name, code, timezone) VALUES "
                    "(:id, :name, :code, 'Asia/Kolkata') ON CONFLICT (code) DO NOTHING"
                ),
                {
                    "id": hospital_id,
                    "name": "Demo Government Hospital, Nagpur",
                    "code": hospital_code,
                },
            )
            for code, prefix, en, hi, mr in _DEMO_DEPARTMENTS:
                await connection.execute(
                    text(
                        "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                        "name_hi, name_mr) VALUES (:id, :hospital_id, :code, :prefix, :en, :hi, "
                        ":mr) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {
                        "id": stable_uuid7(f"{hospital_id}:department:{code}"),
                        "hospital_id": hospital_id,
                        "code": code,
                        "prefix": prefix,
                        "en": en,
                        "hi": hi,
                        "mr": mr,
                    },
                )
            departments = (
                (
                    await connection.execute(
                        text("SELECT id, code FROM departments WHERE hospital_id = :hospital_id"),
                        {"hospital_id": hospital_id},
                    )
                )
                .mappings()
                .all()
            )
            general_medicine_id = next(row["id"] for row in departments if row["code"] == "GENMED")
            department_ids_by_code = {row["code"]: row["id"] for row in departments}
            doctor_department_ids = [department_ids_by_code[code] for code, *_ in _DEMO_DEPARTMENTS]
            existing = (
                (
                    await connection.execute(
                        text(
                            "SELECT lower(email) AS email FROM users "
                            "WHERE hospital_id = :hospital_id AND deleted_at IS NULL"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .scalars()
                .all()
            )
            existing_emails = set(existing)
            # The demo roster: one administrator, one doctor per department (doctor01 is
            # General Medicine) and five nurses who work General Medicine with doctor01.
            users = [("admin", "Demo Administrator", admin_email, admin_password, None)]
            users.extend(
                (
                    "doctor",
                    f"Demo Doctor {index:02d}",
                    f"doctor{index:02d}@demo.opdsathi.test",
                    staff_password,
                    department_id,
                )
                for index, department_id in enumerate(doctor_department_ids, start=1)
            )
            users.extend(
                (
                    "nurse",
                    f"Demo Nurse {index:02d}",
                    f"nurse{index:02d}@demo.opdsathi.test",
                    staff_password,
                    general_medicine_id,
                )
                for index in range(1, 6)
            )
            # Earlier demo seeds made a second doctor for some departments; retire them.
            roster = [email for role, _name, email, _password, _department in users]
            await connection.execute(
                text(
                    "UPDATE users SET deleted_at = now(), is_active = false "
                    "WHERE hospital_id = :hospital_id AND deleted_at IS NULL "
                    "AND role IN ('doctor', 'nurse') "
                    "AND lower(email) LIKE '%@demo.opdsathi.test' "
                    "AND (lower(email) LIKE 'doctor%' OR lower(email) LIKE 'nurse%') "
                    "AND lower(email) NOT IN :roster"
                ).bindparams(bindparam("roster", expanding=True)),
                {"hospital_id": hospital_id, "roster": roster},
            )
            for role, name, email, password, department_id in users:
                if email in existing_emails:
                    # Demo accounts always match the passwords configured for this run.
                    await connection.execute(
                        text(
                            "UPDATE users SET password_hash = :password_hash, failed_logins = 0, "
                            "locked_until = NULL WHERE hospital_id = :hospital_id "
                            "AND lower(email) = :email AND deleted_at IS NULL"
                        ),
                        {
                            "password_hash": hash_password(password),
                            "hospital_id": hospital_id,
                            "email": email,
                        },
                    )
                    if role == "doctor":
                        await connection.execute(
                            text(
                                "UPDATE users SET department_id = :department_id "
                                "WHERE hospital_id = :hospital_id AND lower(email) = :email "
                                "AND role = 'doctor' AND deleted_at IS NULL"
                            ),
                            {
                                "department_id": department_id,
                                "hospital_id": hospital_id,
                                "email": email,
                            },
                        )
                    continue
                await connection.execute(
                    text(
                        "INSERT INTO users (id, hospital_id, role, name, email, password_hash, "
                        "department_id) VALUES (:id, :hospital_id, :role, :name, :email, "
                        ":password_hash, :department_id)"
                    ),
                    {
                        "id": uuid7(),
                        "hospital_id": hospital_id,
                        "role": role,
                        "name": name,
                        "email": email,
                        "password_hash": hash_password(password),
                        "department_id": department_id,
                    },
                )
                created_admin = created_admin or role == "admin"
                created_staff = created_staff or role in {"doctor", "nurse"}
            kiosk_count = await connection.scalar(
                text("SELECT count(*) FROM kiosks WHERE hospital_id = :hospital_id"),
                {"hospital_id": hospital_id},
            )
            for index in range(int(kiosk_count or 0) + 1, 3):
                activation_code = create_opaque_secret()
                new_kiosk_codes.append(activation_code)
                await connection.execute(
                    text(
                        "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash, "
                        "activation_code_expires_at) "
                        "VALUES (:id, :hospital_id, :label, :activation_hash, :expires_at)"
                    ),
                    {
                        "id": uuid7(),
                        "hospital_id": hospital_id,
                        "label": f"Demo kiosk {index}",
                        "activation_hash": digest_secret(activation_code),
                        "expires_at": _activation_expiry(),
                    },
                )
    finally:
        await engine.dispose()
    print(f"Demo hospital: {hospital_id}")
    if created_admin:
        print(f"New admin credentials: {admin_email} / {admin_password}")
    if created_staff:
        print(
            "New staff credentials: doctor01@demo.opdsathi.test and "
            f"nurse01@demo.opdsathi.test / {staff_password}"
        )
    for index, code in enumerate(new_kiosk_codes, start=1):
        print(f"One-time activation code for newly provisioned kiosk {index}: {code}")
    return hospital_id, new_kiosk_codes


async def _seed_bulk(*, visits: int) -> None:
    if not 1 <= visits <= 1_000_000:
        raise SystemExit("--visits must be between 1 and 1000000")
    settings = get_settings()
    hospital_id = stable_uuid7("opd-saathi:demo-nagpur-government")
    engine = create_async_engine(settings.migrations_database_url)
    # Seeded pseudo-randomness is intentional for reproducible synthetic fixtures.
    rng = random.Random(20250308)  # nosec B311
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            patient_ids = list(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM patients WHERE hospital_id = :hospital_id ORDER BY id"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .scalars()
                .all()
            )
            if not patient_ids:
                now = datetime.now(UTC)
                patient_ids = [uuid7() for _ in range(1000)]
                encryption_key = settings.app_data_keyring
                await connection.execute(
                    text(
                        "INSERT INTO patients (id, hospital_id, name_enc, year_of_birth, sex, "
                        "preferred_language, created_at) VALUES (:id, :hospital_id, :name_enc, "
                        ":year, :sex, :language, :created_at)"
                    ),
                    [
                        {
                            "id": patient_id,
                            "hospital_id": hospital_id,
                            "name_enc": encrypt_field(
                                f"Synthetic Patient {index:04d}",
                                key=encryption_key,
                                associated_data=f"patients:name_enc:{patient_id}",
                            ),
                            "year": rng.randint(1940, 2015),
                            "sex": rng.choice(("female", "male", "other", "unknown")),
                            "language": rng.choice(("en", "hi", "mr")),
                            "created_at": now,
                        }
                        for index, patient_id in enumerate(patient_ids, start=1)
                    ],
                )
            departments = list(
                (
                    await connection.execute(
                        text(
                            "SELECT id FROM departments "
                            "WHERE hospital_id = :hospital_id ORDER BY code"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .scalars()
                .all()
            )
            if not departments:
                raise SystemExit("Run `make seed` before `make seed-bulk`.")
            counts: dict[tuple[UUID, date], int] = {}
            existing_counts = (
                (
                    await connection.execute(
                        text(
                            "SELECT department_id, service_date, max(token_number) AS last_token "
                            "FROM visits WHERE hospital_id = :hospital_id "
                            "AND token_number IS NOT NULL "
                            "GROUP BY department_id, service_date"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .mappings()
                .all()
            )
            counts.update(
                {
                    (row["department_id"], row["service_date"]): row["last_token"]
                    for row in existing_counts
                }
            )
            start_date = date.today() - timedelta(days=89)
            batch: list[dict[str, Any]] = []
            created = 0
            for _ in range(visits):
                department_id = rng.choice(departments)
                service_day = start_date + timedelta(days=rng.randrange(90))
                counter_key = (department_id, service_day)
                counts[counter_key] = counts.get(counter_key, 0) + 1
                visit_status = rng.choice(("queued", "called", "in_consult", "done", "cancelled"))
                registered_at = datetime.now(UTC) - timedelta(
                    days=(date.today() - service_day).days
                )
                batch.append(
                    {
                        "id": uuid7(),
                        "hospital_id": hospital_id,
                        "patient_id": rng.choice(patient_ids),
                        "department_id": department_id,
                        "status": visit_status,
                        "urgency": rng.choices(("routine", "priority", "urgent"), (80, 17, 3))[0],
                        "token_number": counts[counter_key],
                        "token_label": f"D-{counts[counter_key]:03d}",
                        "service_date": service_day,
                        "language": rng.choice(("en", "hi", "mr")),
                        "age_years": rng.randint(0, 100),
                        "registered_at": registered_at,
                        "queued_at": (
                            registered_at
                            if visit_status in {"queued", "called", "in_consult"}
                            else None
                        ),
                    }
                )
                if len(batch) == 1000:
                    await connection.execute(
                        text(
                            "INSERT INTO visits (id, hospital_id, patient_id, department_id, "
                            "status, urgency, token_number, token_label, service_date, language, "
                            "age_years, registered_at, queued_at) "
                            "VALUES (:id, :hospital_id, :patient_id, :department_id, :status, "
                            ":urgency, :token_number, :token_label, :service_date, :language, "
                            ":age_years, :registered_at, :queued_at)"
                        ),
                        batch,
                    )
                    created += len(batch)
                    batch.clear()
                    if created % 10_000 == 0:
                        print(f"Inserted {created} synthetic visits")
            if batch:
                await connection.execute(
                    text(
                        "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, "
                        "urgency, token_number, token_label, service_date, language, age_years, "
                        "registered_at, queued_at) "
                        "VALUES (:id, :hospital_id, :patient_id, :department_id, :status, "
                        ":urgency, :token_number, :token_label, :service_date, :language, "
                        ":age_years, :registered_at, :queued_at)"
                    ),
                    batch,
                )
                created += len(batch)
    finally:
        await engine.dispose()
    print(f"Inserted {created} synthetic visits using fixed RNG seed 20250308.")


_DEMO_WORKFLOW_RECORDS = (
    (
        "Asha Sample",
        34,
        "female",
        "GENMED",
        "queued",
        "priority",
        "Fever and cough for two days",
        2,
    ),
    ("Ravi Sample", 52, "male", "GENMED", "called", "routine", "Knee pain after walking", 1),
    ("Mina Sample", 8, "female", "PAED", "queued", "routine", "Sore throat since yesterday", 1),
    (
        "Dev Sample",
        27,
        "male",
        "ORTHO",
        "in_consult",
        "priority",
        "Ankle pain after a minor twist",
        1,
    ),
    ("Noor Sample", 41, "female", "GENMED", "done", "routine", "Headache since yesterday", 1),
)


def _demo_workflow_ids() -> tuple[UUID, list[UUID], list[UUID]]:
    hospital_id = stable_uuid7("opd-saathi:demo-nagpur-government")
    patients = [
        stable_uuid7(f"{hospital_id}:workflow-demo:patient:{index}")
        for index in range(1, len(_DEMO_WORKFLOW_RECORDS) + 1)
    ]
    visits = [
        stable_uuid7(f"{hospital_id}:workflow-demo:visit:{index}")
        for index in range(1, len(_DEMO_WORKFLOW_RECORDS) + 1)
    ]
    return hospital_id, patients, visits


async def _seed_demo_workflow() -> None:
    """Create a tiny, repeatable patient-to-staff walkthrough dataset."""
    settings = get_settings()
    hospital_id, patient_ids, visit_ids = _demo_workflow_ids()
    engine = create_async_engine(settings.migrations_database_url)
    created = 0
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            departments = {
                row["code"]: row["id"]
                for row in (
                    await connection.execute(
                        text("SELECT id, code FROM departments WHERE hospital_id = :hospital_id"),
                        {"hospital_id": hospital_id},
                    )
                ).mappings()
            }
            if not departments:
                raise SystemExit("Run `python -m app.cli seed-demo` before seed-demo-data.")
            today = date.today()
            token_numbers: dict[str, int] = {}
            for index, (record, patient_id, visit_id) in enumerate(
                zip(_DEMO_WORKFLOW_RECORDS, patient_ids, visit_ids, strict=True), start=1
            ):
                name, age, sex, department_code, status, urgency, complaint, duration = record
                department_id = departments.get(department_code)
                if department_id is None:
                    raise SystemExit(f"Demo hospital is missing department {department_code}.")
                exists = await connection.scalar(
                    text(
                        "SELECT id FROM visits WHERE hospital_id = :hospital_id AND id = :visit_id"
                    ),
                    {"hospital_id": hospital_id, "visit_id": visit_id},
                )
                if exists:
                    continue
                patient_exists = await connection.scalar(
                    text(
                        "SELECT id FROM patients "
                        "WHERE hospital_id = :hospital_id AND id = :patient_id"
                    ),
                    {"hospital_id": hospital_id, "patient_id": patient_id},
                )
                if patient_exists is None:
                    await connection.execute(
                        text(
                            "INSERT INTO patients (id, hospital_id, name_enc, year_of_birth, sex, "
                            "preferred_language) VALUES (:id, :hospital_id, :name_enc, "
                            ":year, :sex, 'en')"
                        ),
                        {
                            "id": patient_id,
                            "hospital_id": hospital_id,
                            "name_enc": encrypt_field(
                                name,
                                key=settings.app_data_keyring,
                                associated_data=f"patients:name_enc:{patient_id}",
                            ),
                            "year": today.year - age,
                            "sex": sex,
                        },
                    )
                if department_code not in token_numbers:
                    token_numbers[department_code] = int(
                        await connection.scalar(
                            text(
                                "SELECT coalesce(max(token_number), 0) FROM visits "
                                "WHERE hospital_id = :hospital_id "
                                "AND department_id = :department_id "
                                "AND service_date = :service_date"
                            ),
                            {
                                "hospital_id": hospital_id,
                                "department_id": department_id,
                                "service_date": today,
                            },
                        )
                        or 0
                    )
                token_numbers[department_code] += 1
                queued_at = datetime.now(UTC) - timedelta(minutes=12 + index * 7)
                service_day = today
                await connection.execute(
                    text(
                        "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, "
                        "urgency, token_number, token_label, service_date, language, age_years, "
                        "registered_at, queued_at, called_at, consult_started_at, "
                        "consult_ended_at) "
                        "VALUES (:id, :hospital_id, :patient_id, :department_id, "
                        ":status, :urgency, "
                        ":token_number, :token_label, :service_date, 'en', :age, :registered_at, "
                        ":queued_at, :called_at, :consult_started_at, :consult_ended_at)"
                    ),
                    {
                        "id": visit_id,
                        "hospital_id": hospital_id,
                        "patient_id": patient_id,
                        "department_id": department_id,
                        "status": status,
                        "urgency": urgency,
                        "token_number": token_numbers[department_code],
                        "token_label": f"SAMPLE-{index:03d}",
                        "service_date": service_day,
                        "age": age,
                        "registered_at": queued_at,
                        "queued_at": queued_at,
                        "called_at": queued_at
                        if status in {"called", "in_consult", "done"}
                        else None,
                        "consult_started_at": queued_at
                        if status in {"in_consult", "done"}
                        else None,
                        "consult_ended_at": queued_at if status == "done" else None,
                    },
                )
                sheet = {
                    "schema_version": "1.0",
                    "language": "en",
                    "chief_complaint": {
                        "text_original": complaint,
                        "text_en": complaint,
                        "confidence": 0.95,
                    },
                    "hpi": {
                        "onset": {
                            "text_original": f"{duration} day(s)",
                            "text_en": f"{duration} day(s)",
                        },
                        "associated_symptoms": [],
                    },
                    "past_history": {"conditions": [], "surgeries": []},
                    "medications": [],
                    "allergies": [],
                    "family_history": [],
                    "social": {},
                    "red_flags": [],
                    "routing": {
                        "department_code": department_code,
                        "confidence": 0.9,
                        "reason_en": "Synthetic walkthrough record",
                        "needs_nurse_confirmation": False,
                    },
                    "field_meta": {},
                    "patient_confirmation": {
                        "confirmed_by_patient": True,
                        "corrections": [],
                        "needs_assistance": False,
                    },
                }
                await CaseSheetRepository(connection, settings).save(
                    hospital_id=hospital_id, visit_id=visit_id, data=sheet
                )
                await connection.execute(
                    text(
                        "UPDATE case_sheets SET confirmed_by_patient = true, "
                        "confirmed_at = :confirmed_at "
                        "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
                    ),
                    {"confirmed_at": queued_at, "hospital_id": hospital_id, "visit_id": visit_id},
                )
                created += 1
    finally:
        await engine.dispose()
    print(f"Demo workflow data ready: {created} new fictional patient visits (5 records total).")
    print("To remove only these sample records, run: python -m app.cli clear-demo-data")


async def _clear_demo_workflow() -> None:
    settings = get_settings()
    hospital_id, patient_ids, visit_ids = _demo_workflow_ids()
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            deleted_visits = await connection.execute(
                text(
                    "DELETE FROM visits WHERE hospital_id = :hospital_id AND id = ANY(:visit_ids)"
                ),
                {"hospital_id": hospital_id, "visit_ids": visit_ids},
            )
            await connection.execute(
                text(
                    "DELETE FROM patients WHERE hospital_id = :hospital_id "
                    "AND id = ANY(:patient_ids)"
                ),
                {"hospital_id": hospital_id, "patient_ids": patient_ids},
            )
    finally:
        await engine.dispose()
    print(f"Removed the sample workflow visits. Rows removed: {deleted_visits.rowcount}.")


# The demo's returning patient: her OPD card joins today's check-in to an earlier visit, so the
# doctor sees her earlier visit and the prescription she brought to it. Everything is invented.
_RETURNING_NAME = "Sunita Sample"
_RETURNING_AGE = 52
_RETURNING_DAYS_AGO = 21


def _demo_returning_ids(hospital_id: UUID) -> tuple[UUID, UUID]:
    """Her patient record and earlier visit (stable, so seeding again changes nothing)."""
    return (
        stable_uuid7(f"{hospital_id}:returning-demo:patient"),
        stable_uuid7(f"{hospital_id}:returning-demo:visit"),
    )


def _returning_case_sheet() -> dict[str, Any]:
    """What she reported at the earlier visit, in Marathi with the English beside it."""

    def said(marathi: str, english: str) -> dict[str, str]:
        return {"text_original": marathi, "text_en": english}

    return CaseSheet.model_validate(
        {
            "schema_version": "1.0",
            "language": "mr",
            "chief_complaint": said("थकवा आणि खूप तहान लागते", "Tiredness and a lot of thirst"),
            "hpi": {
                "onset": said("दोन आठवड्यांपासून", "For two weeks"),
                "associated_symptoms": [said("वारंवार लघवी", "Passing urine often")],
            },
            "past_history": {"conditions": ["Type 2 diabetes", "High blood pressure"]},
            "medications": [
                {"name": "Metformin", "dose": "500 mg twice a day", "source": "patient_stated"},
                {"name": "Amlodipine", "dose": "5 mg at night", "source": "patient_stated"},
            ],
            "allergies": [said("पेनिसिलिन", "Penicillin")],
            "family_history": [said("वडिलांना मधुमेह", "Father has diabetes")],
            "routing": {
                "department_code": "GENMED",
                "confidence": 0.9,
                "reason_en": "Synthetic demonstration record",
                "needs_nurse_confirmation": False,
            },
            "patient_confirmation": {"confirmed_by_patient": True},
        }
    ).model_dump(mode="json")


async def _seed_demo_returning(
    hospital_id: UUID | None = None, *, reset: bool = False
) -> dict[str, str]:
    """Create (once) the returning patient, her earlier visit and its paper; print her card.

    ``reset`` also removes visits joined to her record since then (earlier run-throughs), so
    the next demo again starts from the one earlier visit, with the same OPD card.
    """
    settings = get_settings()
    hospital_id = hospital_id or stable_uuid7("opd-saathi:demo-nagpur-government")
    patient_id, visit_id = _demo_returning_ids(hospital_id)
    service_day = date.today() - timedelta(days=_RETURNING_DAYS_AGO)
    removed = 0
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            if reset:
                # A follow-up visit points at an earlier one; unlink them before deleting.
                await connection.execute(
                    text(
                        "UPDATE visits SET previous_visit_id = NULL "
                        "WHERE hospital_id = :hospital_id AND patient_id = :patient_id "
                        "AND id <> :visit_id"
                    ),
                    {"hospital_id": hospital_id, "patient_id": patient_id, "visit_id": visit_id},
                )
                later = await connection.execute(
                    text(
                        "DELETE FROM visits WHERE hospital_id = :hospital_id "
                        "AND patient_id = :patient_id AND id <> :visit_id"
                    ),
                    {"hospital_id": hospital_id, "patient_id": patient_id, "visit_id": visit_id},
                )
                removed = int(later.rowcount)
            department = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, token_prefix FROM departments "
                            "WHERE hospital_id = :hospital_id AND code = 'GENMED'"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .mappings()
                .first()
            )
            if department is None:
                raise SystemExit("Run `python -m app.cli seed-demo` before seed-demo-returning.")
            earlier = (
                (
                    await connection.execute(
                        text(
                            "SELECT service_date FROM visits "
                            "WHERE hospital_id = :hospital_id AND id = :visit_id"
                        ),
                        {"hospital_id": hospital_id, "visit_id": visit_id},
                    )
                )
                .mappings()
                .first()
            )
            if earlier is not None:
                service_day = earlier["service_date"]
            else:
                await connection.execute(
                    text(
                        "INSERT INTO patients (id, hospital_id, name_enc, year_of_birth, sex, "
                        "preferred_language) VALUES (:id, :hospital_id, :name_enc, :year, "
                        "'female', 'mr') ON CONFLICT (id) DO NOTHING"
                    ),
                    {
                        "id": patient_id,
                        "hospital_id": hospital_id,
                        "name_enc": encrypt_field(
                            _RETURNING_NAME,
                            key=settings.app_data_keyring,
                            associated_data=f"patients:name_enc:{patient_id}",
                        ),
                        "year": service_day.year - _RETURNING_AGE,
                    },
                )
                token_number = 1 + int(
                    await connection.scalar(
                        text(
                            "SELECT coalesce(max(token_number), 0) FROM visits "
                            "WHERE hospital_id = :hospital_id AND department_id = :department_id "
                            "AND service_date = :service_date"
                        ),
                        {
                            "hospital_id": hospital_id,
                            "department_id": department["id"],
                            "service_date": service_day,
                        },
                    )
                    or 0
                )
                # About ten in the morning in Nagpur.
                arrived = datetime(
                    service_day.year, service_day.month, service_day.day, 4, 30, tzinfo=UTC
                )
                await connection.execute(
                    text(
                        "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, "
                        "urgency, token_number, token_label, service_date, language, age_years, "
                        "registered_at, queued_at, called_at, consult_started_at, "
                        "consult_ended_at) VALUES (:id, :hospital_id, :patient_id, "
                        ":department_id, 'done', 'routine', :token_number, :token_label, "
                        ":service_date, 'mr', :age, :arrived, :arrived, :called, :started, :ended)"
                    ),
                    {
                        "id": visit_id,
                        "hospital_id": hospital_id,
                        "patient_id": patient_id,
                        "department_id": department["id"],
                        "token_number": token_number,
                        "token_label": token_label(department["token_prefix"], token_number),
                        "service_date": service_day,
                        "age": _RETURNING_AGE,
                        "arrived": arrived,
                        "called": arrived + timedelta(minutes=24),
                        "started": arrived + timedelta(minutes=26),
                        "ended": arrived + timedelta(minutes=38),
                    },
                )
                await CaseSheetRepository(connection, settings).save(
                    hospital_id=hospital_id, visit_id=visit_id, data=_returning_case_sheet()
                )
                await connection.execute(
                    text(
                        "UPDATE case_sheets SET confirmed_by_patient = true, "
                        "confirmed_at = :confirmed_at "
                        "WHERE hospital_id = :hospital_id AND visit_id = :visit_id"
                    ),
                    {"confirmed_at": arrived, "hospital_id": hospital_id, "visit_id": visit_id},
                )
                # She showed her old prescription that day; AI help was not used, so it is a
                # photo only (the doctor reads it).
                document_id = await DocumentRepository(connection, settings).add(
                    hospital_id=hospital_id,
                    visit_id=visit_id,
                    kind="prescription",
                    mime="image/png",
                    content=prescription_png(),
                    source="patient",
                    uploaded_by=None,
                    ocr_status="skipped",
                )
                await connection.execute(
                    text(
                        "UPDATE documents SET created_at = :at "
                        "WHERE hospital_id = :hospital_id AND id = :document_id"
                    ),
                    {"at": arrived, "hospital_id": hospital_id, "document_id": document_id},
                )
            number, version = await PatientRepository(connection, settings).assign_mrn(
                hospital_id=hospital_id, patient_id=patient_id, year=service_day.year
            )
    finally:
        await engine.dispose()
    pin = card_pin(
        key=settings.app_hmac_key_bytes,
        hospital_id=hospital_id,
        patient_id=patient_id,
        version=version,
    )
    if removed:
        print(f"Removed {removed} later visit(s) of the returning patient (earlier run-throughs).")
    print(
        f"Returning patient (fictional): OPD number {number}, PIN {pin}; "
        f"age {_RETURNING_AGE}, female; earlier visit on {service_day.isoformat()}."
    )
    return {"opd_number": number, "pin": pin, "visit_id": str(visit_id)}


async def _clear_demo_returning(hospital_id: UUID | None = None) -> int:
    """Remove the returning patient with every visit joined to her record (rehearsals too)."""
    settings = get_settings()
    hospital_id = hospital_id or stable_uuid7("opd-saathi:demo-nagpur-government")
    patient_id, _ = _demo_returning_ids(hospital_id)
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            # A follow-up visit points at the earlier one; unlink them before deleting.
            await connection.execute(
                text(
                    "UPDATE visits SET previous_visit_id = NULL "
                    "WHERE hospital_id = :hospital_id AND patient_id = :patient_id"
                ),
                {"hospital_id": hospital_id, "patient_id": patient_id},
            )
            removed = await connection.execute(
                text(
                    "DELETE FROM visits WHERE hospital_id = :hospital_id "
                    "AND patient_id = :patient_id"
                ),
                {"hospital_id": hospital_id, "patient_id": patient_id},
            )
            await connection.execute(
                text("DELETE FROM patients WHERE hospital_id = :hospital_id AND id = :patient_id"),
                {"hospital_id": hospital_id, "patient_id": patient_id},
            )
    finally:
        await engine.dispose()
    print(f"Removed the demo's returning patient and {removed.rowcount} visit(s).")
    return int(removed.rowcount)


async def _ai_check() -> int:
    """Ask the configured AI and voice services for a tiny answer; no patient data is sent.

    Returns how many configured services did not answer (the demo still runs without them:
    rules, tapping and typing, and the browser's own voices).
    """
    from app.ai.providers.llm.factory import build_llm_provider
    from app.ai.providers.llm.gemini import GeminiProvider
    from app.ai.speech import SpeechService, SpeechUnavailableError

    settings = get_settings()
    failures = 0
    llm = build_llm_provider(settings)
    try:
        if not isinstance(llm, GeminiProvider):
            print(f"AI: off ({getattr(llm, 'name', 'mock')}). The demo runs on the rules.")
        else:
            started = monotonic()
            try:
                answer = await llm.generate_json(
                    "Reply with ready set to true.",
                    {
                        "type": "object",
                        "properties": {"ready": {"type": "boolean"}},
                        "required": ["ready"],
                    },
                    timeout=15.0,
                )
                answered = answer.get("ready") is True
            except Exception:
                answered = False
            if answered:
                print(f"AI: Gemini ({llm.model}) answered in {monotonic() - started:.1f} s.")
            else:
                failures += 1
                print("AI: Gemini did not answer. Check the internet connection and the key.")
        speech = SpeechService(settings, llm)
        if not speech.server_tts:
            print("Voice: the browser's own voices (no voice service configured).")
        else:
            started = monotonic()
            try:
                clip = await speech.synthesize("नमस्कार", language="mr")
                print(
                    f"Voice: {speech.tts_mode} answered in {monotonic() - started:.1f} s "
                    f"({len(clip.content) // 1024} kB of audio)."
                )
            except SpeechUnavailableError:
                failures += 1
                print(f"Voice: {speech.tts_mode} did not answer; read-aloud uses browser voices.")
    finally:
        if isinstance(llm, GeminiProvider):
            await llm.aclose()
    return failures


async def _demo_history(command: str, days: int = 30) -> None:
    """Seed or remove the believable month of finished OPD days, or clear seed-bulk rows."""
    from app.demo_history import clear_bulk, clear_history, refresh_reports, seed_history

    settings = get_settings()
    hospital_id = stable_uuid7("opd-saathi:demo-nagpur-government")
    engine = create_async_engine(settings.migrations_database_url)
    try:
        async with engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            if command == "seed":
                filled, created = await seed_history(
                    connection, settings, hospital_id=hospital_id, days=days
                )
                message = (
                    f"Demo history: added {created} visits over {filled} new day(s) "
                    f"(last {days} days; days already filled were left as they are)."
                )
            elif command == "clear":
                removed = await clear_history(connection, hospital_id=hospital_id)
                message = f"Removed {removed} demo-history visits and their sample patients."
            else:
                visits, patients = await clear_bulk(connection, hospital_id=hospital_id)
                message = (
                    f"Removed {visits} seed-bulk load-test visits and {patients} patients "
                    "left without any visit."
                )
        async with engine.begin() as connection:
            refreshed = await refresh_reports(connection)
    finally:
        await engine.dispose()
    print(message)
    print(
        "Reports refreshed."
        if refreshed
        else "Reports will show the change within 5 minutes (the worker refreshes them)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("queue-rebuild", help="Rebuild Redis queues from PostgreSQL")
    admin = commands.add_parser("create-admin", help="Create an initial hospital administrator")
    admin.add_argument("--hospital-id", type=UUID)
    admin.add_argument("--hospital-name")
    admin.add_argument("--hospital-code")
    admin.add_argument("--name")
    admin.add_argument("--email")
    setup = commands.add_parser(
        "setup-hospital",
        help="Onboard a new hospital: its first administrator and standard departments",
    )
    setup.add_argument("--name", help="Hospital name, as shown on its kiosks")
    setup.add_argument("--code", help="Short unique code, e.g. gmc-nagpur")
    setup.add_argument("--admin-name")
    setup.add_argument("--admin-email")
    setup.add_argument(
        "--admin-password-stdin",
        action="store_true",
        help="Read the administrator's password from the first line of stdin (unattended)",
    )
    setup.add_argument(
        "--no-standard-departments",
        action="store_true",
        help="Start with no departments (the administrator creates them)",
    )
    reencrypt = commands.add_parser(
        "reencrypt",
        help="Re-seal records with the current data key after a rotation (dry run by default)",
    )
    reencrypt.add_argument("--hospital-id", type=UUID)
    reencrypt.add_argument("--apply", action="store_true", help="Write the re-sealed values")
    reencrypt.add_argument("--batch", type=int, default=500)
    commands.add_parser(
        "content-status",
        help="List each clinical content file and whether it is approved or still a draft",
    )
    commands.add_parser(
        "verify-decrypt",
        help="Decrypt every encrypted value with the keys in the ring (restore drills)",
    )
    reset_mfa = commands.add_parser(
        "reset-mfa",
        help="Break-glass: remove a staff member's two-step sign-in (audited)",
    )
    reset_mfa.add_argument("--hospital-id", type=UUID, required=True)
    reset_mfa.add_argument("--email", required=True)
    reset_mfa.add_argument(
        "--operator", required=True, help="Full name of the person running this (recorded)"
    )
    kiosk = commands.add_parser("create-kiosk", help="Create a one-time kiosk activation code")
    kiosk.add_argument("--hospital-id", type=UUID, required=True)
    kiosk.add_argument("--label", required=True)
    commands.add_parser("seed-demo", help="Create the fictional Nagpur demonstration hospital")
    commands.add_parser("seed-demo-data", help="Create five removable synthetic walkthrough visits")
    commands.add_parser("clear-demo-data", help="Remove only the synthetic walkthrough visits")
    returning = commands.add_parser(
        "seed-demo-returning",
        help="Add the demo's returning patient (an OPD card, an earlier visit, an old paper)",
    )
    returning.add_argument(
        "--reset",
        action="store_true",
        help="Also remove visits joined to her record since (earlier run-throughs)",
    )
    commands.add_parser(
        "clear-demo-returning", help="Remove the demo's returning patient and all her visits"
    )
    commands.add_parser(
        "ai-check", help="Ask the AI and voice services for a tiny answer (no patient data)"
    )
    demo_kiosk = commands.add_parser(
        "demo-kiosk", help="Create a kiosk for the demo hospital and print its activation link"
    )
    demo_kiosk.add_argument("--web-url", default=os.getenv("WEB_ORIGIN", "http://localhost:5173"))
    demo_checkin = commands.add_parser(
        "demo-checkin",
        help="Create the demo hospital's phone check-in QR and print the link phones open",
    )
    demo_checkin.add_argument(
        "--web-url",
        default=os.getenv("PUBLIC_WEB_URL") or os.getenv("WEB_ORIGIN", "http://localhost:5173"),
        help="The address phones use: this PC's Wi-Fi address and the web port",
    )
    bulk = commands.add_parser("seed-bulk", help="Insert deterministic synthetic visits")
    bulk.add_argument("--visits", type=int, default=100_000)
    history = commands.add_parser(
        "seed-demo-history",
        help="Fill the last N days with believable finished OPD days for the demo hospital",
    )
    history.add_argument("--days", type=int, default=30, choices=range(1, 121), metavar="1-120")
    commands.add_parser("clear-demo-history", help="Remove only the seeded demo-history visits")
    commands.add_parser(
        "clear-bulk", help="Remove seed-bulk load-test visits from the demo hospital"
    )
    args = parser.parse_args()
    if args.command == "content-status":
        from app.domain.content_status import content_status

        statuses = content_status()
        for source, status in statuses.items():
            print(f"{status:<30} {source}")
        drafts = sum(status != "approved" for status in statuses.values())
        print(f"{len(statuses) - drafts} approved, {drafts} awaiting clinical sign-off")
        return
    if args.command == "queue-rebuild":
        import redis.asyncio as redis

        from app.workers.main import rebuild_all

        async def run_queue_rebuild() -> None:
            settings = get_settings()
            engine = create_async_engine(settings.worker_database_url or settings.database_url)
            client = redis.from_url(  # type: ignore[no-untyped-call]
                settings.redis_state_url, decode_responses=True
            )
            try:
                async with engine.begin() as connection:
                    count = await rebuild_all(connection, client, settings=settings)
                print(f"Rebuilt Redis queue entries: {count}")
            finally:
                await client.aclose()
                await engine.dispose()

        asyncio.run(run_queue_rebuild())
        return
    if args.command == "seed-demo":
        asyncio.run(_seed_demo())
        return
    if args.command == "demo-kiosk":
        demo_hospital = stable_uuid7("opd-saathi:demo-nagpur-government")
        code = asyncio.run(_create_kiosk(hospital_id=demo_hospital, label="Demo kiosk", reuse=True))
        base = str(args.web_url).split(",")[0].rstrip("/")
        print("Open this link once on the kiosk device to activate it:")
        print(f"  {base}/kiosk?activate={code}")
        return
    if args.command == "demo-checkin":
        demo_hospital = stable_uuid7("opd-saathi:demo-nagpur-government")
        code = asyncio.run(_demo_checkin(hospital_id=demo_hospital, label="Waiting hall phone QR"))
        base = str(args.web_url).split(",")[0].rstrip("/")
        print("Phones on the hospital Wi-Fi check in at:")
        print(f"  {base}/m/{code}")
        return
    if args.command == "seed-demo-data":
        asyncio.run(_seed_demo_workflow())
        return
    if args.command == "clear-demo-data":
        asyncio.run(_clear_demo_workflow())
        return
    if args.command == "seed-demo-returning":
        asyncio.run(_seed_demo_returning(reset=args.reset))
        return
    if args.command == "clear-demo-returning":
        asyncio.run(_clear_demo_returning())
        return
    if args.command == "ai-check":
        raise SystemExit(1 if asyncio.run(_ai_check()) else 0)
    if args.command == "seed-bulk":
        asyncio.run(_seed_bulk(visits=args.visits))
        return
    if args.command == "seed-demo-history":
        asyncio.run(_demo_history("seed", days=args.days))
        return
    if args.command == "clear-demo-history":
        asyncio.run(_demo_history("clear"))
        return
    if args.command == "clear-bulk":
        asyncio.run(_demo_history("bulk"))
        return
    if args.command == "setup-hospital":
        hospital_name = args.name or input("Hospital name (shown on its kiosks): ").strip()
        hospital_code = (
            args.code or input("Short unique hospital code (e.g. gmc-nagpur): ").strip()
        ).lower()
        admin_name = args.admin_name or input("Administrator's full name: ").strip()
        admin_email = args.admin_email or input("Administrator's email: ").strip()
        if not all((hospital_name, hospital_code, admin_name, admin_email)):
            raise SystemExit("Hospital name, code, administrator name and email are required.")
        password = _read_new_password(from_stdin=args.admin_password_stdin)
        try:
            hospital_id = asyncio.run(
                _setup_hospital(
                    hospital_name=hospital_name,
                    hospital_code=hospital_code,
                    admin_name=admin_name,
                    admin_email=admin_email,
                    admin_password=password,
                    standard_departments=not args.no_standard_departments,
                )
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Hospital ready: {hospital_name} ({hospital_id})")
        print(f"Administrator: {admin_email.strip().lower()}")
        if not args.no_standard_departments:
            print(f"Departments: {len(_DEMO_DEPARTMENTS)} standard departments created.")
        print(
            "Next: sign in at /staff, then use Settings to add staff accounts and to "
            "register each kiosk (each one gets its own activation code)."
        )
        return
    if args.command == "verify-decrypt":
        unreadable = asyncio.run(
            _reencrypt(hospital_id=None, apply=False, batch=500, verify=True)
        )
        if unreadable:
            raise SystemExit(f"{unreadable} encrypted value(s) could not be read.")
        print("Every encrypted value was read with the keys in the ring.")
        return
    if args.command == "reencrypt":
        unreadable = asyncio.run(
            _reencrypt(hospital_id=args.hospital_id, apply=args.apply, batch=args.batch)
        )
        if not args.apply:
            print("Dry run: nothing was changed. Run again with --apply to re-seal.")
        if unreadable:
            raise SystemExit(
                f"{unreadable} value(s) could not be read with the keys in the ring. Do not "
                "retire any key until they are explained (docs/runbooks/key-rotation.md)."
            )
        return
    if args.command == "reset-mfa":
        if len(args.operator.strip()) < 3:
            raise SystemExit("--operator must name the person running this command.")
        user_id = asyncio.run(
            _reset_mfa(
                hospital_id=args.hospital_id, email=args.email, operator=args.operator.strip()
            )
        )
        print(f"Two-step sign-in removed for {args.email} ({user_id}); their sessions ended.")
        print("They set it up again at their next sign-in. This was recorded in the audit log.")
        return
    if args.command == "create-admin":
        hospital_name = args.hospital_name
        hospital_code = args.hospital_code
        if args.hospital_id is None:
            hospital_name = hospital_name or input("New hospital name: ").strip()
            hospital_code = hospital_code or input("Unique hospital code: ").strip()
        name = args.name or input("Administrator name: ").strip()
        email = args.email or input("Administrator email: ").strip()
        password = _read_new_password()
        hospital_id = asyncio.run(
            _create_admin(
                hospital_id=args.hospital_id,
                hospital_name=hospital_name,
                hospital_code=hospital_code,
                name=name,
                email=email,
                password=password,
            )
        )
        print(f"Administrator created for hospital {hospital_id}.")
        return
    code = asyncio.run(_create_kiosk(hospital_id=args.hospital_id, label=args.label))
    print(f"One-time kiosk activation code: {code}")


if __name__ == "__main__":
    main()
