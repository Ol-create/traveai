"""OpenAPI documentation: the /docs landing page, tag descriptions and shared error responses."""

from typing import Any

from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

DESCRIPTION = """
Book drone deliveries in Dallas, TX: get a **quote**, book a **delivery**, then follow it
with **live tracking** and **webhooks**. Built for pharmacies (prescriptions, cold chain) and
restaurants (hot food, under 30 minutes).

> Everything flies in a **simulator** for now: real FAA-style rules, simulated drones.

## Quickstart

```bash
KEY=sk_test_...          # from your dashboard (or .env.example locally)
API=http://127.0.0.1:8000

# 1. Quote: is it possible, how much, how long?
curl -X POST $API/v1/quotes \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"pickup": {"lat": 32.7843, "lng": -96.7837},
       "dropoff": {"lat": 32.8120, "lng": -96.7520},
       "payload": {"category": "food", "weight_kg": 1.2,
                   "length_cm": 30, "width_cm": 25, "height_cm": 15}}'

# 2. Book it within 5 minutes
curl -X POST $API/v1/deliveries \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{"quote_id": "quo_..."}'

# 3. Watch it fly (Server-Sent Events)
curl -N $API/v1/deliveries/del_.../track -H "Authorization: Bearer $KEY"
```

Ready-made scripts live in the repository's `examples/` folder.

## Authentication

Send your API key as a bearer token: `Authorization: Bearer sk_test_...`.
Keys starting `sk_test_` are **test mode**: they can use the sandbox endpoints under
`/v1/test` (force failures, change simulation speed, step deliveries by hand).

## Delivery lifecycle

`scheduled → assigned → picking_up → airborne → arriving → delivered`

Before takeoff a delivery can be `canceled`. In the air, problems (wind, low battery, blocked
drop zone, recipient not answering) make it `aborted`: the drone flies the package home
(`returned_to_base`) and it is retried once (`scheduled`), then `failed`.

**Prescriptions:** booking returns a one-time `recipient_pin`. When the drone arrives it hovers
for up to 2 minutes; send the PIN with `POST /v1/deliveries/{id}/handoff` to release the
package.

## Errors

Errors have a stable machine-readable code:

```json
{"detail": {"code": "quote_expired", "message": "Quote expired; request a new one"}}
```

Request validation errors (422 with a list of field problems) follow FastAPI's standard shape.
A quote that can't be flown is **not** an error: it comes back `feasible: false` with
`reasons`.

## Webhooks

Each request carries `TraveAI-Signature: t=<unix time>,v1=<hex>`, where `<hex>` is
HMAC-SHA256 of `"<t>.<raw body>"` with your endpoint's `whsec_...` secret. Reject requests
whose signature doesn't match or whose `t` is more than 5 minutes old. Failed deliveries are
retried for about 10 hours.
"""

TAGS = [
    {"name": "quotes", "description": "Price, ETA and feasibility for a delivery, before booking."},
    {"name": "deliveries", "description": "Book, list, cancel and complete deliveries."},
    {
        "name": "tracking",
        "description": "Where the drone is now: a snapshot, or a live Server-Sent Events stream.",
    },
    {
        "name": "webhooks",
        "description": "Signed event notifications pushed to your server, with retries.",
    },
    {"name": "map", "description": "Layers for live maps (used by the ops dashboard)."},
    {"name": "merchants", "description": "Your account."},
    {
        "name": "test mode",
        "description": "Sandbox helpers for `sk_test_` keys: force failures, set simulation "
        "speed, step deliveries by hand.",
    },
    {"name": "health", "description": "Liveness check."},
]


class ErrorDetail(BaseModel):
    code: str = Field(examples=["delivery_not_found"])
    message: str = Field(examples=["Delivery not found"])


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` entries documenting the API's error shape."""
    text = {
        401: "Missing or invalid API key",
        403: "Not allowed for this key (e.g. needs a test key)",
        404: "Not found (or belongs to another merchant)",
        409: "Not possible in the current state",
        422: "Invalid input",
        429: "Rate limit exceeded (see Retry-After)",
    }
    return {c: {"model": ErrorResponse, "description": text[c]} for c in codes}


def operation_id(route: APIRoute) -> str:
    """Stable, readable operation IDs (`deliveries_cancel`) for generated client SDKs."""
    tag = str(route.tags[0]).replace(" ", "_") if route.tags else "default"
    return f"{tag}_{route.name}"
