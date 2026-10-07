"""Turn a merchant's delivery request into a Quote: is it possible, how much, and when.

For each drone that could take the job we plan the full loop
    hub -> pickup (positioning) -> drop-off (delivery) -> hub (return)
check it against the rules engine, weather and battery range, and keep the best plan.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.domain.enums import Priority, VehicleStatus
from traveai.models import Quote, Vehicle
from traveai.rules.airspace import AirspaceMap
from traveai.rules.codes import RuleCode, Violation
from traveai.rules.config import RulesConfig
from traveai.rules.engine import FlightRequest, RulesResult, VehicleProfile, evaluate
from traveai.rules.geo import haversine_m
from traveai.rules.weather import WeatherConditions, WeatherProvider
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload
from traveai.services.pricing import price_delivery

QUOTE_TTL = timedelta(minutes=5)  # weather and airspace change; quotes go stale fast
LOADING_SECONDS = 180  # merchant hands the package over and it is secured on the drone
UNAVAILABLE_STATUSES = {VehicleStatus.MAINTENANCE, VehicleStatus.OFFLINE}


@dataclass(frozen=True)
class QuoteRequest:
    pickup: Location
    dropoff: Location
    payload: Payload
    priority: Priority = Priority.STANDARD
    pickup_at: datetime | None = None  # when the package is ready; None = now


@dataclass(frozen=True)
class QuoteContext:
    """Everything outside the request that a quote depends on (swappable in tests)."""

    now: datetime
    weather: WeatherProvider
    airspace: AirspaceMap
    config: RulesConfig


@dataclass(frozen=True)
class Candidate:
    vehicle: Vehicle
    rules: RulesResult
    violations: tuple[Violation, ...]  # rules + range
    pickup_departure_at: datetime
    dropoff_arrival_at: datetime
    weather: WeatherConditions

    @property
    def feasible(self) -> bool:
        return not self.violations


def plan_for_vehicle(vehicle: Vehicle, req: QuoteRequest, ctx: QuoteContext) -> Candidate:
    cfg = ctx.config
    speed = vehicle.cruise_speed_mps
    p, d = req.pickup, req.dropoff

    positioning_m = haversine_m(vehicle.base_lat, vehicle.base_lng, p.lat, p.lng)
    delivery_m = haversine_m(p.lat, p.lng, d.lat, d.lng)
    return_m = haversine_m(d.lat, d.lng, vehicle.base_lat, vehicle.base_lng)

    positioning_s = cfg.flight_overhead_seconds + positioning_m / speed
    arrive_at_pickup = ctx.now + timedelta(seconds=positioning_s)
    if req.pickup_at and req.pickup_at > arrive_at_pickup:
        arrive_at_pickup = req.pickup_at
    departure = arrive_at_pickup + timedelta(seconds=LOADING_SECONDS)
    rough_arrival = departure + timedelta(seconds=cfg.flight_overhead_seconds + delivery_m / speed)

    weather = WeatherConditions.worst(
        ctx.weather.conditions_at(p.lat, p.lng, departure),
        ctx.weather.conditions_at(d.lat, d.lng, rough_arrival),
    )
    rules = evaluate(
        FlightRequest(
            pickup=p,
            dropoff=d,
            payload=req.payload,
            vehicle=VehicleProfile.from_vehicle(vehicle),
            departure_at=departure,
            priority=req.priority,
            weather=weather,
        ),
        airspace=ctx.airspace,
        config=cfg,
    )

    violations = list(rules.violations)
    loop_km = (positioning_m + delivery_m + return_m) / 1000
    usable_km = vehicle.max_range_km * (1 - cfg.range_reserve_fraction)
    if loop_km > usable_km:
        violations.append(
            Violation(
                RuleCode.OUT_OF_RANGE,
                f"Round trip {loop_km:.1f} km exceeds usable range {usable_km:.1f} km",
            )
        )

    return Candidate(
        vehicle=vehicle,
        rules=rules,
        violations=tuple(violations),
        pickup_departure_at=departure,
        dropoff_arrival_at=departure + timedelta(seconds=rules.est_flight_seconds),
        weather=weather,
    )


def best_candidate(candidates: list[Candidate]) -> Candidate | None:
    """Earliest drop-off among feasible plans; otherwise the plan closest to working (fewest
    problems), so the reasons we report are the ones worth fixing."""
    if not candidates:
        return None
    feasible = [c for c in candidates if c.feasible]
    if feasible:
        return min(feasible, key=lambda c: c.dropoff_arrival_at)
    return min(candidates, key=lambda c: (len(c.violations), c.dropoff_arrival_at))


def _reasons(violations: tuple[Violation, ...]) -> list[dict[str, str]]:
    """Deduplicate by code, keeping the first message for each."""
    seen: dict[str, str] = {}
    for v in violations:
        seen.setdefault(v.code.value, v.message)
    return [{"code": code, "message": message} for code, message in seen.items()]


def create_quote(session: Session, merchant_id: str, req: QuoteRequest, ctx: QuoteContext) -> Quote:
    """Build and add (not commit) a Quote."""
    vehicles = session.scalars(
        select(Vehicle).where(Vehicle.status.not_in(UNAVAILABLE_STATUSES))
    ).all()
    best = best_candidate([plan_for_vehicle(v, req, ctx) for v in vehicles])

    quote = Quote(
        merchant_id=merchant_id,
        priority=req.priority,
        requested_pickup_at=req.pickup_at,
        distance_m=haversine_m(req.pickup.lat, req.pickup.lng, req.dropoff.lat, req.dropoff.lng),
        created_at=ctx.now,
        updated_at=ctx.now,
        expires_at=ctx.now + QUOTE_TTL,
    )
    quote.pickup, quote.dropoff, quote.payload = req.pickup, req.dropoff, req.payload

    if best is None:
        quote.feasible = False
        quote.infeasible_reasons = [
            {"code": RuleCode.NO_VEHICLE_AVAILABLE.value, "message": "No drones are in service"}
        ]
        quote.requirements = []
        quote.price_breakdown = []
    else:
        quote.feasible = best.feasible
        quote.infeasible_reasons = _reasons(best.violations)
        quote.requirements = sorted(r.value for r in best.rules.requirements)
        quote.cruise_altitude_ft = best.rules.cruise_altitude_ft
        quote.weather = asdict(best.weather)
        if best.feasible:
            price = price_delivery(quote.distance_m, req.payload, req.priority)
            quote.price_cents = price.total_cents
            quote.currency = price.currency
            quote.price_breakdown = price.breakdown()
            quote.estimated_pickup_at = best.pickup_departure_at
            quote.estimated_dropoff_at = best.dropoff_arrival_at
            quote.eta_seconds = round((best.dropoff_arrival_at - ctx.now).total_seconds())
        else:
            quote.price_breakdown = []

    session.add(quote)
    return quote
