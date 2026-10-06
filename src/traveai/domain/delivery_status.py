"""Delivery lifecycle state machine.

Happy path:
    scheduled -> assigned -> picking_up -> airborne -> arriving -> delivered

Failure paths:
    scheduled / assigned / picking_up -> canceled   (merchant cancels before takeoff)
    airborne / arriving -> aborted                  (wind, low battery, blocked drop zone)
    aborted -> returned_to_base                     (drone flew home with the package)
    returned_to_base -> scheduled                   (retry)
    any non-terminal -> failed                      (gave up)

A delivery is created from a quote, so it starts at `scheduled`.
"""

from enum import StrEnum


class DeliveryStatus(StrEnum):
    SCHEDULED = "scheduled"
    ASSIGNED = "assigned"
    PICKING_UP = "picking_up"
    AIRBORNE = "airborne"
    ARRIVING = "arriving"
    DELIVERED = "delivered"
    ABORTED = "aborted"
    RETURNED_TO_BASE = "returned_to_base"
    CANCELED = "canceled"
    FAILED = "failed"


S = DeliveryStatus

TERMINAL_STATUSES: frozenset[DeliveryStatus] = frozenset({S.DELIVERED, S.CANCELED, S.FAILED})

_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    # assigned -> scheduled allows re-assignment if the chosen drone becomes unavailable.
    S.SCHEDULED: frozenset({S.ASSIGNED, S.CANCELED, S.FAILED}),
    S.ASSIGNED: frozenset({S.PICKING_UP, S.SCHEDULED, S.CANCELED, S.FAILED}),
    S.PICKING_UP: frozenset({S.AIRBORNE, S.CANCELED, S.FAILED}),
    S.AIRBORNE: frozenset({S.ARRIVING, S.ABORTED, S.FAILED}),
    S.ARRIVING: frozenset({S.DELIVERED, S.ABORTED, S.FAILED}),
    S.ABORTED: frozenset({S.RETURNED_TO_BASE, S.FAILED}),
    S.RETURNED_TO_BASE: frozenset({S.SCHEDULED, S.FAILED}),
    S.DELIVERED: frozenset(),
    S.CANCELED: frozenset(),
    S.FAILED: frozenset(),
}


class InvalidTransitionError(ValueError):
    def __init__(self, current: DeliveryStatus, target: DeliveryStatus) -> None:
        super().__init__(f"Cannot move delivery from '{current}' to '{target}'")
        self.current = current
        self.target = target


def allowed_next(current: DeliveryStatus) -> frozenset[DeliveryStatus]:
    return _TRANSITIONS[current]


def can_transition(current: DeliveryStatus, target: DeliveryStatus) -> bool:
    return target in _TRANSITIONS[current]


def ensure_transition(current: DeliveryStatus, target: DeliveryStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def event_type_for(status: DeliveryStatus) -> str:
    """Webhook/event name for entering a status, e.g. `delivery.airborne`."""
    return f"delivery.{status.value}"
