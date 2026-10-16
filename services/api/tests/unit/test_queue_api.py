from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import queue
from app.config import Settings
from app.core.errors import ApiProblemError


class Result:
    def __init__(self, *, rows: list[dict[str, object]] | None = None):
        self.rows = rows or []

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, departments: list[dict[str, object]], assigned: object = None):
        self.departments = departments
        self.assigned = assigned
        self.executed: list[tuple[object, object]] = []

    async def scalar(self, *_args: object, **_kwargs: object) -> object:
        return self.assigned

    async def execute(self, *_args: object, **_kwargs: object) -> Result:
        self.executed.append((_args[0], _args[1] if len(_args) > 1 else {}))
        return Result(rows=self.departments)


class Context:
    def __init__(self, connection: Connection):
        self.connection = connection

    async def __aenter__(self) -> Connection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeRedis:
    def __init__(self, value: str | None = None):
        self.value = value
        self.saved: tuple[str, str, int] | None = None

    async def get(self, _key: str) -> str | None:
        return self.value

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.saved = (key, value, ex)


def make_request(connection: Connection, *, redis: FakeRedis | None = None) -> Request:
    settings = Settings()
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: Context(connection)),
            settings=settings,
            redis_state=redis or FakeRedis(),
        )
    )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/queue",
            "headers": [],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )


def row(visit_id, department_id, *, status: str = "queued", urgency: str = "routine"):
    return {
        "visit_id": visit_id,
        "department_id": department_id,
        "status": status,
        "urgency": urgency,
        "queued_at": datetime.now(UTC) - timedelta(minutes=10),
        "age_years": 40,
        "pregnant": False,
        "token_label": f"GM-{str(visit_id.int)[-3:]}",
        "sex": "unknown",
        "case_sheet_data": {
            "chief_complaint": {"text_en": "Synthetic complaint"},
            "routing": {"department_code": "GENMED"},
        },
    }


@pytest.mark.asyncio
async def test_staff_queue_filters_sorts_and_paginates_department_rows(monkeypatch) -> None:
    hospital_id, actor_id, department_id = uuid4(), uuid4(), uuid4()
    visits = [
        row(uuid4(), department_id, urgency="routine"),
        row(uuid4(), department_id, urgency="urgent"),
    ]
    connection = Connection(
        [{"id": department_id, "code": "GENMED", "name_en": "General Medicine"}]
    )

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def queued_visits(self, **_kwargs: object) -> list[dict[str, object]]:
            return visits

    monkeypatch.setattr(queue, "set_hospital_scope", scope)
    monkeypatch.setattr(queue, "QueueRepository", Repository)
    request = make_request(connection)
    principal = {
        "role": "admin",
        "hospital_id": str(hospital_id),
        "sub": str(actor_id),
    }
    first_page = await queue.staff_queue(
        request,
        principal,
        department_id=department_id,
        status=None,
        limit=1,
        cursor=None,
    )
    assert first_page["items"][0]["urgency"] == "urgent"
    assert first_page["items"][0]["complaint"] == "Synthetic complaint"
    assert first_page["next_cursor"]

    second_page = await queue.staff_queue(
        request,
        principal,
        department_id=department_id,
        status="queued",
        limit=1,
        cursor=first_page["next_cursor"],
    )
    assert len(second_page["items"]) == 1
    assert second_page["next_cursor"] is None
    assert second_page["departments"][0]["name"] == "General Medicine"


@pytest.mark.asyncio
async def test_staff_queue_rejects_inactive_department_and_missing_staff_assignment(
    monkeypatch,
) -> None:
    hospital_id, actor_id, department_id = uuid4(), uuid4(), uuid4()

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(queue, "set_hospital_scope", scope)
    request = make_request(Connection([]))
    admin = {"role": "admin", "hospital_id": str(hospital_id), "sub": str(actor_id)}
    with pytest.raises(ApiProblemError) as inactive:
        await queue.staff_queue(
            request,
            admin,
            department_id=department_id,
            status=None,
            limit=50,
            cursor=None,
        )
    assert inactive.value.status == 404

    doctor = {**admin, "role": "doctor"}
    with pytest.raises(ApiProblemError) as unassigned:
        await queue.staff_queue(
            request,
            doctor,
            department_id=None,
            status=None,
            limit=50,
            cursor=None,
        )
    assert unassigned.value.status == 403


@pytest.mark.asyncio
async def test_patient_status_validates_token_uses_cache_then_falls_back_to_queue(
    monkeypatch,
) -> None:
    from redis.exceptions import RedisError

    hospital_id, department_id, visit_id = uuid4(), uuid4(), uuid4()
    token = "T" * 43

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(queue, "set_secret_lookup_scope", scope)
    monkeypatch.setattr(queue, "set_hospital_scope", scope)

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def visit_status_by_hash(self, _digest: str) -> dict[str, object]:
            return {
                "visit_id": visit_id,
                "hospital_id": hospital_id,
                "department_id": department_id,
                "status": "queued",
                "urgency": "routine",
                "token_label": "GM-042",
                "queued_at": datetime.now(UTC),
            }

        async def queued_visits(self, **_kwargs: object) -> list[dict[str, object]]:
            return [row(visit_id, department_id)]

        async def queue_estimate_inputs(self, **_kwargs: object) -> tuple[int, list[float]]:
            return 2, [5.0, 7.0]

    class ReadModel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def position(self, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr(queue, "QueueRepository", Repository)
    monkeypatch.setattr(queue, "QueueRedis", ReadModel)
    cache = FakeRedis(json.dumps({"state": "waiting", "token": "cached"}))
    request = make_request(Connection([]), redis=cache)
    assert (await queue.patient_status(token, request))["token"] == "cached"

    with pytest.raises(ApiProblemError) as invalid:
        await queue.patient_status("short", request)
    assert invalid.value.status == 404

    uncached = FakeRedis()
    request = make_request(Connection([]), redis=uncached)
    result = await queue.patient_status(token, request)
    assert result["state"] == "waiting"
    assert result["token"] == "GM-042"
    assert result["position"] == 1
    assert uncached.saved is not None and uncached.saved[2] == 2

    class RedisUnavailable(FakeRedis):
        async def get(self, _key: str) -> None:
            raise RedisError("cache unavailable")

        async def set(self, *_args: object, **_kwargs: object) -> None:
            raise RedisError("cache unavailable")

    request = make_request(Connection([]), redis=RedisUnavailable())
    assert (await queue.patient_status(token, request))["position"] == 1


@pytest.mark.asyncio
async def test_confirm_routing_goes_through_the_encrypting_repository_and_audits(
    monkeypatch,
) -> None:
    hospital_id, actor_id, visit_id, department_id = (uuid4() for _ in range(4))
    # The stored column is ciphertext; the repository is the only way to read or write it.
    stored: dict[str, object] = {
        "data": {
            "schema_version": "1.0",
            "routing": {"needs_nurse_confirmation": True, "department_code": "GENMED"},
        },
        "version": 3,
    }
    replaced: list[dict[str, object]] = []
    connection = Connection([], assigned=department_id)
    audits: list[dict[str, object]] = []

    class Repository:
        conflict = False

        def __init__(self, *_args: object) -> None:
            pass

        async def get(self, **_kwargs: object) -> dict[str, object]:
            return {
                "id": uuid4(),
                "data": json.loads(json.dumps(stored["data"])),
                "version": stored["version"],
            }

        async def replace_if_version(self, **kwargs: object) -> int | None:
            if self.conflict:
                return None
            replaced.append(kwargs)
            return int(kwargs["expected_version"]) + 1  # type: ignore[call-overload]

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(queue, "set_hospital_scope", scope)
    monkeypatch.setattr(queue, "audit", audit)
    monkeypatch.setattr(queue, "CaseSheetRepository", Repository)
    request = make_request(connection)
    nurse = {"role": "nurse", "hospital_id": str(hospital_id), "sub": str(actor_id)}
    result = await queue.confirm_routing(
        visit_id, queue.RouteConfirmPayload(reason="nurse reviewed"), request, nurse
    )
    assert result["confirmed"] is True
    assert result["routing"]["needs_nurse_confirmation"] is False
    assert replaced[0]["expected_version"] == 3
    assert replaced[0]["editor_id"] == actor_id
    assert replaced[0]["data"]["routing"]["needs_nurse_confirmation"] is False  # type: ignore[index]
    assert audits[0]["action"] == "routing.confirmed"
    # Never a raw UPDATE of the encrypted column outside the repository.
    assert not any("UPDATE case_sheets" in str(sql) for sql, _ in connection.executed)

    connection.assigned = None
    with pytest.raises(ApiProblemError) as unassigned:
        await queue.confirm_routing(
            visit_id, queue.RouteConfirmPayload(reason="reviewed"), request, nurse
        )
    assert unassigned.value.status == 403

    # An administrator needs no department, but the visit must still be live.
    admin = {**nurse, "role": "admin"}
    with pytest.raises(ApiProblemError) as missing:
        await queue.confirm_routing(
            visit_id, queue.RouteConfirmPayload(reason="reviewed"), request, admin
        )
    assert missing.value.status == 404

    connection.assigned = department_id
    stored["data"] = {"schema_version": "1.0", "routing": {"needs_nurse_confirmation": False}}
    with pytest.raises(ApiProblemError) as confirmed:
        await queue.confirm_routing(
            visit_id, queue.RouteConfirmPayload(reason="reviewed"), request, admin
        )
    assert confirmed.value.status == 409
    assert confirmed.value.code == "ROUTE_ALREADY_CONFIRMED"

    stored["data"] = {"schema_version": "1.0", "routing": {"needs_nurse_confirmation": True}}
    Repository.conflict = True
    with pytest.raises(ApiProblemError) as changed:
        await queue.confirm_routing(
            visit_id, queue.RouteConfirmPayload(reason="reviewed"), request, admin
        )
    assert changed.value.code == "CASE_SHEET_CONFLICT"


@pytest.mark.asyncio
async def test_status_change_commits_then_publishes_and_maps_repo_errors(monkeypatch) -> None:
    from redis.exceptions import RedisError

    hospital_id, actor_id, visit_id, department_id = (uuid4() for _ in range(4))
    changes: list[dict[str, object]] = []

    class Repository:
        outcome: Exception | None = None

        def __init__(self, *_args: object) -> None:
            pass

        async def transition_visit(self, **kwargs: object) -> dict[str, object]:
            if self.outcome:
                raise self.outcome
            changes.append(kwargs)
            return {
                "department_id": department_id,
                "token_label": "GM-042",
                "urgency": "urgent",
            }

    class PubSubQueue:
        failure = False

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def remove(self, **_kwargs: object) -> tuple[bool, list[object]]:
            return False, []

        async def publish_delta(self, **kwargs: object) -> None:
            if self.failure:
                raise RedisError("offline")
            changes.append(kwargs)

        async def publish_status(self, **kwargs: object) -> None:
            changes.append(kwargs)

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(queue, "set_hospital_scope", scope)
    monkeypatch.setattr(queue, "audit", audit)
    monkeypatch.setattr(queue, "QueueRepository", Repository)
    monkeypatch.setattr(queue, "QueueRedis", PubSubQueue)
    request = make_request(Connection([]))
    principal = {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"}

    assert await queue._perform_status_change(request, visit_id, principal, "in_consult") == {
        "visit_id": str(visit_id),
        "status": "in_consult",
    }
    assert changes[-2]["ops"][0]["op"] == "upsert"
    assert changes[-1]["payload"] == {"changed": True}

    Repository.outcome = LookupError("missing")
    with pytest.raises(ApiProblemError) as missing:
        await queue._perform_status_change(request, visit_id, principal, "done")
    assert missing.value.status == 404
    Repository.outcome = ValueError("invalid transition")
    with pytest.raises(ApiProblemError) as invalid:
        await queue._perform_status_change(request, visit_id, principal, "done")
    assert invalid.value.status == 409

    Repository.outcome = None
    PubSubQueue.failure = True
    assert (await queue._perform_status_change(request, visit_id, principal, "done"))[
        "status"
    ] == "done"


@pytest.mark.asyncio
async def test_open_and_acknowledge_alerts_apply_staff_scope_and_audit(monkeypatch) -> None:
    hospital_id, actor_id, visit_id, department_id, alert_id = (uuid4() for _ in range(5))
    created = datetime.now(UTC)
    alert = {
        "id": alert_id,
        "visit_id": visit_id,
        "rule_id": "DS_CHEST",
        "level": "urgent",
        "evidence": {"source": "patient selected danger tile"},
        "created_at": created,
        "department_id": department_id,
        "token_label": "GM-042",
        "acknowledged_at": None,
    }
    connection = Connection([alert], assigned=department_id)
    audits: list[dict[str, object]] = []

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(_connection: object, _request: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(queue, "set_hospital_scope", scope)
    monkeypatch.setattr(queue, "audit", audit)
    request = make_request(connection)
    nurse = {"role": "nurse", "hospital_id": str(hospital_id), "sub": str(actor_id)}

    alerts = await queue.open_alerts(request, nurse, acknowledged=False, department_id=None)
    assert alerts["items"][0]["reason_en"]
    assert alerts["items"][0]["created_at"] == created.isoformat()
    assert alerts["items"][0]["acknowledged_at"] is None

    ack_result = await queue.acknowledge_alert(alert_id, request, nurse)
    assert ack_result == {"id": str(alert_id), "acknowledged": True}
    assert audits[0]["action"] == "red_flag.acknowledged"
    assert connection.executed[-1][1]["department_id"] == department_id

    connection.assigned = None
    with pytest.raises(ApiProblemError) as no_assignment:
        await queue.open_alerts(request, nurse, acknowledged=True, department_id=None)
    assert no_assignment.value.status == 403
