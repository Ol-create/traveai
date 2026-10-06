from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from traveai.domain.enums import DropZoneKind
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, str_enum


class DropZone(TimestampMixin, Base):
    """A verified spot where a drone can lower or land a package (the "last 10 meters")."""

    __tablename__ = "drop_zones"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dz"))
    label: Mapped[str] = mapped_column(String(120))
    kind: Mapped[DropZoneKind] = mapped_column(str_enum(DropZoneKind))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[float] = mapped_column(Float, default=3.0)
    address: Mapped[str | None] = mapped_column(String(300))
    verified: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(String(500))
