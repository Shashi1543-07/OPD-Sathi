"""Load smoke: a morning rush of patients checking in on the running stack (synthetic data).

Skipped unless LOAD_SMOKE_URL names a running API, e.g. http://127.0.0.1:5173/api (through
the web container, as kiosks and phones reach it) or http://127.0.0.1:8000. The test creates
its own synthetic hospital in the database that API uses, with AI switched off so no request
leaves the machine, and sends LOAD_SMOKE_PATIENTS patients (default 40) through the whole
check-in at once while four staff screens refresh the queue and alerts and every patient
watches their status page. It then checks that every patient got exactly one token, in
order, with no failed request, prints the latency of each step and removes the hospital.

    LOAD_SMOKE_URL=http://127.0.0.1:5173/api pytest services/api/tests/perf -s

The database and JWT settings must be the stack's own (the repository's .env), so tokens
minted here are accepted there. Make target: ``make load-smoke``.
"""

import asyncio
import math
import os
import random
import re
import time
from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import from_url

from app.core.security import issue_access_token
from tests.integration.synthetic import SyntheticHospital, execute, synthetic_hospital

BASE_URL = os.getenv("LOAD_SMOKE_URL", "").rstrip("/")
PATIENTS = int(os.getenv("LOAD_SMOKE_PATIENTS", "40"))
# Arrivals spread over this many seconds, as at a few kiosks and phones at opening time.
ARRIVAL_SECONDS = float(os.getenv("LOAD_SMOKE_ARRIVAL_SECONDS", "10"))
P95_LIMIT_MS = float(os.getenv("LOAD_SMOKE_P95_MS", "1500"))
BOARDS = 4
COMPLAINTS = (
    "fever",
    "cough and cold",
    "stomach pain since yesterday",
    "headache",
    "burning while passing urine",
    "skin rash and itching",
    "loose motions",
    "body pain and weakness",
)

pytestmark = pytest.mark.skipif(not BASE_URL, reason="set LOAD_SMOKE_URL to a running API")


class Timings:
    """Latency per step and every failed request (none is expected)."""

    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.failures: list[tuple[str, int, str]] = []

    async def call(
        self, client: httpx.AsyncClient, step: str, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        started = time.perf_counter()
        response = await client.request(method, path, **kwargs)
        self.samples[step].append((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            self.failures.append((step, response.status_code, response.text[:200]))
        return response

    def p95(self, step: str) -> float:
        ordered = sorted(self.samples[step])
        return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]

    def report(self) -> str:
        lines = [f"{'step':<28}{'n':>6}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}"]
        for step, values in sorted(self.samples.items()):
            ordered = sorted(values)
            lines.append(
                f"{step:<28}{len(values):>6}{ordered[len(ordered) // 2]:>10.0f}"
                f"{self.p95(step):>10.0f}{ordered[-1]:>10.0f}"
            )
        return "\n".join(lines)


def _reply(question: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """An answer a patient might give: a scale value, tapped options, or skipping."""
    if question["optional"] and rng.random() < 0.3:
        return {"question_id": question["id"], "skip": True}
    if question["input"] == "scale":
        return {"question_id": question["id"], "answer": rng.randint(1, 8)}
    labels = [option["label"] for option in question["options"] if not option.get("none")]
    if labels:
        picked = rng.sample(labels, k=min(len(labels), 2 if question["multi"] else 1))
        return {"question_id": question["id"], "answer": ", ".join(picked)}
    return {"question_id": question["id"], "answer": "since two days"}


async def _patient(
    client: httpx.AsyncClient, timings: Timings, kiosk: dict[str, str], seed: int
) -> dict[str, Any]:
    rng = random.Random(seed)
    await asyncio.sleep(rng.uniform(0, ARRIVAL_SECONDS))
    created = await timings.call(
        client,
        "start session",
        "POST",
        "/intake/sessions",
        json={"language": rng.choice(["hi", "mr", "en"])},
        headers=kiosk,
    )
    body = created.json()
    patient = {"Authorization": f"Bearer {body['patient_access_token']}"}
    base = f"/intake/sessions/{body['visit_id']}"
    consent = body["consent"]
    await timings.call(
        client,
        "consent",
        "POST",
        f"{base}/consent",
        json={"version": consent["version"], "sha256": consent["sha256"], "granted": True},
        headers=patient,
    )
    await timings.call(
        client,
        "age and sex",
        "POST",
        f"{base}/identity",
        json={"age": rng.randint(1, 85), "sex": rng.choice(["female", "male"])},
        headers=patient,
    )
    # About one patient in ten taps a danger sign, which alerts the nurses at once.
    tiles = ["breathing_difficulty"] if rng.random() < 0.1 else []
    await timings.call(
        client,
        "danger signs",
        "POST",
        f"{base}/danger-signs",
        json={"tile_ids": tiles},
        headers=patient,
    )
    step = (
        await timings.call(
            client, "first question", "GET", f"{base}/interview/next-question", headers=patient
        )
    ).json()
    first = True
    while not step.get("complete"):
        payload = (
            {"question_id": step["question"]["id"], "answer": rng.choice(COMPLAINTS)}
            if first
            else _reply(step["question"], rng)
        )
        first = False
        step = (
            await timings.call(
                client,
                "answer",
                "POST",
                f"{base}/interview/answers",
                json=payload,
                headers={**patient, "Idempotency-Key": uuid4().hex},
            )
        ).json()
    receipt = (
        await timings.call(client, "confirm", "POST", f"{base}/confirm", headers=patient)
    ).json()
    for _ in range(3):
        await timings.call(client, "status page", "GET", f"/status/{receipt['status_token']}")
        await asyncio.sleep(1)
    return dict(receipt)


async def _board(
    client: httpx.AsyncClient, timings: Timings, hospital: SyntheticHospital, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        headers = {"Authorization": f"Bearer {hospital.token('admin')}"}
        await timings.call(client, "staff queue", "GET", "/queue?limit=100", headers=headers)
        await timings.call(client, "staff alerts", "GET", "/alerts", headers=headers)
        await asyncio.sleep(2)


async def _board_count(client: httpx.AsyncClient, hospital: SyntheticHospital) -> int:
    """How many patients the staff queue shows, across every page."""
    headers = {"Authorization": f"Bearer {hospital.token('admin')}"}
    count, cursor = 0, None
    while True:
        params = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/queue", params=params, headers=headers)).json()
        count += len(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return count


def _kiosk_headers(hospital: SyntheticHospital) -> dict[str, str]:
    """The hospital's kiosk, already paired, as the synthetic hospital sets it up."""
    token = issue_access_token(
        subject=hospital.kiosk_id,
        role="kiosk",
        hospital_id=hospital.hospital_id,
        settings=hospital.settings,
        lifetime=timedelta(hours=1),
    )
    return {"Authorization": f"Bearer {token}"}


async def _forget_read_model(hospital: SyntheticHospital) -> None:
    """Remove the hospital's Redis queue and session keys the running API created."""
    redis = from_url(hospital.settings.redis_state_url, decode_responses=True)
    try:
        async for key in redis.scan_iter(match=f"*{hospital.hospital_id}*", count=500):
            await redis.delete(key)
    finally:
        await redis.aclose()


async def test_a_morning_rush_of_patients_checks_in_without_errors() -> None:
    async with synthetic_hospital() as hospital:
        # No AI calls: this measures the system itself and spends nobody's AI quota.
        await execute(
            hospital.settings,
            hospital.hospital_id,
            "INSERT INTO hospital_ai_settings (hospital_id, llm_enabled) VALUES (:h, false)",
            h=hospital.hospital_id,
        )
        timings = Timings()
        try:
            limits = httpx.Limits(max_connections=PATIENTS + BOARDS * 2)
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=30, limits=limits) as client:
                kiosk = _kiosk_headers(hospital)
                stop = asyncio.Event()
                boards = [
                    asyncio.create_task(_board(client, timings, hospital, stop))
                    for _ in range(BOARDS)
                ]
                started = time.perf_counter()
                receipts = await asyncio.gather(
                    *(_patient(client, timings, kiosk, seed) for seed in range(PATIENTS))
                )
                elapsed = time.perf_counter() - started
                stop.set()
                await asyncio.gather(*boards)
                on_board = await _board_count(client, hospital)
        finally:
            await _forget_read_model(hospital)

        print(f"\n{PATIENTS} patients checked in within {elapsed:.1f} s against {BASE_URL}")
        print(timings.report())
        assert timings.failures == []
        tokens = [receipt["token"] for receipt in receipts]
        assert len(set(tokens)) == PATIENTS
        # Every department's tokens run 1, 2, 3 ... with no gap and no repeat.
        by_prefix: dict[str, list[int]] = defaultdict(list)
        for token in tokens:
            prefix, number = re.fullmatch(r"([A-Z]+)-(\d+)", token).groups()  # type: ignore[union-attr]
            by_prefix[prefix].append(int(number))
        for numbers in by_prefix.values():
            assert sorted(numbers) == list(range(1, len(numbers) + 1))
        assert on_board == PATIENTS
        for step in ("start session", "consent", "age and sex", "answer", "confirm", "status page"):
            assert timings.p95(step) < P95_LIMIT_MS, (step, timings.p95(step))
