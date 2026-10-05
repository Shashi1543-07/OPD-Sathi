"""Operator-only bootstrap commands for synthetic local staff and kiosks."""

import argparse
import asyncio
import getpass
import os
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.core.crypto import encrypt_field
from app.core.ids import stable_uuid7, uuid7
from app.core.security import create_opaque_secret, digest_secret, hash_password
from app.db.session import set_hospital_scope


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
                await connection.execute(
                    text("INSERT INTO hospitals (id, name, code) VALUES (:id, :name, :code)"),
                    {"id": selected_hospital_id, "name": hospital_name, "code": hospital_code},
                )
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


async def _create_kiosk(*, hospital_id: UUID, label: str) -> str:
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
            await connection.execute(
                text(
                    "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                    "VALUES (:id, :hospital_id, :label, :activation_hash)"
                ),
                {
                    "id": kiosk_id,
                    "hospital_id": hospital_id,
                    "label": label,
                    "activation_hash": digest_secret(code),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, hospital_id, actor_type, action, resource_type, resource_id, "
                    "request_id, meta) VALUES (:id, :hospital_id, 'system', 'kiosk.provisioned', "
                    "'kiosk', :kiosk_id, 'operator-cli', '{}'::jsonb)"
                ),
                {"id": uuid7(), "hospital_id": hospital_id, "kiosk_id": kiosk_id},
            )
    finally:
        await engine.dispose()
    return code


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
                        "ON CONFLICT (hospital_id, code) DO NOTHING"
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
            existing = (
                (
                    await connection.execute(
                        text(
                            "SELECT lower(email) AS email FROM users "
                            "WHERE hospital_id = :hospital_id"
                        ),
                        {"hospital_id": hospital_id},
                    )
                )
                .scalars()
                .all()
            )
            existing_emails = set(existing)
            users = [("admin", "Demo Administrator", admin_email, admin_password, None)]
            users.extend(
                (
                    "doctor",
                    f"Demo Doctor {index:02d}",
                    f"doctor{index:02d}@demo.opdsathi.test",
                    staff_password,
                    departments[index % len(departments)]["id"],
                )
                for index in range(1, 13)
            )
            users.extend(
                (
                    "nurse",
                    f"Demo Nurse {index:02d}",
                    f"nurse{index:02d}@demo.opdsathi.test",
                    staff_password,
                    general_medicine_id,
                )
                for index in range(1, 4)
            )
            for role, name, email, password, department_id in users:
                if email in existing_emails:
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
                        "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash) "
                        "VALUES (:id, :hospital_id, :label, :activation_hash)"
                    ),
                    {
                        "id": uuid7(),
                        "hospital_id": hospital_id,
                        "label": f"Demo kiosk {index}",
                        "activation_hash": digest_secret(activation_code),
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
                encryption_key = settings.app_data_key_bytes
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
    kiosk = commands.add_parser("create-kiosk", help="Create a one-time kiosk activation code")
    kiosk.add_argument("--hospital-id", type=UUID, required=True)
    kiosk.add_argument("--label", required=True)
    commands.add_parser("seed-demo", help="Create the fictional Nagpur demonstration hospital")
    bulk = commands.add_parser("seed-bulk", help="Insert deterministic synthetic visits")
    bulk.add_argument("--visits", type=int, default=100_000)
    args = parser.parse_args()
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
    if args.command == "seed-bulk":
        asyncio.run(_seed_bulk(visits=args.visits))
        return
    if args.command == "create-admin":
        hospital_name = args.hospital_name
        hospital_code = args.hospital_code
        if args.hospital_id is None:
            hospital_name = hospital_name or input("New hospital name: ").strip()
            hospital_code = hospital_code or input("Unique hospital code: ").strip()
        name = args.name or input("Administrator name: ").strip()
        email = args.email or input("Administrator email: ").strip()
        password = getpass.getpass("Initial password (12+ characters): ")
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
