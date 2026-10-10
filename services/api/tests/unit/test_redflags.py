"""Focused coverage for Unicode normalization and draft red-flag rules."""

import pytest
import yaml

from app.api.v1.intake import _patient_case_sheet
from app.domain.redflags.engine import (
    RULE_PATH,
    PatientFacts,
    RedFlag,
    RuleModel,
    evaluate,
    load_rules,
    maximum_urgency,
)
from app.domain.redflags.normalize import normalize_text
from app.domain.routing.engine import route


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ＣＨＥＳＴ　ＰＡＩＮ", "chest pain"),
        ("Chest—pain!", "chest pain"),
        ("  chest   pain  ", "chest pain"),
        ("क़लम", "कलम"),
        ("क़लम", "कलम"),
        ("चाँद", "चांद"),
        ("चांद", "चांद"),
        ("नहीं", "नहीं"),
        ("NAHIN", "nahin"),
        ("Nahi", "nahi"),
    ]
    + [(f"Ｆｅｖｅｒ {index}!", f"fever {index}") for index in range(30)],
)
def test_normalize_unicode_and_spacing(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("chest pain and sweating", {"RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"}),
        ("chest pain but no sweating", {"RF_CHEST_PAIN"}),
        ("no chest pain and sweating", set()),
        ("seene mein dard aur pasina", {"RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"}),
        ("छातीत दुखणे आणि घाम", {"RF_CHEST_PAIN_ASSOC", "RF_CHEST_PAIN"}),
        ("no seizure", set()),
        ("सांस नहीं ले पा रहा", {"RF_BREATH_SEVERE"}),
        ("I do not want to die", set()),
    ],
)
def test_evaluate_multilingual_negation(text: str, expected: set[str]) -> None:
    assert {flag.id for flag in evaluate(text)} == expected


def test_tile_and_structured_conditions_are_conservative() -> None:
    flags = evaluate("", facts=PatientFacts(tile_ids=("DS_CHEST",), severity=10, age=0))
    # A self-rated 10/10 is not a red flag by itself (it only adds a few triage points).
    assert {flag.id for flag in flags} == {"RF_DS_TILE"}
    assert maximum_urgency(flags) == "urgent"
    assert {flag.id for flag in evaluate("fever", facts=PatientFacts(age=0))} == {"RF_CHILD_FEVER"}
    assert not evaluate("fever", facts=PatientFacts(age=6))


def test_negation_does_not_cross_conjunction_to_another_symptom() -> None:
    flags = evaluate("fever and not feeding", facts=PatientFacts(age=3))
    assert {flag.id for flag in flags} == {"RF_CHILD_FEVER_ASSOC"}
    flags = evaluate("fever and no seizure", facts=PatientFacts(age=0))
    assert {flag.id for flag in flags} == {"RF_CHILD_FEVER"}


def test_self_harm_is_staff_only() -> None:
    flags = evaluate("I have thoughts of suicide")
    assert [(flag.id, flag.visible_to_patient) for flag in flags] == [("RF_SELF_HARM", False)]


def test_patient_case_sheet_contains_no_self_harm_flag_or_reason() -> None:
    flags = evaluate("I have thoughts of suicide")
    data = _patient_case_sheet({"red_flags": [], "routing": {}}, flags, route("unclear concern"))
    assert data["red_flags"] == []
    assert "RF_SELF_HARM" not in repr(data)


def test_red_flag_serialization_and_urgency_order() -> None:
    flag = RedFlag("RF_TEST", "priority", "rule", "evidence", "reason")
    assert flag.as_dict() == {
        "id": "RF_TEST",
        "level": "priority",
        "source": "rule",
        "evidence": "evidence",
        "reason_en": "reason",
        "visible_to_patient": True,
    }
    assert maximum_urgency([]) == "routine"
    assert maximum_urgency([flag]) == "priority"
    assert maximum_urgency([flag, RedFlag("RF_U", "urgent", "tile", "", "")]) == "urgent"


def test_structured_conditions_require_each_supplied_constraint() -> None:
    phrases = {language: ["symptom"] for language in ("en", "hi", "mr", "hinglish")}
    rule = RuleModel(
        id="RF_TEST_CONDITIONS",
        level="priority",
        status="draft_needs_clinician_review",
        reviewed_by=None,
        reason_en="Test structured conditions.",
        all_groups=[{"any": phrases}],
        conditions={
            "min_age": 10,
            "max_age": 20,
            "sex_any": ["female"],
            "pregnant": True,
            "severity_min": 7,
        },
    )
    assert evaluate(
        "symptom",
        facts=PatientFacts(age=15, sex="female", pregnant=True, severity=8),
        rules=[rule],
    )
    for facts in (
        PatientFacts(age=None, sex="female", pregnant=True, severity=8),
        PatientFacts(age=5, sex="female", pregnant=True, severity=8),
        PatientFacts(age=15, sex="male", pregnant=True, severity=8),
        PatientFacts(age=15, sex="female", pregnant=False, severity=8),
        PatientFacts(age=15, sex="female", pregnant=True, severity=6),
    ):
        assert not evaluate("symptom", facts=facts, rules=[rule])


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (None, "non-empty YAML list"),
        ([], "non-empty YAML list"),
    ],
)
def test_rule_loader_rejects_empty_or_non_list_documents(
    tmp_path, raw: object, message: str
) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_rules(path)


def test_rule_loader_rejects_duplicate_ids_and_incomplete_languages(tmp_path) -> None:
    rules = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump([rules[0], rules[0]]), encoding="utf-8")
    with pytest.raises(ValueError, match="ids must be unique"):
        load_rules(path)

    broken = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    broken[0]["all_groups"] = [{"any": {"en": ["example"]}}]
    path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="need en, hi, mr, and hinglish"):
        load_rules(path)

    broken[0]["all_groups"] = [
        {"any": {"en": [], "hi": ["x"], "mr": ["x"], "hinglish": ["x"]}}
    ]
    path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="phrase groups cannot be empty"):
        load_rules(path)
