from datetime import UTC, datetime, timedelta

from traveai.rules.codes import RuleCode
from traveai.rules.config import RulesConfig
from traveai.rules.weather import (
    CALM,
    SimulatedWeatherProvider,
    WeatherConditions,
    check_weather,
)

NOON = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)


def codes(weather: WeatherConditions, max_wind_mps: float = 12.0) -> set[str]:
    return {v.code for v in check_weather(weather, max_wind_mps=max_wind_mps, config=RulesConfig())}


def test_calm_weather_is_flyable():
    assert codes(CALM) == set()


def test_each_limit():
    assert codes(WeatherConditions(9, 13, 0, 16, 22)) == {RuleCode.WIND_TOO_HIGH}
    assert codes(WeatherConditions(9, 13, 0, 16, 22), max_wind_mps=15) == set()
    assert codes(WeatherConditions(3, 4, 5.0, 16, 22)) == {RuleCode.PRECIPITATION_TOO_HEAVY}
    assert codes(WeatherConditions(3, 4, 0, 3.0, 22)) == {RuleCode.VISIBILITY_TOO_LOW}
    assert codes(WeatherConditions(3, 4, 0, 16, -15)) == {RuleCode.TEMPERATURE_OUT_OF_RANGE}


def test_worst_combines_readings():
    a = WeatherConditions(3, 5, 0.0, 16, 22)
    b = WeatherConditions(6, 9, 1.5, 8, -2)
    assert WeatherConditions.worst(a, b) == WeatherConditions(6, 9, 1.5, 8, -2)


def test_simulated_weather_is_repeatable_and_varied():
    sim = SimulatedWeatherProvider()
    assert sim.conditions_at(32.78, -96.80, NOON) == sim.conditions_at(32.78, -96.80, NOON)

    # Over a month of hours the simulation should produce both good and bad flying weather.
    hours = [NOON + timedelta(hours=h) for h in range(24 * 30)]
    readings = [sim.conditions_at(32.78, -96.80, t) for t in hours]
    grounded = [r for r in readings if codes(r)]
    assert 0.05 < len(grounded) / len(readings) < 0.40
    assert any(r.precipitation_mm_per_h > 0 for r in readings)


def test_different_seeds_give_different_weather():
    a = SimulatedWeatherProvider(seed="a").conditions_at(32.78, -96.80, NOON)
    b = SimulatedWeatherProvider(seed="b").conditions_at(32.78, -96.80, NOON)
    assert a != b
