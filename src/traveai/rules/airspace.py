"""Airspace map: airports (controlled airspace, needs LAANC), stadium event TFRs and restricted
areas (no-fly). Loaded from GeoJSON; Point features become circles of `radius_m`."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from pathlib import Path

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from traveai.rules.geo import LocalProjection


class ZoneKind(StrEnum):
    AIRPORT = "airport"  # controlled airspace: allowed with LAANC authorization, up to a ceiling
    STADIUM = "stadium"  # temporary flight restriction during events: no-fly while active
    RESTRICTED = "restricted"  # always no-fly
    TFR = "tfr"  # other temporary restriction: no-fly while active


NO_FLY_KINDS = frozenset({ZoneKind.STADIUM, ZoneKind.RESTRICTED, ZoneKind.TFR})


@dataclass(frozen=True)
class LaancRing:
    within_m: float
    ceiling_ft: int


@dataclass(frozen=True)
class AirspaceZone:
    id: str
    name: str
    kind: ZoneKind
    geometry: BaseGeometry  # in the map's local x/y meters
    center: Point | None = None  # for airports: the field itself
    laanc_ceilings: tuple[LaancRing, ...] = ()  # sorted by within_m
    active_windows: tuple[tuple[datetime, datetime], ...] = ()  # empty = always active

    @property
    def is_no_fly(self) -> bool:
        return self.kind in NO_FLY_KINDS

    def is_active(self, at: datetime) -> bool:
        if not self.active_windows:
            return True
        return any(start <= at < end for start, end in self.active_windows)

    def is_active_during(self, start: datetime, end: datetime) -> bool:
        """True if the zone is active at any moment of [start, end) - e.g. a whole flight."""
        if not self.active_windows:
            return True
        return any(w_start < end and start < w_end for w_start, w_end in self.active_windows)

    def laanc_ceiling_ft(self, route: LineString) -> int | None:
        """Lowest ceiling the route passes through. None if the route avoids this zone."""
        if self.center is None or not route.intersects(self.geometry):
            return None
        closest = route.distance(self.center)
        for ring in self.laanc_ceilings:
            if closest <= ring.within_m:
                return ring.ceiling_ft
        return None


@dataclass(frozen=True)
class AirspaceMap:
    projection: LocalProjection
    zones: tuple[AirspaceZone, ...] = field(default_factory=tuple)

    def active_zones(self, at: datetime) -> list[AirspaceZone]:
        return [z for z in self.zones if z.is_active(at)]

    def zones_active_during(self, start: datetime, end: datetime) -> list[AirspaceZone]:
        return [z for z in self.zones if z.is_active_during(start, end)]

    @classmethod
    def from_geojson(cls, data: dict) -> "AirspaceMap":
        features = data["features"]
        projection = _projection_for(features)
        zones = tuple(_zone_from_feature(f, projection) for f in features)
        return cls(projection=projection, zones=zones)

    @classmethod
    def from_file(cls, path: Path) -> "AirspaceMap":
        return cls.from_geojson(json.loads(path.read_text(encoding="utf-8")))


def _projection_for(features: list[dict]) -> LocalProjection:
    """Center the flat projection on the middle of all zones."""
    bounds = [shape(f["geometry"]).bounds for f in features]
    min_lng = min(b[0] for b in bounds)
    min_lat = min(b[1] for b in bounds)
    max_lng = max(b[2] for b in bounds)
    max_lat = max(b[3] for b in bounds)
    return LocalProjection(ref_lat=(min_lat + max_lat) / 2, ref_lng=(min_lng + max_lng) / 2)


def _zone_from_feature(feature: dict, projection: LocalProjection) -> AirspaceZone:
    props = feature["properties"]
    geom = transform(lambda lng, lat: projection.to_xy(lat, lng), shape(feature["geometry"]))
    center = None
    if isinstance(geom, Point):
        center = geom
        geom = geom.buffer(props["radius_m"], quad_segs=32)
    rings = tuple(
        sorted(
            (LaancRing(r["within_m"], r["ceiling_ft"]) for r in props.get("laanc_ceilings", [])),
            key=lambda r: r.within_m,
        )
    )
    windows = tuple(
        (datetime.fromisoformat(start), datetime.fromisoformat(end))
        for start, end in props.get("active_windows", [])
    )
    return AirspaceZone(
        id=props["id"],
        name=props["name"],
        kind=ZoneKind(props["kind"]),
        geometry=geom,
        center=center,
        laanc_ceilings=rings,
        active_windows=windows,
    )


@lru_cache
def default_airspace() -> AirspaceMap:
    """The bundled Dallas demo airspace."""
    path = resources.files("traveai.data") / "airspace" / "dallas.geojson"
    return AirspaceMap.from_geojson(json.loads(path.read_text(encoding="utf-8")))
