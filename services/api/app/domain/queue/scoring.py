"""Deterministic queue scoring and conservative wait estimates."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Any, Literal

from app.domain.triage.score import head_start_minutes

Urgency = Literal["routine", "priority", "urgent"]
MILLISECONDS_PER_MINUTE = 60_000


def queue_score(
    arrival_ms: int,
    urgency: Urgency,
    age_years: int | None,
    pregnant: bool = False,
    *,
    urgent_bonus_min: int = 360,
    priority_bonus_min: int = 45,
    vulnerable_bonus_min: int = 15,
    acuity_score: int | None = None,
) -> int:
    """Return virtual arrival time; low scores are served first.

    With a triage score the head start grows with the score (capped), inside the band of
    the visit's level, so a nurse's level override still decides. Urgent always goes first.
    Visits from before triage scoring keep the fixed bonuses.
    """
    if acuity_score is not None and urgency in ("routine", "priority"):
        minutes = head_start_minutes(urgency, max(0, min(100, acuity_score)))
        return arrival_ms - minutes * MILLISECONDS_PER_MINUTE
    bonuses = {
        "routine": 0,
        "priority": priority_bonus_min,
        "urgent": urgent_bonus_min,
    }
    if urgency not in bonuses:
        raise ValueError("urgency must be routine, priority, or urgent")
    if age_years is not None and not 0 <= age_years <= 120:
        raise ValueError("age_years must be between 0 and 120")
    vulnerable = pregnant or (age_years is not None and (age_years >= 65 or age_years < 5))
    bonus_min = bonuses[urgency] + (vulnerable_bonus_min if vulnerable else 0)
    return arrival_ms - bonus_min * MILLISECONDS_PER_MINUTE


def visit_score(row: Mapping[str, Any], settings: Any) -> int:
    """``queue_score`` for a visit row; the arrival is ``queued_at``, kept through recalls."""
    arrival = row["queued_at"] or datetime.now(UTC)
    return queue_score(
        int(arrival.timestamp() * 1000),
        row["urgency"],
        row["age_years"],
        bool(row["pregnant"]),
        urgent_bonus_min=settings.urgent_bonus_min,
        priority_bonus_min=settings.priority_bonus_min,
        vulnerable_bonus_min=settings.vulnerable_bonus_min,
        acuity_score=row.get("acuity_score"),
    )


def calling_order(row: Mapping[str, Any], settings: Any) -> tuple[int, int, str]:
    """Sort key for whom to call next: urgent always first, then score, then a stable id."""
    visit_id = row.get("visit_id", row.get("id"))
    return (0 if row["urgency"] == "urgent" else 1, visit_score(row, settings), str(visit_id))


@dataclass(frozen=True)
class EtaRange:
    eta_min: int
    eta_max: int


def average_consult_minutes(samples: list[float], default: float = 4.0) -> float:
    """Use the last 20 consults after five samples; bound outliers to 1–15 minutes."""
    if len(samples) < 5:
        value = default
    else:
        value = sum(samples[-20:]) / min(len(samples), 20)
    return min(15.0, max(1.0, value))


def estimate_wait(
    position: int,
    active_doctors: int,
    consult_minutes: float,
) -> EtaRange:
    """Return a five-minute rounded range, avoiding false-precise ETAs."""
    if position < 1:
        raise ValueError("position must be at least one")
    if active_doctors < 0:
        raise ValueError("active_doctors cannot be negative")
    consult = min(15.0, max(1.0, consult_minutes))
    baseline = (position - 1) * consult / max(1, active_doctors)
    low = ceil((baseline * 0.75) / 5) * 5
    high = ceil((baseline * 1.25) / 5) * 5
    return EtaRange(eta_min=low, eta_max=high)
