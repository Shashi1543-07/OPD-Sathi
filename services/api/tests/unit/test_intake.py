import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.api.v1 import intake
from app.config import Settings
from app.core.errors import ApiProblemError
from app.db.repositories import IntakeRepository


def test_offline_submission_requires_consent_and_excludes_contact_details() -> None:
    payload = intake.OfflineSubmissionPayload(
        idempotency_key="0123456789abcdef0123456789abcdef",
        language="en",
        consent_granted=True,
        consent_version="consent-1.0",
        consent_sha256=intake.get_consent_copy("en")["sha256"],
        age=37,
        sex="unknown",
        chief_complaint="  synthetic cough  ",
        duration="two days",
        severity=4,
        conditions="",
        medications="",
        allergies="",
        danger_tile_ids=[],
    )
    assert payload.chief_complaint == "synthetic cough"
    with pytest.raises(ValidationError):
        intake.OfflineSubmissionPayload.model_validate(
            {**payload.model_dump(), "phone": "9876543210"}
        )
    with pytest.raises(ValidationError):
        intake.OfflineSubmissionPayload.model_validate(
            {**payload.model_dump(), "age": 121}
        )


@pytest.mark.asyncio
async def test_offline_replay_rejects_outdated_consent_copy() -> None:
    hospital_id, kiosk_id = uuid4(), uuid4()
    app = SimpleNamespace(state=SimpleNamespace())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/intake/offline-submissions",
            "headers": [(b"idempotency-key", b"0123456789abcdef0123456789abcdef")],
            "app": app,
            "state": {},
        }
    )
    payload = intake.OfflineSubmissionPayload(
        idempotency_key="0123456789abcdef0123456789abcdef",
        language="en",
        consent_granted=True,
        consent_version="consent-old",
        consent_sha256="0" * 64,
        age=37,
        sex="unknown",
        chief_complaint="synthetic concern",
        severity=2,
    )
    with pytest.raises(ApiProblemError) as error:
        await intake.replay_offline_submission(
            payload,
            request,
            {"role": "kiosk", "sub": str(kiosk_id), "hospital_id": str(hospital_id)},
        )
    assert error.value.status == 409
    assert error.value.code == "CONSENT_COPY_MISMATCH"


@pytest.mark.asyncio
async def test_offline_replay_uses_regular_interview_and_idempotent_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, kiosk_id, visit_id = uuid4(), uuid4(), uuid4()
    key = "0123456789abcdef0123456789abcdef"
    redis_values: dict[str, str] = {}

    class Redis:
        async def get(self, name: str) -> str | None:
            return redis_values.get(name)

        async def set(self, name: str, value: str, **_kwargs: object) -> None:
            redis_values[name] = value

    redis_client = Redis()

    class Connection:
        async def __aenter__(self) -> "Connection":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Turns:
        rows: list[dict[str, object]] = []

        def __init__(self, *_args: object) -> None:
            pass

        async def add(self, **kwargs: object) -> None:
            self.rows.append(kwargs)

    class Idempotency:
        cached: dict[str, dict[str, object]] = {}

        def __init__(self, _redis: object) -> None:
            pass

        async def execute(
            self,
            _request: object,
            *,
            body: dict[str, object],
            operation: object,
            **_kwargs: object,
        ) -> dict[str, object]:
            if key not in self.cached:
                self.cached[key] = await operation()  # type: ignore[operator]
            assert body["idempotency_key"] == key
            return self.cached[key]

    created: list[bool] = []

    async def create(_payload: object, _request: object, _principal: object) -> dict[str, str]:
        created.append(True)
        return {"visit_id": str(visit_id)}

    async def noop(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {}

    async def danger(*_args: object, **_kwargs: object) -> dict[str, object]:
        state = {"phase": "interview", "interview": intake.start("en").model_dump(mode="json")}
        await redis_client.set(intake._session_key(hospital_id, visit_id), json.dumps(state))
        return {}

    async def finish(
        _request: object,
        *,
        state_data: dict[str, object],
        interview_state: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        state_data["phase"] = "summary"
        return {"complete": True}

    async def confirm(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"confirmed": True, "token": "GEN-123", "position": 1}

    async def scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(intake, "IdempotencyStore", Idempotency)
    monkeypatch.setattr(intake, "create_session", create)
    monkeypatch.setattr(intake, "record_consent", noop)
    monkeypatch.setattr(intake, "save_identity", noop)
    monkeypatch.setattr(intake, "save_danger_signs", danger)
    monkeypatch.setattr(intake, "InterviewTurnRepository", Turns)
    monkeypatch.setattr(intake, "_finish_interview", finish)
    monkeypatch.setattr(intake, "confirm_summary", confirm)
    monkeypatch.setattr(intake, "set_hospital_scope", scope)
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: Connection()),
            settings=SimpleNamespace(patient_session_hours=8),
            redis_state=redis_client,
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/intake/offline-submissions",
            "headers": [(b"idempotency-key", key.encode())],
            "app": app,
            "state": {},
        }
    )
    consent = intake.get_consent_copy("en")
    payload = intake.OfflineSubmissionPayload(
        idempotency_key=key,
        language="en",
        consent_granted=True,
        consent_version=consent["version"],
        consent_sha256=consent["sha256"],
        age=37,
        sex="unknown",
        chief_complaint="synthetic cough",
        duration="two days",
        severity=4,
        conditions="asthma",
        medications="none",
        allergies="none",
    )
    principal = {"role": "kiosk", "sub": str(kiosk_id), "hospital_id": str(hospital_id)}

    first = await intake.replay_offline_submission(payload, request, principal)
    second = await intake.replay_offline_submission(payload, request, principal)

    assert first["token"] == second["token"] == "GEN-123"
    assert first["visit_id"] == second["visit_id"] == str(visit_id)
    assert len(created) == 1
    assert {row["question_id"] for row in Turns.rows} >= {
        "chief_concern", "duration", "severity", "past_history", "medications", "allergies"
    }


class FakeConnectionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeEngine:
    def begin(self) -> FakeConnectionContext:
        return FakeConnectionContext()


@pytest.mark.asyncio
async def test_intake_routes_reject_all_nonconsented_patient_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, visit_id = uuid4(), uuid4()

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def session_context(self, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "registering",
                "consent_granted": False,
                "language": "en",
            }

    async def no_tenant_scope(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "set_hospital_scope", no_tenant_scope)
    app = SimpleNamespace(state=SimpleNamespace(engine=FakeEngine(), settings=Settings()))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/intake/sessions",
            "headers": [],
            "app": app,
            "state": {},
        }
    )
    principal = {
        "role": "patient",
        "sub": str(visit_id),
        "hospital_id": str(hospital_id),
    }
    with pytest.raises(ApiProblemError) as error:
        await intake._authorized_visit(request, visit_id, principal, consent_required=True)
    assert error.value.status == 409
    assert error.value.code == "CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_assisted_session_is_department_scoped_and_marks_nurse_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hospital_id, nurse_id, department_id, visit_id, patient_id = (uuid4() for _ in range(5))

    class Connection:
        async def __aenter__(self) -> "Connection":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def scalar(self, *_args: object, **_kwargs: object) -> object:
            return department_id

    class Redis:
        value: dict[str, object] | None = None

        async def set(self, _key: str, value: str, **_kwargs: object) -> None:
            self.value = json.loads(value)

    class Repository:
        def __init__(self, *_args: object) -> None:
            pass

        async def active_nurse_department(self, **kwargs: object) -> object:
            assert kwargs == {"hospital_id": hospital_id, "nurse_id": nurse_id}
            return department_id

        async def create_assisted_session(self, **kwargs: object) -> tuple[object, object]:
            assert kwargs == {
                "hospital_id": hospital_id,
                "department_id": department_id,
                "language": "en",
            }
            return visit_id, patient_id

    connection = Connection()
    redis = Redis()
    audits: list[dict[str, object]] = []

    async def no_tenant_scope(*_args: object, **_kwargs: object) -> None:
        return None

    async def record_audit(_connection: object, _request: object, **values: object) -> None:
        audits.append(values)

    monkeypatch.setattr(intake, "set_hospital_scope", no_tenant_scope)
    monkeypatch.setattr(intake, "IntakeRepository", Repository)
    monkeypatch.setattr(intake, "audit", record_audit)
    monkeypatch.setattr(intake, "issue_access_token", lambda **_kwargs: "synthetic-token")
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: connection),
            settings=Settings(),
            redis_state=redis,
        )
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/intake/assisted-sessions",
            "headers": [],
            "app": app,
            "state": {},
        }
    )

    response = await intake.create_assisted_session(
        intake.CreateAssistedSessionPayload(language="en"),
        request,
        {"role": "nurse", "sub": str(nurse_id), "hospital_id": str(hospital_id)},
    )

    assert response["visit_id"] == str(visit_id)
    assert response["patient_access_token"] == "synthetic-token"
    assert response["consent"]["language"] == "en"
    assert redis.value is not None
    assert redis.value["channel"] == "nurse"
    assert redis.value["nurse_id"] == str(nurse_id)
    assert audits[0]["action"] == "intake.assisted_started"
    assert audits[0]["meta"] == {"channel": "nurse", "language": "en"}


@pytest.mark.asyncio
async def test_assisted_consent_persists_nurse_channel() -> None:
    class Connection:
        def __init__(self) -> None:
            self.parameters: dict[str, object] | None = None

        async def scalar(self, *_args: object, **_kwargs: object) -> object:
            return None

        async def execute(self, _statement: object, parameters: dict[str, object]) -> None:
            self.parameters = parameters

    connection = Connection()
    await IntakeRepository(connection, Settings()).record_consent(
        hospital_id=uuid4(),
        visit_id=uuid4(),
        language="en",
        version="synthetic-version",
        text_sha256="0" * 64,
        granted=True,
        channel="nurse",
    )

    assert connection.parameters is not None
    assert connection.parameters["channel"] == "nurse"


def test_intake_payload_rejects_boolean_as_age_or_answer() -> None:
    with pytest.raises(ValueError):
        intake.IdentityPayload.model_validate({"age": True, "sex": "unknown"})
    with pytest.raises(ValueError):
        intake.AnswerPayload.model_validate({"question_id": "severity", "answer": True})


def test_sms_opt_in_requires_valid_phone_and_defaults_to_off() -> None:
    payload = intake.IdentityPayload.model_validate({"age": 42, "sex": "unknown"})
    assert payload.sms_opt_in is False
    with pytest.raises(ValueError):
        intake.IdentityPayload.model_validate({"age": 42, "sex": "unknown", "sms_opt_in": True})
    opted_in = intake.IdentityPayload.model_validate(
        {
            "age": 42,
            "sex": "unknown",
            "phone": "9876543210",
            "sms_opt_in": True,
        }
    )
    assert opted_in.sms_opt_in is True


def test_intake_rejects_oversized_answer_without_echoing_it() -> None:
    assert intake._validate_answer_size("x" * 500) is None
    answer = "x" * 10_000
    with pytest.raises(ApiProblemError) as error:
        intake._validate_answer_size(answer)
    assert error.value.status == 400
    assert error.value.code == "VALIDATION_FAILED"
    assert answer not in error.value.detail
