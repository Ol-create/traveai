"""Restaurant example: send a hot meal and follow it live with Server-Sent Events.

    TRAVEAI_API_KEY=sk_test_... python examples/restaurant_meal.py
    TRAVEAI_API_KEY=sk_test_... python examples/restaurant_meal.py --fail high_wind

Needs a server with the simulator on (TRAVEAI_SIM_ENABLED=true). Only `httpx` is required.

`--fail` (test keys only) forces a failure on the first flight, so you can see how an aborted
delivery comes back to the hub and is retried automatically.
"""

import argparse
import json
import os
from collections.abc import Callable, Iterator

import httpx

KITCHEN = {"lat": 32.7486, "lng": -96.8270, "address": "Bishop Arts, Dallas, TX"}
CUSTOMER = {"lat": 32.7900, "lng": -96.7810, "address": "East Dallas, TX"}
MEAL = {
    "category": "food",
    "weight_kg": 1.6,
    "length_cm": 32,
    "width_cm": 32,
    "height_cm": 10,
    "temperature_controlled": True,  # keep it hot
    "description": "2x pad thai",
}


def sse_events(lines: Iterator[str]) -> Iterator[tuple[str, dict]]:
    """Minimal Server-Sent Events parser: yields (event, data)."""
    event = "message"
    for line in lines:
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            yield event, json.loads(line[6:])
            event = "message"


def run(
    client: httpx.Client,
    fail: str | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Deliver one meal. Returns the final delivery object."""
    quote = client.post(
        "/v1/quotes", json={"pickup": KITCHEN, "dropoff": CUSTOMER, "payload": MEAL}
    ).json()
    if not quote["feasible"]:
        for reason in quote["reasons"]:
            log(f"Can't deliver: {reason['code']} ({reason['message']})")
        raise RuntimeError("quote not feasible")
    log(f"Quote: ${quote['price']['amount_cents'] / 100:.2f}, ~{quote['eta_seconds'] // 60} min")

    body = {"quote_id": quote["id"], "external_reference": "ORDER-7781"}
    if fail:
        body["test_failure"] = fail
    response = client.post("/v1/deliveries", json=body)
    response.raise_for_status()
    delivery = response.json()
    log(f"Booked {delivery['id']}" + (f" (forcing {fail} on the first flight)" if fail else ""))

    # The stream ends by itself when the delivery is delivered, canceled or failed.
    with client.stream("GET", f"/v1/deliveries/{delivery['id']}/track") as stream:
        stream.raise_for_status()
        for event, data in sse_events(stream.iter_lines()):
            if event == "status":
                log(f"  {data['status']}")
            elif event == "end":
                break

    final = client.get(f"/v1/deliveries/{delivery['id']}").json()
    if final["status"] == "delivered":
        log(f"Enjoy! Proof photo: {final['proof']['photo_url']}")
    else:
        log(f"Delivery {final['status']}: {final['failure_reason']}")
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fail", choices=["high_wind", "low_battery", "drop_zone_blocked"])
    args = parser.parse_args()
    key = os.environ.get("TRAVEAI_API_KEY")
    if not key:
        raise SystemExit("Set TRAVEAI_API_KEY to a test key (sk_test_...)")
    base_url = os.environ.get("TRAVEAI_BASE_URL", "http://127.0.0.1:8000")
    with httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=None
    ) as client:
        run(client, fail=args.fail)


if __name__ == "__main__":
    main()
