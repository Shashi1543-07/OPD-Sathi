"""Dry-run-first purge and pseudonymization for expired synthetic clinic records."""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, get_settings
from app.core.crypto import encrypt_field
from app.db.session import set_hospital_scope


def _cutoff_days(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


async def _run(
    *,
    hospital_id: UUID,
    apply: bool,
    retention_days: int,
    anonymise_days: int,
    settings: Settings | None = None,
) -> dict[str, int]:
    configured = settings or get_settings()
    retention_cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    anonymise_cutoff = datetime.now(UTC) - timedelta(days=anonymise_days)
    engine = create_async_engine(configured.migrations_database_url)
    totals = {"interview_turns": 0, "case_sheets": 0, "red_flag_events": 0, "patients": 0}
    try:
        await _process_hospital(
            engine,
            hospital_id,
            configured,
            retention_cutoff=retention_cutoff,
            anonymise_cutoff=anonymise_cutoff,
            apply=apply,
            totals=totals,
        )
    finally:
        await engine.dispose()
    mode = "APPLIED" if apply else "DRY RUN; no rows changed"
    print(
        f"{mode}: interview_turns={totals['interview_turns']} "
        f"case_sheets={totals['case_sheets']} red_flag_events={totals['red_flag_events']} "
        f"patients_to_pseudonymize={totals['patients']}"
    )
    return totals


async def _process_hospital(
    engine: Any,
    hospital_id: UUID,
    settings: Settings,
    *,
    retention_cutoff: datetime,
    anonymise_cutoff: datetime,
    apply: bool,
    totals: dict[str, int],
) -> None:
    async with engine.begin() as connection:
        await set_hospital_scope(connection, hospital_id)
        for table_name, cutoff in (
            ("interview_turns", retention_cutoff),
            ("case_sheets", anonymise_cutoff),
            ("red_flag_events", anonymise_cutoff),
        ):
            count = await connection.scalar(
                text(
                    f"SELECT count(*) FROM {table_name} WHERE hospital_id = :hospital_id "
                    "AND created_at < :cutoff"
                ),
                {"hospital_id": hospital_id, "cutoff": cutoff},
            )
            totals[table_name] += int(count or 0)

        candidates = list(
            (
                await connection.execute(
                    text(
                        "SELECT p.id FROM patients p WHERE p.hospital_id = :hospital_id "
                        "AND EXISTS (SELECT 1 FROM visits v WHERE v.hospital_id = p.hospital_id "
                        "AND v.patient_id = p.id AND v.created_at < :cutoff) "
                        "AND NOT EXISTS (SELECT 1 FROM visits recent WHERE "
                        "recent.hospital_id = p.hospital_id AND recent.patient_id = p.id "
                        "AND recent.created_at >= :cutoff)"
                    ),
                    {"hospital_id": hospital_id, "cutoff": anonymise_cutoff},
                )
            )
            .scalars()
            .all()
        )
        totals["patients"] += len(candidates)
        if not apply:
            return

        await connection.execute(
            text(
                "DELETE FROM interview_turns WHERE hospital_id = :hospital_id "
                "AND created_at < :cutoff"
            ),
            {"hospital_id": hospital_id, "cutoff": retention_cutoff},
        )
        await connection.execute(
            text(
                "DELETE FROM case_sheets WHERE hospital_id = :hospital_id AND visit_id IN "
                "(SELECT id FROM visits WHERE hospital_id = :hospital_id AND created_at < :cutoff)"
            ),
            {"hospital_id": hospital_id, "cutoff": anonymise_cutoff},
        )
        await connection.execute(
            text(
                "DELETE FROM red_flag_events WHERE hospital_id = :hospital_id "
                "AND created_at < :cutoff"
            ),
            {"hospital_id": hospital_id, "cutoff": anonymise_cutoff},
        )
        await connection.execute(
            text(
                "UPDATE visits SET age_years = NULL, pregnant = NULL, urgency_reasons = '[]'::jsonb "
                "WHERE hospital_id = :hospital_id AND created_at < :cutoff"
            ),
            {"hospital_id": hospital_id, "cutoff": anonymise_cutoff},
        )
        for patient_id in candidates:
            anonymised_name = encrypt_field(
                "Anonymous",
                key=settings.app_data_key_bytes,
                associated_data=f"patients:name_enc:{patient_id}",
            )
            await connection.execute(
                text(
                    "UPDATE patients SET name_enc = :name_enc, phone_enc = NULL, "
                    "phone_hmac = NULL, abha_enc = NULL, year_of_birth = NULL, sex = 'unknown' "
                    "WHERE hospital_id = :hospital_id AND id = :patient_id"
                ),
                {
                    "name_enc": anonymised_name,
                    "hospital_id": hospital_id,
                    "patient_id": patient_id,
                },
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hospital-id", type=UUID, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete expired interview/summary records and pseudonymize eligible patients",
    )
    args = parser.parse_args()
    retention_days = _cutoff_days("RETENTION_DAYS", 30)
    anonymise_days = _cutoff_days("ANONYMISE_DAYS", 90)
    if anonymise_days < retention_days:
        parser.error("ANONYMISE_DAYS must be greater than or equal to RETENTION_DAYS")
    asyncio.run(
        _run(
            hospital_id=args.hospital_id,
            apply=args.apply,
            retention_days=retention_days,
            anonymise_days=anonymise_days,
        )
    )


if __name__ == "__main__":
    main()
