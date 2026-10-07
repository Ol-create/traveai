"""Shared FastAPI dependencies. Each one can be overridden in tests (app.dependency_overrides)."""

from datetime import datetime
from functools import lru_cache

from traveai.models.base import utcnow
from traveai.rules.airspace import AirspaceMap, default_airspace
from traveai.rules.config import RulesConfig
from traveai.rules.weather import SimulatedWeatherProvider, WeatherProvider


def get_now() -> datetime:
    return utcnow()


@lru_cache
def get_weather_provider() -> WeatherProvider:
    return SimulatedWeatherProvider()


def get_airspace() -> AirspaceMap:
    return default_airspace()


@lru_cache
def get_rules_config() -> RulesConfig:
    return RulesConfig()
