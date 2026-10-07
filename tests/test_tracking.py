import json
from datetime import timedelta

import pytest

from traveai.deps import get_sleep
from traveai.models import Delivery
from traveai.rules.airspace import default_airspace
from traveai.sim.simulator import SimContext, Simulator

DEEP_ELLUM = {"lat": 32.7843, "lng": -96.7837}
LAKEWOOD = {"lat": 32.8120, "lng": -96.7520}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}


@pytest.fixture
def headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def sim(world):
    return Simulator(SimContext(weather=world, airspace=default_airspace()))


@pytest.fixture
def booked(client, headers, fleet):
    q = client.post(
        "/v1/quotes",
        json={"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO},
        headers=headers,
    ).json()
    return client.post("/v1/deliveries", json={"quote_id": q["id"]}, headers=headers).json()


def fly(sim, session, world, seconds, dt=5.0):
    for _ in range(int(seconds / dt)):
        sim.step(session, world.now, dt)
        world.now += timedelta(seconds=dt)


def read_sse(response):
    """Parse an SSE stream into [(event, data), ...]."""
    out, event = [], None
    for line in response.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[6:])))
    return out


# --- snapshot ------------------------------------------------------------------------------


def test_tracking_before_dispatch_uses_booked_estimate(client, headers, booked):
    t = client.get(f"/v1/deliveries/{booked['id']}/tracking", headers=headers).json()
    assert t["status"] == "scheduled"
    assert t["drone"] is None and t["route"] == []
    assert 0 < t["eta_seconds"] <= 12 * 60


def test_tracking_while_flying(client, headers, booked, sim, session, world):
    fly(sim, session, world, 60)
    first = client.get(f"/v1/deliveries/{booked['id']}/tracking", headers=headers).json()
    fly(sim, session, world, 60)
    second = client.get(f"/v1/deliveries/{booked['id']}/tracking", headers=headers).json()

    assert second["drone"]["call_sign"] in {"Q-1", "Q-2", "W-1"}
    assert second["phase"] in {"loading", "delivering"}
    assert second["route"][0] == second["route"][-1]  # loop: hub ... hub
    assert second["remaining_m"] <= first["remaining_m"]
    assert second["eta_seconds"] < first["eta_seconds"]


def test_tracking_after_delivery_has_no_eta(client, headers, booked, sim, session, world):
    fly(sim, session, world, 20 * 60)
    t = client.get(f"/v1/deliveries/{booked['id']}/tracking", headers=headers).json()
    assert t["status"] == "delivered"
    assert t["eta_seconds"] in (None, 0)


def test_tracking_is_private(client, booked, session):
    from traveai.domain.enums import MerchantCategory
    from traveai.models import Merchant

    other = Merchant(name="Other", category=MerchantCategory.RESTAURANT)
    session.add(other)
    key = other.issue_api_key()
    session.commit()
    auth = {"Authorization": f"Bearer {key}"}
    assert client.get(f"/v1/deliveries/{booked['id']}/tracking", headers=auth).status_code == 404
    assert client.get(f"/v1/deliveries/{booked['id']}/track", headers=auth).status_code == 404


# --- live stream (SSE) ---------------------------------------------------------------------


def test_stream_of_finished_delivery_ends_immediately(client, headers, booked):
    client.post(f"/v1/deliveries/{booked['id']}/cancel", headers=headers)
    with client.stream("GET", f"/v1/deliveries/{booked['id']}/track", headers=headers) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = read_sse(r)
    # Full status history first, then where things stand, then the stream closes.
    assert [e for e, _ in events] == ["status", "status", "position", "end"]
    assert [d["status"] for e, d in events if e == "status"] == ["scheduled", "canceled"]
    assert events[-1][1] == {"status": "canceled"}


def test_stream_follows_a_whole_flight(client, headers, booked, sim, session, world):
    async def fly_while_waiting(seconds: float) -> None:
        # Each "sleep" between updates flies the fleet 30 simulated seconds.
        fly(sim, session, world, 30)

    client.app.dependency_overrides[get_sleep] = lambda: fly_while_waiting
    with client.stream("GET", f"/v1/deliveries/{booked['id']}/track", headers=headers) as r:
        events = read_sse(r)

    statuses = [d["status"] for e, d in events if e == "status"]
    # Every status, even short ones (assigned and picking_up happen in the same tick;
    # arriving lasts ~20 s, less than one 30 s update).
    assert statuses == ["scheduled", "assigned", "picking_up", "airborne", "arriving", "delivered"]
    assert events[-1] == ("end", {"status": "delivered"})

    positions = [d["drone"] for e, d in events if e == "position" and d["drone"]]
    assert len(positions) > 5
    assert len({(p["lat"], p["lng"]) for p in positions}) > 3  # it actually moved
    assert positions[-1]["battery_pct"] < positions[0]["battery_pct"]
    assert session.get(Delivery, booked["id"]).status == "delivered"
