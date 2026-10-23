"""Clinical sign-off of content files (Phase 7, task 7.6.1; CONTRIBUTING.md, "Clinical changes").

Every file of patient-facing or clinical content (question bank, triggers, danger tiles, consent
copy, red-flag rules, triage weights) carries a review status. It is a draft until clinicians
sign it off; ``approved`` is accepted only with the record of that sign-off: who reviewed it,
when, and which version of the content they saw. The same record goes in
docs/CLINICIAN_REVIEW.md. Loaders fail closed on anything else.

Each loader reports what it read here, so the application can refuse to start with draft
content where the deployment requires approved content
(CLINICAL_CONTENT_APPROVAL_REQUIRED; docs/GO_LIVE_CHECKLIST.md).
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DRAFT = "draft_needs_clinician_review"
APPROVED = "approved"

# Source name (for example "bank/core.json") -> the review status it was loaded with.
CONTENT_STATUS: dict[str, str] = {}


class ReviewRecord(BaseModel):
    """Who approved the content, when, and which version of it they reviewed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Name and qualification of each reviewer, e.g. "Dr A. Kulkarni, MD (General Medicine)".
    reviewed_by: list[str] = Field(min_length=1)
    reviewed_on: date
    content_version: str = Field(min_length=1, max_length=40)

    @field_validator("reviewed_by")
    @classmethod
    def _named(cls, reviewers: list[str]) -> list[str]:
        if any(len(name.strip()) < 3 for name in reviewers):
            raise ValueError("every reviewer needs a name")
        return reviewers

    @field_validator("reviewed_on")
    @classmethod
    def _not_in_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("the review date is in the future")
        return value


def check_review(source: str, status: object, record: Any = None) -> str:
    """Validate one file's (or rule's) review status and record it; return the status."""
    if status == DRAFT:
        CONTENT_STATUS[source] = DRAFT
        return DRAFT
    if status != APPROVED:
        raise ValueError(f"{source}: review_status must be {DRAFT} or {APPROVED}")
    try:
        ReviewRecord.model_validate(record)
    except ValidationError as exc:
        raise ValueError(
            f"{source} is marked {APPROVED} without a complete review record "
            "(reviewed_by, reviewed_on, content_version)"
        ) from exc
    CONTENT_STATUS[source] = APPROVED
    return APPROVED


def draft_content() -> list[str]:
    """Content loaded so far that still awaits clinical sign-off."""
    return sorted(source for source, status in CONTENT_STATUS.items() if status == DRAFT)
