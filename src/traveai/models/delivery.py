import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm.attributes import flag_modified

from traveai.domain.delivery_status import DeliveryStatus, ensure_transition, event_type_for
from traveai.domain.enums import CustodyAction, FailureKind, Priority
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, str_enum, utcnow
from traveai.models.drop_zone import DropZone
from traveai.models.event import Event
from traveai.models.merchant import Merchant
from traveai.models.mission import Mission
from traveai.models.mixins import PayloadMixin, RouteMixin
from traveai.models.quote import Quote
from traveai.security import hash_pin, verify_pin

_REASON_STATUSES = {DeliveryStatus.ABORTED, DeliveryStatus.FAILED, DeliveryStatus.CANCELED}
CUSTODY_EVENT_TYPE = "delivery.custody"


class Delivery(TimestampMixin, RouteMixin, PayloadMixin, Base):
    """A merchant's order to move a payload from pickup to dropoff by drone."""

    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("del"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("quotes.id"), unique=True)
    dropoff_zone_id: Mapped[str | None] = mapped_column(ForeignKey("drop_zones.id"))
    status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus), default=DeliveryStatus.SCHEDULED, index=True
    )
    priority: Mapped[Priority] = mapped_column(str_enum(Priority), default=Priority.STANDARD)
    price_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="usd")
    # Secret part of the public tracking link (/t/<token>) shared with the recipient.
    # 192 random bits: unguessable. Kept retrievable so merchants can resend the link.
    tracking_token: Mapped[str] = mapped_column(
        String(64), unique=True, default=lambda: secrets.token_urlsafe(24)
    )
    # The merchant's own order number, so they can match our deliveries to their orders.
    external_reference: Mapped[str | None] = mapped_column(String(64), index=True)

    # Copied from the quote, e.g. ["chain_of_custody", "recipient_pin"]
    requirements: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    estimated_pickup_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    estimated_dropoff_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    recipient_name: Mapped[str | None] = mapped_column(String(100))
    recipient_phone: Mapped[str | None] = mapped_column(String(32))
    # Prescriptions: recipient must enter this PIN at drop-off. Only a salted hash is stored.
    recipient_pin_hash: Mapped[str | None] = mapped_column(String(128))
    pin_failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    failure_reason: Mapped[str | None] = mapped_column(String(200))
    # Test mode: a failure to force on the next mission flown for this delivery.
    test_failure: Mapped[FailureKind | None] = mapped_column(str_enum(FailureKind))

    # Proof of delivery
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_lat: Mapped[float | None] = mapped_column(Float)
    delivered_lng: Mapped[float | None] = mapped_column(Float)
    proof_photo_url: Mapped[str | None] = mapped_column(String(500))

    merchant: Mapped[Merchant] = relationship()
    quote: Mapped[Quote | None] = relationship(back_populates="delivery")
    dropoff_zone: Mapped[DropZone | None] = relationship()
    missions: Mapped[list[Mission]] = relationship(
        back_populates="delivery", order_by=Mission.created_at
    )
    events: Mapped[list[Event]] = relationship(order_by=Event.seq, cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any) -> None:
        # Column defaults only apply on INSERT; set them now so the state machine works on new,
        # unsaved deliveries too.
        kwargs.setdefault("status", DeliveryStatus.SCHEDULED)
        kwargs.setdefault("priority", Priority.STANDARD)
        super().__init__(**kwargs)

    def set_recipient_pin(self, pin: str) -> None:
        self.recipient_pin_hash = hash_pin(pin)

    def check_recipient_pin(self, pin: str) -> bool:
        return self.recipient_pin_hash is not None and verify_pin(pin, self.recipient_pin_hash)

    @property
    def pin_required(self) -> bool:
        return "recipient_pin" in self.requirements

    def transition_to(
        self,
        target: DeliveryStatus,
        *,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
        at: datetime | None = None,
    ) -> Event:
        """Move to `target` if the state machine allows it, and record an event.

        Raises InvalidTransitionError for illegal moves (e.g. delivered -> airborne).
        """
        previous = self.status
        ensure_transition(previous, target)
        self.status = target
        self.updated_at = at or utcnow()  # same clock as the event below
        # Even if the value is unchanged, keep it: otherwise the column's automatic
        # "updated now" (wall clock) would replace it on save.
        flag_modified(self, "updated_at")
        if target in _REASON_STATUSES:
            self.failure_reason = reason
        event = Event(
            merchant_id=self.merchant_id,
            type=event_type_for(target),
            data={"from": previous.value, "to": target.value, "reason": reason, **(data or {})},
            created_at=at or utcnow(),
        )
        self.events.append(event)
        return event

    def record_custody(
        self,
        action: CustodyAction,
        *,
        holder: str,
        lat: float | None = None,
        lng: float | None = None,
        note: str | None = None,
        at: datetime | None = None,
    ) -> Event:
        """Log a hand-off of the physical package (who has it now, and where).

        Required for medical payloads, so there is an audit trail from pharmacy to patient.
        """
        event = Event(
            merchant_id=self.merchant_id,
            type=CUSTODY_EVENT_TYPE,
            data={"action": action.value, "holder": holder, "lat": lat, "lng": lng, "note": note},
            created_at=at or utcnow(),
        )
        self.events.append(event)
        return event

    @property
    def custody_log(self) -> list[Event]:
        return [e for e in self.events if e.type == CUSTODY_EVENT_TYPE]
