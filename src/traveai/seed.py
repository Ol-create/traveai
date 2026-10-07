"""Load demo data for local development: two merchants, a drone fleet and drop zones in Dallas, TX.

Run after migrations:  python -m traveai.seed
Safe to run more than once (existing rows are skipped).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.config import get_settings
from traveai.db import get_sessionmaker
from traveai.domain.enums import DropMethod, DropZoneKind, MerchantCategory
from traveai.models import ApiKey, DropZone, Merchant, Vehicle
from traveai.security import hash_secret

# Approximate locations, demo only.
HUBS = {
    "deep_ellum": (32.7843, -96.7837),
    "medical_district": (32.8125, -96.8400),
}

DEMO_MERCHANTS = [
    ("merch_demo_pharmacy", "Demo Pharmacy (Dallas)", MerchantCategory.PHARMACY),
    ("merch_demo_restaurant", "Demo Kitchen (Dallas)", MerchantCategory.RESTAURANT),
]

QUAD = dict(
    model="QuadLift M2",
    max_payload_kg=2.5,
    max_range_km=16.0,
    cruise_speed_mps=22.0,
    temperature_controlled=False,
    max_wind_mps=12.0,
)
QUAD_THERMO = dict(
    model="QuadLift M2-Thermo",
    max_payload_kg=2.0,
    max_range_km=14.0,
    cruise_speed_mps=20.0,
    temperature_controlled=True,
    max_wind_mps=11.0,
)
FIXED_WING = dict(
    model="FixedWing V1",
    max_payload_kg=1.8,
    max_range_km=40.0,
    cruise_speed_mps=30.0,
    temperature_controlled=True,
    max_wind_mps=15.0,
)

# call_sign, hub, specs, drop method
DEMO_VEHICLES = [
    ("TRV-101", "deep_ellum", QUAD, DropMethod.WINCH),
    ("TRV-102", "deep_ellum", QUAD, DropMethod.WINCH),
    ("TRV-103", "deep_ellum", QUAD_THERMO, DropMethod.WINCH),
    ("TRV-201", "medical_district", FIXED_WING, DropMethod.PARACHUTE),
    ("TRV-202", "medical_district", FIXED_WING, DropMethod.PARACHUTE),
    ("TRV-203", "medical_district", QUAD, DropMethod.LAND),
]

DEMO_DROP_ZONES = [
    ("Hospital helipad (East Dallas)", DropZoneKind.HOSPITAL_PAD, 32.7900, -96.7810, 6.0),
    ("Backyard (Lakewood)", DropZoneKind.YARD, 32.8120, -96.7520, 3.0),
    ("Parcel locker (Bishop Arts)", DropZoneKind.LOCKER, 32.7486, -96.8270, 2.0),
    ("Rooftop pad (Uptown)", DropZoneKind.ROOFTOP_PAD, 32.8010, -96.8010, 4.0),
]


def seed(session: Session) -> None:
    for merchant_id, name, category in DEMO_MERCHANTS:
        if session.get(Merchant, merchant_id) is None:
            session.add(Merchant(id=merchant_id, name=name, category=category))
    session.flush()

    for key, merchant_id in get_settings().seed_api_key_map.items():
        merchant = session.get(Merchant, merchant_id)
        if merchant is None:
            print(f"skip key for unknown merchant {merchant_id}")
            continue
        if session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_secret(key))) is None:
            merchant.issue_api_key(plaintext=key)

    for call_sign, hub, specs, drop in DEMO_VEHICLES:
        vehicle = session.scalar(select(Vehicle).where(Vehicle.call_sign == call_sign))
        if vehicle is None:
            lat, lng = HUBS[hub]
            vehicle = Vehicle(call_sign=call_sign, lat=lat, lng=lng, base_lat=lat, base_lng=lng)
            session.add(vehicle)
        # Refresh specs on every run, so spec changes reach existing dev databases.
        for field, value in specs.items():
            setattr(vehicle, field, value)
        vehicle.drop_method = drop

    for label, kind, lat, lng, radius in DEMO_DROP_ZONES:
        if session.scalar(select(DropZone).where(DropZone.label == label)) is None:
            session.add(
                DropZone(label=label, kind=kind, lat=lat, lng=lng, radius_m=radius, verified=True)
            )

    session.commit()


def main() -> None:
    with get_sessionmaker()() as session:
        seed(session)
        counts = {
            model.__name__: len(session.scalars(select(model)).all())
            for model in (Merchant, ApiKey, Vehicle, DropZone)
        }
    print("Seeded:", ", ".join(f"{n} {name}" for name, n in counts.items()))


if __name__ == "__main__":
    main()
