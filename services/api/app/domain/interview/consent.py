"""Versioned multilingual consent copy, deliberately awaiting clinician review."""

import hashlib
from pathlib import Path
from typing import Literal, TypedDict

from app.domain.interview.bank import Language
from app.domain.review import check_review

CONSENT_DIRECTORY = Path(__file__).with_name("consent")
# "care": the history is collected for the clinic team (required to continue).
# "ai": online AI services may process the answers, voice and photos (optional).
# "sms": queue messages to the phone number the patient gave (optional, nothing pre-ticked).
ConsentPurpose = Literal["care", "ai", "sms"]


class ConsentCopy(TypedDict):
    language: Language
    version: str
    sha256: str
    text: str


def get_consent_copy(language: Language, purpose: ConsentPurpose = "care") -> ConsentCopy:
    name = f"{language}.md" if purpose == "care" else f"{purpose}-{language}.md"
    raw = (CONSENT_DIRECTORY / name).read_text(encoding="utf-8")
    parts = raw.split("---", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("consent copy must contain front matter")
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
    check_review(f"consent/{name}", metadata.get("review_status"), record)
    text = parts[2].strip()
    if not version or not text or len(text.split()) > 60:
        raise ValueError("consent copy must have a version and contain at most 60 words")
    return {
        "language": language,
        "version": version,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }


def load_danger_tiles() -> dict[str, dict[Language, str]]:
    import json

    path = Path(__file__).with_name("bank") / "danger_tiles.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    check_review("bank/danger_tiles.json", payload.get("review_status"), payload.get("review"))
    tiles = payload.get("tiles")
    if not isinstance(tiles, dict) or not tiles:
        raise ValueError("danger tile list is empty")
    for tile_id, labels in tiles.items():
        if (
            not isinstance(tile_id, str)
            or not isinstance(labels, dict)
            or set(labels) != {"en", "hi", "mr"}
        ):
            raise ValueError("each danger tile requires en, hi, and mr labels")
        if any(not isinstance(labels[lang], str) or not labels[lang].strip() for lang in labels):
            raise ValueError("danger tile labels cannot be empty")
    return tiles


DANGER_TILES = load_danger_tiles()
