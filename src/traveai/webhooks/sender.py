"""Queue events for webhook endpoints (outbox) and send them, with retries.

1. `fan_out` turns every new Event into one WebhookMessage per matching endpoint, then marks
   the event done. Because events and the outbox flag live in the same database, an event is
   never lost even if the process dies between steps.
2. `send_due` POSTs messages whose retry time has come. Non-2xx answers and network errors are
   retried with growing gaps (10 s ... 6 h, about 10 hours in total), then marked failed.
"""

import ipaddress
import json
import socket
from collections.abc import Callable
from datetime import datetime, timedelta
from fnmatch import fnmatchcase
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.models import Delivery, Event, WebhookEndpoint, WebhookMessage, WebhookMessageStatus
from traveai.schemas.delivery import DeliveryOut
from traveai.webhooks.signing import SIGNATURE_HEADER, sign

RETRY_DELAYS = [
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=3),
    timedelta(hours=6),
]
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
TIMEOUT_S = 5.0

# (url, headers, body) -> HTTP status code. Swappable in tests.
Transport = Callable[[str, dict[str, str], bytes], int]


class UnsafeUrlError(ValueError):
    pass


def http_transport(url: str, headers: dict[str, str], body: bytes) -> int:
    # No redirects: a redirect could point a "public" URL at an internal one.
    response = httpx.post(
        url, content=body, headers=headers, timeout=TIMEOUT_S, follow_redirects=False
    )
    return response.status_code


def check_url(url: str, *, livemode: bool) -> None:
    """Live endpoints must be public HTTPS. Otherwise anyone with an API key could make our
    servers call internal addresses (SSRF). Test endpoints may use http://localhost."""
    parts = urlsplit(url)
    if parts.scheme not in ({"https"} if livemode else {"http", "https"}):
        raise UnsafeUrlError("live webhook URLs must use https" if livemode else "bad URL scheme")
    if not parts.hostname:
        raise UnsafeUrlError("URL has no host")
    if not livemode:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeUrlError(f"{parts.hostname} resolves to a non-public address")


def matches(enabled_events: list[str], event_type: str) -> bool:
    return any(fnmatchcase(event_type, pattern) for pattern in enabled_events)


def build_payload(session: Session, event: Event) -> dict:
    obj = None
    if event.delivery_id:
        delivery = session.get(Delivery, event.delivery_id)
        obj = DeliveryOut.from_model(delivery).model_dump(mode="json")
    return {
        "id": event.id,
        "object": "event",
        "type": event.type,
        "created_at": event.created_at.isoformat(),
        "data": {"object": obj, "details": event.data},
    }


def fan_out(session: Session, now: datetime, batch: int = 500) -> int:
    """Queue new events for every active, matching endpoint. Returns messages queued."""
    events = session.scalars(
        select(Event).where(Event.webhooks_enqueued.is_(False)).order_by(Event.seq).limit(batch)
    ).all()
    endpoints: dict[str, list[WebhookEndpoint]] = {}
    queued = 0
    for event in events:
        if event.merchant_id not in endpoints:
            endpoints[event.merchant_id] = list(
                session.scalars(
                    select(WebhookEndpoint).where(WebhookEndpoint.merchant_id == event.merchant_id)
                )
            )
        targets = [e for e in endpoints[event.merchant_id] if matches(e.enabled_events, event.type)]
        if targets:
            payload = build_payload(session, event)
            for endpoint in targets:
                session.add(
                    WebhookMessage(
                        endpoint=endpoint,
                        event_id=event.id,
                        event_type=event.type,
                        payload=payload,
                        next_attempt_at=now,
                        created_at=now,
                    )
                )
                queued += 1
        event.webhooks_enqueued = True
    return queued


def send_due(session: Session, now: datetime, transport: Transport, batch: int = 50) -> int:
    """Attempt every message whose time has come. Commits after each one."""
    messages = session.scalars(
        select(WebhookMessage)
        .where(
            WebhookMessage.status == WebhookMessageStatus.PENDING,
            WebhookMessage.next_attempt_at <= now,
        )
        .order_by(WebhookMessage.next_attempt_at)
        .limit(batch)
    ).all()
    for message in messages:
        attempt(message, now, transport)
        session.commit()  # don't hold a DB write lock across many HTTP calls
    return len(messages)


def attempt(message: WebhookMessage, now: datetime, transport: Transport) -> bool:
    endpoint = message.endpoint
    body = json.dumps(message.payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TraveAI-Webhooks/1.0",
        "TraveAI-Event-Id": message.event_id,
        SIGNATURE_HEADER: sign(endpoint.secret, int(now.timestamp()), body),
    }
    message.attempts += 1
    try:
        check_url(endpoint.url, livemode=endpoint.livemode)
        code = transport(endpoint.url, headers, body)
        message.last_status_code = code
        ok = 200 <= code < 300
        message.last_error = None if ok else f"HTTP {code}"
    except Exception as exc:  # network errors, timeouts, unsafe URLs: all retryable
        ok = False
        message.last_status_code = None
        message.last_error = f"{type(exc).__name__}: {exc}"[:300]

    if ok:
        message.status = WebhookMessageStatus.SUCCEEDED
        message.delivered_at = now
        message.next_attempt_at = None
    elif message.attempts >= MAX_ATTEMPTS:
        message.status = WebhookMessageStatus.FAILED
        message.next_attempt_at = None
    else:
        message.next_attempt_at = now + RETRY_DELAYS[message.attempts - 1]
    return ok
