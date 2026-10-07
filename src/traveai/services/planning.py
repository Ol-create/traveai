"""Plan one drone's full loop for a delivery: hub -> pickup -> drop-off -> hub.

Shared by quoting (can it be done, by when?) and dispatch (fly it now, with this drone).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from traveai.domain.enums import Priority
from traveai.models import Vehicle
from traveai.rules.airspace import AirspaceMap
from traveai.rules.codes import RuleCode, Violation
from traveai.rules.config import RulesConfig
from traveai.rules.engine import FlightRequest, RulesResult, VehicleProfile, evaluate
from traveai.rules.routing import LatLng, path_length_m, plan_route
from traveai.rules.weather import WeatherConditions, WeatherProvider
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

LOADING_SECONDS = 180  # merchant hands the package over and it is secured on the drone
# PIN deliveries: how long the drone hovers at the drop-off waiting for the recipient. Hovering
# burns about as much battery per second as cruising, so this time is budgeted in the range.
HANDOFF_TIMEOUT_S = 120


@dataclass(frozen=True)
class LoopPlan:
    vehicle: Vehicle
    rules: RulesResult  # pickup -> drop-off leg
    violations: tuple[Violation, ...]  # rules + routing + range
    waypoints: list[LatLng]  # whole loop
    pickup_index: int
    dropoff_index: int
    total_m: float
    pickup_departure_at: datetime
    dropoff_arrival_at: datetime
    weather: WeatherConditions

    @property
    def feasible(self) -> bool:
        return not self.violations


def usable_range_m(vehicle: Vehicle, battery_pct: float, config: RulesConfig) -> float:
    """Distance the drone may still fly, keeping the reserve untouched."""
    full_m = vehicle.max_range_km * 1000
    return max(0.0, full_m * battery_pct / 100 - full_m * config.range_reserve_fraction)


def plan_loop(
    vehicle: Vehicle,
    *,
    pickup: Location,
    dropoff: Location,
    payload: Payload,
    priority: Priority,
    start_at: datetime,
    ready_at: datetime | None,
    weather: WeatherProvider,
    airspace: AirspaceMap,
    config: RulesConfig,
    battery_pct: float = 100.0,
    hover_s: float = 0.0,
) -> LoopPlan:
    speed = vehicle.cruise_speed_mps
    hub: LatLng = (vehicle.base_lat, vehicle.base_lng)
    p: LatLng = (pickup.lat, pickup.lng)
    d: LatLng = (dropoff.lat, dropoff.lng)
    window = (start_at, start_at + timedelta(hours=2))
    violations: list[Violation] = []

    def leg(a: LatLng, b: LatLng, label: str) -> list[LatLng]:
        path = plan_route(airspace, a, b, window=window)
        if path is None:
            violations.append(
                Violation(RuleCode.ROUTE_CROSSES_NO_FLY_ZONE, f"No safe route for the {label}")
            )
            return [a, b]
        return path

    positioning = leg(hub, p, "flight from the hub to pickup")
    # The delivery leg's own no-fly problems (pickup/drop-off inside a zone) are reported by
    # the rules engine, with more specific codes.
    delivery = plan_route(airspace, p, d, window=window) or [p, d]
    returning = leg(d, hub, "return flight to the hub")

    positioning_s = config.flight_overhead_seconds + path_length_m(positioning) / speed
    arrive_at_pickup = start_at + timedelta(seconds=positioning_s)
    if ready_at and ready_at > arrive_at_pickup:
        arrive_at_pickup = ready_at
    departure = arrive_at_pickup + timedelta(seconds=LOADING_SECONDS)
    rough_arrival = departure + timedelta(
        seconds=config.flight_overhead_seconds + path_length_m(delivery) / speed
    )

    conditions = WeatherConditions.worst(
        weather.conditions_at(*p, departure),
        weather.conditions_at(*d, rough_arrival),
    )
    rules = evaluate(
        FlightRequest(
            pickup=pickup,
            dropoff=dropoff,
            payload=payload,
            vehicle=VehicleProfile.from_vehicle(vehicle),
            departure_at=departure,
            priority=priority,
            weather=conditions,
            waypoints=tuple(delivery[1:-1]),
        ),
        airspace=airspace,
        config=config,
    )
    violations = [*rules.violations, *violations]

    total_m = path_length_m(positioning) + path_length_m(delivery) + path_length_m(returning)
    energy_m = total_m + hover_s * speed  # hover time expressed as cruise distance
    usable_m = usable_range_m(vehicle, battery_pct, config)
    if energy_m > usable_m:
        hover_note = f" incl. {hover_s:.0f} s hover for the PIN" if hover_s else ""
        violations.append(
            Violation(
                RuleCode.OUT_OF_RANGE,
                f"Round trip {total_m / 1000:.1f} km{hover_note} exceeds usable range "
                f"{usable_m / 1000:.1f} km",
            )
        )

    waypoints = [*positioning, *delivery[1:], *returning[1:]]
    pickup_index = len(positioning) - 1
    return LoopPlan(
        vehicle=vehicle,
        rules=rules,
        violations=tuple(violations),
        waypoints=waypoints,
        pickup_index=pickup_index,
        dropoff_index=pickup_index + len(delivery) - 1,
        total_m=total_m,
        pickup_departure_at=departure,
        dropoff_arrival_at=departure + timedelta(seconds=rules.est_flight_seconds),
        weather=conditions,
    )
