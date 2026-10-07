"""Pharmacy example: send a cold-chain prescription by drone, release it with the PIN.

    TRAVEAI_API_KEY=sk_test_... python examples/pharmacy_prescription.py

Needs a server with the simulator on (TRAVEAI_SIM_ENABLED=true). Only `httpx` is required:
copy this file into your own codebase and adapt it.

Flow: quote -> book (get the one-time PIN) -> poll tracking -> when the drone hovers at the
drop-off, send the PIN (in real life the patient types it into your app) -> proof of delivery.
"""

import os
import time
from collections.abc import Callable

import httpx

PHARMACY = {"lat": 32.7843, "lng": -96.7837, "address": "Deep Ellum, Dallas, TX"}
PATIENT = {"lat": 32.8120, "lng": -96.7520, "address": "Lakewood, Dallas, TX"}
INSULIN = {
    "category": "medical",
    "weight_kg": 0.4,
    "length_cm": 15,
    "width_cm": 10,
    "height_cm": 8,
    "temperature_controlled": True,  # cold chain
    "prescription": True,  # recipient PIN required
    "description": "Insulin pens",
}


def run(
    client: httpx.Client,
    wait: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
    timeout_s: float = 600,
) -> dict:
    """Deliver one prescription. Returns the final delivery object."""
    # 1. Quote: can we fly it right now, for how much, by when?
    quote = client.post(
        "/v1/quotes", json={"pickup": PHARMACY, "dropoff": PATIENT, "payload": INSULIN}
    ).json()
    if not quote["feasible"]:
        reasons = "; ".join(r["message"] for r in quote["reasons"])
        raise RuntimeError(f"Can't deliver right now: {reasons}")
    log(
        f"Quote {quote['id']}: ${quote['price']['amount_cents'] / 100:.2f}, "
        f"~{quote['eta_seconds'] // 60} min, requires {', '.join(quote['requirements'])}"
    )

    # 2. Book. The PIN is shown only now: hand it to the patient (SMS, your app...).
    response = client.post(
        "/v1/deliveries",
        json={
            "quote_id": quote["id"],
            "recipient": {"name": "Grace Hopper", "phone": "+12145550199"},
            "external_reference": "RX-20931",
        },
    )
    response.raise_for_status()
    delivery = response.json()
    pin = delivery["recipient_pin"]
    log(f"Booked {delivery['id']}; patient PIN {pin}")

    # 3. Follow the flight. When the drone hovers at the drop-off, release with the PIN.
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        t = client.get(f"/v1/deliveries/{delivery['id']}/tracking").json()
        state = (t["status"], t["phase"])
        if state != last:
            where = f" ({t['drone']['call_sign']}, ETA {t['eta_seconds']} s)" if t["drone"] else ""
            log(f"  {t['status']}{' / ' + t['phase'] if t['phase'] else ''}{where}")
            last = state
        if t["phase"] == "awaiting_handoff":
            r = client.post(f"/v1/deliveries/{delivery['id']}/handoff", json={"pin": pin})
            r.raise_for_status()
            log("  PIN accepted, package released")
        if t["status"] in {"delivered", "failed", "canceled"}:
            break
        wait(1.0)

    # 4. Proof of delivery and chain of custody, for your records.
    final = client.get(f"/v1/deliveries/{delivery['id']}").json()
    if final["status"] != "delivered":
        raise RuntimeError(f"Delivery ended {final['status']}: {final['failure_reason']}")
    proof = final["proof"]
    log(f"Delivered at {proof['delivered_at']} ({proof['lat']:.5f}, {proof['lng']:.5f})")
    events = client.get(f"/v1/deliveries/{delivery['id']}/events").json()["data"]
    custody = [e["data"]["action"] for e in events if e["type"] == "delivery.custody"]
    log("Chain of custody: " + " -> ".join(custody))
    return final


def main() -> None:
    key = os.environ.get("TRAVEAI_API_KEY")
    if not key:
        raise SystemExit("Set TRAVEAI_API_KEY to a test key (sk_test_...)")
    base_url = os.environ.get("TRAVEAI_BASE_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {key}"}) as client:
        run(client)


if __name__ == "__main__":
    main()
