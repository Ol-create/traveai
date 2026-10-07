"""Live tracking view of a delivery: where its drone is and when it will arrive."""

from datetime import datetime, timedelta

from traveai.domain.enums import MissionPhase
from traveai.models import Delivery
from traveai.schemas.tracking import DronePosition, Tracking
from traveai.services.planning import LOADING_SECONDS
from traveai.sim.simulator import active_mission, remaining_m


def tracking_snapshot(delivery: Delivery, now: datetime) -> Tracking:
    mission = active_mission(delivery)
    if mission is None:
        # Not flying (yet, or any more): fall back to the booked estimate.
        eta = None
        if delivery.estimated_dropoff_at and delivery.delivered_at is None:
            eta = max(0, round((delivery.estimated_dropoff_at - now).total_seconds()))
        return Tracking(
            delivery_id=delivery.id,
            status=delivery.status,
            phase=None,
            drone=None,
            remaining_m=None,
            eta_seconds=eta,
            estimated_dropoff_at=delivery.estimated_dropoff_at,
            route=[],
            next_waypoint_index=None,
            as_of=now,
        )

    v = mission.vehicle
    phase = mission.phase
    to_dropoff = remaining_m(mission, v, mission.dropoff_index)
    wait_s = 0.0
    if phase == MissionPhase.POSITIONING:
        wait_s = LOADING_SECONDS
    elif phase == MissionPhase.LOADING:
        wait_s = max(0.0, LOADING_SECONDS - mission.phase_elapsed_s)

    if phase in {MissionPhase.AWAITING_HANDOFF, MissionPhase.RETURNING}:
        remaining, eta = 0.0, 0  # at the drop-off, or already past it
    else:
        remaining = to_dropoff
        eta = round(to_dropoff / v.cruise_speed_mps + wait_s)

    return Tracking(
        delivery_id=delivery.id,
        status=delivery.status,
        phase=phase,
        drone=DronePosition(
            call_sign=v.call_sign,
            lat=v.lat,
            lng=v.lng,
            altitude_ft=v.altitude_ft,
            battery_pct=round(v.battery_pct, 1),
        ),
        remaining_m=round(remaining),
        eta_seconds=eta,
        estimated_dropoff_at=now + timedelta(seconds=eta),
        route=mission.waypoints,
        next_waypoint_index=mission.next_waypoint_index,
        as_of=now,
    )
