"""Evaluate one proposed flight against every rule and return all problems at once, so a
merchant sees every reason a delivery can't happen, not just the first."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from traveai.domain.enums import Priority
from traveai.models import Vehicle
from traveai.rules.airspace import AirspaceMap, default_airspace
from traveai.rules.codes import Requirement, RuleCode, Violation
from traveai.rules.config import RulesConfig
from traveai.rules.daylight import is_daylight_or_civil_twilight
from traveai.rules.geo import haversine_m
from traveai.rules.laanc import LaancDecision, LaancStatus, request_authorization
from traveai.rules.payload_rules import check_payload, requirements_for
from traveai.rules.weather import WeatherConditions, check_weather
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload


@dataclass(frozen=True)
class VehicleProfile:
    """The drone capabilities the rules care about."""

    max_payload_kg: float
    cruise_speed_mps: float
    temperature_controlled: bool
    max_wind_mps: float = 12.0

    @classmethod
    def from_vehicle(cls, vehicle: Vehicle) -> "VehicleProfile":
        return cls(
            max_payload_kg=vehicle.max_payload_kg,
            cruise_speed_mps=vehicle.cruise_speed_mps,
            temperature_controlled=vehicle.temperature_controlled,
            max_wind_mps=vehicle.max_wind_mps,
        )


@dataclass(frozen=True)
class FlightRequest:
    pickup: Location
    dropoff: Location
    payload: Payload
    vehicle: VehicleProfile
    departure_at: datetime
    priority: Priority = Priority.STANDARD
    cruise_altitude_ft: int | None = None  # None = use the configured default
    # Worst-case weather along the route; None skips weather checks.
    weather: WeatherConditions | None = None


@dataclass(frozen=True)
class RulesResult:
    violations: tuple[Violation, ...]
    requirements: frozenset[Requirement]
    distance_m: float
    est_flight_seconds: float
    cruise_altitude_ft: int
    laanc: LaancDecision
    no_fly_zones: tuple[str, ...] = field(default=())  # names of zones in the way

    @property
    def feasible(self) -> bool:
        return not self.violations

    @property
    def reason_codes(self) -> list[str]:
        """Deduplicated codes, in the order found (stored on Quote.infeasible_reasons)."""
        return list(dict.fromkeys(v.code.value for v in self.violations))


def evaluate(
    request: FlightRequest,
    *,
    airspace: AirspaceMap | None = None,
    config: RulesConfig | None = None,
) -> RulesResult:
    airspace = airspace or default_airspace()
    config = config or RulesConfig()
    violations: list[Violation] = []
    p, d = request.pickup, request.dropoff

    distance_m = haversine_m(p.lat, p.lng, d.lat, d.lng)
    est_flight_s = config.flight_overhead_seconds + distance_m / request.vehicle.cruise_speed_mps
    arrival_at = request.departure_at + timedelta(seconds=est_flight_s)

    # --- Part 107 limits -------------------------------------------------------------------
    altitude = request.cruise_altitude_ft or config.default_cruise_altitude_ft
    if altitude > config.max_altitude_ft:
        violations.append(
            Violation(
                RuleCode.ALTITUDE_EXCEEDS_LIMIT,
                f"Cruise altitude {altitude} ft exceeds the {config.max_altitude_ft} ft limit",
            )
        )
    if request.vehicle.cruise_speed_mps > config.max_groundspeed_mps:
        violations.append(
            Violation(RuleCode.SPEED_EXCEEDS_LIMIT, "Drone cruise speed exceeds 100 mph")
        )
    if request.payload.weight_kg > request.vehicle.max_payload_kg:
        violations.append(
            Violation(
                RuleCode.PAYLOAD_EXCEEDS_VEHICLE_CAPACITY,
                f"Payload {request.payload.weight_kg} kg exceeds drone capacity "
                f"{request.vehicle.max_payload_kg} kg",
            )
        )
    if not config.allow_night_operations:
        for label, lat, lng, when in (
            ("Departure", p.lat, p.lng, request.departure_at),
            ("Arrival", d.lat, d.lng, arrival_at),
        ):
            if not is_daylight_or_civil_twilight(lat, lng, when):
                violations.append(Violation(RuleCode.OUTSIDE_DAYLIGHT, f"{label} is after dark"))

    # --- Airspace: no-fly zones ------------------------------------------------------------
    proj = airspace.projection
    pickup_pt, dropoff_pt = proj.point(p.lat, p.lng), proj.point(d.lat, d.lng)
    route = proj.line((p.lat, p.lng), (d.lat, d.lng))
    blocking: list[str] = []
    # A stadium TFR that starts mid-flight still blocks the flight.
    for zone in airspace.zones_active_during(request.departure_at, arrival_at):
        if not zone.is_no_fly or not route.intersects(zone.geometry):
            continue
        blocking.append(zone.name)
        if zone.geometry.contains(pickup_pt):
            code, where = RuleCode.PICKUP_IN_NO_FLY_ZONE, "Pickup is inside"
        elif zone.geometry.contains(dropoff_pt):
            code, where = RuleCode.DROPOFF_IN_NO_FLY_ZONE, "Drop-off is inside"
        else:
            code, where = RuleCode.ROUTE_CROSSES_NO_FLY_ZONE, "Route crosses"
        violations.append(Violation(code, f"{where} {zone.name}"))

    # --- Airspace: controlled (airports) ---------------------------------------------------
    laanc = request_authorization(
        airspace,
        route,
        altitude,
        request.departure_at,
        min_altitude_ft=config.min_cruise_altitude_ft,
    )
    requirements = requirements_for(request.payload)
    if laanc.status == LaancStatus.DENIED:
        violations.append(Violation(RuleCode.LAANC_DENIED, laanc.reason or "LAANC denied"))
    elif laanc.status == LaancStatus.APPROVED:
        requirements.add(Requirement.LAANC_AUTHORIZATION)
        altitude = min(altitude, laanc.max_altitude_ft or altitude)

    # --- Payload: medical / food -----------------------------------------------------------
    violations += check_payload(
        request.payload,
        request.priority,
        vehicle_temperature_controlled=request.vehicle.temperature_controlled,
        est_flight_seconds=est_flight_s,
        config=config,
    )

    # --- Weather ---------------------------------------------------------------------------
    if request.weather is not None:
        violations += check_weather(
            request.weather, max_wind_mps=request.vehicle.max_wind_mps, config=config
        )

    return RulesResult(
        violations=tuple(violations),
        requirements=frozenset(requirements),
        distance_m=distance_m,
        est_flight_seconds=est_flight_s,
        cruise_altitude_ft=altitude,
        laanc=laanc,
        no_fly_zones=tuple(blocking),
    )
