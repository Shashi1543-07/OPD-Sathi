"""Load every clinical content file and report its review status (Phase 7, task 7.6.1)."""

from typing import get_args

from app.domain.interview.bank import Language
from app.domain.interview.consent import ConsentPurpose, get_consent_copy, load_danger_tiles
from app.domain.privacy import get_privacy_notice
from app.domain.redflags import engine as _redflags  # noqa: F401 - loads the rules
from app.domain.review import CONTENT_STATUS, draft_content
from app.domain.triage import score as _triage  # noqa: F401 - loads the bank and weights


def content_status() -> dict[str, str]:
    """Each content source and its status; files read on demand are read here first."""
    for language in get_args(Language):
        for purpose in get_args(ConsentPurpose):
            get_consent_copy(language, purpose)
        get_privacy_notice(language)
    load_danger_tiles()
    return dict(sorted(CONTENT_STATUS.items()))


def require_approved_content() -> None:
    """Refuse to run while any clinical content still awaits sign-off."""
    content_status()
    drafts = draft_content()
    if drafts:
        raise RuntimeError(
            "CLINICAL_CONTENT_APPROVAL_REQUIRED is set but this content is still a draft "
            f"awaiting clinical sign-off (docs/CLINICIAN_REVIEW.md): {', '.join(drafts)}"
        )
