import os
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from traveai.db import get_session, make_engine
from traveai.deps import (
    get_clock,
    get_now,
    get_session_factory,
    get_simulator,
    get_weather_provider,
)
from traveai.domain.enums import DropMethod, MerchantCategory
from traveai.main import create_app
from traveai.models import Base, Merchant, Vehicle
from traveai.portal.api import get_login_limiter
from traveai.ratelimit import MemoryRateLimiter, get_rate_limiter
from traveai.rules.airspace import default_airspace
from traveai.rules.weather import CALM, FixedWeatherProvider, WeatherConditions
from traveai.sim.simulator import SimContext, Simulator

TEST_MERCHANT_ID = "merch_unit_test"

# 1 pm CDT on an ordinary weekday: daylight, no stadium events.
WEEKDAY_1PM = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)

DEEP_ELLUM_HUB = (32.7843, -96.7837)
MEDICAL_DISTRICT_HUB = (32.8125, -96.8400)


@dataclass
class World:
    """Clock and weather the API sees during a test. Change them freely inside a test."""

    now: datetime = WEEKDAY_1PM
    weather: WeatherConditions = field(default=CALM)

    def conditions_at(self, lat: float, lng: float, when: datetime) -> WeatherConditions:
        """Lets the World act as a WeatherProvider that follows `world.weather`."""
        return self.weather


# Run the suite on Postgres (or any database) with e.g.
#   TRAVEAI_TEST_DATABASE_URL=postgresql+psycopg://user@localhost/traveai_test pytest
# Its tables are dropped and recreated for every test. Default: in-memory SQLite.
TEST_DATABASE_URL = os.environ.get("TRAVEAI_TEST_DATABASE_URL")


@pytest.fixture
def session() -> Iterator[Session]:
    """Fresh, empty database per test."""
    if TEST_DATABASE_URL:
        engine = make_engine(TEST_DATABASE_URL)
        Base.metadata.drop_all(engine)
    else:
        engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def merchant(session: Session) -> Merchant:
    m = Merchant(id=TEST_MERCHANT_ID, name="Unit Test Pharmacy", category=MerchantCategory.PHARMACY)
    session.add(m)
    session.commit()
    return m


@pytest.fixture
def api_key(session: Session, merchant: Merchant) -> str:
    key = merchant.issue_api_key(test=True)
    session.commit()
    return key


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(session: Session, world: World) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_now] = lambda: world.now
    app.dependency_overrides[get_weather_provider] = lambda: FixedWeatherProvider(world.weather)
    app.dependency_overrides[get_simulator] = lambda: Simulator(
        SimContext(weather=world, airspace=default_airspace())
    )
    # Streams share the test's session and clock (and tests can swap get_sleep).
    app.dependency_overrides[get_session_factory] = lambda: lambda: nullcontext(session)
    app.dependency_overrides[get_clock] = lambda: lambda: world.now
    # Fresh rate-limit counters per test (all tests share one client address).
    api_limiter, login_limiter = MemoryRateLimiter(600), MemoryRateLimiter(10)
    app.dependency_overrides[get_rate_limiter] = lambda: api_limiter
    app.dependency_overrides[get_login_limiter] = lambda: login_limiter
    return TestClient(app)


def make_vehicle(call_sign: str, hub: tuple[float, float], **specs) -> Vehicle:
    defaults = dict(
        model="QuadLift M2",
        max_payload_kg=2.5,
        max_range_km=16.0,
        cruise_speed_mps=22.0,
        temperature_controlled=False,
        max_wind_mps=12.0,
        drop_method=DropMethod.WINCH,
    )
    lat, lng = hub
    return Vehicle(
        call_sign=call_sign,
        lat=lat,
        lng=lng,
        base_lat=lat,
        base_lng=lng,
        **{**defaults, **specs},
    )


@pytest.fixture
def fleet(session: Session) -> dict[str, Vehicle]:
    """A small fleet like the demo seed: quads in Deep Ellum, fixed-wings in the Medical
    District."""
    vehicles = {
        "quad": make_vehicle("Q-1", DEEP_ELLUM_HUB),
        "thermo": make_vehicle(
            "Q-2",
            DEEP_ELLUM_HUB,
            model="QuadLift M2-Thermo",
            max_payload_kg=2.0,
            max_range_km=14.0,
            cruise_speed_mps=20.0,
            temperature_controlled=True,
            max_wind_mps=11.0,
        ),
        "wing": make_vehicle(
            "W-1",
            MEDICAL_DISTRICT_HUB,
            model="FixedWing V1",
            max_payload_kg=1.8,
            max_range_km=40.0,
            cruise_speed_mps=30.0,
            temperature_controlled=True,
            max_wind_mps=15.0,
            drop_method=DropMethod.PARACHUTE,
        ),
    }
    session.add_all(vehicles.values())
    session.commit()
    return vehicles
