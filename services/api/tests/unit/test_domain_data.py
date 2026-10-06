import base64
import copy
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.core.crypto import decrypt_field, encrypt_field, load_key, lookup_digest
from app.core.errors import ApiProblemError
from app.core.ids import stable_uuid7, uuid7
from app.core.pagination import CursorPosition, decode_cursor, encode_cursor
from app.domain.case_sheet import CaseSheet


def _valid_case_sheet() -> dict:
    return {
        "schema_version": "1.0",
        "language": "en",
        "chief_complaint": {
            "text_original": "Sample concern",
            "text_en": "Sample concern",
            "confidence": 0.95,
        },
        "hpi": {"duration": {"value": 2, "unit": "day"}},
        "routing": {
            "department_code": "GENMED",
            "confidence": 0.9,
            "reason_en": "General review requested",
            "needs_nurse_confirmation": False,
        },
    }


def test_crypto_round_trip_and_context_binding() -> None:
    key = secrets.token_bytes(32)
    ciphertext = encrypt_field("Synthetic Patient", key=key, associated_data="patients:name:1")
    assert ciphertext.startswith("v1:")
    assert "Synthetic Patient" not in ciphertext
    assert (
        decrypt_field(ciphertext, key=key, associated_data="patients:name:1") == "Synthetic Patient"
    )
    with pytest.raises(ValueError):
        decrypt_field(ciphertext, key=key, associated_data="patients:phone:1")
    tampered = ciphertext[:-4] + ("AAAA" if ciphertext[-4:] != "AAAA" else "BBBB")
    with pytest.raises(ValueError):
        decrypt_field(tampered, key=key, associated_data="patients:name:1")


def test_crypto_rejects_invalid_keys_versions_and_payloads() -> None:
    valid_encoded_key = base64.b64encode(b"k" * 32).decode()
    assert load_key(valid_encoded_key, setting_name="APP_DATA_KEY") == b"k" * 32
    with pytest.raises(ValueError):
        load_key("not-base64", setting_name="APP_DATA_KEY")
    with pytest.raises(ValueError):
        load_key(base64.b64encode(b"short").decode(), setting_name="APP_DATA_KEY")
    with pytest.raises(ValueError):
        encrypt_field("value", key=b"short", associated_data="patients:name:1")
    with pytest.raises(ValueError):
        decrypt_field("v1:AAAA", key=b"short", associated_data="patients:name:1")
    with pytest.raises(ValueError):
        decrypt_field("v2:AAAA", key=b"k" * 32, associated_data="patients:name:1")
    with pytest.raises(ValueError, match="truncated"):
        decrypt_field(
            "v1:" + base64.b64encode(b"x" * 10).decode(),
            key=b"k" * 32,
            associated_data="patients:name:1",
        )
    with pytest.raises(ValueError):
        decrypt_field("v1:bad!", key=b"k" * 32, associated_data="patients:name:1")
    with pytest.raises(ValueError):
        lookup_digest("value", key=b"short")


def test_crypto_uses_unique_nonces_for_100000_encryptions() -> None:
    key = secrets.token_bytes(32)
    nonces = {
        encrypt_field(str(index), key=key, associated_data="test") for index in range(100_000)
    }
    assert len(nonces) == 100_000


def test_lookup_digest_is_deterministic_and_keyed() -> None:
    value = lookup_digest("+15555550100", key=b"a" * 32)
    assert value == lookup_digest("+15555550100", key=b"a" * 32)
    assert value != lookup_digest("+15555550100", key=b"b" * 32)


def test_uuid_generators_emit_version_seven() -> None:
    assert uuid7().version == 7
    assert stable_uuid7("demo").version == 7
    assert stable_uuid7("demo") == stable_uuid7("demo")


def test_uuid7_rejects_timestamp_outside_range(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import ids

    monkeypatch.setattr(ids.time, "time_ns", lambda: 1 << 70)
    with pytest.raises(ValueError, match="timestamp range"):
        uuid7()


def test_cursor_round_trip_and_tamper_rejection() -> None:
    key = b"cursor-test-key-that-is-long-enough"
    position = CursorPosition(datetime.now(UTC), uuid4())
    encoded = encode_cursor(position, key=key)
    assert decode_cursor(encoded, key=key) == position
    payload, signature = encoded.split(".")
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(ApiProblemError) as error:
        decode_cursor(f"{payload}.{tampered_signature}", key=key)
    assert error.value.status == 400
    assert error.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"unexpected":"field"}',
        b'{"created_at":"2026-10-05T12:00:00","id":"00000000-0000-4000-8000-000000000000"}',
    ],
)
def test_cursor_rejects_bad_fields_and_naive_time(payload: bytes) -> None:
    key = b"cursor-test-key-that-is-long-enough"
    signature = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    encoded = (
        base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    )
    with pytest.raises(ApiProblemError) as error:
        decode_cursor(encoded, key=key)
    assert error.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("variant", range(20))
def test_case_sheet_accepts_twenty_valid_examples(variant: int) -> None:
    value = _valid_case_sheet()
    value["language"] = ("en", "hi", "mr")[variant % 3]
    value["hpi"]["severity_0_10"] = variant % 11
    value["routing"]["confidence"] = variant / 20
    if variant % 2:
        value["field_meta"] = {
            "/hpi/severity_0_10": {
                "source": "patient_touch",
                "confidence": 1,
                "confirmed": True,
            }
        }
    assert CaseSheet.model_validate(value).schema_version == "1.0"


@pytest.mark.parametrize("variant", range(20))
def test_case_sheet_rejects_twenty_invalid_examples(variant: int) -> None:
    value = copy.deepcopy(_valid_case_sheet())
    invalid_cases = [
        lambda: value.update(extra="forbidden"),
        lambda: value.update(schema_version="2.0"),
        lambda: value.update(language="fr"),
        lambda: value["chief_complaint"].update(confidence=1.1),
        lambda: value["chief_complaint"].update(text_en="x" * 501),
        lambda: value["hpi"].update(severity_0_10=11),
        lambda: value["hpi"].update(duration={"value": 0, "unit": "day"}),
        lambda: value["hpi"].update(duration={"value": 2, "unit": "decade"}),
        lambda: value["hpi"].update(unknown=True),
        lambda: value["hpi"].update(aggravating=["symptom"] * 31),
        lambda: value.update(medications=[{"name": "drug", "source": "unknown"}]),
        lambda: value.update(allergies=[{"text_en": "x"}]),
        lambda: value.update(
            red_flags=[{"id": "bad id", "level": "urgent", "source": "rule", "evidence": "x"}]
        ),
        lambda: value.update(
            red_flags=[{"id": "RF_X", "level": "low", "source": "rule", "evidence": "x"}]
        ),
        lambda: value["routing"].update(department_code="lowercase"),
        lambda: value["routing"].update(confidence=-0.1),
        lambda: value["routing"].update(reason_en="z" * 501),
        lambda: value.update(
            field_meta={"hpi/severity": {"source": "doctor", "confidence": 1, "confirmed": True}}
        ),
        lambda: value.update(
            field_meta={"/bad~2pointer": {"source": "doctor", "confidence": 1, "confirmed": True}}
        ),
        lambda: value.update(disclaimer="d" * 501),
    ]
    invalid_cases[variant]()
    with pytest.raises(ValueError):
        CaseSheet.model_validate(value)


def test_case_sheet_schema_is_json_serializable() -> None:
    json.dumps(CaseSheet.model_json_schema())


@pytest.mark.asyncio
async def test_idempotency_replays_and_rejects_changed_body() -> None:
    from app.core.idempotency import IdempotencyStore

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        async def eval(self, script: str, key_count: int, key: str, *args: object) -> int:
            assert key_count == 1
            import json

            stored = json.loads(self.values[key])
            if "ARGV[2]" in script:
                if stored.get("owner") != args[0]:
                    return 0
                self.values[key] = str(args[1])
                return 1
            if stored.get("owner") == args[0]:
                self.values.pop(key, None)
            return 1

    class TestRequest:
        headers = {"Idempotency-Key": "synthetic-key-1"}
        method = "POST"
        url = SimpleNamespace(path="/admin/departments")

    redis = FakeRedis()
    store = IdempotencyStore(redis)
    operation_count = 0

    async def operation() -> dict[str, str]:
        nonlocal operation_count
        operation_count += 1
        return {"id": "synthetic-record"}

    args = {
        "hospital_id": uuid4(),
        "actor_id": uuid4(),
        "body": {"code": "GENMED", "department_id": uuid4()},
    }
    first = await store.execute(TestRequest(), operation=operation, **args)  # type: ignore[arg-type]
    second = await store.execute(TestRequest(), operation=operation, **args)  # type: ignore[arg-type]
    assert first == second == {"id": "synthetic-record"}
    assert operation_count == 1
    with pytest.raises(ApiProblemError) as error:
        await store.execute(
            TestRequest(),
            operation=operation,
            **{**args, "body": {"code": "ORTHO", "department_id": uuid4()}},
        )  # type: ignore[arg-type]
    assert error.value.status == 422
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"

    TestRequest.headers = {}
    with pytest.raises(ApiProblemError) as missing_key:
        await store.execute(TestRequest(), operation=operation, **args)  # type: ignore[arg-type]
    assert missing_key.value.status == 400

    TestRequest.headers = {"Idempotency-Key": "synthetic-failing-operation"}

    async def failing_operation() -> dict[str, str]:
        raise RuntimeError("synthetic operation failure")

    with pytest.raises(RuntimeError, match="synthetic operation failure"):
        await store.execute(TestRequest(), operation=failing_operation, **args)  # type: ignore[arg-type]
    assert len(redis.values) == 1


@pytest.mark.asyncio
async def test_idempotency_does_not_replay_one_time_response_fields() -> None:
    from app.core.idempotency import IdempotencyStore

    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.values.get(key)

        async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
            del ex
            if nx and key in self.values:
                return False
            self.values[key] = value
            return True

        async def eval(self, script: str, key_count: int, key: str, *args: object) -> int:
            assert key_count == 1
            stored = json.loads(self.values[key])
            if "ARGV[2]" in script:
                if stored.get("owner") != args[0]:
                    return 0
                self.values[key] = str(args[1])
                return 1
            return 0

    class TestRequest:
        headers = {"Idempotency-Key": "synthetic-one-time-kiosk"}
        method = "POST"
        url = SimpleNamespace(path="/admin/kiosks")

    redis = FakeRedis()
    store = IdempotencyStore(redis)
    operation_count = 0

    async def operation() -> dict[str, str]:
        nonlocal operation_count
        operation_count += 1
        return {"id": "synthetic-kiosk", "activation_code": "one-time-secret"}

    args = {
        "hospital_id": uuid4(),
        "actor_id": uuid4(),
        "body": {"label": "Synthetic kiosk"},
        "operation": operation,
        "response_fields_once": frozenset({"activation_code"}),
    }
    first = await store.execute(TestRequest(), **args)  # type: ignore[arg-type]
    replay = await store.execute(TestRequest(), **args)  # type: ignore[arg-type]

    assert first["activation_code"] == "one-time-secret"
    assert replay == {"id": "synthetic-kiosk"}
    assert operation_count == 1


@pytest.mark.asyncio
async def test_auth_dependency_helpers_and_invalid_bearer() -> None:
    from types import SimpleNamespace

    from starlette.requests import Request

    from app.core.auth import current_principal, require_kiosk, require_patient, require_role
    from app.core.security import issue_access_token

    settings = Settings()
    hospital_id = uuid4()
    token = issue_access_token(
        subject=uuid4(), role="kiosk", hospital_id=hospital_id, settings=settings
    )

    def make_request(value: str) -> Request:
        app = SimpleNamespace(state=SimpleNamespace(settings=settings))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/private",
            "headers": [(b"authorization", value.encode())],
            "app": app,
            "state": {},
        }
        return Request(scope)

    principal = current_principal(make_request(f"Bearer {token}"))
    assert principal["role"] == "kiosk"
    with pytest.raises(ApiProblemError) as invalid:
        current_principal(make_request("Bearer invalid-token"))
    assert invalid.value.status == 401
    assert callable(require_kiosk())
    assert callable(require_patient())
    assert current_principal(make_request(f"Bearer {token}")) == principal
    assert await require_role("kiosk")(make_request(f"Bearer {token}")) == principal
    with pytest.raises(ApiProblemError) as forbidden:
        await require_role("admin")(make_request(f"Bearer {token}"))
    assert forbidden.value.status == 403
    with pytest.raises(ValueError, match="at least one role"):
        require_role()
