"""The flight simulator.

Each `step(now, dt_s)` call:
1. dispatches idle drones to scheduled deliveries (most urgent first),
2. moves every active mission `dt_s` simulated seconds along its waypoints, draining battery,
   moving the delivery through its statuses and triggering failures,
3. charges drones parked at their hub.

A mission flies  hub -> pickup -> drop-off -> hub  in phases:

    positioning -> loading -> delivering -> (awaiting_handoff) -> returning -> done

`dt_s` is simulated time; timestamps use the wall clock `now`. Running with `dt_s` larger than
the real tick interval speeds the whole fleet up for demos.
"""

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.domain.delivery_status import DeliveryStatus
from traveai.domain.enums import (
    CustodyAction,
    FailureKind,
    MissionPhase,
    MissionStatus,
    Priority,
    VehicleStatus,
)
from traveai.errors import ApiError
from traveai.models import Delivery, Mission, Vehicle
from traveai.rules.airspace import AirspaceMap
from traveai.rules.codes import Requirement
from traveai.rules.config import RulesConfig
from traveai.rules.geo import haversine_m
from traveai.rules.routing import plan_route
from traveai.rules.weather import WeatherProvider
from traveai.services.deliveries import MAX_PIN_ATTEMPTS, complete_delivery
from traveai.services.planning import HANDOFF_TIMEOUT_S, LOADING_SECONDS, LoopPlan, plan_loop

log = logging.getLogger(__name__)

S = DeliveryStatus
ARRIVING_DISTANCE_M = 400  # "arriving" when this close to the drop-off
HOVER_ALTITUDE_FT = 30
HARD_RESERVE_PCT = 5.0  # abort and fly home if the battery would drop below this
CHARGE_PCT_PER_S = 100 / 1800  # empty to full in 30 minutes
MAX_ATTEMPTS = 2  # a delivery is flown at most twice before it fails
SCHEDULE_LEAD = timedelta(minutes=15)  # dispatch this long before a requested pickup time
DISPATCH_TIMEOUT = timedelta(hours=1)  # fail deliveries no drone could fly for this long
PRIORITY_RANK = {Priority.URGENT: 0, Priority.EXPRESS: 1, Priority.STANDARD: 2}
DISPATCHABLE = {VehicleStatus.IDLE, VehicleStatus.CHARGING}


@dataclass
class SimContext:
    weather: WeatherProvider
    airspace: AirspaceMap
    config: RulesConfig = field(default_factory=RulesConfig)
    failure_rate: float = 0.0  # chance a new mission draws a random failure
    rng: random.Random = field(default_factory=random.Random)


def proof_photo_url(delivery: Delivery) -> str:
    return f"/v1/deliveries/{delivery.id}/proof.svg"


class Simulator:
    def __init__(self, ctx: SimContext) -> None:
        self.ctx = ctx

    # --- public ----------------------------------------------------------------------------

    def step(self, session: Session, now: datetime, dt_s: float) -> None:
        self.dispatch(session, now)
        missions = session.scalars(select(Mission).where(Mission.phase.is_not(None))).all()
        for mission in missions:
            self._advance(mission, now, dt_s)
        for vehicle in session.scalars(
            select(Vehicle).where(Vehicle.status == VehicleStatus.CHARGING)
        ):
            vehicle.battery_pct = min(100.0, vehicle.battery_pct + CHARGE_PCT_PER_S * dt_s)
            if vehicle.battery_pct >= 100.0:
                vehicle.status = VehicleStatus.IDLE
        session.commit()

    def dispatch(self, session: Session, now: datetime) -> list[Mission]:
        """Assign free drones to scheduled deliveries. Returns the missions started."""
        pending = session.scalars(select(Delivery).where(Delivery.status == S.SCHEDULED)).all()
        pending = sorted(pending, key=lambda d: (PRIORITY_RANK[d.priority], d.created_at))
        free = list(session.scalars(select(Vehicle).where(Vehicle.status.in_(DISPATCHABLE))).all())
        started: list[Mission] = []
        for delivery in pending:
            ready_at = delivery.quote.requested_pickup_at if delivery.quote else None
            if ready_at and now < ready_at - SCHEDULE_LEAD:
                continue  # too early for a scheduled pickup
            plans = [self._plan(v, delivery, now, ready_at) for v in free]
            feasible = [p for p in plans if p.feasible]
            if not feasible:
                if now - delivery.created_at > DISPATCH_TIMEOUT:
                    codes = sorted({v.code.value for p in plans for v in p.violations})
                    reason = "no_drone_could_fly: " + (", ".join(codes) or "no drones free")
                    delivery.transition_to(S.FAILED, reason=reason[:200], at=now)
                continue  # hold: weather may clear, a drone may come back
            best = min(feasible, key=lambda p: p.dropoff_arrival_at)
            started.append(self._start_mission(session, delivery, best, now))
            free.remove(best.vehicle)
        return started

    def handoff(self, session: Session, delivery: Delivery, pin: str | None, now: datetime) -> None:
        """Recipient enters the PIN while the drone hovers at the drop-off."""
        mission = _active_mission(delivery)
        if mission is None or mission.phase != MissionPhase.AWAITING_HANDOFF:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "drone_not_at_dropoff",
                "No drone is waiting at the drop-off for this delivery",
            )
        v = mission.vehicle
        complete_delivery(
            delivery, lat=v.lat, lng=v.lng, at=now, pin=pin, photo_url=proof_photo_url(delivery)
        )
        self._head_home(mission, now, package_delivered=True)

    # --- dispatch --------------------------------------------------------------------------

    def _plan(
        self, vehicle: Vehicle, delivery: Delivery, now: datetime, ready_at: datetime | None
    ) -> LoopPlan:
        return plan_loop(
            vehicle,
            pickup=delivery.pickup,
            dropoff=delivery.dropoff,
            payload=delivery.payload,
            priority=delivery.priority,
            start_at=now,
            ready_at=ready_at,
            weather=self.ctx.weather,
            airspace=self.ctx.airspace,
            config=self.ctx.config,
            battery_pct=vehicle.battery_pct,
            hover_s=HANDOFF_TIMEOUT_S if delivery.pin_required else 0.0,
        )

    def _start_mission(
        self, session: Session, delivery: Delivery, plan: LoopPlan, now: datetime
    ) -> Mission:
        failure = delivery.test_failure
        if failure is None and self.ctx.rng.random() < self.ctx.failure_rate:
            failure = self.ctx.rng.choice(list(FailureKind))
        vehicle = plan.vehicle
        mission = Mission(
            delivery=delivery,
            vehicle=vehicle,
            status=MissionStatus.ACTIVE,
            phase=MissionPhase.POSITIONING,
            waypoints=[list(p) for p in plan.waypoints],
            pickup_index=plan.pickup_index,
            dropoff_index=plan.dropoff_index,
            next_waypoint_index=1,
            cruise_altitude_ft=plan.rules.cruise_altitude_ft,
            planned_distance_m=plan.total_m,
            started_at=now,
            injected_failure=failure,
        )
        session.add(mission)
        vehicle.status = VehicleStatus.IN_FLIGHT
        vehicle.altitude_ft = mission.cruise_altitude_ft
        delivery.test_failure = None
        delivery.estimated_pickup_at = plan.pickup_departure_at
        delivery.estimated_dropoff_at = plan.dropoff_arrival_at
        delivery.transition_to(S.ASSIGNED, data={"vehicle": vehicle.call_sign}, at=now)
        delivery.transition_to(S.PICKING_UP, at=now)
        return mission

    # --- flying ----------------------------------------------------------------------------

    def _advance(self, m: Mission, now: datetime, dt: float) -> None:
        d, v = m.delivery, m.vehicle
        phase = m.phase

        if d.status == S.CANCELED and phase in {MissionPhase.POSITIONING, MissionPhase.LOADING}:
            m.abort_reason = "delivery_canceled"
            m.status = MissionStatus.ABORTED
            self._head_home(m, now, package_delivered=False)
            return

        if phase == MissionPhase.POSITIONING:
            if self._too_windy(v, now):
                d.transition_to(S.SCHEDULED, reason="recalled: wind", at=now)  # reassign later
                m.abort_reason = "wind_gust_exceeds_limit"
                m.status = MissionStatus.ABORTED
                self._head_home(m, now, package_delivered=False)
            elif self._move(m, v, dt, m.pickup_index):
                m.phase, m.phase_elapsed_s = MissionPhase.LOADING, 0.0
                v.altitude_ft = 0

        elif phase == MissionPhase.LOADING:
            m.phase_elapsed_s += dt
            if m.phase_elapsed_s >= LOADING_SECONDS:
                m.package_onboard = True
                if Requirement.CHAIN_OF_CUSTODY.value in d.requirements:
                    d.record_custody(
                        CustodyAction.RECEIVED_FROM_MERCHANT, holder="hub:operator", at=now
                    )
                    d.record_custody(
                        CustodyAction.LOADED_ON_DRONE,
                        holder=f"veh:{v.call_sign}",
                        lat=v.lat,
                        lng=v.lng,
                        at=now,
                    )
                d.transition_to(S.AIRBORNE, data={"vehicle": v.call_sign}, at=now)
                m.phase = MissionPhase.DELIVERING
                v.altitude_ft = m.cruise_altitude_ft

        elif phase == MissionPhase.DELIVERING:
            self._deliver_step(m, v, d, now, dt)

        elif phase == MissionPhase.AWAITING_HANDOFF:
            m.phase_elapsed_s += dt
            self._drain(v, v.cruise_speed_mps * dt)  # hovering costs about as much as cruising
            home_m = _path_m(m.waypoints, m.dropoff_index, len(m.waypoints) - 1)
            if v.battery_pct - self._pct_for(v, home_m) < HARD_RESERVE_PCT:
                self._abort(m, "low_battery", now)  # leave while it can still get home
            elif d.pin_failed_attempts >= MAX_PIN_ATTEMPTS:
                self._abort(m, "pin_locked", now)
            elif m.phase_elapsed_s >= HANDOFF_TIMEOUT_S:
                self._abort(m, "recipient_unavailable", now)

        elif phase == MissionPhase.RETURNING:
            if self._move(m, v, dt, len(m.waypoints) - 1):
                self._land_at_hub(m, v, d, now)

        if v.battery_pct <= 0 and m.phase is not None:
            self._emergency_landing(m, v, d, now)

    def _deliver_step(self, m: Mission, v: Vehicle, d: Delivery, now: datetime, dt: float) -> None:
        leg_m = _path_m(m.waypoints, m.pickup_index, m.dropoff_index)
        remaining = self._remaining_to(m, v, m.dropoff_index)
        halfway = remaining <= leg_m / 2

        if halfway and not m.failure_triggered:
            if m.injected_failure == FailureKind.HIGH_WIND:
                m.failure_triggered = True
                self._abort(m, "wind_gust_exceeds_limit", now)
                return
            if m.injected_failure == FailureKind.LOW_BATTERY:
                m.failure_triggered = True
                v.battery_pct = min(v.battery_pct, self._pct_for(v, remaining) + HARD_RESERVE_PCT)
        if self._too_windy(v, now):
            self._abort(m, "wind_gust_exceeds_limit", now)
            return
        home_after = remaining + _path_m(m.waypoints, m.dropoff_index, len(m.waypoints) - 1)
        if v.battery_pct - self._pct_for(v, home_after) < HARD_RESERVE_PCT:
            self._abort(m, "low_battery", now)
            return

        reached = self._move(m, v, dt, m.dropoff_index)
        if d.status == S.AIRBORNE and (
            reached or self._remaining_to(m, v, m.dropoff_index) <= ARRIVING_DISTANCE_M
        ):
            d.transition_to(S.ARRIVING, at=now)
        if not reached:
            return

        if m.injected_failure == FailureKind.DROP_ZONE_BLOCKED and not m.failure_triggered:
            m.failure_triggered = True
            self._abort(m, "drop_zone_blocked", now)
        elif d.pin_required:
            m.phase, m.phase_elapsed_s = MissionPhase.AWAITING_HANDOFF, 0.0
            v.altitude_ft = HOVER_ALTITUDE_FT
        else:
            complete_delivery(d, lat=v.lat, lng=v.lng, at=now, photo_url=proof_photo_url(d))
            self._head_home(m, now, package_delivered=True)

    def _abort(self, m: Mission, reason: str, now: datetime) -> None:
        """Give up on the drop-off and fly home with the package."""
        m.delivery.transition_to(S.ABORTED, reason=reason, at=now)
        m.status = MissionStatus.ABORTED
        m.abort_reason = reason
        self._reroute_home(m, now)

    def _head_home(self, m: Mission, now: datetime, *, package_delivered: bool) -> None:
        if package_delivered:
            m.package_onboard = False
            m.phase = MissionPhase.RETURNING  # the planned return leg is already in waypoints
        else:
            self._reroute_home(m, now)
        m.vehicle.status = VehicleStatus.RETURNING
        m.vehicle.altitude_ft = m.cruise_altitude_ft

    def _reroute_home(self, m: Mission, now: datetime) -> None:
        """Replace the rest of the plan with a direct (no-fly-safe) path from here to the hub."""
        v = m.vehicle
        here, hub = (v.lat, v.lng), (v.base_lat, v.base_lng)
        path = plan_route(self.ctx.airspace, here, hub, window=(now, now + timedelta(hours=1)))
        flown = m.waypoints[: m.next_waypoint_index]
        m.waypoints = [*flown, list(here), *(list(p) for p in (path or [here, hub])[1:])]
        m.next_waypoint_index = len(flown) + 1
        m.phase = MissionPhase.RETURNING
        v.status = VehicleStatus.RETURNING
        v.altitude_ft = m.cruise_altitude_ft

    def _land_at_hub(self, m: Mission, v: Vehicle, d: Delivery, now: datetime) -> None:
        m.phase = None
        m.ended_at = now
        if m.status == MissionStatus.ACTIVE:
            m.status = MissionStatus.COMPLETED
        v.status = VehicleStatus.CHARGING
        v.altitude_ft = 0
        if m.package_onboard:
            m.package_onboard = False
            if Requirement.CHAIN_OF_CUSTODY.value in d.requirements:
                d.record_custody(CustodyAction.RETURNED_TO_HUB, holder="hub:operator", at=now)
            d.transition_to(S.RETURNED_TO_BASE, at=now)
            if len(d.missions) < MAX_ATTEMPTS:
                d.transition_to(S.SCHEDULED, reason="retry", at=now)
            else:
                d.transition_to(
                    S.FAILED,
                    reason=f"gave up after {MAX_ATTEMPTS} attempts: {m.abort_reason}",
                    at=now,
                )

    def _emergency_landing(self, m: Mission, v: Vehicle, d: Delivery, now: datetime) -> None:
        log.warning("Vehicle %s ran out of battery", v.call_sign)
        m.phase, m.ended_at, m.status = None, now, MissionStatus.ABORTED
        m.abort_reason = "emergency_landing"
        v.status, v.altitude_ft = VehicleStatus.OFFLINE, 0
        if d.status not in {S.DELIVERED, S.CANCELED, S.FAILED}:
            d.transition_to(S.FAILED, reason="emergency_landing", at=now)

    # --- physics ---------------------------------------------------------------------------

    def _move(self, m: Mission, v: Vehicle, dt: float, target_index: int) -> bool:
        """Fly toward waypoints up to `target_index`. True once that waypoint is reached."""
        budget = v.cruise_speed_mps * dt
        moved = 0.0
        while budget > 0 and m.next_waypoint_index <= target_index:
            lat, lng = m.waypoints[m.next_waypoint_index]
            dist = haversine_m(v.lat, v.lng, lat, lng)
            if dist <= budget:
                v.lat, v.lng = lat, lng
                budget -= dist
                moved += dist
                m.next_waypoint_index += 1
            else:
                frac = budget / dist
                v.lat += (lat - v.lat) * frac
                v.lng += (lng - v.lng) * frac
                moved += budget
                budget = 0
        self._drain(v, moved)
        return m.next_waypoint_index > target_index

    def _remaining_to(self, m: Mission, v: Vehicle, target_index: int) -> float:
        if m.next_waypoint_index > target_index:
            return 0.0
        lat, lng = m.waypoints[m.next_waypoint_index]
        return haversine_m(v.lat, v.lng, lat, lng) + _path_m(
            m.waypoints, m.next_waypoint_index, target_index
        )

    def _drain(self, v: Vehicle, meters: float) -> None:
        v.battery_pct = max(0.0, v.battery_pct - self._pct_for(v, meters))

    @staticmethod
    def _pct_for(v: Vehicle, meters: float) -> float:
        """Battery percent needed to fly `meters` (a full battery flies max_range_km)."""
        return meters / (v.max_range_km * 1000) * 100

    def _too_windy(self, v: Vehicle, now: datetime) -> bool:
        return self.ctx.weather.conditions_at(v.lat, v.lng, now).wind_gust_mps > v.max_wind_mps


def _path_m(waypoints: list[list[float]], start: int, end: int) -> float:
    return sum(
        haversine_m(*waypoints[i], *waypoints[i + 1])
        for i in range(start, min(end, len(waypoints) - 1))
    )


def _active_mission(delivery: Delivery) -> Mission | None:
    return next((m for m in delivery.missions if m.phase is not None), None)
