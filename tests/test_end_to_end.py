"""Whole-system flows: quote -> book -> fly -> hand off -> proof, with webhooks and tracking."""

import json
from datetime import timedelta

import pytest

from traveai.models import WebhookMessage, WebhookMessageStatus
from traveai.rules.airspace import default_airspace
from traveai.sim.simulator import SimContext, Simulator
from traveai.webhooks.sender import fan_out, send_due
from traveai.webhooks.signing import SIGNATURE_HEADER, verify

PHARMACY = {"lat": 32.7843, "lng": -96.7837}
PATIENT = {"lat": 32.8120, "lng": -96.7520}
INSULIN = {
    "category": "medical",
    "weight_kg": 0.4,
    "length_cm": 15,
    "width_cm": 10,
    "height_cm": 8,
    "temperature_controlled": True,
    "prescription": True,
}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}


@pytest.fixture
def h(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def world_step(session, world):
    """Advance the whole system: simulator + webhook worker, in 5 s steps."""
    sim = Simulator(SimContext(weather=world, airspace=default_airspace()))
    received: list[tuple[dict, bytes, float]] = []

    def transport(url, headers, body):
        # Remember when it arrived: receivers check signatures on arrival (5 min window).
        received.append((headers, body, world.now.timestamp()))
        return 200

    def step(seconds: float = 5, until=None) -> None:
        for _ in range(int(seconds / 5)):
            sim.step(session, world.now, 5)
            fan_out(session, world.now)
            session.commit()
            send_due(session, world.now, transport)
            world.now += timedelta(seconds=5)
            if until and until():
                return
        assert until is None, "condition never became true"

    step.received = received
    return step


def test_prescription_end_to_end(client, h, fleet, session, world, world_step):
    endpoint = client.post(
        "/v1/webhook_endpoints",
        json={"url": "https://pharmacy.example.com/hooks", "enabled_events": ["delivery.*"]},
        headers=h,
    ).json()

    quote = client.post(
        "/v1/quotes", json={"pickup": PHARMACY, "dropoff": PATIENT, "payload": INSULIN}, headers=h
    ).json()
    assert quote["feasible"]
    delivery = client.post(
        "/v1/deliveries",
        json={"quote_id": quote["id"], "external_reference": "RX-1"},
        headers=h,
    ).json()
    url = f"/v1/deliveries/{delivery['id']}"

    def tracking():
        return client.get(f"{url}/tracking", headers=h).json()

    world_step(1800, until=lambda: tracking()["phase"] == "awaiting_handoff")
    assert client.post(
        f"{url}/handoff", json={"pin": delivery["recipient_pin"]}, headers=h
    ).is_success
    world_step(1800, until=lambda: tracking()["phase"] is None)  # drone back at its hub

    final = client.get(url, headers=h).json()
    assert final["status"] == "delivered" and final["proof"]["pin_verified"]
    assert client.get(final["proof"]["photo_url"], headers=h).status_code == 200

    # Every status change reached the merchant's server, signed, in order, exactly once.
    types = []
    for headers, body, arrived in world_step.received:
        assert verify(body, headers[SIGNATURE_HEADER], endpoint["secret"], now=arrived)
        types.append(json.loads(body)["type"])
    assert [t for t in types if t != "delivery.custody"] == [
        "delivery.scheduled",
        "delivery.assigned",
        "delivery.picking_up",
        "delivery.airborne",
        "delivery.arriving",
        "delivery.delivered",
    ]
    assert types.count("delivery.custody") == 3
    assert all(m.status == WebhookMessageStatus.SUCCEEDED for m in session.query(WebhookMessage))


def test_forced_failure_at_booking_retries_and_delivers(client, h, fleet, world_step):
    quote = client.post(
        "/v1/quotes", json={"pickup": PHARMACY, "dropoff": PATIENT, "payload": BURRITO}, headers=h
    ).json()
    d = client.post(
        "/v1/deliveries",
        json={"quote_id": quote["id"], "test_failure": "drop_zone_blocked"},
        headers=h,
    ).json()
    url = f"/v1/deliveries/{d['id']}"
    world_step(3600, until=lambda: client.get(url, headers=h).json()["status"] == "delivered")

    statuses = [
        e["data"]["to"]
        for e in client.get(f"{url}/events", headers=h).json()["data"]
        if e["type"] not in {"delivery.custody"}
    ]
    assert "aborted" in statuses and "returned_to_base" in statuses
    assert statuses[-1] == "delivered"


def test_test_failure_needs_test_key(client, session, merchant, fleet):
    live = merchant.issue_api_key(test=False)
    session.commit()
    lh = {"Authorization": f"Bearer {live}"}
    quote = client.post(
        "/v1/quotes", json={"pickup": PHARMACY, "dropoff": PATIENT, "payload": BURRITO}, headers=lh
    ).json()
    r = client.post(
        "/v1/deliveries", json={"quote_id": quote["id"], "test_failure": "high_wind"}, headers=lh
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "test_mode_only"
