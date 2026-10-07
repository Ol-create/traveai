"""Delivery lifecycle operations: book from a quote, cancel, and complete with proof."""

from dataclasses import dataclass
from datetime import datetime

from fastapi import status
from sqlalchemy.orm import Session

from traveai.domain.delivery_status import DeliveryStatus, can_transition, event_type_for
from traveai.domain.enums import CustodyAction
from traveai.errors import ApiError
from traveai.models import Delivery, DropZone, Event, Quote
from traveai.rules.codes import Requirement
from traveai.rules.geo import haversine_m
from traveai.security import generate_pin

DROP_ZONE_MAX_OFFSET_M = 100  # a chosen drop zone must be this close to the quoted drop-off
DEFAULT_DROP_TOLERANCE_M = 25  # how far from the drop-off point a drop still counts
MAX_PIN_ATTEMPTS = 5


@dataclass(frozen=True)
class BookingRequest:
    quote_id: str
    recipient_name: str | None = None
    recipient_phone: str | None = None
    recipient_pin: str | None = None
    dropoff_zone_id: str | None = None
    external_reference: str | None = None


def book_delivery(
    session: Session, merchant_id: str, req: BookingRequest, now: datetime
) -> tuple[Delivery, str | None]:
    """Create a delivery from a quote. Returns (delivery, recipient PIN in plain text if one
    was set). Each quote can be booked once, so retrying a request can't double-book."""
    quote = session.get(Quote, req.quote_id)
    if quote is None or quote.merchant_id != merchant_id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "quote_not_found", "Quote not found")
    if quote.delivery is not None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "quote_already_used",
            f"Quote was already booked as delivery {quote.delivery.id}",
        )
    if not quote.feasible:
        raise ApiError(
            status.HTTP_409_CONFLICT, "quote_not_feasible", "Quote is not feasible; see reasons"
        )
    if quote.is_expired(now):
        raise ApiError(
            status.HTTP_409_CONFLICT, "quote_expired", "Quote expired; request a new one"
        )

    if req.dropoff_zone_id is not None:
        zone = session.get(DropZone, req.dropoff_zone_id)
        if zone is None or not zone.verified:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid_drop_zone",
                "Drop zone not found or not verified",
            )
        offset = haversine_m(zone.lat, zone.lng, quote.dropoff_lat, quote.dropoff_lng)
        if offset > DROP_ZONE_MAX_OFFSET_M:
            raise ApiError(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "drop_zone_too_far",
                f"Drop zone is {offset:.0f} m from the quoted drop-off "
                f"(max {DROP_ZONE_MAX_OFFSET_M} m)",
            )

    requirements = list(quote.requirements)
    if req.recipient_pin and Requirement.RECIPIENT_PIN.value not in requirements:
        requirements.append(Requirement.RECIPIENT_PIN.value)  # merchant opted in

    delivery = Delivery(
        merchant_id=merchant_id,
        quote=quote,
        dropoff_zone_id=req.dropoff_zone_id,
        priority=quote.priority,
        price_cents=quote.price_cents,
        currency=quote.currency,
        requirements=sorted(requirements),
        estimated_pickup_at=quote.estimated_pickup_at,
        estimated_dropoff_at=quote.estimated_dropoff_at,
        external_reference=req.external_reference,
        recipient_name=req.recipient_name,
        recipient_phone=req.recipient_phone,
        created_at=now,
        updated_at=now,
    )
    delivery.pickup, delivery.dropoff, delivery.payload = quote.pickup, quote.dropoff, quote.payload

    pin = None
    if delivery.pin_required:
        pin = req.recipient_pin or generate_pin()
        delivery.set_recipient_pin(pin)

    delivery.events.append(
        Event(
            merchant_id=merchant_id,
            type=event_type_for(DeliveryStatus.SCHEDULED),
            data={"from": None, "to": DeliveryStatus.SCHEDULED.value, "quote_id": quote.id},
            created_at=now,
        )
    )
    session.add(delivery)
    return delivery, pin


def cancel_delivery(delivery: Delivery, reason: str | None) -> None:
    if not can_transition(delivery.status, DeliveryStatus.CANCELED):
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "cannot_cancel",
            f"A delivery that is '{delivery.status}' can't be canceled"
            + (" (in the air: it will be aborted instead)" if _in_air(delivery) else ""),
        )
    delivery.transition_to(DeliveryStatus.CANCELED, reason=reason or "canceled_by_merchant")


def _in_air(delivery: Delivery) -> bool:
    return delivery.status in {DeliveryStatus.AIRBORNE, DeliveryStatus.ARRIVING}


class PinRejectedError(ApiError):
    """Wrong or missing PIN. The failed attempt is recorded, so callers must commit first."""


def complete_delivery(
    delivery: Delivery,
    *,
    lat: float,
    lng: float,
    at: datetime,
    photo_url: str,
    pin: str | None = None,
) -> None:
    """Hand the package over: verify location and PIN, store proof, mark delivered."""
    if delivery.status != DeliveryStatus.ARRIVING:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "invalid_state",
            f"Only an arriving delivery can be completed (it is '{delivery.status}')",
        )

    tolerance = (
        delivery.dropoff_zone.radius_m if delivery.dropoff_zone else DEFAULT_DROP_TOLERANCE_M
    )
    offset = haversine_m(lat, lng, delivery.dropoff_lat, delivery.dropoff_lng)
    if offset > tolerance:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "drop_location_mismatch",
            f"Drop point is {offset:.0f} m from the drop-off (max {tolerance:.0f} m)",
        )

    if delivery.pin_required:
        if delivery.pin_failed_attempts >= MAX_PIN_ATTEMPTS:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "pin_locked",
                "Too many wrong PINs; the package will be returned",
            )
        if pin is None or not delivery.check_recipient_pin(pin):
            delivery.pin_failed_attempts += 1
            delivery.events.append(
                Event(
                    merchant_id=delivery.merchant_id,
                    type="delivery.pin_failed",
                    data={"attempts": delivery.pin_failed_attempts},
                    created_at=at,
                )
            )
            left = MAX_PIN_ATTEMPTS - delivery.pin_failed_attempts
            raise PinRejectedError(
                status.HTTP_403_FORBIDDEN, "invalid_pin", f"Wrong or missing PIN ({left} left)"
            )

    delivery.delivered_at = at
    delivery.delivered_lat = lat
    delivery.delivered_lng = lng
    delivery.proof_photo_url = photo_url
    if Requirement.CHAIN_OF_CUSTODY.value in delivery.requirements:
        delivery.record_custody(
            CustodyAction.DELIVERED_TO_RECIPIENT,
            holder=f"recipient:{delivery.recipient_name or 'unnamed'}",
            lat=lat,
            lng=lng,
            at=at,
        )
    delivery.transition_to(
        DeliveryStatus.DELIVERED,
        data={"lat": lat, "lng": lng, "pin_verified": delivery.pin_required},
        at=at,
    )
