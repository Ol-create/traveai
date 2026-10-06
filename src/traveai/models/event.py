from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from traveai.ids import new_id
from traveai.models.base import Base, UTCDateTime, utcnow


class Event(Base):
    """Something that happened, e.g. `delivery.airborne`. Source for webhooks and audit trail
    (including the chain of custody for medical deliveries)."""

    __tablename__ = "events"

    # Auto-increment sequence gives a strict order, even for events in the same millisecond.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(40), unique=True, default=lambda: new_id("evt"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    delivery_id: Mapped[str | None] = mapped_column(ForeignKey("deliveries.id"), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
