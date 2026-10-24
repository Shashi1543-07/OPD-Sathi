"""The full privacy notice for patients (Phase 7, task 7.5.2), versioned like the consent copy.

One notice per language: what is collected, why, who sees it, how long it is kept, and the
patient's rights and where to complain. The short consent notices at the kiosk stay; this is
the notice they, the token page and the waiting-area poster point to. It is a draft until
counsel approves it, recorded like clinical content (reviewed_by, reviewed_on,
content_version), so CLINICAL_CONTENT_APPROVAL_REQUIRED also refuses a draft notice.
"""

import hashlib
from pathlib import Path
from typing import TypedDict

from app.domain.interview.bank import Language
from app.domain.review import check_review

NOTICE_DIRECTORY = Path(__file__).parent


class PrivacyNotice(TypedDict):
    language: Language
    version: str
    sha256: str
    review_status: str
    text: str


def get_privacy_notice(language: Language) -> PrivacyNotice:
    name = f"notice-{language}.md"
    raw = (NOTICE_DIRECTORY / name).read_text(encoding="utf-8")
    parts = raw.split("---", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("the privacy notice must contain front matter")
    metadata = {
        key.strip(): value.strip()
        for key, value in (
            line.split(":", maxsplit=1) for line in parts[1].splitlines() if ":" in line
        )
    }
    version = metadata.get("version", "")
    # Reviewers are separated by ";" (their names carry qualifications with commas).
    record = {
        "reviewed_by": [
            name for name in metadata.get("reviewed_by", "").split(";") if name.strip()
        ],
        "reviewed_on": metadata.get("reviewed_on"),
        "content_version": version,
    }
    status = check_review(f"privacy/{name}", metadata.get("review_status"), record)
    text = parts[2].strip()
    if not version or not text:
        raise ValueError("the privacy notice must have a version and text")
    return {
        "language": language,
        "version": version,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "review_status": status,
        "text": text,
    }
