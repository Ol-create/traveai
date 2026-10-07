"""Route planning around no-fly zones.

Shortest path on a visibility graph: the nodes are start, end and the corners of every no-fly
zone (grown by a safety margin); an edge exists when the straight line between two nodes stays
out of every zone. Dijkstra picks the shortest chain. Good enough for a city with a handful of
zones; a production planner would add altitude, wind and terrain.
"""

import heapq
import math
from datetime import datetime

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from traveai.rules.airspace import AirspaceMap
from traveai.rules.geo import haversine_m

SAFETY_MARGIN_M = 150.0  # stay this far outside no-fly zone edges

LatLng = tuple[float, float]


def path_length_m(path: list[LatLng]) -> float:
    return sum(haversine_m(*a, *b) for a, b in zip(path, path[1:], strict=False))


def plan_route(
    airspace: AirspaceMap,
    start: LatLng,
    end: LatLng,
    *,
    window: tuple[datetime, datetime],
) -> list[LatLng] | None:
    """Return [start, ..., end] avoiding no-fly zones active during `window`, or None if the
    start or end is inside one (no detour can fix that)."""
    proj = airspace.projection
    zones = [z.geometry for z in airspace.zones_active_during(*window) if z.is_no_fly]
    a, b = Point(proj.to_xy(*start)), Point(proj.to_xy(*end))
    if any(z.contains(a) or z.contains(b) for z in zones):
        return None

    direct = LineString([a, b])
    if not any(direct.intersects(z) for z in zones):
        return [start, end]

    # Grow zones by the margin and merge overlaps. Then simplify the outline (circles have 128
    # corners) with a tolerance under the margin, so the path still keeps >= margin/2 clearance.
    grown = unary_union([z.buffer(SAFETY_MARGIN_M) for z in zones])
    grown = grown.simplify(SAFETY_MARGIN_M / 2)
    obstacles: list[Polygon] = list(getattr(grown, "geoms", [grown]))
    # Edges may run along an obstacle's boundary, so test against a slightly shrunken copy.
    blockers = [o.buffer(-1.0) for o in obstacles]

    nodes: list[tuple[float, float]] = [(a.x, a.y), (b.x, b.y)]
    for o in obstacles:
        nodes.extend(o.exterior.coords[:-1])

    def clear(i: int, j: int) -> bool:
        seg = LineString([nodes[i], nodes[j]])
        return not any(seg.intersects(blk) for blk in blockers)

    # Dijkstra from node 0 (start) to node 1 (end); edges are checked lazily.
    dist = {0: 0.0}
    prev: dict[int, int] = {}
    heap = [(0.0, 0)]
    done: set[int] = set()
    while heap:
        d, i = heapq.heappop(heap)
        if i in done:
            continue
        if i == 1:
            break
        done.add(i)
        for j in range(len(nodes)):
            if j in done or j == i or not clear(i, j):
                continue
            nd = d + math.dist(nodes[i], nodes[j])
            if nd < dist.get(j, math.inf):
                dist[j], prev[j] = nd, i
                heapq.heappush(heap, (nd, j))

    if 1 not in dist:
        return None  # boxed in; shouldn't happen with a few separate zones
    chain = [1]
    while chain[-1] != 0:
        chain.append(prev[chain[-1]])
    middle = [proj.to_latlng(*nodes[i]) for i in reversed(chain[1:-1])]
    return [start, *middle, end]
