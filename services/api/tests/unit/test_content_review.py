"""Clinical sign-off of content (task 7.6.1): approved only with a complete review record."""

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.domain.interview import consent
from app.domain.interview.bank import BANK_DIRECTORY, load_question_bank
from app.domain.redflags.engine import RULE_PATH, load_rules
from app.domain.review import APPROVED, CONTENT_STATUS, DRAFT, check_review
from app.main import create_app

RECORD = {
    "reviewed_by": ["Dr A. Kulkarni, MD (General Medicine)"],
    "reviewed_on": "2026-10-01",
    "content_version": "core-1.1",
}


def test_draft_and_complete_approval_are_accepted() -> None:
    assert check_review("test/draft", DRAFT) == DRAFT
    assert check_review("test/approved", APPROVED, RECORD) == APPROVED
    assert CONTENT_STATUS["test/approved"] == APPROVED


@pytest.mark.parametrize(
    "record",
    [
        None,
        {**RECORD, "reviewed_by": []},
        {**RECORD, "reviewed_by": [" "]},
        {**RECORD, "reviewed_on": (date.today() + timedelta(days=1)).isoformat()},
        {**RECORD, "content_version": ""},
        {key: value for key, value in RECORD.items() if key != "reviewed_on"},
        {**RECORD, "approved_by_ai": True},
    ],
)
def test_approval_without_a_complete_record_is_refused(record: object) -> None:
    with pytest.raises(ValueError, match="without a complete review record"):
        check_review("test/incomplete", APPROVED, record)


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(ValueError, match="review_status must be"):
        check_review("test/unknown", "reviewed")


def test_an_approved_bank_file_loads_with_its_record(tmp_path: Path) -> None:
    bank = tmp_path / "bank"
    shutil.copytree(BANK_DIRECTORY, bank)
    core = bank / "core.json"
    raw = json.loads(core.read_text(encoding="utf-8"))
    raw.update(review_status=APPROVED, review=RECORD)
    core.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    questions, _ = load_question_bank(bank)
    assert "chief_concern" in questions

    del raw["review"]
    core.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="core.json is marked approved"):
        load_question_bank(bank)


def test_each_red_flag_rule_is_approved_on_its_own(tmp_path: Path) -> None:
    rules = yaml.safe_load(RULE_PATH.read_text(encoding="utf-8"))
    rules[0].update(status=APPROVED, review={**RECORD, "content_version": "rf-1.0"})
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
    assert load_rules(path)[0].status == APPROVED

    rules[1]["status"] = APPROVED
    path.write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match=f"redflags/{rules[1]['id']} is marked approved"):
        load_rules(path)


def test_approved_consent_reads_reviewers_from_front_matter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "We collect your answers for the doctor."
    (tmp_path / "en.md").write_text(
        "---\nreview_status: approved\nversion: consent-1.1\n"
        "reviewed_by: Dr A. Kulkarni, MD; Adv. R. Shah (counsel)\nreviewed_on: 2026-10-01\n"
        f"---\n{text}\n",
        encoding="utf-8",
    )
    (tmp_path / "hi.md").write_text(
        f"---\nreview_status: approved\nversion: consent-1.1\n---\n{text}\n", encoding="utf-8"
    )
    monkeypatch.setattr(consent, "CONSENT_DIRECTORY", tmp_path)
    assert consent.get_consent_copy("en")["version"] == "consent-1.1"
    with pytest.raises(ValueError, match="consent/hi.md is marked approved"):
        consent.get_consent_copy("hi")


def test_the_api_refuses_to_start_with_draft_content_when_approval_is_required() -> None:
    settings = Settings(clinical_content_approval_required=True)
    with pytest.raises(RuntimeError, match="still a draft") as refused:
        create_app(settings)
    assert "triage/weights.yaml" in str(refused.value)
    assert "consent/en.md" in str(refused.value)
