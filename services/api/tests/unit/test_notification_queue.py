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
    def __init__(self, *, opted_in: bool = True, sms_consented: bool | None = None) -> None:
        self.visit_id = uuid4()
        self.opted_in = opted_in
        # Opt-in and a recorded SMS consent go together unless a test separates them.
        self.sms_consented = opted_in if sms_consented is None else sms_consented
        self.dedupe_keys: set[str] = set()
        self.created = 0

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        values = params or {}
        if "SELECT v.id, v.token_label" in sql:
            requested = values["visit_ids"]
            if self.visit_id not in requested:
                return Result()
            return Result(
                [
                    {
                        "id": self.visit_id,
                        "token_label": "GEN-004",
                        "language": "en",
                        "sms_opt_in": self.opted_in,
                        "phone_hmac": "synthetic-phone-digest",
                        "sms_consented": self.sms_consented,
                    }
                ]
            )
        if "INSERT INTO notifications" in sql:
            key = str(values["dedupe_key"])
            if key not in self.dedupe_keys:
                self.dedupe_keys.add(key)
                self.created += 1
        return Result()

    sms_messages: bool | None = True

    async def scalar(self, statement: object, _params: object = None) -> int | bool | None:
        # The hospital's SMS switch (on here unless a test turns it off), else the hourly count.
        if "hospital_feature_settings" in str(statement):
            return self.sms_messages
        return 0


class QueueClient:
    def __init__(self, visit_id: UUID) -> None:
        self.visit_id = visit_id

    async def zrange(
        self, _key: str, _start: int, _stop: int, *, withscores: bool
    ) -> list[tuple[str, int]]:
        assert withscores
        return [(str(self.visit_id), 10)]


NOTIFY_KEY = base64.b64encode(b"n" * 32).decode("ascii")


def _notification(settings: Settings, *, phone: str = "9876543210") -> dict[str, Any]:
    visit_id = uuid4()
    return {
        "id": uuid4(),
        "hospital_id": HOSPITAL_ID,
        "visit_id": visit_id,
        "template": "token_issued",
        "attempt_count": 0,
        "language": "en",
        "token_label": "GEN-004",
        "notify_phone_enc": encrypt_field(
            phone,
            key=settings.notify_key_bytes or b"",
            associated_data=f"visits:notify_phone_enc:{visit_id}",
        ),
    }


class _Savepoint:
    def __init__(self, connection: "DeliveryConnection") -> None:
        self.connection = connection

    async def __aenter__(self) -> None:
        self.connection.savepoints += 1

    async def __aexit__(self, exc_type: object, _exc: object, _tb: object) -> None:
        if exc_type is not None:
            self.connection.rolled_back += 1


class DeliveryConnection:
    """The worker's connection: due rows in, one UPDATE per row out, savepoints counted."""

    def __init__(self, settings: Settings, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else [_notification(settings)]
        self.row = self.rows[0]
        self.status: dict[Any, str] = {row["id"]: "pending" for row in self.rows}
        self.errors: dict[Any, str | None] = {}
        self.attempt_count = 0
        self.delays: list[int | None] = []
        self.savepoints = 0
        self.rolled_back = 0
        self.fail_update_for: Any = None

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self)

    async def execute(self, statement: object, params: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        values = params or {}
        if "SELECT n.id, n.hospital_id" in sql:
            assert "c.purpose = 'sms'" in sql  # Only visits with a recorded SMS consent.
            assert "phone_enc" not in sql.replace("notify_phone_enc", "")
            return Result([row for row in self.rows if self.status[row["id"]] == "pending"])
        if sql.startswith("UPDATE notifications"):
            if values.get("id") == self.fail_update_for:
                raise RuntimeError("synthetic database error")
            if "attempt_count = :attempts" in sql:
                self.attempt_count = int(values["attempts"])
                self.row["attempt_count"] = self.attempt_count
                self.delays.append(values["delay"])
            self.status[values["id"]] = str(values.get("status", "sent"))
            self.errors[values["id"]] = values.get("error_code")
        return Result()


class RecordingProvider:
    def __init__(self, channel: str = "sms") -> None:
        self.channel = channel
        self.phones: list[str] = []

    async def send(self, *, phone: str, body: str) -> str | None:
        assert "GEN-004" in body
        self.phones.append(phone)
        return "provider-id"


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
async def test_no_intent_without_a_recorded_sms_consent() -> None:
    """An opt-in flag alone (as the old self-ticking box set it) is not consent."""
    connection = NotificationConnection(opted_in=True, sms_consented=False)
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
    # The worker's real environment: a notification key and no patient-data key.
    settings = Settings(app_env="test", notify_key=NOTIFY_KEY, app_data_key="")
    connection = DeliveryConnection(settings)

    class BrokenProvider:
        channel = "sms"

        async def send(self, *, phone: str, body: str) -> str | None:
            assert phone == "9876543210"
            assert "GEN-004" in body
            raise TimeoutError

    for _ in range(4):
        await deliver_pending_once(connection, settings=settings, provider=BrokenProvider())  # type: ignore[arg-type]

    assert connection.delays == [1, 5, 30, None]
    assert connection.attempt_count == 4
    assert connection.status[connection.row["id"]] == "failed"
    assert await deliver_pending_once(
        connection,
        settings=settings,
        provider=BrokenProvider(),  # type: ignore[arg-type]
    ) == (0, 0)


@pytest.mark.asyncio
async def test_worker_delivers_with_only_the_notification_key() -> None:
    settings = Settings(app_env="test", notify_key=NOTIFY_KEY, app_data_key="")
    connection = DeliveryConnection(settings)
    provider = RecordingProvider()
    result = await deliver_pending_once(connection, settings=settings, provider=provider)  # type: ignore[arg-type]
    assert result == (1, 0)
    assert provider.phones == ["9876543210"]
    assert connection.status[connection.row["id"]] == "sent"


@pytest.mark.asyncio
async def test_console_delivery_never_decrypts_a_number() -> None:
    """Without a provider no key is needed: rows still reach sent and no number is read."""
    keyed = Settings(app_env="test", notify_key=NOTIFY_KEY)
    connection = DeliveryConnection(keyed)
    provider = RecordingProvider(channel="console")
    unkeyed = Settings(app_env="test", notify_key="", app_data_key="")
    result = await deliver_pending_once(connection, settings=unkeyed, provider=provider)  # type: ignore[arg-type]
    assert result == (1, 0)
    assert provider.phones == [""]


@pytest.mark.asyncio
async def test_one_poisoned_row_does_not_fail_the_batch() -> None:
    settings = Settings(app_env="test", notify_key=NOTIFY_KEY, app_data_key="")
    rows = [_notification(settings) for _ in range(5)]
    # Row 2: ciphertext moved from another visit (fails authentication).
    rows[1]["notify_phone_enc"] = rows[0]["notify_phone_enc"]
    # Row 3: no stored number. Row 4: an unknown template.
    rows[2]["notify_phone_enc"] = None
    rows[3]["template"] = "not-a-template"
    connection = DeliveryConnection(settings, rows)
    # Row 5: the database refuses its update; the savepoint rolls back only that row.
    connection.fail_update_for = rows[4]["id"]
    provider = RecordingProvider()
    sent, failed = await deliver_pending_once(connection, settings=settings, provider=provider)  # type: ignore[arg-type]
    assert (sent, failed) == (1, 4)
    assert connection.status[rows[0]["id"]] == "sent"
    assert connection.errors[rows[1]["id"]] == "phone_unreadable"
    assert connection.errors[rows[2]["id"]] == "no_phone"
    assert connection.errors[rows[3]["id"]] == "bad_template"
    assert connection.status[rows[4]["id"]] == "pending"  # Retried on the next tick.
    assert connection.savepoints == 5 and connection.rolled_back == 1


def test_notify_key_rules() -> None:
    with pytest.raises(ValueError, match="NOTIFY_KEY is required"):
        Settings(
            app_env="test",
            notification_provider="twilio",
            twilio_account_sid="AC1",
            twilio_auth_token="token",
            twilio_from_number="+15550000000",
            notify_key="",
        )
    with pytest.raises(ValueError, match="differ from APP_DATA_KEY"):
        Settings(app_env="test", notify_key=NOTIFY_KEY, app_data_key=NOTIFY_KEY)
    with pytest.raises(ValueError, match="NOTIFY_KEY"):
        Settings(app_env="test", notify_key="too-short")
    assert Settings(app_env="test", notify_key="").notify_key_bytes is None


@pytest.mark.asyncio
async def test_no_intent_while_the_hospital_has_sms_off() -> None:
    connection = NotificationConnection()
    connection.sms_messages = False
    client = QueueClient(connection.visit_id)
    created = await record_queue_event_intents(
        connection,
        client,
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        settings=Settings(app_env="test"),
    )
    assert created == 0 and connection.created == 0


@pytest.mark.asyncio
async def test_sms_is_off_for_a_hospital_that_never_chose() -> None:
    connection = NotificationConnection()
    connection.sms_messages = None
    client = QueueClient(connection.visit_id)
    await record_queue_event_intents(
        connection,
        client,
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        settings=Settings(app_env="test"),
    )
    assert connection.created == 0
