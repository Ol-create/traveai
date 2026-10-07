from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from traveai.domain.delivery_status import DeliveryStatus
from traveai.domain.enums import FailureKind, Priority
from traveai.models import Delivery, Event
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

E164 = r"^\+[1-9]\d{6,14}$"
PIN = r"^\d{4,8}$"


class Recipient(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(
        default=None, pattern=E164, description="E.164 format, e.g. +12145550123"
    )


class DeliveryCreate(BaseModel):
    quote_id: str
    recipient: Recipient = Field(default_factory=Recipient)
    recipient_pin: str | None = Field(
        default=None,
        pattern=PIN,
        description="PIN the recipient enters at drop-off. Generated for prescriptions if "
        "omitted; setting one adds PIN check to any delivery.",
    )
    dropoff_zone_id: str | None = Field(default=None, description="A verified drop zone.")
    external_reference: str | None = Field(
        default=None, max_length=64, description="Your order number."
    )


class DeliveryCancel(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class PriceOut(BaseModel):
    amount_cents: int
    currency: str


class ProofOut(BaseModel):
    delivered_at: datetime
    lat: float
    lng: float
    photo_url: str | None
    pin_verified: bool


class DeliveryOut(BaseModel):
    id: str
    object: Literal["delivery"] = "delivery"
    status: DeliveryStatus
    quote_id: str | None
    external_reference: str | None
    pickup: Location
    dropoff: Location
    dropoff_zone_id: str | None
    payload: Payload
    priority: Priority
    price: PriceOut
    requirements: list[str]
    recipient: Recipient
    estimated_pickup_at: datetime | None
    estimated_dropoff_at: datetime | None
    failure_reason: str | None
    proof: ProofOut | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, d: Delivery) -> "DeliveryOut":
        proof = None
        if d.delivered_at is not None:
            proof = ProofOut(
                delivered_at=d.delivered_at,
                lat=d.delivered_lat,
                lng=d.delivered_lng,
                photo_url=d.proof_photo_url,
                pin_verified=d.pin_required,
            )
        return cls(
            id=d.id,
            status=d.status,
            quote_id=d.quote_id,
            external_reference=d.external_reference,
            pickup=d.pickup,
            dropoff=d.dropoff,
            dropoff_zone_id=d.dropoff_zone_id,
            payload=d.payload,
            priority=d.priority,
            price=PriceOut(amount_cents=d.price_cents, currency=d.currency),
            requirements=d.requirements,
            recipient=Recipient(name=d.recipient_name, phone=d.recipient_phone),
            estimated_pickup_at=d.estimated_pickup_at,
            estimated_dropoff_at=d.estimated_dropoff_at,
            failure_reason=d.failure_reason,
            proof=proof,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class DeliveryCreated(DeliveryOut):
    recipient_pin: str | None = Field(
        description="Share this with the recipient. Shown only once; we store only a hash."
    )


class DeliveryList(BaseModel):
    object: Literal["list"] = "list"
    data: list[DeliveryOut]
    has_more: bool


class EventOut(BaseModel):
    id: str
    type: str
    data: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_model(cls, e: Event) -> "EventOut":
        return cls(id=e.id, type=e.type, data=e.data, created_at=e.created_at)


class EventList(BaseModel):
    object: Literal["list"] = "list"
    data: list[EventOut]


class HandoffRequest(BaseModel):
    pin: str = Field(pattern=PIN)


class InjectFailureRequest(BaseModel):
    kind: FailureKind


class AdvanceRequest(BaseModel):
    """Test mode only: move a delivery along, standing in for the flight simulator."""

    to: DeliveryStatus | None = Field(
        default=None, description="Target status. Omit to take the next happy-path step."
    )
    reason: str | None = Field(default=None, max_length=200)
    pin: str | None = Field(default=None, description="Recipient PIN, when delivering.")
