import base64
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.config import Settings
from app.core.crypto import encrypt_field
from app.notifications.queue import (
    RETRY_DELAYS,
    deliver_pending_once,
    record_queue_event_intents,
    retry_delay_seconds,
    templates_for_position,
)

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
DEPARTMENT_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")


class Result:
    def __init__(self, rows: list[Mapping[str, Any]] | None = None):
        self.rows = rows or []

    def mappings(self) -> "Result":
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows


class NotificationConnection:
    def __init__(self, *, opted_in: bool = True) -> None:
        self.visit_id = uuid4()
        self.opted_in = opted_in
        self.dedupe_keys: set[str] = set()
        self.created = 0

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        values = params or {}
        if "SELECT v.id, v.patient_id" in sql:
            requested = values["visit_ids"]
            if self.visit_id not in requested:
                return Result()
            return Result(
                [
                    {
                        "id": self.visit_id,
                        "patient_id": uuid4(),
                        "token_label": "GEN-004",
                        "language": "en",
                        "sms_opt_in": self.opted_in,
                        "phone_hmac": "synthetic-phone-digest",
                    }
                ]
            )
        if "INSERT INTO notifications" in sql:
            key = str(values["dedupe_key"])
            if key not in self.dedupe_keys:
                self.dedupe_keys.add(key)
                self.created += 1
        return Result()

    async def scalar(self, _statement: object, _params: object = None) -> int:
        return 0


class QueueClient:
    def __init__(self, visit_id: UUID) -> None:
        self.visit_id = visit_id

    async def zrange(
        self, _key: str, _start: int, _stop: int, *, withscores: bool
    ) -> list[tuple[str, int]]:
        assert withscores
        return [(str(self.visit_id), 10)]


class DeliveryConnection:
    def __init__(self, settings: Settings) -> None:
        self.hospital_id = HOSPITAL_ID
        self.visit_id = uuid4()
        self.patient_id = uuid4()
        self.notification_id = uuid4()
        self.status = "pending"
        self.attempt_count = 0
        self.delays: list[int | None] = []
        self.row = {
            "id": self.notification_id,
            "hospital_id": self.hospital_id,
            "visit_id": self.visit_id,
            "template": "token_issued",
            "attempt_count": 0,
            "recipient_hmac": "synthetic-phone-digest",
            "patient_id": self.patient_id,
            "language": "en",
            "token_label": "GEN-004",
            "phone_enc": encrypt_field(
                "9876543210",
                key=settings.app_data_key_bytes,
                associated_data=f"patients:phone_enc:{self.patient_id}",
            ),
        }

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        values = params or {}
        if "SELECT n.id, n.hospital_id" in sql:
            return Result([self.row] if self.status == "pending" else [])
        if "UPDATE notifications SET attempt_count = :attempts" in sql:
            self.attempt_count = int(values["attempts"])
            self.status = str(values["status"])
            self.row["attempt_count"] = self.attempt_count
            self.delays.append(values["delay"])
        return Result()


def test_thresholds_and_retry_schedule_are_exact_and_bounded() -> None:
    assert templates_for_position(6) == ("five_ahead",)
    assert templates_for_position(3) == ("two_ahead",)
    assert templates_for_position(1) == ("your_turn",)
    assert templates_for_position(2) == ()
    assert [retry_delay_seconds(i) for i in (1, 2, 3)] == list(RETRY_DELAYS)
    assert retry_delay_seconds(4) is None


@pytest.mark.asyncio
async def test_one_thousand_duplicate_queue_events_create_one_template_intent() -> None:
    connection = NotificationConnection()
    client = QueueClient(connection.visit_id)
    settings = Settings(app_env="test")
    for _ in range(1000):
        await record_queue_event_intents(
            connection,
            client,
            hospital_id=HOSPITAL_ID,
            department_id=DEPARTMENT_ID,
            settings=settings,
        )
    assert connection.created == 1
    assert connection.dedupe_keys == {f"{connection.visit_id}:your_turn"}


@pytest.mark.asyncio
async def test_queue_event_never_creates_notification_without_explicit_opt_in() -> None:
    connection = NotificationConnection(opted_in=False)
    await record_queue_event_intents(
        connection,
        QueueClient(connection.visit_id),
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        settings=Settings(app_env="test"),
    )
    assert connection.created == 0


@pytest.mark.asyncio
async def test_delivery_retries_one_five_thirty_then_dead_letters_after_final_failure() -> None:
    settings = Settings(app_env="test", app_data_key=base64.b64encode(b"a" * 32).decode("ascii"))
    connection = DeliveryConnection(settings)

    class BrokenProvider:
        channel = "console"

        async def send(self, *, phone: str, body: str) -> str | None:
            assert phone == "9876543210"
            assert "GEN-004" in body
            raise TimeoutError

    for _ in range(4):
        await deliver_pending_once(connection, settings=settings, provider=BrokenProvider())  # type: ignore[arg-type]

    assert connection.delays == [1, 5, 30, None]
    assert connection.attempt_count == 4
    assert connection.status == "failed"
    assert await deliver_pending_once(
        connection,
        settings=settings,
        provider=BrokenProvider(),  # type: ignore[arg-type]
    ) == (0, 0)
