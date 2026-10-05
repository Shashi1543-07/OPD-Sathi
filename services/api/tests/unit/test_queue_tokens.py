from datetime import date
from uuid import UUID

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import Settings
from app.db.repositories import QueueRepository
from app.domain.queue.tokens import (
    create_status_token,
    daily_counter_key,
    status_token_digest,
    token_label,
)

HOSPITAL_ID = UUID("018e5a3d-6c10-7abc-8def-0123456789ab")
DEPARTMENT_ID = UUID("018e5a3d-6c10-7abc-8def-1123456789ab")
VISIT_ID = UUID("018e5a3d-6c10-7abc-8def-2123456789ab")


def test_daily_counter_is_tenant_department_and_date_scoped() -> None:
    expected = f"osa:test:{HOSPITAL_ID}:tok:{DEPARTMENT_ID}:20261006"
    assert daily_counter_key("test", HOSPITAL_ID, DEPARTMENT_ID, date(2026, 10, 6)) == expected
    assert daily_counter_key("prod", HOSPITAL_ID, DEPARTMENT_ID, date(2026, 10, 6)) != expected


@pytest.mark.parametrize(
    ("number", "expected"), [(1, "MED-001"), (999, "MED-999"), (1000, "MED-1000")]
)
def test_token_label_formats_three_digit_minimum(number: int, expected: str) -> None:
    assert token_label("MED", number) == expected


def test_token_label_rejects_invalid_prefix_or_number() -> None:
    with pytest.raises(ValueError):
        token_label("", 1)
    with pytest.raises(ValueError):
        token_label("MED", 0)


def test_status_token_is_stable_scoped_opaque_and_stored_as_digest() -> None:
    key = b"k" * 32
    token = create_status_token(key=key, hospital_id=HOSPITAL_ID, visit_id=VISIT_ID)
    assert token == create_status_token(key=key, hospital_id=HOSPITAL_ID, visit_id=VISIT_ID)
    assert len(token) == 43
    assert token != create_status_token(key=key, hospital_id=HOSPITAL_ID, visit_id=DEPARTMENT_ID)
    assert status_token_digest(token) != token


def test_status_token_rejects_short_keys() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        create_status_token(key=b"short", hospital_id=HOSPITAL_ID, visit_id=VISIT_ID)


class FakeConnection:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.queries: list[str] = []

    async def execute(self, statement: object, _params: object) -> None:
        self.queries.append(str(statement))

    async def scalar(self, statement: object, _params: object) -> int:
        self.queries.append(str(statement))
        return self.maximum


class FakeRedis:
    def __init__(self, result: int = 9, error: Exception | None = None):
        self.result = result
        self.error = error
        self.call: tuple[object, ...] | None = None

    async def eval(self, *args: object) -> int:
        self.call = args
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_token_allocator_uses_redis_counter_and_db_floor() -> None:
    connection = FakeConnection(maximum=8)
    redis = FakeRedis(result=9)
    repository = QueueRepository(connection, Settings(app_env="test"))
    number = await repository.allocate_daily_token_number(
        redis=redis,
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        service_date=date(2026, 10, 6),
    )
    assert number == 9
    assert redis.call is not None
    assert redis.call[-2:] == (9, 36 * 60 * 60)
    assert "pg_advisory_xact_lock" in connection.queries[0]


@pytest.mark.asyncio
async def test_token_allocator_falls_back_to_database_max_when_redis_is_down() -> None:
    connection = FakeConnection(maximum=14)
    redis = FakeRedis(error=RedisConnectionError("offline"))
    repository = QueueRepository(connection, Settings(app_env="test"))
    number = await repository.allocate_daily_token_number(
        redis=redis,
        hospital_id=HOSPITAL_ID,
        department_id=DEPARTMENT_ID,
        service_date=date(2026, 10, 6),
    )
    assert number == 15
