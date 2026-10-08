"""Data for the live map (dashboard): airspace, hubs, drop zones, drones and deliveries."""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from shapely.geometry import mapping
from shapely.ops import transform
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from traveai.auth import CurrentAuth
from traveai.db import get_session
from traveai.deps import get_airspace, get_now
from traveai.domain.delivery_status import TERMINAL_STATUSES
from traveai.models import Delivery, DropZone, Vehicle
from traveai.openapi import errors
from traveai.rules.airspace import AirspaceMap
from traveai.sim.simulator import active_mission
from traveai.sim.tracking import tracking_snapshot

router = APIRouter(prefix="/v1/map", tags=["map"], responses=errors(401, 429))

SessionDep = Annotated[Session, Depends(get_session)]
NowDep = Annotated[datetime, Depends(get_now)]
RECENT = timedelta(minutes=30)  # finished deliveries stay on the map this long
MAX_DELIVERIES = 50


@router.get("/static", summary="Static map layers")
def static_layers(
    auth: CurrentAuth,
    session: SessionDep,
    now: NowDep,
    airspace: Annotated[AirspaceMap, Depends(get_airspace)],
) -> dict[str, Any]:
    """Slow-changing layers: airspace (GeoJSON), hubs and drop zones. Reload every minute or so
    (stadium restrictions switch on and off)."""
    proj = airspace.projection

    def to_lnglat(x, y, z=None):
        lat, lng = proj.to_latlng(x, y)
        return lng, lat

    features = []
    for zone in airspace.zones:
        props: dict[str, Any] = {
            "id": zone.id,
            "name": zone.name,
            "kind": zone.kind.value,
            "no_fly": zone.is_no_fly,
            "active": zone.is_active(now),
        }
        if zone.center is not None:
            lat, lng = proj.to_latlng(zone.center.x, zone.center.y)
            props["center"] = [lat, lng]
            props["laanc_ceilings"] = [
                {"within_m": r.within_m, "ceiling_ft": r.ceiling_ft} for r in zone.laanc_ceilings
            ]
        geometry = transform(to_lnglat, zone.geometry.simplify(25))
        features.append({"type": "Feature", "properties": props, "geometry": mapping(geometry)})

    hubs: dict[tuple[float, float], list[str]] = {}
    for v in session.scalars(select(Vehicle).order_by(Vehicle.call_sign)):
        hubs.setdefault((v.base_lat, v.base_lng), []).append(v.call_sign)

    return {
        "airspace": {"type": "FeatureCollection", "features": features},
        "hubs": [{"lat": lat, "lng": lng, "drones": cs} for (lat, lng), cs in hubs.items()],
        "drop_zones": [
            {
                "id": z.id,
                "label": z.label,
                "kind": z.kind.value,
                "lat": z.lat,
                "lng": z.lng,
                "radius_m": z.radius_m,
            }
            for z in session.scalars(select(DropZone).where(DropZone.verified.is_(True)))
        ],
        "as_of": now,
    }


@router.get("/live", summary="Live map layers")
def live(auth: CurrentAuth, session: SessionDep, now: NowDep) -> dict[str, Any]:
    """Fast-changing layers, for polling every second or two: the whole fleet's positions,
    and your own active and recently finished deliveries (with routes and live ETAs)."""
    merchant_id = auth.merchant.id
    deliveries = session.scalars(
        select(Delivery)
        .where(
            Delivery.merchant_id == merchant_id,
            or_(
                Delivery.status.not_in(TERMINAL_STATUSES),
                Delivery.updated_at >= now - RECENT,
            ),
        )
        .order_by(Delivery.created_at.desc())
        .limit(MAX_DELIVERIES)
    ).all()

    own_by_vehicle: dict[str, str] = {}
    out_deliveries = []
    for d in deliveries:
        snap = tracking_snapshot(d, now)
        mission = active_mission(d)
        if mission is not None:
            own_by_vehicle[mission.vehicle_id] = d.id
        out_deliveries.append(
            {
                "id": d.id,
                "status": d.status.value,
                "priority": d.priority.value,
                "category": d.payload_category.value,
                "description": d.payload_description,
                "external_reference": d.external_reference,
                "pickup": [d.pickup_lat, d.pickup_lng],
                "dropoff": [d.dropoff_lat, d.dropoff_lng],
                "pin_required": d.pin_required,
                "failure_reason": d.failure_reason,
                "phase": snap.phase.value if snap.phase else None,
                "eta_seconds": snap.eta_seconds,
                "remaining_m": snap.remaining_m,
                "drone": snap.drone.call_sign if snap.drone else None,
                "route": snap.route,
                "next_waypoint_index": snap.next_waypoint_index,
                "created_at": d.created_at,
            }
        )

    drones = [
        {
            "id": v.id,
            "call_sign": v.call_sign,
            "model": v.model,
            "status": v.status.value,
            "lat": v.lat,
            "lng": v.lng,
            "altitude_ft": v.altitude_ft,
            "battery_pct": round(v.battery_pct, 1),
            "temperature_controlled": v.temperature_controlled,
            # Only your own delivery is named; other merchants' jobs stay private.
            "delivery_id": own_by_vehicle.get(v.id),
        }
        for v in session.scalars(select(Vehicle).order_by(Vehicle.call_sign))
    ]
    return {"as_of": now, "drones": drones, "deliveries": out_deliveries}
