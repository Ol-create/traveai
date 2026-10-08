"""Public tracking for recipients: no API key; the unguessable token in the link is the
credential. Exposes only what a recipient needs (see PublicTracking)."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.db import get_session
from traveai.deps import get_now, get_simulator
from traveai.domain.delivery_status import TERMINAL_STATUSES, DeliveryStatus
from traveai.domain.enums import MissionPhase
from traveai.errors import ApiError
from traveai.models import Delivery
from traveai.openapi import errors
from traveai.proof import proof_svg
from traveai.ratelimit import RateLimiter, get_rate_limiter
from traveai.schemas.public_tracking import LatLng, PublicDrone, PublicHandoff, PublicTracking
from traveai.services.deliveries import MAX_PIN_ATTEMPTS, PinRejectedError
from traveai.sim.simulator import Simulator, active_mission
from traveai.sim.tracking import tracking_snapshot

router = APIRouter(tags=["public tracking"], responses=errors(404, 429))

PAGE = Path(__file__).resolve().parent.parent / "tracking_page" / "index.html"
LINK_TTL_AFTER_FINISH = timedelta(hours=24)
SHOW_DRONE_PHASES = {
    MissionPhase.POSITIONING,
    MissionPhase.LOADING,
    MissionPhase.DELIVERING,
    MissionPhase.AWAITING_HANDOFF,
}
# Recipient-facing words for internal failure reasons, each reading "... because <phrase>".
# Anything else stays generic, so merchant-written cancel reasons or internal codes never
# reach the public page.
FRIENDLY_REASONS = {
    "wind_gust_exceeds_limit": "of strong wind",
    "low_battery": "its battery ran low",
    "drop_zone_blocked": "the drop-off spot was blocked",
    "recipient_unavailable": "nobody entered the PIN in time",
    "pin_locked": "of too many wrong PINs",
    "emergency_landing": "of a drone fault",
}
# Strict security headers: the token is in the URL, so never send it on as a Referer (e.g. to
# the map tile servers), keep the page out of caches and search engines, and lock down sources.
PAGE_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://unpkg.com; "
        "style-src 'self' https://unpkg.com; "
        "img-src 'self' data: https://*.tile.openstreetmap.org; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

SessionDep = Annotated[Session, Depends(get_session)]
NowDep = Annotated[datetime, Depends(get_now)]


def _limit(request: Request, limiter: Annotated[RateLimiter, Depends(get_rate_limiter)]) -> None:
    """Public endpoints are rate limited per client IP."""
    client = request.client.host if request.client else "unknown"
    decision = limiter.hit(f"public:{client}")
    if decision.limit and not decision.allowed:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many requests; slow down",
            headers=decision.headers(),
        )


def _delivery(session: Session, token: str, now: datetime) -> Delivery:
    delivery = session.scalar(select(Delivery).where(Delivery.tracking_token == token))
    if delivery is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "tracking_link_not_found", "Unknown link")
    if delivery.status in TERMINAL_STATUSES:
        finished = delivery.delivered_at or delivery.updated_at
        if finished < now - LINK_TTL_AFTER_FINISH:
            raise ApiError(status.HTTP_410_GONE, "tracking_link_expired", "This link has expired")
    return delivery


def _problem(d: Delivery) -> str | None:
    reason = d.failure_reason or ""
    if reason.startswith("gave up after"):
        reason = reason.rsplit(": ", 1)[-1]
    cause = FRIENDLY_REASONS.get(reason)
    if reason.startswith("no_drone_could_fly"):
        cause = "of weather or airspace conditions"
    because = f" because {cause}" if cause else ""
    if d.status in {DeliveryStatus.ABORTED, DeliveryStatus.RETURNED_TO_BASE}:
        return f"The drone had to turn back{because}. We'll try again if we can."
    if d.status == DeliveryStatus.FAILED:
        return f"We couldn't complete this delivery{because}. {d.merchant.name} has been told."
    if d.status == DeliveryStatus.CANCELED:
        return f"{d.merchant.name} canceled this delivery."
    return None


def public_view(d: Delivery, token: str, now: datetime) -> PublicTracking:
    snap = tracking_snapshot(d, now)
    mission = active_mission(d)
    drone, route = None, [[d.pickup_lat, d.pickup_lng], [d.dropoff_lat, d.dropoff_lng]]
    if mission is not None and mission.phase in SHOW_DRONE_PHASES:
        v = mission.vehicle
        drone = PublicDrone(lat=v.lat, lng=v.lng, altitude_ft=v.altitude_ft)
        # Only the pickup -> drop-off leg: the hub's location is the operator's business.
        if mission.phase == MissionPhase.DELIVERING:
            ahead = mission.waypoints[mission.next_waypoint_index : mission.dropoff_index + 1]
            route = [[v.lat, v.lng], *ahead]
        elif mission.phase == MissionPhase.AWAITING_HANDOFF:
            route = []  # it's here; no path left to show
        else:
            route = mission.waypoints[mission.pickup_index : mission.dropoff_index + 1]
    awaiting = mission is not None and mission.phase == MissionPhase.AWAITING_HANDOFF
    finished = d.status in TERMINAL_STATUSES
    return PublicTracking(
        merchant_name=d.merchant.name,
        category=d.payload_category,
        status=d.status,
        eta_seconds=None if finished else snap.eta_seconds,
        estimated_dropoff_at=None if finished else snap.estimated_dropoff_at,
        pickup=LatLng(lat=d.pickup_lat, lng=d.pickup_lng),
        dropoff=LatLng(lat=d.dropoff_lat, lng=d.dropoff_lng),
        drone=drone,
        route=route,
        awaiting_pin=awaiting and d.pin_required,
        pin_attempts_left=MAX_PIN_ATTEMPTS - d.pin_failed_attempts if d.pin_required else None,
        delivered_at=d.delivered_at,
        proof_photo_url=f"/v1/public/tracking/{token}/proof.svg" if d.delivered_at else None,
        problem=_problem(d),
        as_of=now,
    )


@router.get("/t/{token}", include_in_schema=False)
def page(token: str) -> FileResponse:
    """The recipient's tracking page. It reads the token from its own URL."""
    return FileResponse(PAGE, media_type="text/html", headers=PAGE_HEADERS)


@router.get(
    "/v1/public/tracking/{token}",
    response_model=PublicTracking,
    summary="Public tracking (for recipients)",
    dependencies=[Depends(_limit)],
    responses=errors(410),
)
def public_tracking(
    token: str, response: Response, session: SessionDep, now: NowDep
) -> PublicTracking:
    """Status, ETA and live drone position for the recipient. No API key: the `tracking_url`
    from the delivery is the credential. Expires 24 h after the delivery finishes."""
    response.headers["Cache-Control"] = "no-store"
    return public_view(_delivery(session, token, now), token, now)


@router.post(
    "/v1/public/tracking/{token}/handoff",
    response_model=PublicTracking,
    summary="Recipient enters the PIN (public)",
    dependencies=[Depends(_limit)],
    responses=errors(403, 409, 410, 422),
)
def public_handoff(
    token: str,
    body: PublicHandoff,
    session: SessionDep,
    now: NowDep,
    sim: Annotated[Simulator, Depends(get_simulator)],
) -> PublicTracking:
    """The recipient types the PIN while the drone hovers; a correct PIN releases the package.
    Five wrong PINs and the drone flies it back."""
    delivery = _delivery(session, token, now)
    try:
        sim.handoff(session, delivery, body.pin, now)
    except PinRejectedError:
        session.commit()  # keep the failed-attempt count
        raise
    session.commit()
    return public_view(delivery, token, now)


@router.get(
    "/v1/public/tracking/{token}/proof.svg",
    response_class=Response,
    summary="Proof-of-delivery photo (public)",
    dependencies=[Depends(_limit)],
    responses=errors(410),
)
def public_proof(token: str, session: SessionDep, now: NowDep) -> Response:
    d = _delivery(session, token, now)
    if d.delivered_at is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "no_proof", "Not delivered yet")
    return Response(
        proof_svg(d, public=True), media_type="image/svg+xml", headers={"Cache-Control": "no-store"}
    )
