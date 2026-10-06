"""Sun position, for Part 107's daylight / civil-twilight rule. No external API needed."""

import math
from datetime import UTC, datetime

# The sun is less than 6 degrees below the horizon during civil twilight.
CIVIL_TWILIGHT_ELEVATION_DEG = -6.0


def solar_elevation_deg(lat: float, lng: float, when: datetime) -> float:
    """Sun's angle above the horizon in degrees (NOAA approximation, accurate to ~0.5 deg)."""
    when = when.astimezone(UTC)
    hour = when.hour + when.minute / 60 + when.second / 3600
    gamma = 2 * math.pi / 365 * (when.timetuple().tm_yday - 1 + (hour - 12) / 24)

    eqtime_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    true_solar_minutes = hour * 60 + eqtime_min + 4 * lng
    hour_angle = math.radians(true_solar_minutes / 4 - 180)

    lat_r = math.radians(lat)
    cos_zenith = math.sin(lat_r) * math.sin(declination) + math.cos(lat_r) * math.cos(
        declination
    ) * math.cos(hour_angle)
    zenith = math.degrees(math.acos(max(-1.0, min(1.0, cos_zenith))))
    return 90.0 - zenith


def is_daylight_or_civil_twilight(lat: float, lng: float, when: datetime) -> bool:
    return solar_elevation_deg(lat, lng, when) >= CIVIL_TWILIGHT_ELEVATION_DEG
