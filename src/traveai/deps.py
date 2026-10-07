"""Shared FastAPI dependencies. Each one can be overridden in tests (app.dependency_overrides)."""

from datetime import datetime
from functools import lru_cache

from traveai.config import get_settings
from traveai.models.base import utcnow
from traveai.rules.airspace import AirspaceMap, default_airspace
from traveai.rules.config import RulesConfig
from traveai.rules.weather import SimulatedWeatherProvider, WeatherProvider
from traveai.sim.runner import build_simulator
from traveai.sim.simulator import Simulator


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
