from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from traveai.domain.enums import DropMethod, VehicleStatus
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, str_enum


class Vehicle(TimestampMixin, Base):
    """A (simulated) delivery drone in the fleet."""

    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("veh"))
    call_sign: Mapped[str] = mapped_column(String(32), unique=True)
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[VehicleStatus] = mapped_column(
        str_enum(VehicleStatus), default=VehicleStatus.IDLE, index=True
    )

    # Capabilities
    max_payload_kg: Mapped[float] = mapped_column(Float)
    max_range_km: Mapped[float] = mapped_column(Float)
    cruise_speed_mps: Mapped[float] = mapped_column(Float)
    temperature_controlled: Mapped[bool] = mapped_column(default=False)
    drop_method: Mapped[DropMethod] = mapped_column(str_enum(DropMethod))
    # Max wind gust it can safely fly in (fixed-wing drones tolerate more than quadcopters).
    max_wind_mps: Mapped[float] = mapped_column(Float, default=12.0, server_default="12.0")

    # Live state
    battery_pct: Mapped[float] = mapped_column(Float, default=100.0)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    altitude_ft: Mapped[float] = mapped_column(Float, default=0.0)

    # Home hub it returns to for charging
    base_lat: Mapped[float] = mapped_column(Float)
    base_lng: Mapped[float] = mapped_column(Float)
