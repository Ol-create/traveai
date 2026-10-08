from datetime import timedelta
from urllib.parse import urlsplit

import pytest

from traveai.models import Delivery
from traveai.ratelimit import MemoryRateLimiter, get_rate_limiter
from traveai.rules.airspace import default_airspace
from traveai.sim.simulator import SimContext, Simulator

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
    "description": "Insulin pens",
}


@pytest.fixture
def h(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def booked(client, h, fleet):
    q = client.post(
        "/v1/quotes", json={"pickup": PHARMACY, "dropoff": PATIENT, "payload": INSULIN}, headers=h
    ).json()
    d = client.post(
        "/v1/deliveries",
        json={
            "quote_id": q["id"],
            "external_reference": "RX-SECRET-1",
            "recipient": {"name": "Grace", "phone": "+12145550199"},
        },
        headers=h,
    ).json()
    d["public"] = "/v1/public/tracking/" + urlsplit(d["tracking_url"]).path.split("/")[-1]
    return d


@pytest.fixture
def fly(session, world):
    sim = Simulator(SimContext(weather=world, airspace=default_airspace()))

    def _fly(until, seconds=3600):
        for _ in range(int(seconds / 5)):
            sim.step(session, world.now, 5)
            world.now += timedelta(seconds=5)
            session.expire_all()
            if until():
                return
        raise AssertionError("never happened")

    return _fly


def test_booking_returns_a_tracking_link(client, h, booked):
    url = booked["tracking_url"]
    assert url.startswith("http://127.0.0.1:8000/t/")
    assert len(url.rsplit("/", 1)[1]) >= 32  # unguessable token
    again = client.get(f"/v1/deliveries/{booked['id']}", headers=h).json()
    assert again["tracking_url"] == url  # stable, so it can be resent


def test_public_view_needs_no_key_and_hides_private_details(client, booked):
    r = client.get(booked["public"])  # no Authorization header
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scheduled"
    assert body["merchant_name"] == "Unit Test Pharmacy"
    assert body["drone"] is None
    assert body["route"] == [[PHARMACY["lat"], PHARMACY["lng"]], [PATIENT["lat"], PATIENT["lng"]]]
    for private in ("RX-SECRET-1", "Grace", "+12145550199", "Insulin", "price", booked["id"]):
        assert private not in r.text, f"{private!r} leaked"
    assert r.headers["Cache-Control"] == "no-store"


def test_unknown_and_expired_links(client, h, booked, world):
    assert client.get("/v1/public/tracking/not-a-real-token").status_code == 404
    client.post(f"/v1/deliveries/{booked['id']}/cancel", headers=h)
    assert (
        client.get(booked["public"]).json()["problem"]
        == "Unit Test Pharmacy canceled this delivery."
    )
    world.now += timedelta(hours=25)
    r = client.get(booked["public"])
    assert r.status_code == 410 and r.json()["detail"]["code"] == "tracking_link_expired"


def test_live_drone_and_remaining_route_while_flying(client, booked, fly, session):
    fly(until=lambda: session.get(Delivery, booked["id"]).status == "airborne")
    fly(until=lambda: True, seconds=60)
    body = client.get(booked["public"]).json()
    assert body["drone"] is not None
    assert body["route"][0] == [body["drone"]["lat"], body["drone"]["lng"]]  # from the drone...
    assert body["route"][-1] == [PATIENT["lat"], PATIENT["lng"]]  # ...to the recipient
    assert 0 < body["eta_seconds"] < 600


def test_recipient_releases_package_with_pin(client, booked, fly, session):
    fly(until=lambda: client.get(booked["public"]).json()["awaiting_pin"])
    view = client.get(booked["public"]).json()
    assert view["pin_attempts_left"] == 5 and view["eta_seconds"] == 0

    wrong = client.post(booked["public"] + "/handoff", json={"pin": "000000"})
    assert wrong.status_code == 403 and "4 left" in wrong.json()["detail"]["message"]
    assert client.get(booked["public"]).json()["pin_attempts_left"] == 4

    ok = client.post(booked["public"] + "/handoff", json={"pin": booked["recipient_pin"]})
    assert ok.status_code == 200
    done = ok.json()
    assert done["status"] == "delivered" and done["drone"] is None
    proof = client.get(done["proof_photo_url"])
    assert proof.status_code == 200 and proof.headers["content-type"].startswith("image/svg")
    assert booked["id"] not in proof.text  # no internal ids on the public photo


def test_pin_only_when_drone_waits(client, booked):
    r = client.post(booked["public"] + "/handoff", json={"pin": booked["recipient_pin"]})
    assert r.status_code == 409


def test_problem_messages_use_plain_words(client, h, booked, fly, session):
    client.post(
        f"/v1/test/deliveries/{booked['id']}/failures", json={"kind": "high_wind"}, headers=h
    )
    fly(until=lambda: session.get(Delivery, booked["id"]).status == "aborted")
    problem = client.get(booked["public"]).json()["problem"]
    assert (
        problem == "The drone had to turn back because of strong wind. We'll try again if we can."
    )


def test_public_endpoints_rate_limited_per_ip(client, booked):
    limiter = MemoryRateLimiter(limit=2)  # one shared counter
    client.app.dependency_overrides[get_rate_limiter] = lambda: limiter
    assert client.get(booked["public"]).status_code == 200
    assert client.get(booked["public"]).status_code == 200
    r = client.get(booked["public"])
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_page_is_served_with_strict_headers(client, booked):
    r = client.get(urlsplit(booked["tracking_url"]).path)
    assert r.status_code == 200 and "Track your delivery" in r.text
    assert r.headers["Referrer-Policy"] == "no-referrer"  # token never leaks to tile servers
    assert "noindex" in r.headers["X-Robots-Tag"]
    assert r.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    for asset in ("app.js", "styles.css"):
        assert client.get(f"/track-assets/{asset}").status_code == 200


def test_tracking_url_is_in_webhooks_and_map(client, h, booked):
    live = client.get("/v1/map/live", headers=h).json()
    assert live["deliveries"][0]["tracking_url"] == booked["tracking_url"]


def test_problem_phrases_read_naturally():
    from traveai.api.public import FRIENDLY_REASONS

    for phrase in FRIENDLY_REASONS.values():
        sentence = f"The drone had to turn back because {phrase}."
        assert "because of nobody" not in sentence and "because of the" not in sentence
        assert not phrase[0].isupper()
