"""Mock LAANC (Low Altitude Authorization and Notification Capability).

In the real US system an approved provider submits the flight to the FAA, which approves it
near-instantly up to the ceiling on its UAS facility map grid. Here the grid is the
`laanc_ceilings` rings around each airport in the airspace map.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from shapely.geometry import LineString

from traveai.rules.airspace import AirspaceMap, ZoneKind


class LaancStatus(StrEnum):
    NOT_REQUIRED = "not_required"  # route stays outside controlled airspace
    APPROVED = "approved"
    DENIED = "denied"


@dataclass(frozen=True)
class LaancDecision:
    status: LaancStatus
    max_altitude_ft: int | None = None  # approved ceiling; fly at or below it
    reference_code: str | None = None
    airports: tuple[str, ...] = ()
    reason: str | None = None


def request_authorization(
    airspace: AirspaceMap,
    route: LineString,
    requested_altitude_ft: int,
    departure_at: datetime,
    *,
    min_altitude_ft: int,
) -> LaancDecision:
    """Ask for permission to fly `route` through controlled airspace.

    Approves at the lower of the requested altitude and the lowest grid ceiling on the route.
    Denies if that is below `min_altitude_ft` (too low to fly safely over a city).
    """
    ceilings: dict[str, int] = {}
    for zone in airspace.active_zones(departure_at):
        if zone.kind != ZoneKind.AIRPORT:
            continue
        ceiling = zone.laanc_ceiling_ft(route)
        if ceiling is not None:
            ceilings[zone.name] = ceiling

    if not ceilings:
        return LaancDecision(status=LaancStatus.NOT_REQUIRED)

    airports = tuple(sorted(ceilings))
    lowest_airport = min(ceilings, key=ceilings.__getitem__)
    lowest = ceilings[lowest_airport]
    if lowest < min_altitude_ft:
        return LaancDecision(
            status=LaancStatus.DENIED,
            max_altitude_ft=lowest,
            airports=airports,
            reason=f"Ceiling near {lowest_airport} is {lowest} ft (need {min_altitude_ft} ft)",
        )

    approved = min(requested_altitude_ft, lowest)
    digest = hashlib.sha256(
        f"{route.wkt}|{approved}|{departure_at.isoformat()}".encode()
    ).hexdigest()
    return LaancDecision(
        status=LaancStatus.APPROVED,
        max_altitude_ft=approved,
        reference_code=f"LAANC-SIM-{digest[:8].upper()}",
        airports=airports,
    )
