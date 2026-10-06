from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import Request, Response

from app.api.v1 import case_sheets
from app.api.v1.case_sheets import AddOrReplace
from app.core.errors import ApiProblemError


class FakeConnection:
    def __init__(self, visit_department, staff_department) -> None:
        self.values = iter((visit_department, staff_department))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def begin(self):
        return self

    async def scalar(self, *_args, **_kwargs):
        return next(self.values)


class FakeCaseSheetRepository:
    def __init__(self, *, version: int = 1) -> None:
        self.version = version
        self.data = {
            "schema_version": "1.0",
            "language": "en",
            "chief_complaint": {
                "text_original": "Headache",
                "text_en": "Headache",
                "confidence": 0.8,
            },
            "hpi": {},
            "routing": {
                "department_code": "GENMED",
                "confidence": 0.9,
                "reason_en": "General medicine review",
                "needs_nurse_confirmation": False,
            },
        }
        self.saved_id = uuid4()
        self.requested_for_update = False

    async def get(self, **kwargs):
        self.requested_for_update = kwargs.get("for_update", False)
        return {"id": self.saved_id, "version": self.version, "data": self.data.copy()}

    async def replace_if_version(self, **kwargs):
        if kwargs["expected_version"] != self.version:
            return None
        self.data = kwargs["data"]
        self.version += 1
        return self.version


def make_request(connection: FakeConnection) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            engine=SimpleNamespace(begin=lambda: connection),
            settings=object(),
        )
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/visits/test/case-sheet",
        "headers": [],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "app": app,
        "state": {},
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_case_sheet_read_is_department_scoped_and_audited(monkeypatch) -> None:
    hospital_id, actor_id, department_id, visit_id = (uuid4() for _ in range(4))
    repository = FakeCaseSheetRepository(version=4)
    audited: list[dict] = []
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())
    monkeypatch.setattr(case_sheets, "CaseSheetRepository", lambda *_args: repository)

    async def record_audit(_connection, _request, **values) -> None:
        audited.append(values)

    monkeypatch.setattr(case_sheets, "audit", record_audit)
    request = make_request(FakeConnection(department_id, department_id))
    response = Response()
    result = await case_sheets.get_case_sheet(
        visit_id,
        request,
        response,
        {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
    )

    assert result.version == 4
    assert result.case_sheet.chief_complaint.text_original == "Headache"
    assert response.headers["etag"] == '"4"'
    assert audited[0]["action"] == "case_sheet.read"
    assert audited[0]["meta"] == {"version": 4}


@pytest.mark.asyncio
async def test_case_sheet_patch_versions_update_metadata_and_audit_paths(monkeypatch) -> None:
    hospital_id, actor_id, department_id, visit_id = (uuid4() for _ in range(4))
    repository = FakeCaseSheetRepository(version=3)
    audited: list[dict] = []
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())
    monkeypatch.setattr(case_sheets, "CaseSheetRepository", lambda *_args: repository)

    async def record_audit(_connection, _request, **values) -> None:
        audited.append(values)

    monkeypatch.setattr(case_sheets, "audit", record_audit)
    request = make_request(FakeConnection(department_id, department_id))
    response = Response()
    result = await case_sheets.patch_case_sheet(
        visit_id,
        request,
        response,
        {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
        [
            AddOrReplace(
                op="replace",
                path="/chief_complaint/text_original",
                value="Severe headache",
            )
        ],
        '"3"',
    )

    assert result.version == 4
    assert response.headers["etag"] == '"4"'
    assert result.case_sheet.field_meta["/chief_complaint/text_original"].source == "doctor"
    assert repository.requested_for_update is True
    assert audited[0]["action"] == "case_sheet.updated"
    assert audited[0]["meta"]["changes"] == ["replace:/chief_complaint/text_original"]


@pytest.mark.asyncio
async def test_case_sheet_patch_returns_conflict_for_a_stale_version(monkeypatch) -> None:
    hospital_id, actor_id, department_id, visit_id = (uuid4() for _ in range(4))
    repository = FakeCaseSheetRepository(version=5)
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())
    monkeypatch.setattr(case_sheets, "CaseSheetRepository", lambda *_args: repository)
    request = make_request(FakeConnection(department_id, department_id))

    with pytest.raises(ApiProblemError) as conflict:
        await case_sheets.patch_case_sheet(
            visit_id,
            request,
            Response(),
            {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
            [AddOrReplace(op="replace", path="/chief_complaint/text_en", value="Pain")],
            '"4"',
        )
    assert conflict.value.status == 409


@pytest.mark.asyncio
async def test_case_sheet_reads_hide_other_departments(monkeypatch) -> None:
    hospital_id, actor_id, assigned_department, other_department, visit_id = (
        uuid4() for _ in range(5)
    )
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())
    request = make_request(FakeConnection(other_department, assigned_department))
    with pytest.raises(ApiProblemError) as not_found:
        await case_sheets.get_case_sheet(
            visit_id,
            request,
            Response(),
            {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
        )
    assert not_found.value.status == 404


async def _noop() -> None:
    return None
