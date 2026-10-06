"""Reject model text that could diagnose, advise, prescribe, or direct to a URL."""

import re
from collections.abc import Mapping, Sequence
from typing import Any


class UnsafeLLMOutputError(ValueError):
    """Generated text contains a prohibited clinical recommendation or external link."""


_PROHIBITED_PATTERNS = (
    re.compile(
        r"\b(?:diagnosis|diagnosed|you have|you are suffering from|likely have|"
        r"likely suffering from|this is (?:a|an)|you may have)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:you should|you must|you need to|i recommend|please (?:take|use|do|try|avoid))\b",
        re.I,
    ),
    re.compile(
        r"\b(?:medicine|medication|tablet|capsule|prescription|antibiotic|dose|dosage|"
        r"paracetamol|ibuprofen|amoxicillin)\b|\b\d+(?:\.\d+)?\s?(?:mg|mcg|ml|tablets?)\b",
        re.I,
    ),
    re.compile(r"\b(?:https?://|ftp://|www\.)[^\s]+|\b[a-z0-9-]+\.(?:com|in|org|net|io)\b", re.I),
    re.compile(
        r"(?:निदान|रोग|बीमारी|दवा|गोली|खुराक|मिलीग्राम|करें|कीजिए|चाहिए|लेना चाहिए|"
        r"आपको .{0,80} है|आपको .{0,80} हो सकता)"
    ),
    re.compile(
        r"(?:निदान|आजार|रोग|औषध|गोळी|मात्रा|करा|करावे|घ्या|घ्यावे|पाहिजे|"
        r"तुम्हाला .{0,80} आहे|तुम्हाला .{0,80} असू शकते)"
    ),
    re.compile(r"\b(?:toma dawai|dawa lo|dawa lijiye|goli lo|aapko .* hai|aapko .* hoga)\b", re.I),
    re.compile(r"\b(?:dawa ghya|goli ghya|aushadh ghya|tumhala .* aahe|tumhala .* asel)\b", re.I),
)


def validate_safe_output(value: Any) -> None:
    """Recursively reject prohibited generated strings without changing safe patient text."""
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _PROHIBITED_PATTERNS):
            raise UnsafeLLMOutputError("generated text is outside the allowed clinical scope")
        return
    if isinstance(value, Mapping):
        for item in value.values():
            validate_safe_output(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            validate_safe_output(item)
