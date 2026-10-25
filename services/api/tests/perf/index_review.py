"""Index review on a million visits (Phase 7, task 7.8.4). A tool, not a test: run by hand.

Seeds a synthetic hospital with a busy district hospital's history (VISITS visits over DAYS
days, their patients, case sheets, consents, interviews, timelines and audit trail) straight
into a scratch database, gives it a waiting queue today, puts a few patients through the real
kiosk interview, then drives every hot route and worker job through the real application.
PostgreSQL's pg_stat_statements reports each statement's mean and worst time; auto_explain
logs the plans of slow ones.

Needs a throwaway PostgreSQL 16 started with
  shared_preload_libraries=pg_stat_statements,auto_explain
  auto_explain.log_min_duration=20, auto_explain.log_analyze=on
the roles from scripts/init-db.sh and the migrations applied, plus a Redis. Settings come
from the environment as for the integration tests (TEST_DATABASE_URL, ...), and
SCALE_SUPERUSER_URL (postgresql://postgres:...@host:port/osa) seeds past row-level security.
A later run reuses the seeded hospital; on a new day it adds only that day's queue.

    cd services/api && python -m tests.perf.index_review

Synthetic data only; never point it at a hospital's database.
"""

import asyncio
import os
import time
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
import redis.asyncio as redis

from app.core.crypto import encrypt_field, lookup_digest
from app.core.ids import uuid7
from app.core.security import digest_secret, hash_password
from app.db.session import create_engine
from app.domain.patients import format_mrn
from app.workers.main import (
    close_previous_days,
    collect_operational_metrics,
    rebuild_all,
    rebuild_analytics,
    refresh_analytics,
    relay_once,
)
from tests.integration.synthetic import (
    DEPARTMENTS,
    STAFF,
    STAFF_PASSWORD,
    SyntheticHospital,
    activate_kiosk,
    api_client,
    complete_intake,
    integration_settings,
    start_kiosk_session,
)

VISITS = int(os.getenv("SCALE_VISITS", "1000000"))
DAYS = int(os.getenv("SCALE_DAYS", "500"))
PATIENTS = int(os.getenv("SCALE_PATIENTS", "400000"))
TODAY_QUEUED = int(os.getenv("SCALE_TODAY_QUEUED", "300"))
SLOW_MS = float(os.getenv("SCALE_SLOW_MS", "20"))

UUID7 = """
CREATE OR REPLACE FUNCTION pg_temp.uuid7_at(ts timestamptz) RETURNS uuid AS $$
  SELECT (lpad(to_hex((extract(epoch FROM ts) * 1000)::bigint), 12, '0') || '7'
    || substr(md5(random()::text), 1, 3) || '8' || substr(md5(random()::text), 1, 15))::uuid
$$ LANGUAGE sql VOLATILE
"""

# Each past visit's records, as the application writes them. Old case sheets and answers are
# unreadable here (not real ciphertext): the routes treat a sheet they cannot decrypt as
# absent, and no route reads another visit's answers.
HISTORY_RECORDS = (
    "INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data, "
    "confirmed_by_patient, confirmed_at, created_at) SELECT gen_random_uuid(), "
    "hospital_id, id, '1.0', jsonb_build_object('ciphertext', 'v1:history'), true, "
    "registered_at, registered_at FROM visits WHERE hospital_id = $1",
    "INSERT INTO consents (id, hospital_id, visit_id, language, text_version, "
    "text_sha256, granted, granted_at, channel, purpose, created_at) SELECT "
    "gen_random_uuid(), hospital_id, id, language, 'consent-1.0', repeat('a', 64), "
    "true, registered_at, 'kiosk', 'care', registered_at FROM visits "
    "WHERE hospital_id = $1",
    # 8 to 15 answers a visit, as a kiosk interview gives; every fourth one spoken.
    "INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, question_id, "
    "input_mode, text_original, stt_provider, stt_confidence, created_at) SELECT "
    "gen_random_uuid(), v.hospital_id, v.id, s.i, 'q' || s.i, CASE WHEN s.i % 4 = 3 "
    "THEN 'voice' ELSE 'touch' END, 'v1:' || md5(v.id::text || s.i) || md5(s.i || "
    "v.id::text), CASE WHEN s.i % 4 = 3 THEN 'sarvam' END, CASE WHEN s.i % 4 = 3 THEN 0.9 "
    "END, v.registered_at + s.i * interval '8 seconds' FROM visits v CROSS JOIN LATERAL "
    "generate_series(0, 7 + (hashtext(v.id::text) & 7)) AS s(i) WHERE v.hospital_id = $1",
    "INSERT INTO visit_events (id, hospital_id, visit_id, from_status, to_status, "
    "actor_type, actor_id, created_at) SELECT gen_random_uuid(), v.hospital_id, v.id, "
    "e.f, e.t, e.a, CASE WHEN e.a = 'user' THEN v.called_by END, v.registered_at "
    "+ e.offset_ FROM visits v CROSS JOIN (VALUES ('awaiting_confirmation', 'queued', "
    "'patient', interval '2 minutes'), ('queued', 'called', 'user', "
    "interval '40 minutes'), ('called', 'in_consult', 'user', interval '45 minutes'), "
    "('in_consult', 'done', 'user', interval '55 minutes')) AS e(f, t, a, offset_) "
    "WHERE v.hospital_id = $1 AND v.status = 'done'",
    "INSERT INTO audit_log (id, hospital_id, actor_type, actor_id, action, "
    "resource_type, resource_id, request_id, created_at) SELECT gen_random_uuid(), "
    "v.hospital_id, a.actor_type, CASE WHEN a.actor_type = 'user' THEN v.called_by END, "
    "a.action, 'visit', v.id, md5(v.id::text || a.action), v.registered_at + a.offset_ "
    "FROM visits v CROSS JOIN (VALUES ('patient', 'intake.confirmed', "
    "interval '2 minutes'), ('user', 'queue.called', interval '40 minutes'), ('user', "
    "'case_sheet.viewed', interval '46 minutes')) AS a(actor_type, action, offset_) "
    "WHERE v.hospital_id = $1 AND v.called_by IS NOT NULL",
    "INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, level, "
    "evidence, acknowledged_by, acknowledged_at, created_at) SELECT gen_random_uuid(), "
    "hospital_id, id, 'tile', 'chest_pain', 'urgent', 'chest_pain', called_by, "
    "registered_at + interval '3 minutes', registered_at FROM visits "
    "WHERE hospital_id = $1 AND urgency = 'urgent'",
)

# Today's waiting line and what each waiting visit already has: its case sheet, consent,
# answers and arrival on the timeline, and an urgent danger sign nobody has acknowledged.
TODAY_QUEUE = """
WITH waiting AS (
  INSERT INTO visits (id, hospital_id, patient_id, department_id, status, urgency,
    token_number, token_label, service_date, language, age_years, registered_at, queued_at)
  SELECT pg_temp.uuid7_at(t.at), $1, p.id, dep.id, 'queued',
    CASE WHEN s.n % 31 = 0 THEN 'urgent' WHEN s.n % 7 = 0 THEN 'priority' ELSE 'routine' END,
    500 + s.n, dep.token_prefix || '-' || (500 + s.n), (now() AT TIME ZONE 'Asia/Kolkata')::date,
    (ARRAY['hi','mr','en'])[1 + s.n % 3], 1 + (s.n * 7) % 90, t.at, t.at
  FROM (SELECT id, token_prefix, row_number() OVER (ORDER BY id) AS di
        FROM departments WHERE id = ANY($2::uuid[])) dep
  CROSS JOIN generate_series(1, $3) AS s(n)
  CROSS JOIN LATERAL (SELECT now() - s.n * interval '20 seconds' AS at) t
  JOIN pids p ON p.rn = 1 + ((s.n * 31 + dep.di * 7) % $4)
  RETURNING id, hospital_id, language, urgency, registered_at
), sheets AS (
  INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data,
    confirmed_by_patient, confirmed_at, created_at)
  SELECT gen_random_uuid(), hospital_id, id, '1.0',
    jsonb_build_object('ciphertext', 'v1:today'), true, registered_at, registered_at
  FROM waiting
), consents AS (
  INSERT INTO consents (id, hospital_id, visit_id, language, text_version, text_sha256,
    granted, granted_at, channel, purpose, created_at)
  SELECT gen_random_uuid(), hospital_id, id, language, 'consent-1.0', repeat('a', 64), true,
    registered_at, 'kiosk', 'care', registered_at
  FROM waiting
), answers AS (
  INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, question_id,
    input_mode, text_original, created_at)
  SELECT gen_random_uuid(), w.hospital_id, w.id, s.i, 'q' || s.i, 'touch',
    'v1:' || md5(w.id::text || s.i), w.registered_at + s.i * interval '8 seconds'
  FROM waiting w CROSS JOIN generate_series(0, 11) AS s(i)
), arrivals AS (
  INSERT INTO visit_events (id, hospital_id, visit_id, from_status, to_status, actor_type,
    created_at)
  SELECT gen_random_uuid(), hospital_id, id, 'awaiting_confirmation', 'queued', 'patient',
    registered_at
  FROM waiting
), alerts AS (
  INSERT INTO red_flag_events (id, hospital_id, visit_id, source, rule_id, level, evidence,
    created_at)
  SELECT gen_random_uuid(), hospital_id, id, 'tile', 'chest_pain', 'urgent', 'chest_pain',
    registered_at
  FROM waiting WHERE urgency = 'urgent'
)
SELECT count(*) FROM waiting
"""


def phone(index: int) -> str:
    return f"9{index:09d}"[:10]


def this_year() -> int:
    return datetime.now(ZoneInfo("Asia/Kolkata")).year


async def attach_or_create(superuser_url: str, settings: Any) -> SyntheticHospital:
    """The review's hospital: the one already in the scratch database, or a new one.

    It is never deleted: the database is thrown away after the review.
    """
    connection = await asyncpg.connect(superuser_url)
    try:
        hospital_id = await connection.fetchval(
            "SELECT id FROM hospitals ORDER BY created_at LIMIT 1"
        )
        if hospital_id is None:
            hospital_id = uuid7()
            await connection.execute(
                "INSERT INTO hospitals (id, name, code) VALUES ($1, 'Scale review hospital', $2)",
                hospital_id, f"scale-{hospital_id}",
            )
            for code, prefix, name in DEPARTMENTS:
                await connection.execute(
                    "INSERT INTO departments (id, hospital_id, code, token_prefix, name_en, "
                    "name_hi, name_mr) VALUES ($1, $2, $3, $4, $5, $5, $5)",
                    uuid7(), hospital_id, code, prefix, name,
                )
            password_hash = hash_password(STAFF_PASSWORD)
            for name, (role, department) in STAFF.items():
                await connection.execute(
                    "INSERT INTO users (id, hospital_id, role, name, email, password_hash, "
                    "department_id) VALUES ($1, $2, $3, $4, $5, $6, (SELECT id FROM departments "
                    "WHERE hospital_id = $2 AND code = $7))",
                    uuid7(), hospital_id, role, f"Synthetic {name}",
                    f"{name}-{hospital_id}@example.test", password_hash, department,
                )
            await connection.execute(
                "INSERT INTO kiosks (id, hospital_id, label, activation_code_hash, secret_hash, "
                "paired_at) VALUES ($1, $2, 'Synthetic kiosk', $3, $4, now())",
                uuid7(), hospital_id, digest_secret(uuid7().hex), digest_secret(uuid7().hex),
            )
        departments = {
            row["code"]: row["id"]
            for row in await connection.fetch(
                "SELECT code, id FROM departments WHERE hospital_id = $1", hospital_id
            )
        }
        users = {
            row["email"].split("-", 1)[0]: row["id"]
            for row in await connection.fetch(
                "SELECT email, id FROM users WHERE hospital_id = $1", hospital_id
            )
        }
        kiosk_id = await connection.fetchval(
            "SELECT id FROM kiosks WHERE hospital_id = $1 AND label = 'Synthetic kiosk'",
            hospital_id,
        )
    finally:
        await connection.close()
    return SyntheticHospital(
        settings=settings, hospital_id=hospital_id, departments=departments, users=users,
        kiosk_id=kiosk_id, activation_code="",
    )


async def number_patients(connection: asyncpg.Connection, hospital_id: UUID) -> None:
    """pids(rn, id): the hospital's patients numbered 1..n, to spread visits over them."""
    await connection.execute(
        "CREATE TEMP TABLE IF NOT EXISTS pids AS SELECT row_number() OVER (ORDER BY id) AS rn, "
        "id FROM patients WHERE hospital_id = $1",
        hospital_id,
    )
    await connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS pids_rn ON pids (rn)")


async def seed(superuser_url: str, hospital: Any) -> dict[str, Any]:
    """The history, written as the database owner (COPY and bulk INSERT skip the API)."""
    settings = hospital.settings
    hospital_id: UUID = hospital.hospital_id
    departments = list(hospital.departments.values())
    doctor = hospital.users["doctor"]
    per_day = max(1, VISITS // (DAYS * len(departments)))
    connection = await asyncpg.connect(superuser_url)
    started = time.perf_counter()
    try:
        await connection.execute(UUID7)
        # Patients, with real encryption and phone digests so lookups behave as in production.
        year = this_year() % 100
        rows = []
        for index in range(PATIENTS):
            patient_id = uuid7()
            rows.append(
                (
                    patient_id,
                    hospital_id,
                    encrypt_field(
                        f"Synthetic Patient {index}",
                        key=settings.app_data_keyring,
                        associated_data=f"patients:name_enc:{patient_id}",
                    ),
                    lookup_digest(phone(index), key=settings.app_hmac_key_bytes)
                    if index % 5
                    else None,
                    1940 + index % 80,
                    ("female", "male")[index % 2],
                    ("hi", "mr", "en")[index % 3],
                    format_mrn(year, index + 1),
                )
            )
        await connection.copy_records_to_table(
            "patients",
            records=rows,
            columns=[
                "id", "hospital_id", "name_enc", "phone_hmac", "year_of_birth", "sex",
                "preferred_language", "mrn",
            ],
        )
        print(f"patients: {PATIENTS} in {time.perf_counter() - started:.0f} s", flush=True)
        await number_patients(connection, hospital_id)
        # Past days, all closed: seen, absent or cancelled.
        await connection.execute(
            """
            INSERT INTO visits (id, hospital_id, patient_id, department_id, status, urgency,
              token_number, token_label, service_date, language, age_years, registered_at,
              queued_at, called_at, consult_started_at, consult_ended_at, assigned_doctor_id,
              called_by, acuity_score)
            SELECT pg_temp.uuid7_at(t.at), $1, p.id, dep.id,
              CASE WHEN s.n % 20 = 0 THEN 'no_show' WHEN s.n % 29 = 0 THEN 'cancelled'
                   ELSE 'done' END,
              CASE WHEN s.n % 31 = 0 THEN 'urgent' WHEN s.n % 7 = 0 THEN 'priority'
                   ELSE 'routine' END,
              s.n, dep.token_prefix || '-' || lpad(s.n::text, 3, '0'), d.day,
              (ARRAY['hi','mr','en'])[1 + s.n % 3], 1 + (s.n * 7) % 90, t.at,
              t.at + interval '2 minutes',
              CASE WHEN s.n % 29 = 0 THEN NULL ELSE t.at + interval '40 minutes' END,
              CASE WHEN s.n % 20 = 0 OR s.n % 29 = 0 THEN NULL
                   ELSE t.at + interval '45 minutes' END,
              CASE WHEN s.n % 20 = 0 OR s.n % 29 = 0 THEN NULL
                   ELSE t.at + interval '55 minutes' END,
              CASE WHEN s.n % 20 = 0 OR s.n % 29 = 0 THEN NULL ELSE $2::uuid END,
              CASE WHEN s.n % 29 = 0 THEN NULL ELSE $2::uuid END,
              (s.n * 13) % 100
            FROM (SELECT g AS ago, (now() AT TIME ZONE 'Asia/Kolkata')::date - g AS day
                  FROM generate_series(1, $3) g) d
            CROSS JOIN (SELECT id, token_prefix, row_number() OVER (ORDER BY id) AS di
                        FROM departments WHERE id = ANY($4::uuid[])) dep
            CROSS JOIN generate_series(1, $5) AS s(n)
            CROSS JOIN LATERAL (SELECT ((d.day + time '08:00') AT TIME ZONE 'Asia/Kolkata')
                                       + s.n * interval '20 seconds' AS at) t
            JOIN pids p ON p.rn = 1 + ((s.n * 7919 + d.ago * 104729 + dep.di * 15485863) % $6)
            """,
            hospital_id, doctor, DAYS, departments, per_day, PATIENTS,
        )
        print(f"visits in {time.perf_counter() - started:.0f} s", flush=True)
        for statement in HISTORY_RECORDS:
            await connection.execute(statement, hospital_id)
            print(f"  {statement.split()[2]} in {time.perf_counter() - started:.0f} s", flush=True)
        await connection.execute("ANALYZE")
        return {"seconds": time.perf_counter() - started}
    finally:
        await connection.close()


async def queue_today(superuser_url: str, hospital: Any) -> int:
    """Today's waiting line (tokens from 501), unless an earlier run made it; returns the
    visits added."""
    connection = await asyncpg.connect(superuser_url)
    try:
        if await connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM visits WHERE hospital_id = $1 AND token_number > 500 "
            "AND service_date = (now() AT TIME ZONE 'Asia/Kolkata')::date)",
            hospital.hospital_id,
        ):
            return 0
        await connection.execute(UUID7)
        await number_patients(connection, hospital.hospital_id)
        added = await connection.fetchval(
            TODAY_QUEUE, hospital.hospital_id, list(hospital.departments.values()), TODAY_QUEUED,
            PATIENTS,
        )
        await connection.execute("ANALYZE")
        return int(added)
    finally:
        await connection.close()


def timed(results: list[tuple[str, int, float]], label: str, call: Any) -> Any:
    started = time.perf_counter()
    response = call()
    status = getattr(response, "status_code", 0)
    results.append((label, status, (time.perf_counter() - started) * 1000))
    return response


async def worker_jobs(
    settings: Any, names: tuple[str, ...], *, when: str
) -> list[tuple[str, int, float]]:
    """The named worker jobs, each in its own transaction, as the worker runs them."""
    # The worker's own engine, its 5-second statement limit included: a job over it fails here.
    engine = create_engine(settings.worker_database_url or settings.database_url)
    state = redis.from_url(settings.redis_state_url, decode_responses=True)  # type: ignore[no-untyped-call]
    jobs = {
        "close-out": lambda c: close_previous_days(c),
        "monitoring gauges": lambda c: collect_operational_metrics(c),
        "outbox relay": lambda c: relay_once(c, state, settings=settings),
        "queue rebuild": lambda c: rebuild_all(c, state, settings=settings),
    }
    results: list[tuple[str, int, float]] = []
    try:
        for name in names:
            started = time.perf_counter()
            if name == "analytics refresh":
                await refresh_analytics({"engine": engine})
            elif name == "analytics rebuild":
                await rebuild_analytics({"engine": engine})
            else:
                async with engine.begin() as connection:
                    await jobs[name](connection)
            results.append((f"{when}: {name}", 0, (time.perf_counter() - started) * 1000))
    finally:
        await state.aclose()
        await engine.dispose()
    return results


async def drive(hospital: Any, facts: dict[str, Any]) -> list[tuple[str, int, float]]:
    """Every hot route and worker job, once warm, as staff, patients and the worker use them."""
    settings = hospital.settings
    # As at the worker's start: the queue rebuilt from the database, the rollup refreshed.
    results = await worker_jobs(settings, ("queue rebuild", "analytics refresh"), when="start")
    routes: list[tuple[str, int, float]] = []
    medicine = hospital.departments["GENMED"]
    with api_client(settings) as client:
        visits, status_tokens = [], []
        for complaint in ("fever and cough", "back pain", "headache", "loose motions", "rash"):
            session = start_kiosk_session(client, activate_kiosk(client, hospital))
            complete_intake(session, complaint=complaint, phone=phone(7))
            confirmed = session.confirm()
            assert confirmed.status_code == 200, confirmed.text
            visits.append(session.visit_id)
            status_tokens.append(confirmed.json()["status_token"])
        visit = visits[0]
        doctor, nurse, admin = (hospital.staff(name) for name in ("doctor", "nurse", "admin"))
        for _ in range(2):  # the second round is the warm one that counts
            routes.clear()
            timed(routes, "status page", lambda: client.get(f"/status/{status_tokens[0]}"))
            timed(routes, "queue", lambda: client.get(
                f"/queue?department_id={medicine}&limit=50", headers=nurse))
            timed(routes, "queue waiting", lambda: client.get(
                f"/queue?department_id={medicine}&status=queued&limit=50", headers=nurse))
            timed(routes, "queue all departments", lambda: client.get(
                "/queue?limit=50", headers=admin))
            timed(routes, "alerts", lambda: client.get(
                f"/alerts?acknowledged=false&department_id={medicine}", headers=nurse))
            timed(routes, "register today", lambda: client.get(
                "/patients?limit=50&days=1", headers=admin))
            timed(routes, "register 30 days", lambda: client.get(
                "/patients?limit=50&days=30", headers=admin))
            timed(routes, "register newest", lambda: client.get(
                "/patients?limit=50", headers=nurse))
            timed(routes, "lookup OPD card", lambda: client.get(
                f"/patients/lookup?opd_number={facts['mrn']}", headers=nurse))
            timed(routes, "lookup phone", lambda: client.get(
                f"/patients/lookup?phone={phone(7)}", headers=nurse))
            for path in ("case-sheet", "history", "transcript", "doctor-notes", "documents",
                         "investigations", "queue-events", "fhir"):
                timed(routes, f"visit {path}", lambda path=path: client.get(
                    f"/visits/{visit}/{path}", headers=doctor))
            timed(routes, "analytics summary", lambda: client.get(
                "/analytics/summary?days=30", headers=admin))
            timed(routes, "analytics timeseries", lambda: client.get(
                "/analytics/timeseries?days=30", headers=admin))
            timed(routes, "audit newest", lambda: client.get("/audit?limit=50", headers=admin))
            timed(routes, "audit by visit", lambda: client.get(
                f"/audit?record={visit}&limit=50", headers=admin))
            timed(routes, "audit by person", lambda: client.get(
                f"/audit?person={hospital.users['nurse']}&limit=50", headers=admin))
            timed(routes, "system", lambda: client.get("/admin/system", headers=admin))
            called = timed(routes, "call next", lambda: client.post(
                f"/queue/call-next?department_id={medicine}", headers=hospital.staff("doctor2")))
            assert called.status_code == 200, called.text
            consult = called.json()["visit_id"]
            timed(routes, "start consult", lambda consult=consult: client.post(
                f"/queue/{consult}/start", headers=hospital.staff("doctor2")))
            timed(routes, "complete consult", lambda consult=consult: client.post(
                f"/queue/{consult}/complete", headers=hospital.staff("doctor2")))
    # The day's recurring jobs: every 2 s (relay), 30 s (gauges), 5 min (rollup), 15 min, and
    # the nightly rebuild of the rollup.
    return results + routes + await worker_jobs(
        settings,
        (
            "outbox relay",
            "monitoring gauges",
            "analytics refresh",
            "close-out",
            "queue rebuild",
            "analytics rebuild",
        ),
        when="worker",
    )


async def statement_report(superuser_url: str) -> list[asyncpg.Record]:
    connection = await asyncpg.connect(superuser_url)
    try:
        return await connection.fetch(
            "SELECT calls, round(mean_exec_time::numeric, 1) AS mean_ms, "
            "round(max_exec_time::numeric, 1) AS max_ms, rows, "
            "regexp_replace(query, '\\s+', ' ', 'g') AS query FROM pg_stat_statements "
            "WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND query NOT ILIKE '%pg_stat_statements%' "
            "ORDER BY mean_exec_time DESC LIMIT 30"
        )
    finally:
        await connection.close()


async def main() -> None:
    superuser_url = os.environ["SCALE_SUPERUSER_URL"]
    settings = integration_settings()
    hospital = await attach_or_create(superuser_url, settings)
    connection = await asyncpg.connect(superuser_url)
    try:
        seeded = await connection.fetchval(
            "SELECT count(*) > 100000 FROM visits WHERE hospital_id = $1", hospital.hospital_id
        )
    finally:
        await connection.close()
    if not seeded:
        print(f"seeded in {(await seed(superuser_url, hospital))['seconds']:.0f} s", flush=True)
    print(f"queued today: {await queue_today(superuser_url, hospital)} added", flush=True)
    connection = await asyncpg.connect(superuser_url)
    try:
        # New OPD card numbers continue after the seeded ones.
        await connection.execute(
            "INSERT INTO patient_number_counters (hospital_id, year, last_value) "
            "SELECT $1, $2, coalesce(max(split_part(mrn, '-', 2)::int), 0) FROM patients "
            "WHERE hospital_id = $1 AND mrn LIKE $3 ON CONFLICT (hospital_id, year) DO UPDATE "
            "SET last_value = GREATEST(patient_number_counters.last_value, EXCLUDED.last_value)",
            hospital.hospital_id, this_year(), f"{this_year() % 100:02d}-%",
        )
        counts = {
            table: await connection.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
            for table in ("patients", "visits", "case_sheets", "consents", "interview_turns",
                          "visit_events", "audit_log", "red_flag_events")
        }
        print("rows:", counts, flush=True)
        mrn = await connection.fetchval(
            "SELECT p.mrn FROM visits v JOIN patients p ON p.id = v.patient_id "
            "WHERE v.hospital_id = $1 AND v.service_date = (now() AT TIME ZONE "
            "'Asia/Kolkata')::date - 3 LIMIT 1",
            hospital.hospital_id,
        )
        await connection.execute("SELECT pg_stat_statements_reset()")
    finally:
        await connection.close()
    results = await drive(hospital, {"mrn": mrn})
    print(f"\n{'route or job':<28}{'status':>8}{'ms':>10}")
    for label, status, ms in results:
        flag = "  <-- slow" if ms > 200 else ""
        print(f"{label:<28}{status:>8}{ms:>10.1f}{flag}")
    print(f"\nStatements by mean time (pg_stat_statements; slow means over {SLOW_MS} ms):")
    for row in await statement_report(superuser_url):
        flag = "SLOW " if float(row["mean_ms"]) > SLOW_MS else "     "
        print(f"{flag}{row['mean_ms']:>8} ms mean {row['max_ms']:>8} ms max "
              f"{row['calls']:>5} calls  {row['query'][:220]}")
    print("\nDone. Plans of slow statements: the server log (auto_explain).")


if __name__ == "__main__":
    asyncio.run(main())
