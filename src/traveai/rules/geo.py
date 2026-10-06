import math
from dataclasses import dataclass

from shapely.geometry import LineString, Point

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass(frozen=True)
class LocalProjection:
    """Flat x/y meters around a reference point. Accurate to well under 1% across a metro area,
    which is all a city delivery network needs, and lets Shapely work in meters."""

    ref_lat: float
    ref_lng: float

    @property
    def _m_per_deg_lat(self) -> float:
        return math.pi * EARTH_RADIUS_M / 180

    @property
    def _m_per_deg_lng(self) -> float:
        return self._m_per_deg_lat * math.cos(math.radians(self.ref_lat))

    def to_xy(self, lat: float, lng: float) -> tuple[float, float]:
        return (
            (lng - self.ref_lng) * self._m_per_deg_lng,
            (lat - self.ref_lat) * self._m_per_deg_lat,
        )

    def to_latlng(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.ref_lat + y / self._m_per_deg_lat,
            self.ref_lng + x / self._m_per_deg_lng,
        )

    def point(self, lat: float, lng: float) -> Point:
        return Point(self.to_xy(lat, lng))

    def line(self, *latlngs: tuple[float, float]) -> LineString:
        return LineString([self.to_xy(lat, lng) for lat, lng in latlngs])
