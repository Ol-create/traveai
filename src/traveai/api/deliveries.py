from datetime import datetime
from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from traveai.auth import AuthContext, CurrentAuth
from traveai.db import get_session
from traveai.deps import get_now, get_simulator
from traveai.domain.delivery_status import DeliveryStatus
from traveai.errors import ApiError
from traveai.models import Delivery
from traveai.schemas.delivery import (
    DeliveryCancel,
    DeliveryCreate,
    DeliveryCreated,
    DeliveryList,
    DeliveryOut,
    EventList,
    EventOut,
    HandoffRequest,
)
from traveai.services.deliveries import (
    BookingRequest,
    PinRejectedError,
    book_delivery,
    cancel_delivery,
)
from traveai.sim.simulator import Simulator

router = APIRouter(prefix="/v1/deliveries", tags=["deliveries"])

SessionDep = Annotated[Session, Depends(get_session)]
NowDep = Annotated[datetime, Depends(get_now)]


def get_owned_delivery(session: Session, auth: AuthContext, delivery_id: str) -> Delivery:
    delivery = session.get(Delivery, delivery_id)
    # Another merchant's delivery looks exactly like a missing one.
    if delivery is None or delivery.merchant_id != auth.merchant.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "delivery_not_found", "Delivery not found")
    return delivery


@router.post("", response_model=DeliveryCreated, status_code=status.HTTP_201_CREATED)
def create(body: DeliveryCreate, auth: CurrentAuth, session: SessionDep, now: NowDep):
    """Book a delivery from a feasible, unexpired quote. Each quote can be booked once.

    For prescriptions the response includes `recipient_pin`, shown only this once.
    """
    delivery, pin = book_delivery(
        session,
        auth.merchant.id,
        BookingRequest(
            quote_id=body.quote_id,
            recipient_name=body.recipient.name,
            recipient_phone=body.recipient.phone,
            recipient_pin=body.recipient_pin,
            dropoff_zone_id=body.dropoff_zone_id,
            external_reference=body.external_reference,
        ),
        now,
    )
    try:
        session.commit()
    except IntegrityError:
        # Two requests raced to book the same quote; the other one won.
        session.rollback()
        raise ApiError(
            status.HTTP_409_CONFLICT, "quote_already_used", "Quote was already booked"
        ) from None
    out = DeliveryOut.from_model(delivery)
    return DeliveryCreated(**out.model_dump(), recipient_pin=pin)


@router.get("", response_model=DeliveryList)
def list_deliveries(
    auth: CurrentAuth,
    session: SessionDep,
    status_: Annotated[list[DeliveryStatus] | None, Query(alias="status")] = None,
    external_reference: str | None = None,
    created_gte: datetime | None = None,
    created_lt: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    starting_after: Annotated[
        str | None, Query(description="Cursor: id of the last delivery on the previous page.")
    ] = None,
) -> DeliveryList:
    """Your deliveries, newest first. Repeat `status` to match several."""
    query = select(Delivery).where(Delivery.merchant_id == auth.merchant.id)
    if status_:
        query = query.where(Delivery.status.in_(status_))
    if external_reference:
        query = query.where(Delivery.external_reference == external_reference)
    if created_gte:
        query = query.where(Delivery.created_at >= created_gte)
    if created_lt:
        query = query.where(Delivery.created_at < created_lt)
    if starting_after:
        cursor = get_owned_delivery(session, auth, starting_after)
        query = query.where(
            or_(
                Delivery.created_at < cursor.created_at,
                and_(Delivery.created_at == cursor.created_at, Delivery.id < cursor.id),
            )
        )
    rows = session.scalars(
        query.order_by(Delivery.created_at.desc(), Delivery.id.desc()).limit(limit + 1)
    ).all()
    return DeliveryList(
        data=[DeliveryOut.from_model(d) for d in rows[:limit]], has_more=len(rows) > limit
    )


@router.get("/{delivery_id}", response_model=DeliveryOut)
def retrieve(delivery_id: str, auth: CurrentAuth, session: SessionDep) -> DeliveryOut:
    return DeliveryOut.from_model(get_owned_delivery(session, auth, delivery_id))


@router.get("/{delivery_id}/events", response_model=EventList)
def events(delivery_id: str, auth: CurrentAuth, session: SessionDep) -> EventList:
    """Full timeline: status changes, chain-of-custody hand-offs, failed PIN attempts."""
    delivery = get_owned_delivery(session, auth, delivery_id)
    return EventList(data=[EventOut.from_model(e) for e in delivery.events])


@router.post("/{delivery_id}/cancel", response_model=DeliveryOut)
def cancel(
    delivery_id: str, auth: CurrentAuth, session: SessionDep, body: DeliveryCancel | None = None
) -> DeliveryOut:
    """Cancel before takeoff. Once airborne a delivery can't be canceled."""
    delivery = get_owned_delivery(session, auth, delivery_id)
    cancel_delivery(delivery, body.reason if body else None)
    session.commit()
    return DeliveryOut.from_model(delivery)


@router.post("/{delivery_id}/handoff", response_model=DeliveryOut)
def handoff(
    delivery_id: str,
    body: HandoffRequest,
    auth: CurrentAuth,
    session: SessionDep,
    now: NowDep,
    sim: Annotated[Simulator, Depends(get_simulator)],
) -> DeliveryOut:
    """Release the package: the recipient's PIN, entered while the drone hovers at the drop-off
    (your app forwards it). 5 wrong PINs and the drone flies the package home."""
    delivery = get_owned_delivery(session, auth, delivery_id)
    try:
        sim.handoff(session, delivery, body.pin, now)
    except PinRejectedError:
        session.commit()  # keep the failed-attempt count
        raise
    session.commit()
    return DeliveryOut.from_model(delivery)


@router.get("/{delivery_id}/proof.svg", response_class=Response)
def proof_photo(delivery_id: str, auth: CurrentAuth, session: SessionDep) -> Response:
    """Simulated drop-off photo (there is no real camera yet)."""
    d = get_owned_delivery(session, auth, delivery_id)
    if d.delivered_at is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "no_proof", "Delivery has no proof yet")
    lines = [
        "SIMULATED DROP-OFF PHOTO",
        d.id,
        f"{d.delivered_lat:.5f}, {d.delivered_lng:.5f}",
        d.delivered_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "PIN verified" if d.pin_required else "No PIN required",
    ]
    text = "".join(
        f'<text x="20" y="{50 + i * 34}" font-size="{22 if i == 0 else 18}">{escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="240" '
        'font-family="monospace"><rect width="100%" height="100%" fill="#e8efe6"/>'
        f"{text}</svg>"
    )
    return Response(svg, media_type="image/svg+xml")
