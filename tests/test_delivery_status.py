import pytest

from traveai.domain.delivery_status import (
    TERMINAL_STATUSES,
    DeliveryStatus,
    InvalidTransitionError,
    allowed_next,
    can_transition,
    ensure_transition,
)

S = DeliveryStatus

HAPPY_PATH = [S.SCHEDULED, S.ASSIGNED, S.PICKING_UP, S.AIRBORNE, S.ARRIVING, S.DELIVERED]


def test_happy_path_is_allowed():
    for current, target in zip(HAPPY_PATH, HAPPY_PATH[1:], strict=False):
        assert can_transition(current, target), f"{current} -> {target}"


def test_every_status_has_transition_rules():
    for status in S:
        allowed_next(status)  # raises KeyError if a status was added without rules


def test_terminal_statuses_have_no_exits():
    for status in TERMINAL_STATUSES:
        assert allowed_next(status) == frozenset()


def test_non_terminal_statuses_can_fail():
    for status in set(S) - TERMINAL_STATUSES:
        assert can_transition(status, S.FAILED), status


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.SCHEDULED, S.DELIVERED),  # can't skip the flight
        (S.DELIVERED, S.AIRBORNE),  # terminal
        (S.AIRBORNE, S.CANCELED),  # can't cancel mid-air; must abort
        (S.SCHEDULED, S.ABORTED),  # can't abort before flying
        (S.CANCELED, S.SCHEDULED),
    ],
)
def test_illegal_transitions_raise(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError):
        ensure_transition(current, target)


def test_abort_then_retry_path():
    path = [S.AIRBORNE, S.ABORTED, S.RETURNED_TO_BASE, S.SCHEDULED]
    for current, target in zip(path, path[1:], strict=False):
        assert can_transition(current, target)
