from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.queue.scoring import (
    MILLISECONDS_PER_MINUTE,
    average_consult_minutes,
    estimate_wait,
    queue_score,
)


@pytest.mark.parametrize(
    ("urgency", "age", "pregnant", "expected_bonus_min"),
    [
        ("routine", None, False, 0),
        ("priority", None, False, 45),
        ("urgent", None, False, 360),
        ("routine", 65, False, 15),
        ("routine", 64, False, 0),
        ("routine", 4, False, 15),
        ("routine", 5, False, 0),
        ("routine", None, True, 15),
        ("priority", 70, False, 60),
        ("urgent", 3, False, 375),
        ("routine", 0, False, 15),
        ("routine", 120, False, 15),
        ("routine", 6, False, 0),
        ("routine", 64, True, 15),
        ("priority", 4, False, 60),
        ("priority", 5, False, 45),
        ("priority", 65, False, 60),
        ("priority", None, True, 60),
        ("urgent", 64, False, 360),
        ("urgent", 65, False, 375),
        ("urgent", None, True, 375),
        ("routine", None, False, 0),
        ("routine", 14, False, 0),
        ("priority", 64, True, 60),
    ],
)
def test_queue_score_applies_urgency_and_vulnerability_bonuses(
    urgency: str, age: int | None, pregnant: bool, expected_bonus_min: int
) -> None:
    arrival = 1_700_000_000_000
    assert queue_score(arrival, urgency, age, pregnant) == arrival - (
        expected_bonus_min * MILLISECONDS_PER_MINUTE
    )


def test_worked_example_orders_c_b_d_a() -> None:
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC).timestamp() * 1000
    visits = [
        ("A", int(start), "routine", None),
        ("B", int(start + 20 * 60_000), "priority", None),
        ("C", int(start + 30 * 60_000), "urgent", None),
        ("D", int(start + 10 * 60_000), "routine", 70),
    ]
    ordered = sorted(visits, key=lambda item: queue_score(item[1], item[2], item[3]))
    assert [visit[0] for visit in ordered] == ["C", "B", "D", "A"]


def test_routine_patient_overtakes_new_priority_after_45_minutes() -> None:
    routine_waiting = queue_score(0, "routine", 35)
    priority_arriving_now = queue_score(46 * 60_000, "priority", 35)
    assert routine_waiting < priority_arriving_now


@pytest.mark.parametrize("urgency", ["emergency", "", "URGENT"])
def test_queue_score_rejects_unknown_urgency(urgency: str) -> None:
    with pytest.raises(ValueError, match="urgency"):
        queue_score(100, urgency, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("age", [-1, 121])
def test_queue_score_rejects_out_of_range_age(age: int) -> None:
    with pytest.raises(ValueError, match="age_years"):
        queue_score(100, "routine", age)


@settings(max_examples=500, deadline=None)
@given(
    ages=st.lists(st.integers(min_value=0, max_value=120), min_size=1, max_size=40),
    urgencies=st.lists(st.sampled_from(["routine", "priority", "urgent"]), min_size=1, max_size=40),
    arrivals=st.lists(st.integers(min_value=0, max_value=2**60), min_size=1, max_size=40),
)
def test_sorted_queue_is_always_sorted_by_virtual_arrival_score(
    ages: list[int], urgencies: list[str], arrivals: list[int]
) -> None:
    count = min(len(ages), len(urgencies), len(arrivals))
    scores = [queue_score(arrivals[index], urgencies[index], ages[index]) for index in range(count)]
    assert sorted(scores) == [
        queue_score(arrivals[index], urgencies[index], ages[index])
        for index in sorted(range(count), key=lambda item: scores[item])
    ]


def test_average_consult_uses_default_until_five_samples_and_last_twenty_after() -> None:
    assert average_consult_minutes([1, 7, 3, 2], default=4) == 4
    assert average_consult_minutes([1, 2, 3, 4, 5], default=4) == 3
    assert average_consult_minutes([1] * 20 + [9] * 20) == 9
    assert average_consult_minutes([0, 0, 0, 0, 0]) == 1
    assert average_consult_minutes([60, 60, 60, 60, 60]) == 15


@pytest.mark.parametrize(
    ("position", "doctors", "consult", "expected"),
    [(1, 0, 4, (0, 0)), (2, 1, 4, (5, 5)), (4, 2, 4, (5, 10)), (8, 3, 12, (25, 35))],
)
def test_estimate_wait_returns_rounded_range(
    position: int, doctors: int, consult: float, expected: tuple[int, int]
) -> None:
    result = estimate_wait(position, doctors, consult)
    assert (result.eta_min, result.eta_max) == expected


@pytest.mark.parametrize(("position", "doctors"), [(0, 1), (-1, 1), (1, -1)])
def test_estimate_wait_rejects_invalid_position_or_doctor_count(
    position: int, doctors: int
) -> None:
    with pytest.raises(ValueError):
        estimate_wait(position, doctors, 4)
