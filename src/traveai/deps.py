"""Shared FastAPI dependencies. Each one can be overridden in tests (app.dependency_overrides)."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime
from functools import lru_cache

from sqlalchemy.orm import Session

from traveai.config import get_settings
from traveai.db import get_sessionmaker
from traveai.models.base import utcnow
from traveai.rules.airspace import AirspaceMap, default_airspace
from traveai.rules.config import RulesConfig
from traveai.rules.weather import SimulatedWeatherProvider, WeatherProvider
from traveai.sim.simulator import Simulator
from traveai.workers import build_simulator


def get_now() -> datetime:
    return utcnow()


@lru_cache
def get_weather_provider() -> WeatherProvider:
    return SimulatedWeatherProvider()


def get_airspace() -> AirspaceMap:
    return default_airspace()


@lru_cache
def get_rules_config() -> RulesConfig:
    return RulesConfig(allow_night_operations=get_settings().allow_night_operations)


def get_simulator() -> Simulator:
    """The simulator the API uses for drone hand-offs (same rules as the background runner)."""
    return build_simulator(get_settings())


# --- for long-lived responses (live tracking streams) -----------------------------------------
# A stream outlives its request's DB session, so it opens short sessions of its own, reads the
# clock fresh on each update, and waits between updates. Tests swap all three.


def get_session_factory() -> Callable[[], AbstractContextManager[Session]]:
    return get_sessionmaker()


def get_clock() -> Callable[[], datetime]:
    return utcnow


def get_sleep() -> Callable[[float], Awaitable[None]]:
    return asyncio.sleep
