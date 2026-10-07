from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from traveai.domain.enums import Priority
from traveai.models import Quote
from traveai.rules.geo import haversine_m
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

MIN_DISTANCE_M = 50


class QuoteCreate(BaseModel):
    pickup: Location
    dropoff: Location
    payload: Payload
    priority: Priority = Priority.STANDARD
    pickup_at: AwareDatetime | None = Field(
        default=None,
        description="When the package will be ready for pickup (ISO 8601 with timezone). "
        "Omit for as soon as possible.",
    )

    @model_validator(mode="after")
    def _distinct_points(self) -> Self:
        p, d = self.pickup, self.dropoff
        if haversine_m(p.lat, p.lng, d.lat, d.lng) < MIN_DISTANCE_M:
            raise ValueError(f"pickup and dropoff must be at least {MIN_DISTANCE_M} m apart")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "pickup": {"lat": 32.7843, "lng": -96.7837, "address": "Deep Ellum, Dallas"},
                    "dropoff": {"lat": 32.8120, "lng": -96.7520, "address": "Lakewood, Dallas"},
                    "payload": {
                        "category": "medical",
                        "weight_kg": 0.4,
                        "length_cm": 15,
                        "width_cm": 10,
                        "height_cm": 8,
                        "temperature_controlled": True,
                        "prescription": True,
                        "description": "Insulin pens",
                    },
                    "priority": "standard",
                }
            ]
        }
    }


class Reason(BaseModel):
    code: str
    message: str


class PriceLineItem(BaseModel):
    item: str
    amount_cents: int


class PriceOut(BaseModel):
    amount_cents: int
    currency: str
    breakdown: list[PriceLineItem]


class WeatherOut(BaseModel):
    wind_speed_mps: float
    wind_gust_mps: float
    precipitation_mm_per_h: float
    visibility_km: float
    temperature_c: float


class QuoteOut(BaseModel):
    id: str
    object: Literal["quote"] = "quote"
    feasible: bool
    reasons: list[Reason] = Field(description="Why the delivery can't happen (empty if feasible).")
    requirements: list[str] = Field(
        description="What the delivery must include, e.g. `recipient_pin` for prescriptions."
    )
    price: PriceOut | None
    eta_seconds: int | None = Field(description="Seconds from quote creation to drop-off.")
    estimated_pickup_at: datetime | None
    estimated_dropoff_at: datetime | None
    distance_m: int
    cruise_altitude_ft: int | None
    pickup: Location
    dropoff: Location
    payload: Payload
    priority: Priority
    requested_pickup_at: datetime | None
    weather: WeatherOut | None
    expires_at: datetime
    created_at: datetime

    @classmethod
    def from_model(cls, q: Quote) -> "QuoteOut":
        price = None
        if q.price_cents is not None:
            price = PriceOut(
                amount_cents=q.price_cents,
                currency=q.currency,
                breakdown=[PriceLineItem(**li) for li in q.price_breakdown],
            )
        return cls(
            id=q.id,
            feasible=q.feasible,
            reasons=[Reason(**r) for r in q.infeasible_reasons],
            requirements=q.requirements,
            price=price,
            eta_seconds=q.eta_seconds,
            estimated_pickup_at=q.estimated_pickup_at,
            estimated_dropoff_at=q.estimated_dropoff_at,
            distance_m=round(q.distance_m),
            cruise_altitude_ft=q.cruise_altitude_ft,
            pickup=q.pickup,
            dropoff=q.dropoff,
            payload=q.payload,
            priority=q.priority,
            requested_pickup_at=q.requested_pickup_at,
            weather=WeatherOut(**q.weather) if q.weather else None,
            expires_at=q.expires_at,
            created_at=q.created_at,
        )
