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

# call_sign, model, hub, max_payload_kg, max_range_km, cruise_speed_mps, temp_controlled, drop
DEMO_VEHICLES = [
    ("TRV-101", "QuadLift M2", "deep_ellum", 2.5, 16.0, 22.0, False, DropMethod.WINCH),
    ("TRV-102", "QuadLift M2", "deep_ellum", 2.5, 16.0, 22.0, False, DropMethod.WINCH),
    ("TRV-103", "QuadLift M2-Thermo", "deep_ellum", 2.0, 14.0, 20.0, True, DropMethod.WINCH),
    ("TRV-201", "FixedWing V1", "medical_district", 1.8, 40.0, 30.0, True, DropMethod.PARACHUTE),
    ("TRV-202", "FixedWing V1", "medical_district", 1.8, 40.0, 30.0, True, DropMethod.PARACHUTE),
    ("TRV-203", "QuadLift M2", "medical_district", 2.5, 16.0, 22.0, False, DropMethod.LAND),
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

    for call_sign, model, hub, payload, range_km, speed, thermo, drop in DEMO_VEHICLES:
        if session.scalar(select(Vehicle).where(Vehicle.call_sign == call_sign)) is None:
            lat, lng = HUBS[hub]
            session.add(
                Vehicle(
                    call_sign=call_sign,
                    model=model,
                    max_payload_kg=payload,
                    max_range_km=range_km,
                    cruise_speed_mps=speed,
                    temperature_controlled=thermo,
                    drop_method=drop,
                    lat=lat,
                    lng=lng,
                    base_lat=lat,
                    base_lng=lng,
                )
            )

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
