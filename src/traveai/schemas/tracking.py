from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from traveai.domain.delivery_status import DeliveryStatus
from traveai.domain.enums import MissionPhase


class DronePosition(BaseModel):
    call_sign: str
    lat: float
    lng: float
    altitude_ft: float
    battery_pct: float


class Tracking(BaseModel):
    object: Literal["tracking"] = "tracking"
    delivery_id: str
    status: DeliveryStatus
    phase: MissionPhase | None = Field(
        description="What the drone is doing, while one is assigned."
    )
    drone: DronePosition | None
    remaining_m: float | None = Field(description="Distance left to the drop-off.")
    eta_seconds: int | None = Field(description="Live estimate of seconds until drop-off.")
    estimated_dropoff_at: datetime | None
    route: list[list[float]] = Field(
        description="Planned [lat, lng] points of the current flight: hub, pickup, drop-off, hub."
    )
    next_waypoint_index: int | None
    as_of: datetime
