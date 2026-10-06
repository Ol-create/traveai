from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from traveai.domain.enums import MissionStatus
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, str_enum
from traveai.models.vehicle import Vehicle
from traveai.rules.config import DEFAULT_CRUISE_ALTITUDE_FT

if TYPE_CHECKING:
    from traveai.models.delivery import Delivery


class Mission(TimestampMixin, Base):
    """One flight by one drone for one delivery. A retried delivery gets a new mission."""

    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mis"))
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"), index=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"), index=True)
    status: Mapped[MissionStatus] = mapped_column(
        str_enum(MissionStatus), default=MissionStatus.PLANNED
    )
    # [[lat, lng, altitude_ft], ...] from the hub to pickup to dropoff and back.
    waypoints: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    cruise_altitude_ft: Mapped[int] = mapped_column(Integer, default=DEFAULT_CRUISE_ALTITUDE_FT)
    planned_distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    abort_reason: Mapped[str | None] = mapped_column(String(200))

    delivery: Mapped["Delivery"] = relationship(back_populates="missions")
    vehicle: Mapped[Vehicle] = relationship()
