from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from uuid import UUID

from app import demo_history
from app.demo_history import case_sheet, interview_turns, patient_pool, plan_day

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
CODES = list(demo_history.DEPARTMENT_WEIGHTS)
PATIENTS = patient_pool(HOSPITAL_ID)


def month(start: date = date(2026, 9, 1), days: int = 28) -> list[demo_history.PlannedVisit]:
    visits = []
    for offset in range(days):
        visits += plan_day(
            start + timedelta(days=offset),
            hospital_id=HOSPITAL_ID,
            department_codes=CODES,
            patients=PATIENTS,
        )
    return visits


def minutes(later, earlier) -> float:  # type: ignore[no-untyped-def]
    return (later - earlier).total_seconds() / 60


def test_a_day_is_the_same_every_time_it_is_generated() -> None:
    first = plan_day(
        date(2026, 9, 7), hospital_id=HOSPITAL_ID, department_codes=CODES, patients=PATIENTS
    )
    again = plan_day(
        date(2026, 9, 7), hospital_id=HOSPITAL_ID, department_codes=CODES, patients=PATIENTS
    )
    assert [(v.id, v.status, v.consult_started_at) for v in first] == [
        (v.id, v.status, v.consult_started_at) for v in again
    ]


def test_mondays_are_busy_and_sundays_quiet() -> None:
    monday = plan_day(
        date(2026, 9, 7), hospital_id=HOSPITAL_ID, department_codes=CODES, patients=PATIENTS
    )
    sunday = plan_day(
        date(2026, 9, 6), hospital_id=HOSPITAL_ID, department_codes=CODES, patients=PATIENTS
    )
    assert 180 <= len(monday) <= 240
    assert len(sunday) < 50


def test_every_finished_visit_has_a_believable_timeline() -> None:
    visits = month()
    done = [v for v in visits if v.status == "done"]
    assert len(done) / len(visits) > 0.8
    for visit in done:
        assert (
            visit.queued_at
            and visit.called_at
            and visit.consult_started_at
            and visit.consult_ended_at
        )
        assert visit.registered_at < visit.queued_at <= visit.called_at <= visit.consult_started_at
        assert 2 <= minutes(visit.consult_started_at, visit.queued_at) <= 180
        assert 2 <= minutes(visit.consult_ended_at, visit.consult_started_at) <= 30
        # Morning OPD: everyone has arrived by about 13:30.
        assert 9 <= visit.registered_at.hour <= 13
    waits = {
        level: median(
            minutes(v.consult_started_at, v.queued_at) for v in done if v.urgency == level
        )  # type: ignore[arg-type]
        for level in ("urgent", "priority", "routine")
    }
    assert waits["urgent"] < waits["priority"] < waits["routine"]
    assert 15 <= waits["routine"] <= 60
    consults = [minutes(v.consult_ended_at, v.consult_started_at) for v in done]  # type: ignore[arg-type]
    assert 5 <= median(consults) <= 10


def test_outcomes_and_patients_fit_the_department() -> None:
    visits = month()
    statuses = {
        status: sum(v.status == status for v in visits) / len(visits)
        for status in ("no_show", "cancelled", "awaiting_confirmation")
    }
    assert 0.03 <= statuses["no_show"] <= 0.1
    assert 0 < statuses["cancelled"] < 0.06
    assert all(v.queued_at is None for v in visits if v.status == "awaiting_confirmation")
    assert all(v.consult_started_at is None for v in visits if v.status in {"no_show", "cancelled"})
    assert all(v.age_years <= 14 for v in visits if v.department_code == "PAED")
    assert all(
        v.patient.sex == "female" and 18 <= v.age_years <= 45
        for v in visits
        if v.department_code == "OBG"
    )
    languages = {code: sum(v.language == code for v in visits) for code in ("hi", "mr", "en")}
    assert languages["hi"] > languages["mr"] > languages["en"] > 0
    urgent = [v for v in visits if v.urgency == "urgent"]
    assert urgent and all(v.complaint.red_flag for v in urgent)


def test_case_sheet_and_interview_match_what_the_kiosk_would_record() -> None:
    visit = next(
        v for v in month() if v.urgency == "urgent" and v.language == "hi" and v.status == "done"
    )
    sheet = case_sheet(visit)
    assert sheet["chief_complaint"]["text_en"] == visit.complaint.words[0]
    assert sheet["chief_complaint"]["text_original"] == visit.complaint.words[1]
    assert sheet["hpi"]["severity_0_10"] == visit.severity
    assert sheet["red_flags"][0]["level"] == "urgent"
    assert sheet["red_flags"][0]["reason_en"]
    turns = interview_turns(visit)
    assert [turn["turn_index"] for turn in turns] == [0, 1, 2, 3]
    assert turns[0]["input_mode"] == "voice" and turns[0]["stt_confidence"]
    assert all(a["created_at"] < b["created_at"] for a, b in zip(turns, turns[1:], strict=False))
    assert turns[-1]["created_at"] <= visit.queued_at  # type: ignore[operator]


def test_doctor_edits_are_marked_as_staff_changes() -> None:
    edited = next(v for v in month() if v.doctor_edited)
    sheet = case_sheet(edited)
    assert sheet["field_meta"]["/chief_complaint/text_en"]["source"] == "doctor"
    share = sum(v.doctor_edited for v in month()) / sum(v.status == "done" for v in month())
    assert 0.04 <= share <= 0.15
