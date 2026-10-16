from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import intake
from app.config import Settings
from app.core.errors import ApiProblemError


class FakeResult:
    def __init__(self, row: dict[str, object] | None = None, values: list[object] | None = None):
        self.row = row
        self.values = values or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, object] | None:
        return self.row

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[object]:
        return self.values


class FakeConnection:
    def __init__(
        self, *, row: dict[str, object] | None = None, existing: list[object] | None = None
    ):
        self.row = row
        self.existing = existing or []
        self.executed: list[tuple[object, object]] = []

    async def execute(self, query: object, parameters: object = None) -> FakeResult:
        self.executed.append((query, parameters))
        if len(self.executed) == 1:
            return FakeResult(row=self.row)
        if len(self.executed) == 2:
            return FakeResult(values=self.existing)
        return FakeResult()

    async def scalar(self, query: object, parameters: object = None) -> object:
        sql = str(query)
        if "INSERT INTO red_flag_events" in sql:
            # Red-flag events are inserted with RETURNING id; a new row returns its id.
            self.executed.append((query, parameters))
            return uuid4()
        if "hospital_ai_settings" in sql:
            return False  # This synthetic hospital has AI switched off.
        return None


class MemoryRedis:
    def __init__(self, initial: dict[str, object] | None = None):
        self.values = {
            key: json.dumps(value, separators=(",", ":")) for key, value in (initial or {}).items()
        }
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs: object) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def publish(self, *_args: object) -> None:
        return None


class FakeConnectionContext:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


def request_for(connection: FakeConnection, redis: object | None = None) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: FakeConnectionContext(connection)),
            settings=Settings(),
            redis_state=redis,
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/intake/test",
            "headers": [],
            "server": ("test", 80),
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "app": app,
            "state": {},
        }
    )


def test_intake_identity_state_readback_and_patient_copy_validation() -> None:
    hospital_id, visit_id = uuid4(), uuid4()
    assert intake._identity({"hospital_id": str(hospital_id), "sub": str(visit_id)}) == (
        hospital_id,
        visit_id,
    )
    for principal in ({}, {"hospital_id": "bad", "sub": str(visit_id)}):
        with pytest.raises(ApiProblemError) as invalid:
            intake._identity(principal)
        assert invalid.value.status == 401

    assert intake._read_state('{"phase":"consent"}') == {"phase": "consent"}
    for raw, expected in ((None, 410), ("not-json", 503), ("[]", 503)):
        with pytest.raises(ApiProblemError) as error:
            intake._read_state(raw)
        assert error.value.status == expected

    data = {"language": "en", "chief_complaint": {"text_original": "Synthetic pain"}}
    assert "Synthetic pain" in intake._readback(data)
    assert intake._patient_message("en") == "Please inform the staff at the desk now."
    assert intake._patient_message("hi")
    assert intake._patient_message("mr")
    assert intake._question_payload(intake.start("en"), "chief_concern")["id"] == "chief_concern"


@pytest.mark.asyncio
async def test_intake_authorization_rejects_wrong_subject_closed_and_missing_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, visit_id = uuid4(), uuid4()
    context: dict[str, object] | None = None

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def session_context(self, **_kwargs: object) -> dict[str, object] | None:
            return context

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    request = request_for(FakeConnection())
    patient = {"role": "patient", "hospital_id": str(hospital_id), "sub": str(visit_id)}

    with pytest.raises(ApiProblemError) as wrong_subject:
        await intake._authorized_visit(request, uuid4(), patient, consent_required=False)
    assert wrong_subject.value.status == 404
    with pytest.raises(ApiProblemError) as wrong_role:
        await intake._authorized_visit(
            request, visit_id, {**patient, "role": "doctor"}, consent_required=False
        )
    assert wrong_role.value.status == 404

    for value, expected in (
        (None, 404),
        ({"status": "cancelled", "consent_granted": False, "language": "en"}, 410),
        ({"status": "registering", "consent_granted": False, "language": "en"}, 409),
    ):
        context = value
        with pytest.raises(ApiProblemError) as failure:
            await intake._authorized_visit(request, visit_id, patient, consent_required=True)
        assert failure.value.status == expected

    context = {"status": "registering", "consent_granted": True, "language": "en"}
    assert await intake._authorized_visit(request, visit_id, patient, consent_required=True) == (
        hospital_id,
        context,
    )


@pytest.mark.asyncio
async def test_visit_evaluation_persists_new_flags_and_only_alerts_urgent(monkeypatch) -> None:
    hospital_id, visit_id, department_id = uuid4(), uuid4(), uuid4()
    row = {
        "age_years": 45,
        "pregnant": False,
        "department_id": department_id,
        "status": "queued",
        "urgency": "routine",
        "token_label": "GM-042",
        "urgency_reasons": [],
        "sex": "unknown",
    }
    connection = FakeConnection(row=row, existing=[])
    flags, route, alerts = await intake._evaluate_visit(
        connection,
        hospital_id=hospital_id,
        visit_id=visit_id,
        answers={"chief_concern": "severe chest pain"},
        tile_ids=["chest_pain"],
    )

    assert flags
    assert route.department_code
    assert alerts and alerts[0]["type"] == "alert.urgent"
    assert alerts[0]["token_label"] == "GM-042"
    assert alerts[0]["department_id"] == str(department_id)
    assert (
        len(connection.executed) >= 4
    )  # fetch, existing flags, inserts, and urgency/outbox writes

    no_visit = FakeConnection(row=None)
    no_flags, default_route, no_alerts = await intake._evaluate_visit(
        no_visit,
        hospital_id=hospital_id,
        visit_id=visit_id,
        answers={},
        tile_ids=[],
    )
    assert no_flags == [] and no_alerts == []
    assert default_route.department_code


@pytest.mark.asyncio
async def test_alert_publish_is_best_effort_and_removes_department_field() -> None:
    from redis.exceptions import RedisError

    class Publisher:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail
            self.messages: list[tuple[str, str]] = []

        async def publish(self, channel: str, message: str) -> None:
            if self.fail:
                raise RedisError("synthetic Redis outage")
            self.messages.append((channel, message))

    hospital_id, department_id = uuid4(), uuid4()
    event = {
        "type": "alert.urgent",
        "id": "synthetic-alert",
        "department_id": str(department_id),
    }
    publisher = Publisher()
    await intake._publish_alerts(
        publisher,
        app_env="test",
        hospital_id=hospital_id,
        alerts=[event],
    )
    assert publisher.messages[0][0] == f"osa:test:{hospital_id}:alerts:{department_id}"
    assert "department_id" not in json.loads(publisher.messages[0][1])

    await intake._publish_alerts(
        Publisher(fail=True),
        app_env="test",
        hospital_id=hospital_id,
        alerts=[
            {
                "type": "alert.urgent",
                "id": "synthetic-alert",
                "department_id": str(department_id),
            }
        ],
    )


@pytest.mark.asyncio
async def test_kiosk_session_creation_persists_consent_state_and_cancels_on_redis_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, kiosk_id, visit_id, patient_id = (uuid4() for _ in range(4))
    created: list[dict[str, object]] = []

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def create_session(self, **kwargs: object) -> tuple[object, object]:
            created.append(kwargs)
            return visit_id, patient_id

        async def set_visit_status(self, **kwargs: object) -> None:
            created.append(kwargs)

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "issue_access_token", lambda **_kwargs: "synthetic-patient-token")
    redis = MemoryRedis()
    request = request_for(FakeConnection(), redis)
    result = await intake.create_session(
        intake.CreateSessionPayload(language="en"),
        request,
        {"hospital_id": str(hospital_id), "sub": str(kiosk_id), "role": "kiosk"},
    )
    state_key = intake._session_key(hospital_id, visit_id)
    state = json.loads(redis.values[state_key])
    assert result["patient_access_token"] == "synthetic-patient-token"
    assert result["consent"]["language"] == "en"
    assert state["phase"] == "consent" and state["kiosk_id"] == str(kiosk_id)
    assert created[0]["language"] == "en"

    class FailedRedis(MemoryRedis):
        async def set(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic Redis outage")

    failed_request = request_for(FakeConnection(), FailedRedis())
    with pytest.raises(ApiProblemError) as unavailable:
        await intake.create_session(
            intake.CreateSessionPayload(language="en"),
            failed_request,
            {"hospital_id": str(hospital_id), "sub": str(kiosk_id), "role": "kiosk"},
        )
    assert unavailable.value.status == 503
    assert created[-1]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_consent_grant_and_decline_persist_the_right_channel_and_next_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, visit_id, nurse_id = uuid4(), uuid4(), uuid4()
    consent = intake.get_consent_copy("en")
    state_key = intake._session_key(hospital_id, visit_id)
    state = {
        "visit_id": str(visit_id),
        "channel": "nurse",
        "assisted": True,
        "nurse_id": str(nurse_id),
        "phase": "consent",
    }
    redis = MemoryRedis({state_key: state})
    recorded: list[dict[str, object]] = []

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def record_consent(self, **kwargs: object) -> None:
            recorded.append(kwargs)

        async def set_visit_status(self, **kwargs: object) -> None:
            recorded.append(kwargs)

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, {"language": "en", "consent_granted": False}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def audit(*_args: object, **kwargs: object) -> None:
        recorded.append(kwargs)

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "audit", audit)
    request = request_for(FakeConnection(), redis)
    principal = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    granted = await intake.record_consent(
        visit_id,
        intake.ConsentPayload(version=consent["version"], sha256=consent["sha256"], granted=True),
        request,
        principal,
    )
    assert granted == {"granted": True, "next": "identity"}
    assert json.loads(redis.values[state_key])["phase"] == "identity"
    assert recorded[0]["channel"] == "nurse"
    assert recorded[1]["actor_type"] == "user"

    declined = await intake.record_consent(
        visit_id,
        intake.ConsentPayload(version=consent["version"], sha256=consent["sha256"], granted=False),
        request,
        principal,
    )
    assert declined["next"] == "staff_help"
    assert redis.deleted == [state_key]
    assert any(entry.get("status") == "cancelled" for entry in recorded)

    with pytest.raises(ApiProblemError) as stale_copy:
        await intake.record_consent(
            visit_id,
            intake.ConsentPayload(version="old", sha256=consent["sha256"], granted=True),
            request,
            principal,
        )
    assert stale_copy.value.status == 409


@pytest.mark.asyncio
async def test_identity_and_danger_sign_routes_advance_only_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, visit_id, patient_id = uuid4(), uuid4(), uuid4()
    state_key = intake._session_key(hospital_id, visit_id)
    redis = MemoryRedis(
        {
            state_key: {
                "phase": "identity",
                "assisted": True,
                "language": "en",
            }
        }
    )
    updates: list[dict[str, object]] = []

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def update_identity(self, **kwargs: object) -> None:
            updates.append(kwargs)

        async def record_danger_tiles(self, **kwargs: object) -> None:
            updates.append(kwargs)

    context = {"patient_id": patient_id, "language": "en"}

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, context

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def evaluate(*_args: object, **_kwargs: object):
        return [], intake.route_department(""), []

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "_evaluate_visit", evaluate)
    request = request_for(FakeConnection(), redis)
    principal = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    with pytest.raises(ApiProblemError) as missing_name:
        await intake.save_identity(
            visit_id,
            intake.IdentityPayload(age=42, sex="unknown"),
            request,
            principal,
        )
    assert missing_name.value.status == 422

    identity = await intake.save_identity(
        visit_id,
        intake.IdentityPayload(name="Synthetic person", age=42, sex="unknown"),
        request,
        principal,
    )
    assert identity == {"next": "danger_signs"}
    assert updates[0]["name"] == "Synthetic person"
    assert json.loads(redis.values[state_key])["phase"] == "danger_signs"

    with pytest.raises(ApiProblemError) as duplicate:
        await intake.save_danger_signs(
            visit_id,
            intake.DangerSignsPayload(tile_ids=["chest_pain", "chest_pain"]),
            request,
            principal,
        )
    assert duplicate.value.status == 422

    selection = await intake.save_danger_signs(
        visit_id,
        intake.DangerSignsPayload(tile_ids=["chest_pain"]),
        request,
        principal,
    )
    assert selection["staff_help_required"] is True
    assert selection["patient_message"] == intake._patient_message("en")
    saved = json.loads(redis.values[state_key])
    assert saved["phase"] == "interview" and saved["danger_ids"] == ["chest_pain"]


@pytest.mark.asyncio
async def test_interview_answer_route_validates_records_and_advances_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.interview.engine import start

    hospital_id, visit_id = uuid4(), uuid4()
    state_key = intake._session_key(hospital_id, visit_id)
    state = {"phase": "interview", "interview": start("en").model_dump(mode="json")}
    redis = MemoryRedis({state_key: state})
    turns: list[dict[str, object]] = []

    class TurnRepository:
        def __init__(self, *_args: object) -> None:
            pass

        async def add(self, **kwargs: object) -> None:
            turns.append(kwargs)

    class Idempotency:
        def __init__(self, *_args: object) -> None:
            pass

        async def execute(self, _request: object, *, operation, **_kwargs: object):
            return await operation()

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, {}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def evaluate(*_args: object, **_kwargs: object):
        return [], intake.route_department(""), []

    async def next_response(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"complete": False, "question": {"id": "duration"}}

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "InterviewTurnRepository", TurnRepository)
    monkeypatch.setattr(intake, "IdempotencyStore", Idempotency)
    monkeypatch.setattr(intake, "_evaluate_visit", evaluate)
    monkeypatch.setattr(intake, "_question_response", next_response)
    request = request_for(FakeConnection(), redis)
    principal = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    response = await intake.submit_answer(
        visit_id,
        intake.AnswerPayload(question_id="chief_concern", answer="  chest pain  "),
        request,
        principal,
    )
    assert response["question"]["id"] == "duration"
    updated = json.loads(redis.values[state_key])["interview"]
    assert updated["answers"]["chief_concern"] == "chest pain"
    assert turns[0]["turn_index"] == 0 and turns[0]["text_original"] == "chest pain"

    with pytest.raises(ApiProblemError) as invalid:
        await intake.submit_answer(
            visit_id,
            intake.AnswerPayload(question_id="severity", answer=5),
            request,
            principal,
        )
    assert invalid.value.status == 422
    with pytest.raises(ApiProblemError) as too_large:
        intake._validate_answer_size("x" * 501)
    assert too_large.value.status == 400


@pytest.mark.asyncio
async def test_answer_correction_route_updates_summary_and_rejects_unasked_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.interview.engine import apply_answer, next_question, start

    hospital_id, visit_id = uuid4(), uuid4()
    state_key = intake._session_key(hospital_id, visit_id)
    interview = apply_answer(
        start("en", sex="male", age=30), "chief_concern", "headache since morning"
    )
    while (question := next_question(interview)) is not None:
        interview = apply_answer(interview, question.id, None, skip=True)
    redis = MemoryRedis(
        {
            state_key: {
                "phase": "summary",
                "interview": interview.model_dump(mode="json"),
                "corrections": 0,
            }
        }
    )
    turns: list[dict[str, object]] = []
    saved: list[dict[str, object]] = []

    class TurnRepository:
        def __init__(self, *_args: object) -> None:
            pass

        async def add(self, **kwargs: object) -> None:
            turns.append(kwargs)

    class SheetRepository:
        def __init__(self, *_args: object) -> None:
            pass

        async def save(self, **kwargs: object) -> None:
            saved.append(kwargs)

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, {}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def evaluate(*_args: object, **_kwargs: object):
        return [], intake.route_department(""), []

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "InterviewTurnRepository", TurnRepository)
    monkeypatch.setattr(intake, "CaseSheetRepository", SheetRepository)
    monkeypatch.setattr(intake, "_evaluate_visit", evaluate)
    request = request_for(FakeConnection(), redis)
    principal = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    result = await intake.correct_interview_answer(
        visit_id,
        intake.AnswerPayload(question_id="chief_concern", answer="updated headache"),
        request,
        principal,
    )
    assert result["complete"] is True
    assert "updated headache" in result["readback"]
    assert len(saved) == 1 and turns[0]["turn_index"] == intake._CORRECTION_TURN_BASE
    persisted = json.loads(redis.values[state_key])
    assert persisted["corrections"] == 1
    assert persisted["interview"]["answers"]["chief_concern"] == "updated headache"

    with pytest.raises(ApiProblemError) as unavailable:
        await intake.correct_interview_answer(
            visit_id,
            intake.AnswerPayload(question_id="duration", answer="yesterday"),
            request,
            principal,
        )
    assert unavailable.value.status == 422

    # A different main problem on the review screen asks its own questions before confirming.
    statuses: list[str] = []

    class Intake:
        def __init__(self, *_args: object) -> None:
            pass

        async def set_visit_status(self, **kwargs: object) -> None:
            statuses.append(str(kwargs["status"]))

    monkeypatch.setattr(intake, "IntakeRepository", Intake)
    reopened = await intake.correct_interview_answer(
        visit_id,
        intake.AnswerPayload(question_id="chief_concern", answer="Skin rash or itching"),
        request,
        principal,
    )
    assert reopened["complete"] is False
    assert reopened["question"]["id"] == "skin"
    assert statuses == ["registering"]
    persisted = json.loads(redis.values[state_key])
    assert persisted["phase"] == "interview"
    assert "pain" not in persisted["interview"]["queued"]


def test_earlier_steps_stay_open_until_the_first_answer() -> None:
    from app.domain.interview.engine import apply_answer, start

    fresh = start("en", sex="male", age=30)
    assert intake._interview_not_started(
        {"phase": "interview", "interview": fresh.model_dump(mode="json")}
    )
    answered = apply_answer(fresh, "chief_concern", "Fever")
    assert not intake._interview_not_started(
        {"phase": "interview", "interview": answered.model_dump(mode="json")}
    )
    assert not intake._interview_not_started({"phase": "summary", "interview": {}})


@pytest.mark.asyncio
async def test_back_undoes_one_answer_and_refills_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain.interview.engine import apply_answer, start

    hospital_id, visit_id = uuid4(), uuid4()
    state_key = intake._session_key(hospital_id, visit_id)
    interview = apply_answer(start("hi", sex="female", age=28), "chief_concern", "बुखार")
    interview = apply_answer(interview, "duration", "आज से")
    followup_key = intake._followup_key(hospital_id, visit_id)
    redis = MemoryRedis(
        {
            state_key: {
                "phase": "interview",
                "interview": interview.model_dump(mode="json"),
                "followups_requested": True,
                "turns": 2,
            },
            followup_key: {"basis": "old", "questions": []},
        }
    )

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, {}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    request = request_for(FakeConnection(), redis)
    principal = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    result = await intake.go_back_one_question(visit_id, request, principal)
    assert result["question"]["id"] == "duration"
    assert result["draft"] == "आज से"
    persisted = json.loads(redis.values[state_key])
    assert persisted["interview"]["asked"] == ["chief_concern"]
    # The AI draft was based on the undone answer, so it is requested again later.
    assert "followups_requested" not in persisted and followup_key not in redis.values

    result = await intake.go_back_one_question(visit_id, request, principal)
    assert result["question"]["id"] == "chief_concern" and result["draft"] == "बुखार"
    assert json.loads(redis.values[state_key])["interview"]["queued"] == [
        "chief_concern",
        "duration",
        "severity",
    ]
    with pytest.raises(ApiProblemError) as nothing_left:
        await intake.go_back_one_question(visit_id, request, principal)
    assert nothing_left.value.status == 409


@pytest.mark.asyncio
async def test_intake_read_routes_and_patient_cancellation_enforce_session_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.interview.engine import start

    hospital_id, visit_id, other_id = uuid4(), uuid4(), uuid4()
    state_key = intake._session_key(hospital_id, visit_id)
    interview = start("en").model_dump(mode="json")
    redis = MemoryRedis({state_key: {"phase": "interview", "interview": interview}})
    cancelled: list[dict[str, object]] = []

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def set_visit_status(self, **kwargs: object) -> None:
            cancelled.append(kwargs)

    async def authorized(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return hospital_id, {"language": "en"}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def response(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"complete": False, "question": {"id": "chief_concern"}}

    monkeypatch.setattr(intake, "_authorized_visit", authorized)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "_question_response", response)
    request = request_for(FakeConnection(), redis)
    patient = {"hospital_id": str(hospital_id), "sub": str(visit_id), "role": "patient"}

    tiles = await intake.get_danger_tiles(visit_id, request, patient)
    assert len(tiles["tiles"]) == len(intake.DANGER_TILES)
    next_question = await intake.get_next_question(visit_id, request, patient)
    assert next_question["question"]["id"] == "chief_concern"
    with pytest.raises(ApiProblemError) as wrong_patient:
        await intake.cancel_session(visit_id, request, {**patient, "sub": str(other_id)})
    assert wrong_patient.value.status == 404

    result = await intake.cancel_session(visit_id, request, patient)
    assert result.status_code == 204
    assert cancelled == [{"hospital_id": hospital_id, "visit_id": visit_id, "status": "cancelled"}]
    assert redis.deleted == [state_key]
