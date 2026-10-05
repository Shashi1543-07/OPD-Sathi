from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1 import intake
from app.config import Settings
from app.core.errors import ApiProblemError


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


def test_intake_payload_rejects_boolean_as_age_or_answer() -> None:
    with pytest.raises(ValueError):
        intake.IdentityPayload.model_validate({"age": True, "sex": "unknown"})
    with pytest.raises(ValueError):
        intake.AnswerPayload.model_validate({"question_id": "severity", "answer": True})


def test_intake_rejects_oversized_answer_without_echoing_it() -> None:
    assert intake._validate_answer_size("x" * 500) is None
    answer = "x" * 10_000
    with pytest.raises(ApiProblemError) as error:
        intake._validate_answer_size(answer)
    assert error.value.status == 400
    assert error.value.code == "VALIDATION_FAILED"
    assert answer not in error.value.detail
