from datetime import timedelta

import pytest

from traveai.domain.enums import VehicleStatus
from traveai.rules.weather import WeatherConditions

DEEP_ELLUM = {"lat": 32.7843, "lng": -96.7837, "address": "Deep Ellum, Dallas"}
LAKEWOOD = {"lat": 32.8120, "lng": -96.7520, "address": "Lakewood, Dallas"}

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
def post_quote(client, api_key):
    def _post(expected_status: int = 201, **overrides):
        body = {"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO, **overrides}
        response = client.post(
            "/v1/quotes", json=body, headers={"Authorization": f"Bearer {api_key}"}
        )
        assert response.status_code == expected_status, response.text
        return response.json()

    return _post


def reason_codes(quote) -> set[str]:
    return {r["code"] for r in quote["reasons"]}


# --- happy path ----------------------------------------------------------------------------


def test_feasible_food_quote(post_quote, fleet, world):
    q = post_quote()
    assert q["object"] == "quote" and q["id"].startswith("quo_")
    assert q["feasible"] is True and q["reasons"] == []
    assert q["price"]["currency"] == "usd"
    assert q["price"]["amount_cents"] == sum(li["amount_cents"] for li in q["price"]["breakdown"])
    assert q["price"]["amount_cents"] == 499 + 513 + 75  # base + 4.27 km + 0.2 kg over 1 kg
    assert q["cruise_altitude_ft"] == 300
    assert q["requirements"] == []
    assert q["weather"]["wind_gust_mps"] == 4.5


def test_eta_and_expiry_follow_the_clock(post_quote, fleet, world):
    q = post_quote()
    # Quad from the Deep Ellum hub: ~2 min positioning + 3 min loading + ~5.2 min flight.
    assert 8 * 60 < q["eta_seconds"] < 12 * 60
    assert q["created_at"] == "2026-10-06T18:00:00Z"
    assert q["expires_at"] == "2026-10-06T18:05:00Z"
    assert q["estimated_pickup_at"] < q["estimated_dropoff_at"]


def test_insulin_needs_cold_chain_drone_and_lists_requirements(post_quote, fleet):
    q = post_quote(payload=INSULIN)
    assert q["feasible"] is True
    assert q["requirements"] == [
        "chain_of_custody",
        "recipient_pin",
        "temperature_controlled_vehicle",
    ]
    items = {li["item"] for li in q["price"]["breakdown"]}
    assert "temperature_control" in items


def test_cold_chain_impossible_without_thermo_drones(post_quote, session, fleet):
    for name in ("thermo", "wing"):
        fleet[name].status = VehicleStatus.MAINTENANCE
    session.commit()
    q = post_quote(payload=INSULIN)
    assert q["feasible"] is False
    assert q["price"] is None and q["eta_seconds"] is None
    assert reason_codes(q) == {"temperature_control_unavailable"}


# --- weather -------------------------------------------------------------------------------


def test_gusty_weather_only_fixed_wing_can_fly(post_quote, fleet, world):
    world.weather = WeatherConditions(9.0, 13.0, 0.0, 16.0, 22.0)  # 13 m/s gusts
    q = post_quote(payload=INSULIN)  # quads limited to 11-12 m/s, fixed-wing to 15
    assert q["feasible"] is True


def test_storm_grounds_everything(post_quote, fleet, world):
    world.weather = WeatherConditions(14.0, 20.0, 6.0, 3.0, 18.0)
    q = post_quote()
    assert q["feasible"] is False
    assert reason_codes(q) == {
        "wind_too_high",
        "precipitation_too_heavy",
        "visibility_too_low",
    }


# --- fleet and range -----------------------------------------------------------------------


def test_no_drones_in_service(post_quote, session, fleet):
    for v in fleet.values():
        v.status = VehicleStatus.OFFLINE
    session.commit()
    q = post_quote()
    assert reason_codes(q) == {"no_vehicle_available"}


def test_long_trip_needs_long_range_drone(post_quote, session, fleet):
    # Range covers the whole loop: hub -> pickup -> drop-off -> hub, minus a 20% reserve.
    # Deep Ellum to White Rock: quad loop 15.8 km > 12.8 km usable;
    # fixed-wing loop (from the Medical District) 25.7 km < 32 km usable.
    white_rock = {"lat": 32.8270, "lng": -96.7160}
    light = {**BURRITO, "weight_kg": 1.0}
    assert post_quote(dropoff=white_rock, payload=light)["feasible"] is True

    fleet["wing"].status = VehicleStatus.MAINTENANCE
    session.commit()
    assert reason_codes(post_quote(dropoff=white_rock, payload=light)) == {"out_of_range"}


def test_fastest_drone_wins(post_quote, session, fleet):
    # Both quads can carry food; the fixed-wing is too far away to beat them.
    q = post_quote()
    assert q["feasible"] and q["eta_seconds"] < 12 * 60


# --- scheduling ----------------------------------------------------------------------------


def test_scheduled_pickup_later_today(post_quote, fleet, world):
    later = world.now + timedelta(hours=2)
    q = post_quote(pickup_at=later.isoformat())
    assert q["requested_pickup_at"] == "2026-10-06T20:00:00Z"
    assert q["estimated_pickup_at"] >= "2026-10-06T20:03:00Z"  # pickup + loading


def test_scheduled_pickup_at_night_is_infeasible(post_quote, fleet, world):
    q = post_quote(pickup_at=(world.now + timedelta(hours=10)).isoformat())  # 11 pm CDT
    assert "outside_daylight" in reason_codes(q)


@pytest.mark.parametrize("delta", [timedelta(hours=-1), timedelta(days=8)])
def test_pickup_time_out_of_bounds(post_quote, fleet, world, delta):
    post_quote(expected_status=422, pickup_at=(world.now + delta).isoformat())


def test_naive_pickup_time_rejected(post_quote, fleet):
    post_quote(expected_status=422, pickup_at="2026-10-06T20:00:00")


# --- validation and access -----------------------------------------------------------------


def test_same_pickup_and_dropoff_rejected(post_quote, fleet):
    post_quote(expected_status=422, dropoff=DEEP_ELLUM)


def test_prescription_food_rejected(post_quote, fleet):
    post_quote(expected_status=422, payload={**BURRITO, "prescription": True})


def test_quote_requires_api_key(client, fleet):
    body = {"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO}
    assert client.post("/v1/quotes", json=body).status_code == 401


def test_get_quote_only_for_owner(client, session, api_key, post_quote, fleet):
    q = post_quote()
    own = client.get(f"/v1/quotes/{q['id']}", headers={"Authorization": f"Bearer {api_key}"})
    assert own.status_code == 200 and own.json() == q

    from traveai.domain.enums import MerchantCategory
    from traveai.models import Merchant

    other = Merchant(name="Other Kitchen", category=MerchantCategory.RESTAURANT)
    session.add(other)
    other_key = other.issue_api_key()
    session.commit()
    response = client.get(f"/v1/quotes/{q['id']}", headers={"Authorization": f"Bearer {other_key}"})
    assert response.status_code == 404


def test_unknown_quote_404(client, api_key):
    response = client.get("/v1/quotes/quo_nope", headers={"Authorization": f"Bearer {api_key}"})
    assert response.status_code == 404
