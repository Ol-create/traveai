import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from traveai.api.deliveries import get_owned_delivery
from traveai.auth import CurrentAuth
from traveai.db import get_session
from traveai.deps import get_clock, get_now, get_session_factory, get_sleep
from traveai.domain.delivery_status import TERMINAL_STATUSES, DeliveryStatus, event_type_for
from traveai.models import Delivery
from traveai.openapi import errors
from traveai.schemas.tracking import Tracking
from traveai.sim.tracking import tracking_snapshot

router = APIRouter(prefix="/v1/deliveries", tags=["tracking"], responses=errors(401, 404))

HEARTBEAT_EVERY_S = 15.0
STATUS_EVENT_TYPES = {event_type_for(s) for s in DeliveryStatus}


class StatusChange(NamedTuple):
    seq: int
    data: dict
    at: str


# Fields that change every tick on their own; ignore them when deciding if anything moved.
_NOISY_FIELDS = {"as_of", "eta_seconds", "estimated_dropoff_at"}


@router.get("/{delivery_id}/tracking", response_model=Tracking, summary="Tracking snapshot")
def tracking(
    delivery_id: str,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
) -> Tracking:
    """Where the drone is right now, and a live ETA."""
    return tracking_snapshot(get_owned_delivery(session, auth, delivery_id), now)


@router.get(
    "/{delivery_id}/track",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
    summary="Live tracking stream (SSE)",
)
def track(
    delivery_id: str,
    request: Request,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    session_factory: Annotated[
        Callable[[], AbstractContextManager[Session]], Depends(get_session_factory)
    ],
    clock: Annotated[Callable[[], datetime], Depends(get_clock)],
    sleep: Annotated[Callable[[float], Awaitable[None]], Depends(get_sleep)],
    interval: Annotated[float, Query(ge=0.5, le=30, description="Seconds between updates.")] = 1.0,
) -> StreamingResponse:
    """Live updates as Server-Sent Events, until the delivery is finished.

    Events: `status` (status changed), `position` (tracking snapshot, same shape as
    `/tracking`), `end` (final status; the stream closes). A `: keep-alive` comment is sent
    every 15 s when nothing changes.

    Browser: `new EventSource(url)` cannot send headers, so call this from your server (or a
    proxy that adds the `Authorization` header).
    """
    get_owned_delivery(session, auth, delivery_id)  # 404 before we start streaming

    def read(after_seq: int) -> tuple[Tracking, list[StatusChange], bool]:
        with session_factory() as s:
            delivery = s.get(Delivery, delivery_id)
            # Status changes come from the event log, so a short-lived status (arriving lasts
            # ~20 s) is never skipped between two updates.
            changes = [
                StatusChange(e.seq, e.data, e.created_at.isoformat())
                for e in delivery.events
                if e.seq > after_seq and e.type in STATUS_EVENT_TYPES
            ]
            snap = tracking_snapshot(delivery, clock())
            return snap, changes, delivery.status in TERMINAL_STATUSES

    async def events() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"  # tell the browser how soon to reconnect
        last_seq, last_shape, quiet_s = 0, None, 0.0
        while not await request.is_disconnected():
            snap, changes, finished = read(last_seq)
            for change in changes:
                yield _sse("status", {"status": change.data["to"], "at": change.at})
                last_seq = change.seq
            shape = snap.model_dump(exclude=_NOISY_FIELDS)
            if shape != last_shape or snap.drone is not None:
                yield _sse("position", snap.model_dump(mode="json"))
                last_shape, quiet_s = shape, 0.0
            elif quiet_s >= HEARTBEAT_EVERY_S:
                yield ": keep-alive\n\n"
                quiet_s = 0.0
            if finished:
                yield _sse("end", {"status": snap.status})
                return
            await sleep(interval)
            quiet_s += interval

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
