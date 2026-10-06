"""Measure the indexed daily summary query against a seeded 100k-visit hospital."""

import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.session import set_hospital_scope

SUMMARY = text(
    "SELECT a.department_id, a.patient_count, a.wait_seconds_total, a.wait_sample_count, "
    "a.consult_seconds_total, a.consult_sample_count, a.urgent_count, "
    "a.interview_seconds_total, a.interview_sample_count, a.confirmed_count, "
    "a.confirmed_without_edits_count, a.doctor_edited_count, a.language_mix "
    "FROM osa_analytics.analytics_daily_department_tenant a JOIN departments d "
    "ON d.hospital_id = a.hospital_id AND d.id = a.department_id "
    "WHERE a.hospital_id = :hospital_id AND a.service_date = :service_date"
)
TOTAL_VISITS = text(
    "SELECT coalesce(sum(patient_count), 0) FROM "
    "osa_analytics.analytics_daily_department_tenant WHERE hospital_id = :hospital_id"
)
IST = timezone(timedelta(hours=5, minutes=30))


async def benchmark(hospital_id: UUID, iterations: int) -> float:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    measurements: list[float] = []
    try:
        service_date = datetime.now(IST).date()
        for index in range(iterations + 5):
            start = time.perf_counter()
            async with engine.begin() as connection:
                await set_hospital_scope(connection, hospital_id)
                if index == 0:
                    total_visits = await connection.scalar(
                        TOTAL_VISITS, {"hospital_id": hospital_id}
                    )
                    if int(total_visits or 0) < 100_000:
                        raise SystemExit(
                            "Seed at least 100,000 visits for this hospital and refresh analytics."
                        )
                await connection.execute(
                    SUMMARY,
                    {"hospital_id": hospital_id, "service_date": service_date},
                )
            elapsed_ms = (time.perf_counter() - start) * 1000
            if index >= 5:
                measurements.append(elapsed_ms)
    finally:
        await engine.dispose()
    measurements.sort()
    p95 = measurements[min(len(measurements) - 1, int(len(measurements) * 0.95))]
    print(f"summary query p95: {p95:.2f} ms / 100 ms ({iterations} measured runs)")
    if p95 > 100:
        raise SystemExit(1)
    return p95


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hospital-id", required=True, type=UUID)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if not 10 <= args.iterations <= 1000:
        parser.error("--iterations must be between 10 and 1000")
    asyncio.run(benchmark(args.hospital_id, args.iterations))


if __name__ == "__main__":
    main()
