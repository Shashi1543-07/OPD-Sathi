"""Allowed visit queue transitions shared by API and worker paths."""

from typing import Literal

VisitState = Literal[
    "registering",
    "awaiting_confirmation",
    "queued",
    "called",
    "in_consult",
    "done",
    "no_show",
    "cancelled",
]

TRANSITIONS: dict[str, frozenset[str]] = {
    "registering": frozenset({"awaiting_confirmation", "cancelled"}),
    "awaiting_confirmation": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"called", "no_show", "cancelled"}),
    "called": frozenset({"in_consult", "no_show", "cancelled"}),
    "in_consult": frozenset({"done", "cancelled"}),
    "done": frozenset(),
    "no_show": frozenset(),
    "cancelled": frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """Return whether a visit state change is valid."""
    return target in TRANSITIONS.get(current, frozenset())


def require_transition(current: str, target: str) -> None:
    """Raise a stable error for an invalid visit transition."""
    if not can_transition(current, target):
        raise ValueError(f"invalid visit transition: {current} -> {target}")
