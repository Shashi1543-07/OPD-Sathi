import pytest

from app.domain.queue.state import TRANSITIONS, can_transition, require_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("awaiting_confirmation", "queued"),
        ("queued", "called"),
        ("called", "in_consult"),
        ("in_consult", "done"),
        # Called and not there: marked absent, then recalled or (only then) a no-show.
        ("called", "skipped"),
        ("skipped", "queued"),
        ("skipped", "no_show"),
        # A call made by mistake is undone without a missed call on record.
        ("called", "queued"),
        ("registering", "cancelled"),
        ("awaiting_confirmation", "cancelled"),
        ("queued", "cancelled"),
        ("called", "cancelled"),
        ("in_consult", "cancelled"),
        ("skipped", "cancelled"),
    ],
)
def test_allowed_visit_transitions(current: str, target: str) -> None:
    assert can_transition(current, target)
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("registering", "done"),
        ("queued", "in_consult"),
        ("called", "done"),
        ("done", "queued"),
        ("no_show", "called"),
        ("cancelled", "queued"),
        ("unknown", "queued"),
        # A patient is never a no-show without first being called and marked absent.
        ("queued", "no_show"),
        ("called", "no_show"),
        # A skipped patient goes back in line first; they are not called or seen directly.
        ("skipped", "called"),
        ("skipped", "in_consult"),
        ("queued", "skipped"),
    ],
)
def test_invalid_visit_transitions_are_rejected(current: str, target: str) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ValueError, match="invalid visit transition"):
        require_transition(current, target)


def test_every_terminal_state_has_no_outgoing_edges() -> None:
    for terminal in ("done", "no_show", "cancelled"):
        assert not TRANSITIONS[terminal]
