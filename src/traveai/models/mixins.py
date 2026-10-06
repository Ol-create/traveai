from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from traveai.domain.enums import PayloadCategory
from traveai.models.base import str_enum
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload


class RouteMixin:
    """Pickup and dropoff points, shared by quotes and deliveries."""

    pickup_lat: Mapped[float] = mapped_column(Float)
    pickup_lng: Mapped[float] = mapped_column(Float)
    pickup_address: Mapped[str | None] = mapped_column(String(300))
    dropoff_lat: Mapped[float] = mapped_column(Float)
    dropoff_lng: Mapped[float] = mapped_column(Float)
    dropoff_address: Mapped[str | None] = mapped_column(String(300))

    @property
    def pickup(self) -> Location:
        return Location(lat=self.pickup_lat, lng=self.pickup_lng, address=self.pickup_address)

    @pickup.setter
    def pickup(self, loc: Location) -> None:
        self.pickup_lat, self.pickup_lng, self.pickup_address = loc.lat, loc.lng, loc.address

    @property
    def dropoff(self) -> Location:
        return Location(lat=self.dropoff_lat, lng=self.dropoff_lng, address=self.dropoff_address)

    @dropoff.setter
    def dropoff(self, loc: Location) -> None:
        self.dropoff_lat, self.dropoff_lng, self.dropoff_address = loc.lat, loc.lng, loc.address


class PayloadMixin:
    """Payload stored as flat columns (queryable), exposed as a validated `Payload`."""

    payload_category: Mapped[PayloadCategory] = mapped_column(str_enum(PayloadCategory))
    payload_weight_kg: Mapped[float] = mapped_column(Float)
    payload_length_cm: Mapped[float] = mapped_column(Float)
    payload_width_cm: Mapped[float] = mapped_column(Float)
    payload_height_cm: Mapped[float] = mapped_column(Float)
    payload_temperature_controlled: Mapped[bool] = mapped_column(default=False)
    payload_fragile: Mapped[bool] = mapped_column(default=False)
    payload_prescription: Mapped[bool] = mapped_column(default=False)
    payload_description: Mapped[str | None] = mapped_column(String(200))

    @property
    def payload(self) -> Payload:
        return Payload(
            category=self.payload_category,
            weight_kg=self.payload_weight_kg,
            length_cm=self.payload_length_cm,
            width_cm=self.payload_width_cm,
            height_cm=self.payload_height_cm,
            temperature_controlled=self.payload_temperature_controlled,
            fragile=self.payload_fragile,
            prescription=self.payload_prescription,
            description=self.payload_description,
        )

    @payload.setter
    def payload(self, p: Payload) -> None:
        self.payload_category = p.category
        self.payload_weight_kg = p.weight_kg
        self.payload_length_cm = p.length_cm
        self.payload_width_cm = p.width_cm
        self.payload_height_cm = p.height_cm
        self.payload_temperature_controlled = p.temperature_controlled
        self.payload_fragile = p.fragile
        self.payload_prescription = p.prescription
        self.payload_description = p.description
