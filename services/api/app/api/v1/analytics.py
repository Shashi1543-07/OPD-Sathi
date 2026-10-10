"""Tenant-scoped administrator analytics backed by daily materialized rollups."""

import csv
import io
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.audit import audit
from app.core.auth import require_role
from app.db.session import set_hospital_scope

router = APIRouter(prefix="/analytics", tags=["analytics"])
AdminPrincipal = Annotated[dict[str, Any], Depends(require_role("admin"))]
_KOLKATA = timezone(timedelta(hours=5, minutes=30))

# Each query is one fixed statement; every value is a bound parameter.
_ROLLUP_DAY_SQL = (
    "SELECT a.service_date, a.department_id, d.name_en, d.name_hi, d.name_mr, "
    "a.patient_count, a.wait_seconds_total, a.wait_sample_count, "
    "a.consult_seconds_total, a.consult_sample_count, a.urgent_count, "
    "a.interview_seconds_total, a.interview_sample_count, a.confirmed_count, "
    "a.confirmed_without_edits_count, a.case_sheet_count, a.doctor_edited_count, "
    "a.language_mix "
    "FROM osa_analytics.analytics_daily_department_tenant a JOIN departments d "
    "ON d.hospital_id = a.hospital_id AND d.id = a.department_id "
    "WHERE a.hospital_id = :hospital_id AND a.service_date = :service_date"
)
_ROLLUP_RANGE_SQL = (
    "SELECT a.service_date, a.department_id, d.name_en, d.name_hi, d.name_mr, "
    "a.patient_count, a.wait_seconds_total, a.wait_sample_count, "
    "a.consult_seconds_total, a.consult_sample_count, a.urgent_count, "
    "a.interview_seconds_total, a.interview_sample_count, a.confirmed_count, "
    "a.confirmed_without_edits_count, a.case_sheet_count, a.doctor_edited_count, "
    "a.language_mix "
    "FROM osa_analytics.analytics_daily_department_tenant a JOIN departments d "
    "ON d.hospital_id = a.hospital_id AND d.id = a.department_id "
    "WHERE a.hospital_id = :hospital_id "
    "AND a.service_date >= :start_day AND a.service_date <= :end_day "
    "AND (CAST(:department_id AS uuid) IS NULL "
    "OR a.department_id = CAST(:department_id AS uuid)) "
    "ORDER BY a.service_date, d.name_en"
)


def _mean(total: float, count: int) -> float | None:
    return round(total / count, 1) if count else None


def _safe_csv_cell(value: Any) -> Any:
    """Prevent tenant-controlled labels from becoming spreadsheet formulas."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine weighted daily rollups without averaging averages."""
    totals: dict[str, float] = defaultdict(float)
    languages: dict[str, int] = defaultdict(int)
    departments: dict[str, dict[str, Any]] = {}
    for row in rows:
        for key in (
            "patient_count",
            "urgent_count",
            "wait_seconds_total",
            "wait_sample_count",
            "consult_seconds_total",
            "consult_sample_count",
            "interview_seconds_total",
            "interview_sample_count",
            "confirmed_count",
            "confirmed_without_edits_count",
            "case_sheet_count",
            "doctor_edited_count",
        ):
            totals[key] += float(row[key] or 0)
        mix = row["language_mix"] or {}
        if isinstance(mix, str):
            mix = json.loads(mix)
        for language, count in mix.items():
            languages[language] += int(count)
        # A range has one row per department per day: add them up, never list a
        # department twice or average its daily averages.
        department = departments.setdefault(
            str(row["department_id"]),
            {
                "department_id": str(row["department_id"]),
                "name": {
                    "en": row["name_en"],
                    "hi": row["name_hi"],
                    "mr": row["name_mr"],
                },
                "patients": 0,
                "urgent_count": 0,
                "wait_seconds_total": 0.0,
                "wait_sample_count": 0,
            },
        )
        department["patients"] += int(row["patient_count"] or 0)
        department["urgent_count"] += int(row["urgent_count"] or 0)
        department["wait_seconds_total"] += float(row["wait_seconds_total"] or 0)
        department["wait_sample_count"] += int(row["wait_sample_count"] or 0)
    department_load = [
        {
            "department_id": item["department_id"],
            "name": item["name"],
            "patients": item["patients"],
            "urgent_count": item["urgent_count"],
            "average_wait_seconds": _mean(item["wait_seconds_total"], item["wait_sample_count"]),
        }
        for item in departments.values()
    ]
    patients = int(totals["patient_count"])
    confirmed = int(totals["confirmed_count"])
    # Only visits that produced a case sheet can have one edited by a doctor.
    case_sheets = int(totals["case_sheet_count"])
    return {
        "patients": patients,
        "average_wait_seconds": _mean(
            totals["wait_seconds_total"], int(totals["wait_sample_count"])
        ),
        "average_consult_seconds": _mean(
            totals["consult_seconds_total"], int(totals["consult_sample_count"])
        ),
        "urgent_count": int(totals["urgent_count"]),
        "language_mix": dict(languages),
        "average_interview_seconds": _mean(
            totals["interview_seconds_total"], int(totals["interview_sample_count"])
        ),
        "confirmed_without_edits_percent": round(
            100 * totals["confirmed_without_edits_count"] / confirmed, 1
        )
        if confirmed
        else None,
        "doctor_edit_rate_percent": round(100 * totals["doctor_edited_count"] / case_sheets, 1)
        if case_sheets
        else None,
        "department_load": sorted(department_load, key=lambda item: item["patients"], reverse=True),
    }


async def _record_view(
    connection: Any,
    request: Request,
    *,
    hospital_id: UUID,
    actor_id: UUID,
    action: str,
    meta: dict[str, Any],
) -> None:
    await audit(
        connection,
        request,
        hospital_id=hospital_id,
        actor_type="user",
        actor_id=actor_id,
        action=action,
        resource_type="analytics",
        meta=meta,
    )


@router.get("/summary")
async def analytics_summary(
    request: Request,
    principal: AdminPrincipal,
    days: Annotated[int | None, Query(ge=1, le=365)] = None,
    department_id: UUID | None = None,
) -> dict[str, Any]:
    """Today's figures by default; with ``days`` the same window the timeseries uses."""
    hospital_id, actor_id = UUID(principal["hospital_id"]), UUID(principal["sub"])
    end_day = datetime.now(_KOLKATA).date()
    start_day = end_day - timedelta(days=(days or 1) - 1)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        meta: dict[str, Any] = {}
        if days is not None or department_id is not None:
            meta = {"days": days, "department_id": str(department_id) if department_id else None}
        await _record_view(
            connection,
            request,
            hospital_id=hospital_id,
            actor_id=actor_id,
            action="analytics.summary.viewed",
            meta=meta,
        )
        if days is None and department_id is None:
            statement, params = (
                _ROLLUP_DAY_SQL,
                {"hospital_id": hospital_id, "service_date": end_day},
            )
        else:
            statement, params = (
                _ROLLUP_RANGE_SQL,
                {
                    "hospital_id": hospital_id,
                    "start_day": start_day,
                    "end_day": end_day,
                    "department_id": department_id,
                },
            )
        rows = (await connection.execute(text(statement), params)).mappings().all()
    return {
        **summarize([dict(row) for row in rows]),
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
    }


@router.get("/timeseries")
async def analytics_timeseries(
    request: Request,
    principal: AdminPrincipal,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    department_id: UUID | None = None,
) -> dict[str, Any]:
    hospital_id, actor_id = UUID(principal["hospital_id"]), UUID(principal["sub"])
    end_day = datetime.now(_KOLKATA).date()
    start_day = end_day - timedelta(days=days - 1)
    query = text(_ROLLUP_RANGE_SQL)
    params: dict[str, Any] = {
        "hospital_id": hospital_id,
        "start_day": start_day,
        "end_day": end_day,
        "department_id": department_id,
    }
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _record_view(
            connection,
            request,
            hospital_id=hospital_id,
            actor_id=actor_id,
            action="analytics.timeseries.viewed",
            meta={"days": days, "department_id": str(department_id) if department_id else None},
        )
        rows = (await connection.execute(query, params)).mappings().all()
    return {"days": days, "items": [dict(row) for row in rows]}


@router.get("/export.csv")
async def analytics_export(
    request: Request,
    principal: AdminPrincipal,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    department_id: UUID | None = None,
) -> StreamingResponse:
    hospital_id, actor_id = UUID(principal["hospital_id"]), UUID(principal["sub"])
    end_day = datetime.now(_KOLKATA).date()
    start_day = end_day - timedelta(days=days - 1)
    async with request.app.state.engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        await _record_view(
            connection,
            request,
            hospital_id=hospital_id,
            actor_id=actor_id,
            action="analytics.csv.exported",
            meta={
                "days": days,
                "department_id": str(department_id) if department_id else None,
            },
        )

    async def csv_rows() -> AsyncIterator[str]:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "service_date",
                "department_en",
                "patients",
                "urgent_count",
                "average_wait_seconds",
                "average_consult_seconds",
                "average_interview_seconds",
                "confirmed_without_edits_count",
                "confirmed_count",
                "doctor_edited_count",
                "case_sheet_count",
                "language_mix",
            ]
        )
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        async with request.app.state.engine.begin() as connection:
            await set_hospital_scope(connection, hospital_id)
            result = await connection.stream(
                text(_ROLLUP_RANGE_SQL),
                {
                    "hospital_id": hospital_id,
                    "start_day": start_day,
                    "end_day": end_day,
                    "department_id": department_id,
                },
            )
            async for row in result.mappings():
                writer.writerow(
                    [
                        row["service_date"],
                        _safe_csv_cell(row["name_en"]),
                        row["patient_count"],
                        row["urgent_count"],
                        _mean(row["wait_seconds_total"], row["wait_sample_count"]),
                        _mean(row["consult_seconds_total"], row["consult_sample_count"]),
                        _mean(row["interview_seconds_total"], row["interview_sample_count"]),
                        row["confirmed_without_edits_count"],
                        row["confirmed_count"],
                        row["doctor_edited_count"],
                        row["case_sheet_count"],
                        row["language_mix"],
                    ]
                )
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

    return StreamingResponse(
        csv_rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="opd-analytics.csv"'},
    )
