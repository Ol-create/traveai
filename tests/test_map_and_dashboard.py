from datetime import UTC, datetime, timedelta

import pytest

from traveai.domain.enums import MerchantCategory
from traveai.models import Merchant
from traveai.rules.airspace import default_airspace
from traveai.sim.simulator import SimContext, Simulator

DEEP_ELLUM = {"lat": 32.7843, "lng": -96.7837}
LAKEWOOD = {"lat": 32.8120, "lng": -96.7520}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}
DALLAS_BBOX = (32.5, 33.1, -97.3, -96.5)  # lat min/max, lng min/max


@pytest.fixture
def headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


def book(client, headers):
    q = client.post(
        "/v1/quotes",
        json={"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO},
        headers=headers,
    ).json()
    return client.post("/v1/deliveries", json={"quote_id": q["id"]}, headers=headers).json()


def test_dashboard_page_is_served(client):
    r = client.get("/", follow_redirects=True)
    assert r.status_code == 200
    assert "<title>TraveAI Ops</title>" in r.text
    for asset in ("app.js", "styles.css"):
        assert client.get(f"/dashboard/{asset}").status_code == 200


def test_map_requires_api_key(client):
    assert client.get("/v1/map/static").status_code == 401
    assert client.get("/v1/map/live").status_code == 401


def test_static_layers(client, headers, fleet):
    data = client.get("/v1/map/static", headers=headers).json()
    features = data["airspace"]["features"]
    assert {f["properties"]["kind"] for f in features} == {"airport", "stadium", "restricted"}

    # GeoJSON is [lng, lat]: every point must land in the Dallas area.
    lat_min, lat_max, lng_min, lng_max = DALLAS_BBOX
    for f in features:
        for lng, lat in f["geometry"]["coordinates"][0]:
            assert lat_min < lat < lat_max and lng_min < lng < lng_max

    love_field = next(f for f in features if "Love Field" in f["properties"]["name"])
    assert love_field["properties"]["laanc_ceilings"][0]["ceiling_ft"] == 0
    restricted = next(f for f in features if f["properties"]["kind"] == "restricted")
    assert restricted["properties"]["active"] and restricted["properties"]["no_fly"]
    assert len(data["hubs"]) == 2


def test_stadium_shows_active_on_game_day(client, headers, world):
    world.now = datetime(2026, 10, 10, 18, 0, tzinfo=UTC)
    features = client.get("/v1/map/static", headers=headers).json()["airspace"]["features"]
    cotton_bowl = next(f for f in features if "Cotton Bowl" in f["properties"]["name"])
    assert cotton_bowl["properties"]["active"] is True


def test_live_shows_fleet_and_own_flying_delivery(client, headers, fleet, session, world):
    d = book(client, headers)
    sim = Simulator(SimContext(weather=world, airspace=default_airspace()))
    for _ in range(12):  # one simulated minute
        sim.step(session, world.now, 5)
        world.now += timedelta(seconds=5)

    live = client.get("/v1/map/live", headers=headers).json()
    assert {v["call_sign"] for v in live["drones"]} == {"Q-1", "Q-2", "W-1"}
    [mine] = live["deliveries"]
    assert mine["id"] == d["id"] and mine["drone"] and mine["route"]
    flying = next(v for v in live["drones"] if v["call_sign"] == mine["drone"])
    assert flying["delivery_id"] == d["id"]


def test_live_hides_other_merchants_deliveries(client, headers, fleet, session, world):
    d = book(client, headers)
    Simulator(SimContext(weather=world, airspace=default_airspace())).dispatch(session, world.now)
    session.commit()

    other = Merchant(name="Other", category=MerchantCategory.RESTAURANT)
    session.add(other)
    key = other.issue_api_key()
    session.commit()
    live = client.get("/v1/map/live", headers={"Authorization": f"Bearer {key}"}).json()
    assert live["deliveries"] == []
    assert all(v["delivery_id"] is None for v in live["drones"])  # can't see whose job it is
    assert d["id"] not in str(live)


def test_finished_deliveries_drop_off_the_map(client, headers, fleet, world):
    d = book(client, headers)
    client.post(f"/v1/deliveries/{d['id']}/cancel", headers=headers)
    assert [x["id"] for x in client.get("/v1/map/live", headers=headers).json()["deliveries"]] == [
        d["id"]
    ]
    world.now += timedelta(minutes=31)
    assert client.get("/v1/map/live", headers=headers).json()["deliveries"] == []
