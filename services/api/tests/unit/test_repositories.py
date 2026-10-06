from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config import Settings
from app.core.crypto import encrypt_field
from app.db.repositories import CaseSheetRepository, PatientRepository, VisitRepository


def make_test_settings() -> Settings:
    return Settings(
        app_env="test",
        app_data_key=base64.b64encode(b"s" * 32).decode("ascii"),
        app_hmac_key=base64.b64encode(b"h" * 32).decode("ascii"),
    )


class Result:
    def __init__(
        self, *, rows: list[dict[str, object]] | None = None, scalar=None, rowcount: int = 0
    ):
        self.rows = rows or []
        self.value = scalar
        self.rowcount = rowcount

    def mappings(self) -> Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.value


class Connection:
    def __init__(
        self,
        *,
        rows: list[dict[str, object]] | None = None,
        scalar=None,
        rowcount: int = 0,
    ):
        self.rows = rows or []
        self.value = scalar
        self.rowcount = rowcount
        self.calls: list[tuple[object, object]] = []

    async def execute(self, query: object, params: object = None) -> Result:
        self.calls.append((query, params))
        return Result(rows=self.rows, scalar=self.value, rowcount=self.rowcount)

    async def scalar(self, *_args: object, **_kwargs: object):
        return self.value


@pytest.mark.asyncio
async def test_patient_repository_encrypts_writes_and_decrypts_tenant_scoped_reads() -> None:
    hospital_id, patient_id = uuid4(), uuid4()
    settings = make_test_settings()
    connection = Connection()
    repo = PatientRepository(connection, settings)
    created_id = await repo.create(
        hospital_id=hospital_id,
        name="Synthetic Patient",
        phone=" 555 0100 ",
        abha="synthetic-abha",
        year_of_birth=1980,
        sex="unknown",
        preferred_language="en",
    )
    params = connection.calls[0][1]
    assert created_id == params["id"]
    assert params["phone_hmac"]
    assert params["name_enc"] != "Synthetic Patient"

    encrypted = {
        "id": patient_id,
        "name_enc": encrypt_field(
            "Synthetic Patient",
            key=settings.app_data_key_bytes,
            associated_data=f"patients:name_enc:{patient_id}",
        ),
        "phone_enc": None,
        "abha_enc": None,
        "year_of_birth": 1980,
        "sex": "unknown",
        "preferred_language": "en",
        "created_at": datetime.now(UTC),
    }
    connection.rows = [encrypted]
    patient = await repo.get(hospital_id=hospital_id, patient_id=patient_id)
    assert patient and patient["name"] == "Synthetic Patient"
    assert patient["phone"] is None and patient["abha"] is None


@pytest.mark.asyncio
async def test_patient_and_visit_repositories_paginate_and_validate_limits() -> None:
    hospital_id, patient_id, department_id = uuid4(), uuid4(), uuid4()
    first_id, second_id = uuid4(), uuid4()
    created = datetime.now(UTC)
    settings = make_test_settings()
    connection = Connection(
        rows=[
            {
                "id": first_id,
                "name_enc": encrypt_field(
                    "first",
                    key=settings.app_data_key_bytes,
                    associated_data=f"patients:name_enc:{first_id}",
                ),
                "phone_enc": None,
                "created_at": created,
            },
            {
                "id": second_id,
                "name_enc": encrypt_field(
                    "second",
                    key=settings.app_data_key_bytes,
                    associated_data=f"patients:name_enc:{second_id}",
                ),
                "phone_enc": None,
                "created_at": created,
            },
        ]
    )
    patients = PatientRepository(connection, settings)
    with pytest.raises(ValueError):
        await patients.list(hospital_id=hospital_id, limit=0, cursor=None)
    page = await patients.list(hospital_id=hospital_id, limit=1, cursor=None)
    assert len(page.items) == 1 and page.next_cursor
    assert page.items[0]["name"] == "first"

    connection.rows = [
        {
            "id": first_id,
            "patient_id": patient_id,
            "department_id": department_id,
            "status": "queued",
            "urgency": "routine",
            "service_date": created.date(),
            "created_at": created,
        }
    ]
    visits = VisitRepository(connection, settings)
    page = await visits.list(hospital_id=hospital_id, limit=1, cursor=None)
    assert page.items[0]["id"] == first_id


@pytest.mark.asyncio
async def test_case_sheet_repository_round_trips_encrypted_data_and_checks_versions() -> None:
    hospital_id, visit_id, sheet_id, editor_id = (uuid4() for _ in range(4))
    settings = make_test_settings()
    connection = Connection(scalar=sheet_id)
    repo = CaseSheetRepository(connection, settings)
    data = {"schema_version": "1.0", "language": "en", "chief_complaint": {"text": "test"}}
    assert (
        await repo.save(hospital_id=hospital_id, visit_id=visit_id, data=data, editor_id=editor_id)
        == sheet_id
    )
    stored_params = connection.calls[0][1]
    assert json.loads(stored_params["data"])["ciphertext"]

    ciphertext = encrypt_field(
        json.dumps(data, separators=(",", ":")),
        key=settings.app_data_key_bytes,
        associated_data=f"case_sheets:data:{visit_id}",
    )
    connection.rows = [{"id": sheet_id, "data": {"ciphertext": ciphertext}, "version": 2}]
    loaded = await repo.get(hospital_id=hospital_id, visit_id=visit_id, for_update=True)
    assert loaded == {
        "id": sheet_id,
        "data": data,
        "version": 2,
        "confirmed_by_patient": False,
        "confirmed_at": None,
    }

    connection.value = 3
    assert (
        await repo.replace_if_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=2,
            data=data,
            editor_id=editor_id,
        )
        == 3
    )
    connection.value = None
    assert (
        await repo.replace_if_version(
            hospital_id=hospital_id,
            visit_id=visit_id,
            expected_version=2,
            data=data,
            editor_id=editor_id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_case_sheet_confirmation_is_timestamped_and_idempotently_reported() -> None:
    hospital_id, visit_id = uuid4(), uuid4()
    connection = Connection(rowcount=1)
    repository = CaseSheetRepository(connection, make_test_settings())
    assert await repository.confirm(hospital_id=hospital_id, visit_id=visit_id) is True
    sql = str(connection.calls[0][0])
    assert "confirmed_by_patient = true" in sql
    assert "confirmed_at = now()" in sql
    assert connection.calls[0][1] == {"hospital_id": hospital_id, "visit_id": visit_id}

    connection.rowcount = 0
    assert await repository.confirm(hospital_id=hospital_id, visit_id=visit_id) is False
