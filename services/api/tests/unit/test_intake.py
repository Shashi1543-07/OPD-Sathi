import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import intake
from app.config import Settings
from app.core.errors import ApiProblemError
from app.db.repositories import IntakeRepository


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
