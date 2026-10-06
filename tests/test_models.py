from datetime import UTC, datetime, timedelta

import pytest

from traveai.domain.delivery_status import DeliveryStatus, InvalidTransitionError
from traveai.domain.enums import DropMethod, MissionStatus, PayloadCategory
from traveai.models import Delivery, Event, Mission, Quote, Vehicle
from traveai.models.base import utcnow
from traveai.schemas.location import Location
from traveai.schemas.payload import Payload

PICKUP = Location(lat=32.7843, lng=-96.7837, address="Pickup St, Dallas, TX")
DROPOFF = Location(lat=32.8120, lng=-96.7520, address="Dropoff Ave, Dallas, TX")
INSULIN = Payload(
    category=PayloadCategory.MEDICAL,
    weight_kg=0.4,
    length_cm=15,
    width_cm=10,
    height_cm=8,
    temperature_controlled=True,
    prescription=True,
)


def make_delivery(merchant) -> Delivery:
    d = Delivery(merchant_id=merchant.id, price_cents=1299)
    d.pickup = PICKUP
    d.dropoff = DROPOFF
    d.payload = INSULIN
    return d


def test_delivery_roundtrip_keeps_payload_and_route(session, merchant):
    session.add(make_delivery(merchant))
    session.commit()
    session.expire_all()

    loaded = session.query(Delivery).one()
    assert loaded.id.startswith("del_")
    assert loaded.status == DeliveryStatus.SCHEDULED
    assert loaded.payload == INSULIN
    assert loaded.pickup == PICKUP and loaded.dropoff == DROPOFF
    assert loaded.created_at.tzinfo is not None  # UTC-aware even on SQLite


def test_transition_records_ordered_events(session, merchant):
    d = make_delivery(merchant)
    session.add(d)
    for status in (DeliveryStatus.ASSIGNED, DeliveryStatus.PICKING_UP, DeliveryStatus.AIRBORNE):
        d.transition_to(status)
    d.transition_to(DeliveryStatus.ABORTED, reason="wind_gust_exceeds_limit")
    session.commit()
    session.expire_all()

    events = session.query(Event).order_by(Event.seq).all()
    assert [e.type for e in events] == [
        "delivery.assigned",
        "delivery.picking_up",
        "delivery.airborne",
        "delivery.aborted",
    ]
    assert events[-1].data == {
        "from": "airborne",
        "to": "aborted",
        "reason": "wind_gust_exceeds_limit",
    }
    assert all(e.delivery_id == d.id and e.merchant_id == merchant.id for e in events)
    assert session.get(Delivery, d.id).failure_reason == "wind_gust_exceeds_limit"


def test_illegal_transition_leaves_delivery_unchanged(session, merchant):
    d = make_delivery(merchant)
    with pytest.raises(InvalidTransitionError):
        d.transition_to(DeliveryStatus.DELIVERED)
    assert d.status == DeliveryStatus.SCHEDULED
    assert d.events == []


def test_recipient_pin_is_hashed(merchant):
    d = make_delivery(merchant)
    d.set_recipient_pin("4821")
    assert d.recipient_pin_hash != "4821"
    assert d.check_recipient_pin("4821")
    assert not d.check_recipient_pin("0000")


def test_quote_expiry(merchant):
    q = Quote(
        merchant_id=merchant.id,
        feasible=True,
        distance_m=4200,
        expires_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert not q.is_expired(now=q.expires_at - timedelta(seconds=1))
    assert q.is_expired(now=q.expires_at)


def test_delivery_links_quote_and_mission(session, merchant):
    quote = Quote(
        merchant_id=merchant.id,
        feasible=True,
        price_cents=1299,
        eta_seconds=600,
        distance_m=4200,
        expires_at=utcnow() + timedelta(minutes=5),
    )
    quote.pickup, quote.dropoff, quote.payload = PICKUP, DROPOFF, INSULIN
    drone = Vehicle(
        call_sign="TST-1",
        model="QuadLift M2-Thermo",
        max_payload_kg=2.0,
        max_range_km=14,
        cruise_speed_mps=20,
        temperature_controlled=True,
        drop_method=DropMethod.WINCH,
        lat=PICKUP.lat,
        lng=PICKUP.lng,
        base_lat=PICKUP.lat,
        base_lng=PICKUP.lng,
    )
    delivery = make_delivery(merchant)
    delivery.quote = quote
    session.add_all([quote, drone, delivery])
    session.flush()
    session.add(Mission(delivery_id=delivery.id, vehicle_id=drone.id))
    session.commit()
    session.expire_all()

    loaded = session.get(Delivery, delivery.id)
    assert loaded.quote.id == quote.id
    assert quote.delivery.id == delivery.id
    assert loaded.missions[0].vehicle.call_sign == "TST-1"
    assert loaded.missions[0].status == MissionStatus.PLANNED
    assert loaded.missions[0].cruise_altitude_ft <= 400  # Part 107 ceiling


def test_naive_datetime_is_rejected(session, merchant):
    q = Quote(
        merchant_id=merchant.id,
        feasible=False,
        distance_m=1,
        expires_at=datetime(2026, 1, 1),  # no timezone
    )
    q.pickup, q.dropoff, q.payload = PICKUP, DROPOFF, INSULIN
    session.add(q)
    with pytest.raises(Exception, match="Naive datetime"):
        session.commit()
