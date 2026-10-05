import pytest

from app.domain.queue.state import TRANSITIONS, can_transition, require_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("awaiting_confirmation", "queued"),
        ("queued", "called"),
        ("called", "in_consult"),
        ("in_consult", "done"),
        ("queued", "no_show"),
        ("called", "no_show"),
        ("registering", "cancelled"),
        ("awaiting_confirmation", "cancelled"),
        ("queued", "cancelled"),
        ("called", "cancelled"),
        ("in_consult", "cancelled"),
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
    ],
)
def test_invalid_visit_transitions_are_rejected(current: str, target: str) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ValueError, match="invalid visit transition"):
        require_transition(current, target)


def test_every_terminal_state_has_no_outgoing_edges() -> None:
    for terminal in ("done", "no_show", "cancelled"):
        assert not TRANSITIONS[terminal]
