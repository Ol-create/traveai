"""Weather: conditions at a place and time, and the go/no-go rules for flying in them.

The simulated provider is deterministic (same place + hour = same weather), so quotes and
tests are reproducible. A real provider (e.g. the free NOAA api.weather.gov) can implement the
same `WeatherProvider` protocol later.
"""

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from traveai.rules.codes import RuleCode, Violation
from traveai.rules.config import RulesConfig


@dataclass(frozen=True)
class WeatherConditions:
    wind_speed_mps: float
    wind_gust_mps: float
    precipitation_mm_per_h: float
    visibility_km: float
    temperature_c: float

    @classmethod
    def worst(cls, *conditions: "WeatherConditions") -> "WeatherConditions":
        """Combine readings along a route into the worst case for each measure."""
        return cls(
            wind_speed_mps=max(c.wind_speed_mps for c in conditions),
            wind_gust_mps=max(c.wind_gust_mps for c in conditions),
            precipitation_mm_per_h=max(c.precipitation_mm_per_h for c in conditions),
            visibility_km=min(c.visibility_km for c in conditions),
            temperature_c=max(conditions, key=lambda c: abs(c.temperature_c - 20)).temperature_c,
        )


CALM = WeatherConditions(
    wind_speed_mps=3.0,
    wind_gust_mps=4.5,
    precipitation_mm_per_h=0.0,
    visibility_km=16.0,
    temperature_c=22.0,
)


class WeatherProvider(Protocol):
    def conditions_at(self, lat: float, lng: float, when: datetime) -> WeatherConditions: ...


@dataclass(frozen=True)
class FixedWeatherProvider:
    """Same weather everywhere. For tests and forced sandbox scenarios."""

    conditions: WeatherConditions = CALM

    def conditions_at(self, lat: float, lng: float, when: datetime) -> WeatherConditions:
        return self.conditions


@dataclass(frozen=True)
class SimulatedWeatherProvider:
    """Plausible, repeatable weather: mostly flyable, with occasional wind and rain.

    Weather is constant within a ~25 km grid cell for one clock hour.
    """

    seed: str = "traveai"
    windy_chance: float = 0.10
    rain_chance: float = 0.08

    def conditions_at(self, lat: float, lng: float, when: datetime) -> WeatherConditions:
        cell = f"{self.seed}|{round(lat * 4)}|{round(lng * 4)}|{when.strftime('%Y-%m-%dT%H')}"
        rng = random.Random(hashlib.sha256(cell.encode()).digest())

        wind = rng.uniform(10.0, 16.0) if rng.random() < self.windy_chance else rng.uniform(1, 7)
        gust = wind * rng.uniform(1.2, 1.6)
        raining = rng.random() < self.rain_chance
        precip = rng.uniform(0.5, 8.0) if raining else 0.0
        visibility = rng.uniform(2.5, 9.0) if precip > 4 else rng.uniform(12.0, 16.0)
        # Rough Dallas seasonal temperature: ~9 C in January, ~30 C in July.
        seasonal = 19.5 - 10.5 * math.cos(2 * math.pi * (when.timetuple().tm_yday - 15) / 365)
        temperature = seasonal + rng.uniform(-4, 4)

        return WeatherConditions(
            wind_speed_mps=round(wind, 1),
            wind_gust_mps=round(gust, 1),
            precipitation_mm_per_h=round(precip, 1),
            visibility_km=round(visibility, 1),
            temperature_c=round(temperature, 1),
        )


def check_weather(
    weather: WeatherConditions, *, max_wind_mps: float, config: RulesConfig
) -> list[Violation]:
    violations: list[Violation] = []
    if weather.wind_gust_mps > max_wind_mps:
        violations.append(
            Violation(
                RuleCode.WIND_TOO_HIGH,
                f"Wind gusts {weather.wind_gust_mps} m/s exceed this drone's "
                f"{max_wind_mps} m/s limit",
            )
        )
    if weather.precipitation_mm_per_h > config.max_precipitation_mm_per_h:
        violations.append(
            Violation(
                RuleCode.PRECIPITATION_TOO_HEAVY,
                f"Rain {weather.precipitation_mm_per_h} mm/h exceeds "
                f"{config.max_precipitation_mm_per_h} mm/h",
            )
        )
    if weather.visibility_km < config.min_visibility_km:
        violations.append(
            Violation(
                RuleCode.VISIBILITY_TOO_LOW,
                f"Visibility {weather.visibility_km} km is below the Part 107 minimum "
                f"of 3 statute miles",
            )
        )
    if not config.min_temperature_c <= weather.temperature_c <= config.max_temperature_c:
        violations.append(
            Violation(
                RuleCode.TEMPERATURE_OUT_OF_RANGE,
                f"Temperature {weather.temperature_c} C is outside the safe battery range",
            )
        )
    return violations
