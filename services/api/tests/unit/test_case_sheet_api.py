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
async def test_case_sheet_pdf_is_department_scoped_and_audited(monkeypatch) -> None:
    hospital_id, actor_id, department_id, visit_id = (uuid4() for _ in range(4))
    repository = FakeCaseSheetRepository(version=6)
    audited: list[dict] = []
    rendered: list[dict] = []
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())
    monkeypatch.setattr(case_sheets, "CaseSheetRepository", lambda *_args: repository)

    async def record_audit(_connection, _request, **values) -> None:
        audited.append(values)

    class Result:
        def mappings(self):
            return self

        def first(self):
            return {"token_label": "GM-042", "urgency_reasons": []}

    class PdfConnection(FakeConnection):
        async def execute(self, *_args, **_kwargs):
            return Result()

    def render_pdf(case_sheet, *, queue_number, attention_reasons):
        rendered.append(
            {
                "case_sheet": case_sheet,
                "queue_number": queue_number,
                "attention_reasons": attention_reasons,
            }
        )
        return b"%PDF-synthetic"

    monkeypatch.setattr(case_sheets, "audit", record_audit)
    monkeypatch.setattr(case_sheets, "render_case_sheet_pdf", render_pdf)
    request = make_request(PdfConnection(department_id, department_id))
    response = await case_sheets.export_case_sheet_pdf(
        visit_id,
        request,
        {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
    )

    assert response.body == b"%PDF-synthetic"
    assert response.media_type == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert rendered[0]["queue_number"] == "GM-042"
    assert audited[0]["action"] == "case_sheet.pdf_exported"
    assert audited[0]["meta"] == {"version": 6, "red_flag_count": 0}


def test_case_sheet_pdf_contains_english_and_devanagari_text() -> None:
    import shutil
    import subprocess
    from io import BytesIO

    from pypdf import PdfReader

    from app.pdf import render_case_sheet_pdf

    sheet = {
        "language": "hi",
        "chief_complaint": {
            "text_original": "सिर में दर्द",
            "text_en": "Headache",
        },
        "hpi": {"duration": {"value": 3, "unit": "day"}},
        "past_history": {"conditions": ["Synthetic history"]},
        "medications": [],
        "allergies": [],
        "red_flags": [],
        "routing": {"department_code": "GENMED"},
    }
    try:
        pdf = render_case_sheet_pdf(
                sheet,
                queue_number="GM-042",
            attention_reasons=["Synthetic attention reason"],
        )
    except OSError as error:
        if "libgobject-2.0-0" in str(error):
            pytest.skip("WeasyPrint's native Pango library is unavailable on this host")
        raise
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)

    pdftotext = shutil.which("pdftotext")
    if pdftotext is None:
        pytest.skip("pdftotext is unavailable on this host")
    extracted = subprocess.run(
        [pdftotext, "-", "-"], input=pdf, capture_output=True, check=True
    ).stdout.decode("utf-8")

    assert "सिर में द" in extracted
    assert "मुख्य शिकायत" in text
    assert len(PdfReader(BytesIO(pdf)).pages) == 1
    assert "GM-042" in text
    assert "Synthetic attention reason" in text
    assert "Not a diagnosis" in text


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


@pytest.mark.asyncio
async def test_transcript_is_doctor_scoped_and_audited_without_text_in_audit(monkeypatch) -> None:
    hospital_id, actor_id, department_id, visit_id = (uuid4() for _ in range(4))
    monkeypatch.setattr(case_sheets, "set_hospital_scope", lambda *_args, **_kwargs: _noop())

    class FakeTranscriptRepository:
        def __init__(self, *_args) -> None:
            pass

        async def list_for_visit(self, **_kwargs):
            return [
                {
                    "turn_index": 1,
                    "question_id": "chief_complaint",
                    "input_mode": "text",
                    "text_original": "Synthetic complaint",
                    "text_english": "Synthetic complaint",
                    "stt_confidence": None,
                }
            ]

    audited: list[dict] = []

    async def record_audit(_connection, _request, **values) -> None:
        audited.append(values)

    monkeypatch.setattr(case_sheets, "InterviewTurnRepository", FakeTranscriptRepository)
    monkeypatch.setattr(case_sheets, "audit", record_audit)
    request = make_request(FakeConnection(department_id, department_id))
    response = await case_sheets.get_transcript(
        visit_id,
        request,
        {"hospital_id": str(hospital_id), "sub": str(actor_id), "role": "doctor"},
    )

    assert response.turns[0].text_original == "Synthetic complaint"
    assert audited[0]["action"] == "transcript.read"
    assert audited[0]["meta"] == {"turn_count": 1}
    assert "Synthetic complaint" not in str(audited[0])


async def _noop() -> None:
    return None
