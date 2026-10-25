"""A believable month of finished OPD days for the fictional demo hospital.

``seed-bulk`` exists for load tests: it creates visits with no consultation, case sheet or
interview, so reports built on it look broken. This module instead writes what a real
outpatient day leaves behind: morning arrivals, a short kiosk interview, waiting that grows
with the crowd, consultations of a few minutes, some no-shows, and the case sheets and
interview turns the staff screens read. Everything is synthetic and deterministic (one
fixed random seed per day), every record is written through the same encryption as the
application, and all of it belongs to a fixed pool of sample patients so it can be removed
again without touching anything else.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import Settings
from app.core.crypto import encrypt_field
from app.core.ids import stable_uuid7

IST = timezone(timedelta(hours=5, minutes=30))
PATIENT_POOL = 1500
# Monday-first share of an average day's patients; Sunday is an emergency-only OPD.
WEEKDAY_LOAD = (1.25, 1.1, 1.0, 1.0, 0.95, 0.8, 0.2)
BASE_DAILY_VISITS = 170
DEPARTMENT_WEIGHTS = {
    "GENMED": 30,
    "PAED": 14,
    "ORTHO": 12,
    "OBG": 10,
    "DERM": 9,
    "SURG": 9,
    "ENT": 8,
    "OPHTH": 8,
}
LANGUAGES = (("hi", 45), ("mr", 38), ("en", 17))

# (english, hindi, marathi)
Words = tuple[str, str, str]
DURATIONS: tuple[Words, ...] = (
    ("1 to 3 days", "1 से 3 दिन", "1 ते 3 दिवस"),
    ("4 to 7 days", "4 से 7 दिन", "4 ते 7 दिवस"),
    ("More than a week", "एक हफ़्ते से ज़्यादा", "एका आठवड्यापेक्षा जास्त"),
)


@dataclass(frozen=True)
class Complaint:
    words: Words
    associated: tuple[Words, ...]
    severity: tuple[int, int]
    red_flag: tuple[str, str] | None = None  # (rule id, reason) for urgent presentations


COMPLAINTS: dict[str, tuple[Complaint, ...]] = {
    "GENMED": (
        Complaint(
            ("Fever", "बुखार", "ताप"),
            (("Body ache", "बदन दर्द", "अंगदुखी"), ("Chills", "ठंड लगना", "थंडी वाजणे")),
            (4, 7),
        ),
        Complaint(
            ("Cough", "खांसी", "खोकला"),
            (
                ("Cough with phlegm", "बलगम वाली खांसी", "कफाचा खोकला"),
                ("Sore throat", "गले में ख़राश", "घसा खवखवणे"),
            ),
            (3, 6),
        ),
        Complaint(
            ("Stomach pain", "पेट दर्द", "पोटदुखी"),
            (("Vomiting", "उल्टी", "उलटी"), ("Loose motions", "दस्त", "जुलाब")),
            (4, 8),
        ),
        Complaint(("Headache", "सिर दर्द", "डोकेदुखी"), (("Dizziness", "चक्कर", "चक्कर"),), (3, 7)),
        Complaint(
            ("Weakness", "कमज़ोरी", "अशक्तपणा"), (("Poor appetite", "भूख कम", "भूक कमी"),), (2, 5)
        ),
        Complaint(
            ("Chest pain", "सीने में दर्द", "छातीत दुखणे"),
            (("Sweating", "पसीना", "घाम"), ("Breathlessness", "सांस फूलना", "धाप लागणे")),
            (7, 10),
            ("RF_CHEST_PAIN", "Chest pain with sweating or breathlessness"),
        ),
    ),
    "PAED": (
        Complaint(
            ("Fever", "बुखार", "ताप"),
            (("Not feeding well", "ठीक से खा नहीं रहा", "नीट खात नाही"),),
            (3, 7),
        ),
        Complaint(
            ("Cough and cold", "सर्दी-खांसी", "सर्दी-खोकला"),
            (("Runny nose", "नाक बहना", "नाक गळणे"),),
            (2, 5),
        ),
        Complaint(("Loose motions", "दस्त", "जुलाब"), (("Vomiting", "उल्टी", "उलटी"),), (3, 6)),
        Complaint(
            ("Fast breathing", "तेज़ सांस", "जलद श्वास"),
            (("Fever", "बुखार", "ताप"),),
            (7, 10),
            ("RF_PAED_BREATHING", "Fast or difficult breathing in a child"),
        ),
    ),
    "ORTHO": (
        Complaint(("Knee pain", "घुटने में दर्द", "गुडघेदुखी"), (("Swelling", "सूजन", "सूज"),), (4, 7)),
        Complaint(
            ("Back pain", "कमर दर्द", "पाठदुखी"),
            (("Pain going down the leg", "पैर में जाता दर्द", "पायात जाणारी वेदना"),),
            (4, 8),
        ),
        Complaint(
            ("Wrist pain after a fall", "गिरने के बाद कलाई में दर्द", "पडल्यानंतर मनगट दुखणे"),
            (("Swelling", "सूजन", "सूज"),),
            (5, 8),
        ),
    ),
    "OBG": (
        Complaint(
            ("Lower abdominal pain", "पेट के निचले हिस्से में दर्द", "ओटीपोटात दुखणे"),
            (("White discharge", "सफ़ेद पानी", "पांढरे पाणी"),),
            (4, 7),
        ),
        Complaint(
            ("Irregular periods", "अनियमित माहवारी", "अनियमित पाळी"),
            (("Tiredness", "थकान", "थकवा"),),
            (2, 4),
        ),
        Complaint(
            ("Pregnancy check-up", "गर्भावस्था जांच", "गर्भारपण तपासणी"),
            (("Nausea", "जी मिचलाना", "मळमळ"),),
            (1, 3),
        ),
    ),
    "DERM": (
        Complaint(
            ("Itchy rash", "खुजली वाले दाने", "खाज सुटणारे पुरळ"),
            (("Worse at night", "रात में ज़्यादा", "रात्री जास्त"),),
            (3, 6),
        ),
        Complaint(("Pimples", "मुंहासे", "मुरुम"), (("Oily skin", "तैलीय त्वचा", "तेलकट त्वचा"),), (2, 4)),
    ),
    "SURG": (
        Complaint(
            ("Swelling", "सूजन", "सूज"), (("Pain on touch", "छूने पर दर्द", "स्पर्श केल्यास दुखणे"),), (3, 6)
        ),
        Complaint(
            ("Wound not healing", "घाव भर नहीं रहा", "जखम भरत नाही"), (("Pus", "मवाद", "पू"),), (4, 7)
        ),
    ),
    "ENT": (
        Complaint(
            ("Ear pain", "कान दर्द", "कानदुखी"), (("Ear discharge", "कान बहना", "कान वाहणे"),), (4, 7)
        ),
        Complaint(
            ("Sore throat", "गले में ख़राश", "घसा खवखवणे"),
            (("Pain while swallowing", "निगलने में दर्द", "गिळताना दुखणे"),),
            (3, 6),
        ),
    ),
    "OPHTH": (
        Complaint(
            ("Red eyes", "आँखें लाल", "डोळे लाल"), (("Watering", "आँखों से पानी", "डोळ्यांतून पाणी"),), (3, 5)
        ),
        Complaint(
            ("Blurred vision", "धुंधला दिखना", "अंधुक दिसणे"),
            (("Headache", "सिर दर्द", "डोकेदुखी"),),
            (2, 4),
        ),
    ),
}
FIRST_NAMES = (
    "Asha",
    "Ravi",
    "Meena",
    "Sunil",
    "Kavita",
    "Imran",
    "Pooja",
    "Ganesh",
    "Farah",
    "Anil",
    "Sunita",
    "Rahul",
    "Lata",
    "Vijay",
    "Shabana",
    "Prakash",
    "Neha",
    "Suresh",
    "Rekha",
    "Arjun",
)


@dataclass(frozen=True)
class Patient:
    id: UUID
    first_name: str
    sex: str
    year_of_birth: int
    language: str


@dataclass
class PlannedVisit:
    id: UUID
    patient: Patient
    department_code: str
    status: str
    urgency: str
    language: str
    age_years: int
    complaint: Complaint
    duration: Words
    severity: int
    registered_at: datetime
    queued_at: datetime | None
    called_at: datetime | None
    consult_started_at: datetime | None
    consult_ended_at: datetime | None
    turn_times: list[datetime] = field(default_factory=list)
    confirmed: bool = False
    doctor_edited: bool = False


def patient_pool(hospital_id: UUID) -> list[Patient]:
    """A fixed set of fictional patients: children, adults and older people, both sexes."""
    patients = []
    for index in range(1, PATIENT_POOL + 1):
        rng = random.Random(f"patient:{index}")  # nosec B311
        child = index % 6 == 0
        age = (
            rng.randint(1, 14)
            if child
            else rng.choice((rng.randint(16, 35), rng.randint(36, 60), rng.randint(61, 85)))
        )
        language = rng.choices([code for code, _ in LANGUAGES], [w for _, w in LANGUAGES])[0]
        patients.append(
            Patient(
                id=stable_uuid7(f"{hospital_id}:history:patient:{index}"),
                first_name=FIRST_NAMES[index % len(FIRST_NAMES)],
                sex="female" if index % 2 else "male",
                year_of_birth=date.today().year - age,
                language=language,
            )
        )
    return patients


def _lognormal(rng: random.Random, median: float, spread: float, low: float, high: float) -> float:
    return min(high, max(low, median * math.exp(rng.gauss(0, spread))))


def plan_day(
    day: date,
    *,
    hospital_id: UUID,
    department_codes: list[str],
    patients: list[Patient],
) -> list[PlannedVisit]:
    """Every visit of one finished OPD day, deterministically from the date."""
    rng = random.Random(f"opd-day:{day.isoformat()}")  # nosec B311
    load = WEEKDAY_LOAD[day.weekday()]
    total = max(0, round(BASE_DAILY_VISITS * load * rng.uniform(0.9, 1.1)))
    codes = [code for code in DEPARTMENT_WEIGHTS if code in department_codes]
    if not codes or total == 0:
        return []
    weights = [DEPARTMENT_WEIGHTS[code] for code in codes]
    children = [p for p in patients if day.year - p.year_of_birth <= 14]
    women = [p for p in patients if p.sex == "female" and 18 <= day.year - p.year_of_birth <= 45]
    adults = [p for p in patients if day.year - p.year_of_birth >= 16]
    opening = datetime.combine(day, time(9, 0), tzinfo=IST)
    visits: list[PlannedVisit] = []
    for sequence in range(total):
        code = rng.choices(codes, weights)[0]
        pool = children if code == "PAED" else women if code == "OBG" else adults
        patient = rng.choice(pool)
        urgent_choices = [c for c in COMPLAINTS[code] if c.red_flag]
        roll = rng.random()
        if roll < 0.03 and urgent_choices:
            urgency, complaint = "urgent", rng.choice(urgent_choices)
        else:
            urgency = "priority" if roll < 0.17 else "routine"
            complaint = rng.choice([c for c in COMPLAINTS[code] if not c.red_flag])
        # Arrivals peak around 09:45 and taper off by early afternoon.
        arrival = opening + timedelta(minutes=rng.triangular(0, 270, 45))
        interview = _lognormal(rng, 4.5, 0.35, 2, 12)
        outcome = rng.random()
        status = (
            "awaiting_confirmation"
            if outcome < 0.03
            else "cancelled"
            if outcome < 0.06
            else "no_show"
            if outcome < 0.12
            else "done"
        )
        queued_at = (
            None
            if status == "awaiting_confirmation"
            else arrival + timedelta(minutes=interview + 0.5)
        )
        turns = 4
        turn_times = [
            arrival + timedelta(minutes=interview * step / (turns - 1)) for step in range(turns)
        ]
        called_at = consult_started_at = consult_ended_at = None
        if queued_at is not None and status in {"done", "no_show"}:
            # The wait grows with the day's crowd and with how late in the rush someone arrives.
            rush = 1 + 0.6 * max(0.0, 1 - abs((arrival - opening).total_seconds() / 60 - 75) / 120)
            median = {"urgent": 6, "priority": 18, "routine": 32}[urgency] * load * rush
            wait = _lognormal(rng, median, 0.45, 2, 180)
            called_at = queued_at + timedelta(minutes=max(1.0, wait - rng.uniform(1, 3)))
            if status == "done":
                consult_started_at = queued_at + timedelta(minutes=wait)
                length = _lognormal(rng, 9 if code in {"ORTHO", "SURG", "OBG"} else 7, 0.35, 2, 30)
                consult_ended_at = consult_started_at + timedelta(minutes=length)
        visits.append(
            PlannedVisit(
                id=stable_uuid7(f"{hospital_id}:history:visit:{day.isoformat()}:{sequence}"),
                patient=patient,
                department_code=code,
                status=status,
                urgency=urgency,
                language=patient.language,
                age_years=day.year - patient.year_of_birth,
                complaint=complaint,
                duration=rng.choice(DURATIONS),
                severity=rng.randint(*complaint.severity),
                registered_at=arrival,
                queued_at=queued_at,
                called_at=called_at,
                consult_started_at=consult_started_at,
                consult_ended_at=consult_ended_at,
                turn_times=turn_times,
                confirmed=queued_at is not None and rng.random() < 0.96,
                doctor_edited=status == "done" and rng.random() < 0.09,
            )
        )
    return visits


def _words(words: Words, language: str) -> str:
    return words[{"en": 0, "hi": 1, "mr": 2}[language]]


def case_sheet(visit: PlannedVisit) -> dict[str, Any]:
    """The organized history a kiosk interview would have produced for this visit."""
    language = visit.language

    def pair(words: Words, confidence: float) -> dict[str, Any]:
        return {
            "text_original": _words(words, language),
            "text_en": words[0],
            "confidence": confidence,
        }

    sheet: dict[str, Any] = {
        "schema_version": "1.0",
        "language": language,
        "chief_complaint": pair(visit.complaint.words, 0.93),
        "hpi": {
            "onset": pair(visit.duration, 0.95),
            "severity_0_10": visit.severity,
            "associated_symptoms": [pair(words, 0.9) for words in visit.complaint.associated],
            "pertinent_negatives": [],
        },
        "past_history": {"conditions": [], "surgeries": []},
        "medications": [],
        "allergies": [],
        "family_history": [],
        "social": {},
        "red_flags": [],
        "routing": {
            "department_code": visit.department_code,
            "confidence": 0.9,
            "reason_en": "Matched from the patient's main concern.",
            "needs_nurse_confirmation": False,
        },
        "field_meta": {
            "/chief_complaint": {
                "source": "patient_voice",
                "confidence": 0.93,
                "confirmed": visit.confirmed,
                "ai_assisted": False,
            }
        },
        "patient_confirmation": {
            "confirmed_by_patient": visit.confirmed,
            "corrections": [],
            "needs_assistance": False,
        },
        "disclaimer": (
            "Collected by OPD Saathi from the patient's own statements. "
            "History only. Not a diagnosis."
        ),
    }
    if visit.complaint.red_flag:
        rule, reason = visit.complaint.red_flag
        sheet["red_flags"] = [
            {
                "id": rule,
                "level": "urgent",
                "source": "rule",
                "evidence": _words(visit.complaint.words, language),
                "reason_en": reason,
            }
        ]
    if visit.doctor_edited:
        sheet["chief_complaint"]["text_en"] = (
            f"{visit.complaint.words[0]} ({visit.duration[0].lower()})"
        )
        sheet["field_meta"]["/chief_complaint/text_en"] = {
            "source": "doctor",
            "confidence": 1.0,
            "confirmed": True,
            "ai_assisted": False,
        }
    return sheet


def interview_turns(visit: PlannedVisit) -> list[dict[str, Any]]:
    language = visit.language
    voice = 0.82 + (visit.severity % 5) * 0.03
    answers: list[tuple[str, str, Words | str]] = [
        ("chief_complaint", "voice", visit.complaint.words),
        ("duration", "touch", visit.duration),
        ("severity", "touch", str(visit.severity)),
        ("general_context", "touch", visit.complaint.associated[0]),
    ]
    turns = []
    for index, ((question, mode, answer), at) in enumerate(
        zip(answers, visit.turn_times, strict=True)
    ):
        original = answer if isinstance(answer, str) else _words(answer, language)
        english = answer if isinstance(answer, str) else answer[0]
        turns.append(
            {
                "turn_index": index,
                "question_id": question,
                "input_mode": mode,
                "text_original": original,
                "text_english": english,
                "stt_confidence": round(voice, 2) if mode == "voice" else None,
                "created_at": at,
            }
        )
    return turns


async def seed_history(
    connection: AsyncConnection, settings: Settings, *, hospital_id: UUID, days: int
) -> tuple[int, int]:
    """Fill each missing day of the last ``days`` days (not today). Returns (days, visits)."""
    key = settings.app_data_keyring
    departments = {
        row["code"]: row
        for row in (
            await connection.execute(
                text(
                    "SELECT id, code, token_prefix FROM departments "
                    "WHERE hospital_id = :hospital_id AND is_active"
                ),
                {"hospital_id": hospital_id},
            )
        ).mappings()
    }
    if not departments:
        raise SystemExit("Run `python -m app.cli seed-demo` before seed-demo-history.")
    doctors: dict[UUID, list[UUID]] = {}
    for row in (
        await connection.execute(
            text(
                "SELECT id, department_id FROM users WHERE hospital_id = :hospital_id "
                "AND role = 'doctor' AND is_active AND department_id IS NOT NULL ORDER BY email"
            ),
            {"hospital_id": hospital_id},
        )
    ).mappings():
        doctors.setdefault(row["department_id"], []).append(row["id"])

    patients = patient_pool(hospital_id)
    await connection.execute(
        text(
            "INSERT INTO patients (id, hospital_id, name_enc, year_of_birth, sex, "
            "preferred_language, created_at) VALUES (:id, :hospital_id, :name_enc, :year, :sex, "
            ":language, :created_at) ON CONFLICT (id) DO NOTHING"
        ),
        [
            {
                "id": patient.id,
                "hospital_id": hospital_id,
                "name_enc": encrypt_field(
                    f"{patient.first_name} Sample {index:04d}",
                    key=key,
                    associated_data=f"patients:name_enc:{patient.id}",
                ),
                "year": patient.year_of_birth,
                "sex": patient.sex,
                "language": patient.language,
                "created_at": datetime.now(UTC) - timedelta(days=days + 1),
            }
            for index, patient in enumerate(patients, start=1)
        ],
    )
    pool_ids = [patient.id for patient in patients]
    seeded_days = set(
        (
            await connection.execute(
                text(
                    "SELECT DISTINCT service_date FROM visits WHERE hospital_id = :hospital_id "
                    "AND patient_id = ANY(:pool)"
                ),
                {"hospital_id": hospital_id, "pool": pool_ids},
            )
        ).scalars()
    )
    today = datetime.now(IST).date()
    filled_days = 0
    created = 0
    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        if day in seeded_days:
            continue
        planned = plan_day(
            day,
            hospital_id=hospital_id,
            department_codes=list(departments),
            patients=patients,
        )
        if not planned:
            continue
        last_tokens = {
            row["department_id"]: int(row["last"] or 0)
            for row in (
                await connection.execute(
                    text(
                        "SELECT department_id, max(token_number) AS last FROM visits "
                        "WHERE hospital_id = :hospital_id AND service_date = :day "
                        "GROUP BY department_id"
                    ),
                    {"hospital_id": hospital_id, "day": day},
                )
            ).mappings()
        }
        visit_rows: list[dict[str, Any]] = []
        sheet_rows: list[dict[str, Any]] = []
        turn_rows: list[dict[str, Any]] = []
        for visit in sorted(planned, key=lambda item: item.registered_at):
            department = departments[visit.department_code]
            token_number = None
            token_label = None
            if visit.queued_at is not None:
                token_number = last_tokens.get(department["id"], 0) + 1
                last_tokens[department["id"]] = token_number
                token_label = f"{department['token_prefix']}-{token_number:03d}"
            team = doctors.get(department["id"], [])
            doctor = team[token_number % len(team)] if team and token_number else None
            visit_rows.append(
                {
                    "id": visit.id,
                    "hospital_id": hospital_id,
                    "patient_id": visit.patient.id,
                    "department_id": department["id"],
                    "status": visit.status,
                    "urgency": visit.urgency,
                    "token_number": token_number,
                    "token_label": token_label,
                    "service_date": day,
                    "language": visit.language,
                    "age_years": visit.age_years,
                    "registered_at": visit.registered_at,
                    "queued_at": visit.queued_at,
                    "called_at": visit.called_at,
                    "consult_started_at": visit.consult_started_at,
                    "consult_ended_at": visit.consult_ended_at,
                    "assigned_doctor_id": doctor if visit.consult_started_at else None,
                    "created_at": visit.registered_at,
                }
            )
            sheet = case_sheet(visit)
            sheet_rows.append(
                {
                    "id": stable_uuid7(f"{visit.id}:case-sheet"),
                    "hospital_id": hospital_id,
                    "visit_id": visit.id,
                    "data": json.dumps(
                        {
                            "ciphertext": encrypt_field(
                                json.dumps(sheet, separators=(",", ":")),
                                key=key,
                                associated_data=f"case_sheets:data:{visit.id}",
                            )
                        }
                    ),
                    "version": 2 if visit.doctor_edited else 1,
                    "confirmed": visit.confirmed,
                    "confirmed_at": visit.queued_at if visit.confirmed else None,
                    "editor": doctor if visit.doctor_edited else None,
                    "created_at": visit.queued_at or visit.turn_times[-1],
                }
            )
            for turn in interview_turns(visit):
                turn_id = stable_uuid7(f"{visit.id}:turn:{turn['turn_index']}")
                turn_rows.append(
                    {
                        "id": turn_id,
                        "hospital_id": hospital_id,
                        "visit_id": visit.id,
                        "turn_index": turn["turn_index"],
                        "question_id": turn["question_id"],
                        "input_mode": turn["input_mode"],
                        "text_original": encrypt_field(
                            turn["text_original"],
                            key=key,
                            associated_data=f"interview_turns:text_original:{turn_id}",
                        ),
                        "text_english": encrypt_field(
                            turn["text_english"],
                            key=key,
                            associated_data=f"interview_turns:text_english:{turn_id}",
                        ),
                        "stt_provider": "demo-history" if turn["input_mode"] == "voice" else None,
                        "stt_confidence": turn["stt_confidence"],
                        "created_at": turn["created_at"],
                    }
                )
        await connection.execute(
            text(
                "INSERT INTO visits (id, hospital_id, patient_id, department_id, status, urgency, "
                "token_number, token_label, service_date, language, age_years, registered_at, "
                "queued_at, called_at, consult_started_at, consult_ended_at, assigned_doctor_id, "
                "created_at) VALUES (:id, :hospital_id, :patient_id, :department_id, :status, "
                ":urgency, :token_number, :token_label, :service_date, :language, :age_years, "
                ":registered_at, :queued_at, :called_at, :consult_started_at, :consult_ended_at, "
                ":assigned_doctor_id, :created_at)"
            ),
            visit_rows,
        )
        await connection.execute(
            text(
                "INSERT INTO case_sheets (id, hospital_id, visit_id, schema_version, data, "
                "version, confirmed_by_patient, confirmed_at, last_edited_by, created_at) "
                "VALUES (:id, "
                ":hospital_id, :visit_id, '1.0', CAST(:data AS jsonb), :version, :confirmed, "
                ":confirmed_at, :editor, :created_at)"
            ),
            sheet_rows,
        )
        await connection.execute(
            text(
                "INSERT INTO interview_turns (id, hospital_id, visit_id, turn_index, "
                "question_id, input_mode, text_original, text_english, stt_provider, "
                "stt_confidence, created_at) "
                "VALUES (:id, :hospital_id, :visit_id, :turn_index, :question_id, :input_mode, "
                ":text_original, :text_english, :stt_provider, :stt_confidence, :created_at)"
            ),
            turn_rows,
        )
        filled_days += 1
        created += len(visit_rows)
    return filled_days, created


async def clear_history(connection: AsyncConnection, *, hospital_id: UUID) -> int:
    """Remove only the sample-history visits (their case sheets and turns cascade)."""
    pool_ids = [patient.id for patient in patient_pool(hospital_id)]
    result = await connection.execute(
        text("DELETE FROM visits WHERE hospital_id = :hospital_id AND patient_id = ANY(:pool)"),
        {"hospital_id": hospital_id, "pool": pool_ids},
    )
    await connection.execute(
        text("DELETE FROM patients WHERE hospital_id = :hospital_id AND id = ANY(:pool)"),
        {"hospital_id": hospital_id, "pool": pool_ids},
    )
    return int(result.rowcount or 0)


async def clear_bulk(connection: AsyncConnection, *, hospital_id: UUID) -> tuple[int, int]:
    """Remove ``seed-bulk`` load-test visits: ``D-`` tokens that never got a case sheet,
    interview, consent or status link. Patients left with no visit at all go too."""
    visits = await connection.execute(
        text(
            "DELETE FROM visits v WHERE v.hospital_id = :hospital_id "
            "AND v.token_label LIKE 'D-%' AND v.status_token_hash IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM case_sheets c WHERE c.hospital_id = v.hospital_id "
            "AND c.visit_id = v.id) "
            "AND NOT EXISTS (SELECT 1 FROM interview_turns t WHERE t.hospital_id = v.hospital_id "
            "AND t.visit_id = v.id) "
            "AND NOT EXISTS (SELECT 1 FROM consents s WHERE s.hospital_id = v.hospital_id "
            "AND s.visit_id = v.id)"
        ),
        {"hospital_id": hospital_id},
    )
    patients = await connection.execute(
        text(
            "DELETE FROM patients p WHERE p.hospital_id = :hospital_id "
            "AND p.phone_hmac IS NULL AND p.abha_enc IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM visits v WHERE v.hospital_id = p.hospital_id "
            "AND v.patient_id = p.id)"
        ),
        {"hospital_id": hospital_id},
    )
    return int(visits.rowcount or 0), int(patients.rowcount or 0)


async def refresh_reports(connection: AsyncConnection) -> bool:
    """Recount every day of the reports now, instead of at the worker's nightly rebuild."""
    # Run in a transaction of its own: the role change lasts until it ends.
    try:
        await connection.execute(text("SET LOCAL ROLE osa_analytics_worker"))
        await connection.execute(text("SELECT osa_analytics.rebuild_daily_department()"))
    except Exception:  # the worker recounts today and yesterday within 5 minutes, the rest at night
        return False
    return True
