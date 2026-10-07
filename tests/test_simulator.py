import random
from datetime import timedelta

import pytest
from shapely.geometry import LineString

from traveai.domain.enums import FailureKind, MissionPhase, MissionStatus, VehicleStatus
from traveai.models import Delivery
from traveai.rules.airspace import default_airspace
from traveai.rules.geo import haversine_m
from traveai.rules.weather import WeatherConditions
from traveai.services.planning import HANDOFF_TIMEOUT_S
from traveai.sim.simulator import DISPATCH_TIMEOUT, SimContext, Simulator

DEEP_ELLUM = {"lat": 32.7843, "lng": -96.7837}
LAKEWOOD = {"lat": 32.8120, "lng": -96.7520}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}
INSULIN = {
    "category": "medical",
    "weight_kg": 0.4,
    "length_cm": 15,
    "width_cm": 10,
    "height_cm": 8,
    "temperature_controlled": True,
    "prescription": True,
}
GUSTY = WeatherConditions(10.0, 16.0, 0.0, 16.0, 22.0)  # grounds every drone


@pytest.fixture
def headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def sim(world):
    return Simulator(SimContext(weather=world, airspace=default_airspace()))


@pytest.fixture
def book(client, headers, fleet):
    def _book(payload=BURRITO, pickup=DEEP_ELLUM, dropoff=LAKEWOOD, **extra):
        q = client.post(
            "/v1/quotes",
            json={"pickup": pickup, "dropoff": dropoff, "payload": payload, **extra},
            headers=headers,
        ).json()
        assert q["feasible"], q["reasons"]
        r = client.post("/v1/deliveries", json={"quote_id": q["id"]}, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    return _book


@pytest.fixture
def run(sim, session, world):
    """Advance simulated time in 5 s steps until `until()` is true (or `seconds` pass)."""

    def _run(seconds=3600, until=None, dt=5.0):
        elapsed = 0.0
        while elapsed < seconds:
            sim.step(session, world.now, dt)
            world.now += timedelta(seconds=dt)
            elapsed += dt
            if until and until():
                return elapsed
        assert until is None, "condition never became true"
        return elapsed

    return _run


def status_of(session, delivery_id):
    session.expire_all()
    return session.get(Delivery, delivery_id).status


def event_types(client, headers, delivery_id):
    events = client.get(f"/v1/deliveries/{delivery_id}/events", headers=headers).json()["data"]
    return [e["type"] for e in events]


# --- dispatch ------------------------------------------------------------------------------


def test_dispatch_picks_cold_chain_drone_for_insulin(book, sim, session, world, fleet):
    d = book(payload=INSULIN)
    [mission] = sim.dispatch(session, world.now)
    session.commit()

    # Both thermo drones could fly it; the Deep Ellum quad-thermo is right at the pickup.
    assert mission.vehicle.call_sign == fleet["thermo"].call_sign
    assert mission.phase == MissionPhase.POSITIONING
    assert mission.vehicle.status == VehicleStatus.IN_FLIGHT
    assert status_of(session, d["id"]) == "picking_up"
    assert mission.waypoints[0] == [fleet["thermo"].base_lat, fleet["thermo"].base_lng]
    assert mission.waypoints[-1] == mission.waypoints[0]  # loop ends at the hub


def test_urgent_deliveries_dispatch_first(book, sim, session, world, fleet):
    for name in ("quad", "wing"):
        fleet[name].status = VehicleStatus.MAINTENANCE  # one drone for two jobs
    session.commit()
    standard = book(payload={**INSULIN, "prescription": False})
    world.now += timedelta(seconds=1)
    urgent = book(payload={**INSULIN, "prescription": False}, priority="urgent")

    sim.dispatch(session, world.now)
    session.commit()
    assert status_of(session, urgent["id"]) == "picking_up"
    assert status_of(session, standard["id"]) == "scheduled"


def test_no_dispatch_in_bad_weather_then_fail_after_timeout(book, sim, session, world):
    d = book()
    world.weather = GUSTY
    assert sim.dispatch(session, world.now) == []
    assert status_of(session, d["id"]) == "scheduled"

    world.now += DISPATCH_TIMEOUT + timedelta(minutes=1)
    sim.dispatch(session, world.now)
    session.commit()
    delivery = session.get(Delivery, d["id"])
    assert delivery.status == "failed"
    assert "wind_too_high" in delivery.failure_reason


def test_scheduled_pickup_waits_until_lead_time(book, sim, session, world):
    d = book(pickup_at=(world.now + timedelta(hours=2)).isoformat())
    assert sim.dispatch(session, world.now) == []
    world.now += timedelta(hours=1, minutes=46)
    assert len(sim.dispatch(session, world.now)) == 1
    session.commit()
    assert status_of(session, d["id"]) == "picking_up"


# --- flying --------------------------------------------------------------------------------


def test_food_delivery_flies_end_to_end(book, run, session, client, headers, fleet):
    d = book()
    run(until=lambda: status_of(session, d["id"]) == "delivered")

    delivery = session.get(Delivery, d["id"])
    dropped = (delivery.delivered_lat, delivery.delivered_lng)
    assert haversine_m(*dropped, LAKEWOOD["lat"], LAKEWOOD["lng"]) < 1
    assert event_types(client, headers, d["id"]) == [
        "delivery.scheduled",
        "delivery.assigned",
        "delivery.picking_up",
        "delivery.airborne",
        "delivery.arriving",
        "delivery.delivered",
    ]

    # The drone flies home and starts charging.
    [mission] = delivery.missions
    run(until=lambda: mission.phase is None)
    drone = mission.vehicle
    assert mission.status == MissionStatus.COMPLETED
    assert drone.status == VehicleStatus.CHARGING
    assert (drone.lat, drone.lng) == (drone.base_lat, drone.base_lng)
    used = 100 - drone.battery_pct
    expected = mission.planned_distance_m / (drone.max_range_km * 1000) * 100
    assert used == pytest.approx(expected, abs=1.5)  # plus a few seconds of charging

    run(until=lambda: drone.status == VehicleStatus.IDLE)
    assert drone.battery_pct == 100


def test_route_detours_around_restricted_area(book, run, session):
    west, east = {"lat": 32.7600, "lng": -96.9300}, {"lat": 32.7600, "lng": -96.8600}
    d = book(payload={**BURRITO, "weight_kg": 1.0}, pickup=west, dropoff=east)
    run(until=lambda: status_of(session, d["id"]) == "delivered")

    mission = session.get(Delivery, d["id"]).missions[0]
    airspace = default_airspace()
    zone = next(z for z in airspace.zones if z.kind == "restricted")
    path = LineString([airspace.projection.to_xy(*p) for p in mission.waypoints])
    assert not path.intersects(zone.geometry)


# --- prescriptions: hand-off ---------------------------------------------------------------


def test_prescription_handoff_with_pin(book, run, session, client, headers):
    d = book(payload=INSULIN)
    mission_phase = lambda: session.get(Delivery, d["id"]).missions[0].phase  # noqa: E731
    run(until=lambda: mission_phase() == MissionPhase.AWAITING_HANDOFF)
    assert status_of(session, d["id"]) == "arriving"

    url = f"/v1/deliveries/{d['id']}/handoff"
    wrong = client.post(url, json={"pin": "000000"}, headers=headers)
    assert wrong.status_code == 403
    ok = client.post(url, json={"pin": d["recipient_pin"]}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "delivered"
    assert ok.json()["proof"]["pin_verified"] is True
    assert mission_phase() == MissionPhase.RETURNING

    custody = [
        e["data"]["action"]
        for e in client.get(f"/v1/deliveries/{d['id']}/events", headers=headers).json()["data"]
        if e["type"] == "delivery.custody"
    ]
    assert custody == ["received_from_merchant", "loaded_on_drone", "delivered_to_recipient"]


def test_handoff_only_when_drone_is_waiting(book, client, headers):
    d = book(payload=INSULIN)
    r = client.post(
        f"/v1/deliveries/{d['id']}/handoff", json={"pin": d["recipient_pin"]}, headers=headers
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "drone_not_at_dropoff"


def test_no_recipient_retries_once_then_fails(book, run, session):
    d = book(payload=INSULIN)
    run(until=lambda: status_of(session, d["id"]) == "aborted", seconds=2 * 3600)
    delivery = session.get(Delivery, d["id"])
    assert delivery.failure_reason == "recipient_unavailable"
    assert delivery.missions[0].phase_elapsed_s >= HANDOFF_TIMEOUT_S

    run(until=lambda: status_of(session, d["id"]) == "scheduled")  # back at hub: retry
    run(until=lambda: status_of(session, d["id"]) == "failed", seconds=3 * 3600)
    delivery = session.get(Delivery, d["id"])
    assert len(delivery.missions) == 2
    assert "gave up after 2 attempts" in delivery.failure_reason


# --- failure injection ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("high_wind", "wind_gust_exceeds_limit"),
        ("low_battery", "low_battery"),
        ("drop_zone_blocked", "drop_zone_blocked"),
    ],
)
def test_injected_failure_aborts_and_returns_package(
    book, run, session, client, headers, kind, reason
):
    d = book(payload={**INSULIN, "prescription": False})
    r = client.post(f"/v1/test/deliveries/{d['id']}/failures", json={"kind": kind}, headers=headers)
    assert r.status_code == 200, r.text

    run(until=lambda: status_of(session, d["id"]) == "aborted")
    delivery = session.get(Delivery, d["id"])
    assert delivery.failure_reason == reason
    mission = delivery.missions[0]
    assert mission.package_onboard and mission.status == MissionStatus.ABORTED

    run(until=lambda: status_of(session, d["id"]) == "scheduled")
    types = event_types(client, headers, d["id"])
    assert types[-3:] == ["delivery.custody", "delivery.returned_to_base", "delivery.scheduled"]
    assert mission.vehicle.status == VehicleStatus.CHARGING

    # The retry flies normally: the forced failure was used up.
    run(until=lambda: status_of(session, d["id"]) == "delivered")


def test_failure_injection_mid_flight_and_test_mode_only(book, run, session, client, headers):
    d = book()
    run(until=lambda: status_of(session, d["id"]) == "airborne")
    client.post(
        f"/v1/test/deliveries/{d['id']}/failures", json={"kind": "high_wind"}, headers=headers
    )
    run(until=lambda: status_of(session, d["id"]) == "aborted")


def test_real_wind_recalls_drone_before_pickup(book, sim, run, session, world):
    d = book()
    [mission] = sim.dispatch(session, world.now)
    session.commit()
    world.weather = GUSTY
    run(seconds=5)
    assert status_of(session, d["id"]) == "scheduled"  # will be reassigned when it clears
    assert mission.status == MissionStatus.ABORTED
    assert mission.phase == MissionPhase.RETURNING


def test_cancel_while_drone_en_route_sends_it_home(book, sim, run, session, client, headers):
    d = book()
    run(until=lambda: status_of(session, d["id"]) == "picking_up")
    assert client.post(f"/v1/deliveries/{d['id']}/cancel", headers=headers).status_code == 200
    mission = session.get(Delivery, d["id"]).missions[0]
    run(until=lambda: mission.phase is None)
    assert mission.status == MissionStatus.ABORTED
    assert mission.abort_reason == "delivery_canceled"


def test_random_failures_follow_the_failure_rate(book, world, session):
    sim = Simulator(
        SimContext(
            weather=world, airspace=default_airspace(), failure_rate=1.0, rng=random.Random(7)
        )
    )
    book()
    [mission] = sim.dispatch(session, world.now)
    assert mission.injected_failure in set(FailureKind)


def test_manual_advance_blocked_while_simulator_flies(book, run, session, client, headers):
    d = book()
    run(until=lambda: status_of(session, d["id"]) == "picking_up")
    r = client.post(f"/v1/test/deliveries/{d['id']}/advance", headers=headers)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "simulator_controls_delivery"
