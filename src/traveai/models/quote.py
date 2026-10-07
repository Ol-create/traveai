from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from traveai.domain.enums import Priority
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, str_enum, utcnow
from traveai.models.mixins import PayloadMixin, RouteMixin

if TYPE_CHECKING:
    from traveai.models.delivery import Delivery


class Quote(TimestampMixin, RouteMixin, PayloadMixin, Base):
    """Price + ETA + feasibility for a proposed delivery. Short-lived."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("quo"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    priority: Mapped[Priority] = mapped_column(str_enum(Priority), default=Priority.STANDARD)

    feasible: Mapped[bool]
    # Why not feasible: [{"code": "wind_too_high", "message": "Wind gusts ..."}, ...]
    infeasible_reasons: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    # What an accepted delivery must satisfy, e.g. ["recipient_pin", "chain_of_custody"]
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list)

    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    # [{"item": "base_fee", "amount_cents": 499}, ...]
    price_breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    distance_m: Mapped[float] = mapped_column(Float)
    cruise_altitude_ft: Mapped[int | None] = mapped_column(Integer)
    requested_pickup_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    estimated_pickup_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    estimated_dropoff_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    eta_seconds: Mapped[int | None] = mapped_column(Integer)  # from quote creation to drop-off
    # Weather snapshot the quote was based on.
    weather: Mapped[dict[str, float] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    delivery: Mapped["Delivery | None"] = relationship(back_populates="quote")

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or utcnow()) >= self.expires_at
