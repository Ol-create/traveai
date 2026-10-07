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
from traveai.rules.geo import haversine_m
from traveai.rules.weather import WeatherProvider
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload
from traveai.services.planning import HANDOFF_TIMEOUT_S, LoopPlan, plan_loop
from traveai.services.pricing import price_delivery

QUOTE_TTL = timedelta(minutes=5)  # weather and airspace change; quotes go stale fast
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


def plan_for_vehicle(vehicle: Vehicle, req: QuoteRequest, ctx: QuoteContext) -> LoopPlan:
    # Quotes assume the drone leaves its hub now, fully charged.
    return plan_loop(
        vehicle,
        pickup=req.pickup,
        dropoff=req.dropoff,
        payload=req.payload,
        priority=req.priority,
        start_at=ctx.now,
        ready_at=req.pickup_at,
        weather=ctx.weather,
        airspace=ctx.airspace,
        config=ctx.config,
        hover_s=HANDOFF_TIMEOUT_S if req.payload.prescription else 0.0,
    )


def best_candidate(candidates: list[LoopPlan]) -> LoopPlan | None:
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
        # Flown distance (with detours) when we have a plan, otherwise straight-line.
        distance_m=best.rules.distance_m
        if best
        else haversine_m(req.pickup.lat, req.pickup.lng, req.dropoff.lat, req.dropoff.lng),
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
