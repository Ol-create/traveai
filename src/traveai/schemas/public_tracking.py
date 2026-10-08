from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from traveai.domain.delivery_status import DeliveryStatus
from traveai.domain.enums import PayloadCategory


class LatLng(BaseModel):
    lat: float
    lng: float


class PublicDrone(BaseModel):
    lat: float
    lng: float
    altitude_ft: float


class PublicTracking(BaseModel):
    """What a recipient may see. Deliberately minimal: no price, phone, order reference, notes,
    or (for medical orders) anything about the contents."""

    object: Literal["public_tracking"] = "public_tracking"
    merchant_name: str
    category: PayloadCategory
    status: DeliveryStatus
    eta_seconds: int | None
    estimated_dropoff_at: datetime | None
    pickup: LatLng
    dropoff: LatLng
    drone: PublicDrone | None = Field(description="Live position while the drone is out.")
    route: list[list[float]] = Field(description="Remaining pickup → drop-off path.")
    awaiting_pin: bool = Field(description="The drone is hovering at the drop-off for the PIN.")
    pin_attempts_left: int | None
    delivered_at: datetime | None
    proof_photo_url: str | None
    problem: str | None = Field(
        description="Why the delivery is delayed or failed, in plain words (if it is)."
    )
    as_of: datetime


class PublicHandoff(BaseModel):
    pin: str = Field(pattern=r"^\d{4,8}$", examples=["482913"])
