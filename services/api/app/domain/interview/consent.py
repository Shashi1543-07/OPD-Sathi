"""Versioned multilingual consent copy, deliberately awaiting clinician review."""

import hashlib
from pathlib import Path
from typing import TypedDict

from app.domain.interview.bank import REVIEW_STATUS, Language

CONSENT_DIRECTORY = Path(__file__).with_name("consent")


class ConsentCopy(TypedDict):
    language: Language
    version: str
    sha256: str
    text: str


def get_consent_copy(language: Language) -> ConsentCopy:
    raw = (CONSENT_DIRECTORY / f"{language}.md").read_text(encoding="utf-8")
    parts = raw.split("---", maxsplit=2)
    if len(parts) != 3:
        raise ValueError("consent copy must contain front matter")
    metadata = dict(line.split(":", maxsplit=1) for line in parts[1].splitlines() if ":" in line)
    if metadata.get("review_status", "").strip() != REVIEW_STATUS:
        raise ValueError("consent copy must remain marked for clinician review")
    version = metadata.get("version", "").strip()
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
    if payload.get("review_status") != REVIEW_STATUS:
        raise ValueError("danger tiles must remain marked for clinician review")
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
