"""Measure the indexed queue lookup against the 100k-visit demo dataset."""

import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.core.ids import stable_uuid7
from app.db.session import set_hospital_scope

_QUEUE_SQL = text(
    "SELECT id, patient_id, department_id, status, urgency, queued_at "
    "FROM visits WHERE hospital_id = :hospital_id AND department_id = :department_id "
    "AND status = 'queued' AND queued_at IS NOT NULL ORDER BY queued_at ASC LIMIT 50"
)


def _walk_plan(node: dict) -> list[str]:
    indexes = [node["Index Name"]] if "Index Name" in node else []
    for child in node.get("Plans", []):
        indexes.extend(_walk_plan(child))
    return indexes


async def main() -> None:
    settings = get_settings()
    hospital_id = stable_uuid7("opd-saathi:demo-nagpur-government")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await set_hospital_scope(connection, hospital_id)
                count = await connection.scalar(
                    text("SELECT count(*) FROM visits WHERE hospital_id = :hospital_id"),
                    {"hospital_id": hospital_id},
                )
                if int(count or 0) < 100_000:
                    raise SystemExit(
                        "The benchmark requires at least 100000 demo visits; run make seed-bulk."
                    )
                department_id = await connection.scalar(
                    text(
                        "SELECT department_id FROM visits WHERE hospital_id = :hospital_id "
                        "AND status = 'queued' AND queued_at IS NOT NULL LIMIT 1"
                    ),
                    {"hospital_id": hospital_id},
                )
                if not isinstance(department_id, UUID):
                    raise SystemExit("No queued demo visits with queued_at were found.")
                params = {"hospital_id": hospital_id, "department_id": department_id}
                plan_result = await connection.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + _QUEUE_SQL.text), params
                )
                plan = plan_result.scalar_one()[0]
                durations_ms: list[float] = []
                for _ in range(100):
                    started = time.perf_counter()
                    await connection.execute(_QUEUE_SQL, params)
                    durations_ms.append((time.perf_counter() - started) * 1000)
        plan_root = plan["Plan"]
        indexes = _walk_plan(plan_root)
        ordered = sorted(durations_ms)
        p95 = ordered[94]
        report = {
            "measured_at": datetime.now(UTC).isoformat(),
            "visits": int(count or 0),
            "iterations": len(ordered),
            "index_names": indexes,
            "plan_execution_ms": plan_root.get("Actual Total Time"),
            "client_latency_ms": {
                "median": statistics.median(ordered),
                "p95": p95,
                "max": max(ordered),
            },
            "gate": "pass" if "ix_visits_queue" in indexes and p95 < 20 else "fail",
            "plan": plan,
        }
        destination = (
            Path(__file__).resolve().parents[3]
            / "docs"
            / "phase-reports"
            / "phase-03-query-plan.json"
        )
        destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "plan"}, indent=2))
        if report["gate"] != "pass":
            raise SystemExit("Queue benchmark did not meet index and 20 ms p95 requirements.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
