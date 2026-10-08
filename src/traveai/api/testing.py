"""Test-mode helpers for developers integrating with the API. Only `sk_test_` keys may use
them. Stand-ins until the flight simulator drives deliveries for real."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from traveai.api.deliveries import get_owned_delivery
from traveai.auth import AuthContext, CurrentAuth
from traveai.config import get_settings
from traveai.db import get_session
from traveai.deps import get_now
from traveai.domain.delivery_status import (
    TERMINAL_STATUSES,
    DeliveryStatus,
    InvalidTransitionError,
)
from traveai.domain.enums import CustodyAction
from traveai.errors import ApiError
from traveai.openapi import errors
from traveai.rules.codes import Requirement
from traveai.schemas.delivery import AdvanceRequest, DeliveryOut, InjectFailureRequest
from traveai.schemas.sandbox import SimulatorStatus, SimulatorUpdate
from traveai.services.deliveries import PinRejectedError, complete_delivery
from traveai.workers import SIMULATOR, SPEED_KEY, lease_alive, set_runtime, sim_speed

router = APIRouter(prefix="/v1/test", tags=["test mode"], responses=errors(401, 429, 403))

S = DeliveryStatus
NEXT_STEP = {
    S.SCHEDULED: S.ASSIGNED,
    S.ASSIGNED: S.PICKING_UP,
    S.PICKING_UP: S.AIRBORNE,
    S.AIRBORNE: S.ARRIVING,
    S.ARRIVING: S.DELIVERED,
    S.ABORTED: S.RETURNED_TO_BASE,
    S.RETURNED_TO_BASE: S.SCHEDULED,
}


@router.post(
    "/deliveries/{delivery_id}/advance",
    response_model=DeliveryOut,
    summary="Step a delivery by hand",
    responses=errors(404, 409),
)
def advance(
    delivery_id: str,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    body: AdvanceRequest | None = None,
) -> DeliveryOut:
    """Move a delivery one step (or to `to`). Delivering drops at the exact drop-off point
    and checks `pin` like the real drone would."""
    _require_test_mode(auth)
    body = body or AdvanceRequest()
    delivery = get_owned_delivery(session, auth, delivery_id)
    if any(m.phase is not None for m in delivery.missions):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "simulator_controls_delivery",
            "A simulated drone is flying this delivery; use failures to steer it instead",
        )
    target = body.to or NEXT_STEP.get(delivery.status)
    if target is None:
        raise ApiError(
            status.HTTP_409_CONFLICT, "invalid_state", f"'{delivery.status}' is a final status"
        )

    if target == S.DELIVERED:
        try:
            complete_delivery(
                delivery,
                lat=delivery.dropoff_lat,
                lng=delivery.dropoff_lng,
                at=now,
                pin=body.pin,
                photo_url=f"/v1/deliveries/{delivery.id}/proof.svg",
            )
        except PinRejectedError:
            session.commit()  # keep the failed-attempt count
            raise
    else:
        try:
            delivery.transition_to(target, reason=body.reason)
        except InvalidTransitionError as exc:
            raise ApiError(status.HTTP_409_CONFLICT, "invalid_transition", str(exc)) from None
        if target == S.AIRBORNE and Requirement.CHAIN_OF_CUSTODY.value in delivery.requirements:
            delivery.record_custody(CustodyAction.RECEIVED_FROM_MERCHANT, holder="sim:operator")
            delivery.record_custody(CustodyAction.LOADED_ON_DRONE, holder="sim:drone")

    session.commit()
    return DeliveryOut.from_model(delivery)


@router.post(
    "/deliveries/{delivery_id}/failures",
    response_model=DeliveryOut,
    summary="Force a failure on a flight",
    responses=errors(404, 409),
)
def inject_failure(
    delivery_id: str,
    body: InjectFailureRequest,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
) -> DeliveryOut:
    """Force a failure on the simulated flight: `high_wind` or `low_battery` strike halfway to
    the drop-off, `drop_zone_blocked` on arrival. The drone aborts and flies home."""
    _require_test_mode(auth)
    delivery = get_owned_delivery(session, auth, delivery_id)
    if delivery.status in TERMINAL_STATUSES:
        raise ApiError(
            status.HTTP_409_CONFLICT, "invalid_state", f"'{delivery.status}' is a final status"
        )
    active = next((m for m in delivery.missions if m.phase is not None), None)
    if active is not None and not active.failure_triggered:
        active.injected_failure = body.kind
    else:
        delivery.test_failure = body.kind  # applied when the next mission starts
    session.commit()
    return DeliveryOut.from_model(delivery)


def _require_test_mode(auth: AuthContext) -> None:
    if not auth.test_mode:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "test_mode_only", "Use an sk_test_ key for this endpoint"
        )


@router.get("/simulator", response_model=SimulatorStatus, summary="Simulator status")
def simulator_status(
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
) -> SimulatorStatus:
    """Is a flight simulator worker running, and how fast?"""
    _require_test_mode(auth)
    return _status(session, now)


@router.patch("/simulator", response_model=SimulatorStatus, summary="Set simulation speed")
def update_simulator(
    body: SimulatorUpdate,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
) -> SimulatorStatus:
    """Change the simulation speed (1-100x) without a restart. Sandbox servers only
    (`TRAVEAI_SANDBOX_CONTROLS=true`): the fleet is shared by everyone on the server."""
    _require_test_mode(auth)
    if not get_settings().sandbox_controls:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "sandbox_controls_disabled",
            "Simulator controls are off on this server",
        )
    set_runtime(session, SPEED_KEY, body.speed)  # workers pick it up on their next tick
    return _status(session, now)


def _status(session: Session, now: datetime) -> SimulatorStatus:
    settings = get_settings()
    return SimulatorStatus(
        running=lease_alive(session, SIMULATOR, now),
        speed=sim_speed(session, settings),
        failure_rate=settings.sim_failure_rate,
        night_operations=settings.allow_night_operations,
        controls_enabled=settings.sandbox_controls,
    )
