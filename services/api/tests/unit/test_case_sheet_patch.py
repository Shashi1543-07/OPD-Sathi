import copy

import pytest

from app.domain.case_sheet_patch import CaseSheetPatchError, apply_case_sheet_patch


def valid_sheet() -> dict:
    return {
        "schema_version": "1.0",
        "language": "en",
        "chief_complaint": {
            "text_original": "Headache",
            "text_en": "Headache",
            "confidence": 0.8,
        },
        "hpi": {"associated_symptoms": []},
        "routing": {
            "department_code": "GENMED",
            "confidence": 0.9,
            "reason_en": "General medicine review",
            "needs_nurse_confirmation": False,
        },
    }


def test_patch_updates_validated_fields_and_doctor_provenance() -> None:
    current = valid_sheet()
    updated, paths = apply_case_sheet_patch(
        current,
        [
            {
                "op": "replace",
                "path": "/chief_complaint/text_original",
                "value": "Severe headache",
            },
            {
                "op": "add",
                "path": "/hpi/associated_symptoms/-",
                "value": {
                    "text_original": "Nausea",
                    "text_en": "Nausea",
                    "confidence": 1.0,
                },
            },
        ],
    )

    assert updated["chief_complaint"]["text_original"] == "Severe headache"
    assert updated["hpi"]["associated_symptoms"][0]["text_en"] == "Nausea"
    assert updated["field_meta"]["/chief_complaint/text_original"] == {
        "source": "doctor",
        "confidence": 1.0,
        "confirmed": True,
    }
    assert paths == ["/chief_complaint/text_original", "/hpi/associated_symptoms/-"]
    assert current == valid_sheet()


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "/routing/department_code", "value": "PED"},
        {"op": "replace", "path": "/field_meta/x", "value": {}},
        {"op": "replace", "path": "/chief_complaint/text~2original", "value": "x"},
        {"op": "replace", "path": "/chief_complaint/missing", "value": "x"},
        {"op": "copy", "path": "/chief_complaint/text_en", "from": "/hpi/onset"},
    ],
)
def test_patch_rejects_read_only_invalid_and_unsupported_paths(operation: dict) -> None:
    with pytest.raises(CaseSheetPatchError):
        apply_case_sheet_patch(valid_sheet(), [operation])


def test_patch_revalidates_the_complete_case_sheet_schema() -> None:
    with pytest.raises(CaseSheetPatchError, match="schema"):
        apply_case_sheet_patch(
            valid_sheet(),
            [{"op": "replace", "path": "/chief_complaint/confidence", "value": 2}],
        )


def test_patch_does_not_mutate_current_data_when_validation_fails() -> None:
    current = valid_sheet()
    before = copy.deepcopy(current)
    with pytest.raises(CaseSheetPatchError):
        apply_case_sheet_patch(
            current,
            [{"op": "replace", "path": "/chief_complaint/confidence", "value": 2}],
        )
    assert current == before
