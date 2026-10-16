"""Allowed visit queue transitions shared by API and worker paths.

A called patient who is not there is *skipped*, never dropped: they keep their token, encounter
and arrival time, and staff recall them to the queue when they come back. Only a skipped visit
becomes a no-show (by staff, or at the end of the day). A called patient can also be put back
in the queue unchanged, for a call made by mistake.
"""

from typing import Any, Literal

VisitState = Literal[
    "registering",
    "awaiting_confirmation",
    "queued",
    "called",
    "in_consult",
    "skipped",
    "done",
    "no_show",
    "cancelled",
]

TRANSITIONS: dict[str, frozenset[str]] = {
    "registering": frozenset({"awaiting_confirmation", "cancelled"}),
    "awaiting_confirmation": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"called", "cancelled"}),
    "called": frozenset({"in_consult", "skipped", "queued", "cancelled"}),
    "in_consult": frozenset({"done", "cancelled"}),
    "skipped": frozenset({"queued", "no_show", "cancelled"}),
    "done": frozenset(),
    "no_show": frozenset(),
    "cancelled": frozenset(),
}
# States in which a visit holds a doctor's attention: at most one per doctor.
ACTIVE_CLAIM = frozenset({"called", "in_consult"})
# Waiting to be marked absent after a call, unless the hospital sets its own period.
DEFAULT_CALL_GRACE_SECONDS = 180


class QueueRuleError(Exception):
    """A valid transition that a queue rule refuses right now (stable code for the API)."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra


def can_transition(current: str, target: str) -> bool:
    """Return whether a visit state change is valid."""
    return target in TRANSITIONS.get(current, frozenset())


def require_transition(current: str, target: str) -> None:
    """Raise a stable error for an invalid visit transition."""
    if not can_transition(current, target):
        raise ValueError(f"invalid visit transition: {current} -> {target}")
