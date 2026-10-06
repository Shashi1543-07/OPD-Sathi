"""Unicode-aware normalization for deterministic symptom phrase matching."""

import re
import unicodedata

_SPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize compatibility forms, Devanagari variants, punctuation, and spacing."""
    value = unicodedata.normalize("NFKC", value).casefold()
    # Hindi/Marathi nukta spellings have both precomposed and decomposed forms.
    value = unicodedata.normalize("NFD", value)
    value = value.replace("\u093c", "")
    # Chandrabindu and anusvara are commonly interchanged in informal typing.
    value = value.replace("\u0901", "\u0902")
    value = unicodedata.normalize("NFC", value)
    value = "".join(
        character
        if character.isalnum()
        or character.isspace()
        or unicodedata.category(character).startswith("M")
        else " "
        for character in value
    )
    return _SPACE.sub(" ", value).strip()


def tokens(value: str) -> list[str]:
    """Return normalized whitespace-delimited tokens."""
    normalized = normalize_text(value)
    return normalized.split() if normalized else []
