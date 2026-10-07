from datetime import timedelta

import pytest

from traveai.domain.enums import DropZoneKind, MerchantCategory
from traveai.models import Delivery, DropZone, Merchant

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

HAPPY_PATH = ["assigned", "picking_up", "airborne", "arriving"]


@pytest.fixture
def headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def api(client, headers):
    """Small helper: api.post(path, json, expect=201) -> parsed JSON."""

    class Api:
        def post(self, path, json=None, expect=200):
            r = client.post(path, json=json, headers=headers)
            assert r.status_code == expect, r.text
            return r.json()

        def get(self, path, expect=200, **params):
            r = client.get(path, params=params, headers=headers)
            assert r.status_code == expect, r.text
            return r.json()

        def quote(self, payload=BURRITO, dropoff=LAKEWOOD):
            body = {"pickup": DEEP_ELLUM, "dropoff": dropoff, "payload": payload}
            return self.post("/v1/quotes", body, expect=201)

        def book(self, payload=BURRITO, expect=201, **extra):
            q = self.quote(payload)
            return self.post("/v1/deliveries", {"quote_id": q["id"], **extra}, expect=expect)

        def advance(self, delivery_id, expect=200, **body):
            return self.post(f"/v1/test/deliveries/{delivery_id}/advance", body, expect=expect)

    return Api()


def error_code(response_json) -> str:
    return response_json["detail"]["code"]


# --- booking -------------------------------------------------------------------------------


def test_book_food_delivery_from_quote(api, fleet):
    q = api.quote()
    d = api.post(
        "/v1/deliveries",
        {
            "quote_id": q["id"],
            "recipient": {"name": "Ada", "phone": "+12145550123"},
            "external_reference": "order-1001",
        },
        expect=201,
    )
    assert d["id"].startswith("del_") and d["object"] == "delivery"
    assert d["status"] == "scheduled"
    assert d["quote_id"] == q["id"]
    assert d["price"] == {"amount_cents": q["price"]["amount_cents"], "currency": "usd"}
    assert d["estimated_dropoff_at"] == q["estimated_dropoff_at"]
    assert d["recipient"] == {"name": "Ada", "phone": "+12145550123"}
    assert d["recipient_pin"] is None
    assert d["proof"] is None

    events = api.get(f"/v1/deliveries/{d['id']}/events")["data"]
    assert [e["type"] for e in events] == ["delivery.scheduled"]


def test_prescription_gets_one_time_pin(api, fleet, session):
    d = api.book(payload=INSULIN)
    pin = d["recipient_pin"]
    assert pin and pin.isdigit() and len(pin) == 6
    assert "recipient_pin" in d["requirements"]
    # Not shown again, and not stored in plain text.
    assert "recipient_pin" not in api.get(f"/v1/deliveries/{d['id']}")
    stored = session.get(Delivery, d["id"]).recipient_pin_hash
    assert pin not in stored and stored.startswith("pbkdf2$")


def test_merchant_chosen_pin_opts_any_delivery_in(api, fleet):
    d = api.book(recipient_pin="2468")
    assert d["recipient_pin"] == "2468"
    assert "recipient_pin" in d["requirements"]


def test_quote_can_only_be_booked_once(api, fleet):
    q = api.quote()
    first = api.post("/v1/deliveries", {"quote_id": q["id"]}, expect=201)
    second = api.post("/v1/deliveries", {"quote_id": q["id"]}, expect=409)
    assert error_code(second) == "quote_already_used"
    assert first["id"] in second["detail"]["message"]


def test_expired_quote_rejected(api, fleet, world):
    q = api.quote()
    world.now += timedelta(minutes=6)
    r = api.post("/v1/deliveries", {"quote_id": q["id"]}, expect=409)
    assert error_code(r) == "quote_expired"


def test_infeasible_quote_rejected(api, fleet, world):
    world.now = world.now + timedelta(hours=10)  # 11 pm in Dallas
    q = api.quote()
    assert q["feasible"] is False
    r = api.post("/v1/deliveries", {"quote_id": q["id"]}, expect=409)
    assert error_code(r) == "quote_not_feasible"


def test_unknown_or_foreign_quote_is_404(api, fleet, client, session):
    assert error_code(api.post("/v1/deliveries", {"quote_id": "quo_x"}, expect=404)) == (
        "quote_not_found"
    )
    q = api.quote()
    other = Merchant(name="Other", category=MerchantCategory.RESTAURANT)
    session.add(other)
    key = other.issue_api_key()
    session.commit()
    r = client.post(
        "/v1/deliveries", json={"quote_id": q["id"]}, headers={"Authorization": f"Bearer {key}"}
    )
    assert r.status_code == 404


def test_bad_recipient_phone_and_pin_rejected(api, fleet):
    q = api.quote()
    api.post("/v1/deliveries", {"quote_id": q["id"], "recipient": {"phone": "555-1234"}}, 422)
    api.post("/v1/deliveries", {"quote_id": q["id"], "recipient_pin": "12"}, 422)


# --- drop zones ----------------------------------------------------------------------------


@pytest.fixture
def lakewood_yard(session):
    zone = DropZone(
        label="Backyard",
        kind=DropZoneKind.YARD,
        lat=LAKEWOOD["lat"] + 0.0002,  # ~22 m away
        lng=LAKEWOOD["lng"],
        radius_m=30,
        verified=True,
    )
    session.add(zone)
    session.commit()
    return zone


def test_book_with_drop_zone(api, fleet, lakewood_yard):
    d = api.book(dropoff_zone_id=lakewood_yard.id)
    assert d["dropoff_zone_id"] == lakewood_yard.id


def test_drop_zone_must_be_verified_and_nearby(api, fleet, session, lakewood_yard):
    far = DropZone(label="Far", kind=DropZoneKind.LOCKER, lat=32.75, lng=-96.83, verified=True)
    unverified = DropZone(
        label="New", kind=DropZoneKind.YARD, lat=LAKEWOOD["lat"], lng=LAKEWOOD["lng"]
    )
    session.add_all([far, unverified])
    session.commit()
    assert error_code(api.book(dropoff_zone_id=far.id, expect=422)) == "drop_zone_too_far"
    assert error_code(api.book(dropoff_zone_id=unverified.id, expect=422)) == "invalid_drop_zone"


# --- reading and listing -------------------------------------------------------------------


def test_list_newest_first_with_pagination(api, fleet, world):
    ids = []
    for _ in range(5):
        ids.append(api.book()["id"])
        world.now += timedelta(seconds=1)

    page1 = api.get("/v1/deliveries", limit=2)
    assert [d["id"] for d in page1["data"]] == ids[::-1][:2]
    assert page1["has_more"] is True

    page2 = api.get("/v1/deliveries", limit=2, starting_after=page1["data"][-1]["id"])
    page3 = api.get("/v1/deliveries", limit=2, starting_after=page2["data"][-1]["id"])
    assert [d["id"] for d in page2["data"] + page3["data"]] == ids[::-1][2:]
    assert page3["has_more"] is False


def test_list_filters(api, fleet, world):
    a = api.book(external_reference="A-1")
    world.now += timedelta(minutes=1)
    b = api.book()
    api.post(f"/v1/deliveries/{b['id']}/cancel")

    def ids(**params):
        return [d["id"] for d in api.get("/v1/deliveries", **params)["data"]]

    assert ids(external_reference="A-1") == [a["id"]]
    assert ids(status="canceled") == [b["id"]]
    assert set(ids(status=["canceled", "scheduled"])) == {a["id"], b["id"]}
    assert ids(created_gte=b["created_at"]) == [b["id"]]
    assert ids(created_lt=b["created_at"]) == [a["id"]]


def test_list_only_shows_own_deliveries(api, fleet, client, session):
    api.book()
    other = Merchant(name="Other", category=MerchantCategory.RESTAURANT)
    session.add(other)
    key = other.issue_api_key()
    session.commit()
    r = client.get("/v1/deliveries", headers={"Authorization": f"Bearer {key}"})
    assert r.json()["data"] == []


# --- cancel --------------------------------------------------------------------------------


def test_cancel_before_takeoff(api, fleet):
    d = api.book()
    api.advance(d["id"])  # assigned
    out = api.post(f"/v1/deliveries/{d['id']}/cancel", {"reason": "customer changed mind"})
    assert out["status"] == "canceled"
    assert out["failure_reason"] == "customer changed mind"


def test_cannot_cancel_in_the_air(api, fleet):
    d = api.book()
    for _ in range(3):
        api.advance(d["id"])  # -> airborne
    r = api.post(f"/v1/deliveries/{d['id']}/cancel", expect=409)
    assert error_code(r) == "cannot_cancel"
    assert "aborted instead" in r["detail"]["message"]


def test_cannot_cancel_twice(api, fleet):
    d = api.book()
    api.post(f"/v1/deliveries/{d['id']}/cancel")
    assert error_code(api.post(f"/v1/deliveries/{d['id']}/cancel", expect=409)) == "cannot_cancel"


# --- full lifecycle and proof of delivery --------------------------------------------------


def test_food_delivery_full_lifecycle_with_proof(api, fleet, world, client, headers):
    d = api.book()
    for expected in HAPPY_PATH:
        assert api.advance(d["id"])["status"] == expected
    done = api.advance(d["id"])

    assert done["status"] == "delivered"
    proof = done["proof"]
    assert proof["delivered_at"] == "2026-10-06T18:00:00Z"
    assert (proof["lat"], proof["lng"]) == (LAKEWOOD["lat"], LAKEWOOD["lng"])
    assert proof["pin_verified"] is False

    photo = client.get(proof["photo_url"], headers=headers)
    assert photo.status_code == 200
    assert photo.headers["content-type"].startswith("image/svg+xml")
    assert d["id"] in photo.text

    types = [e["type"] for e in api.get(f"/v1/deliveries/{d['id']}/events")["data"]]
    assert types == [
        "delivery.scheduled",
        *(f"delivery.{s}" for s in HAPPY_PATH),
        "delivery.delivered",
    ]


def test_prescription_needs_correct_pin_and_logs_custody(api, fleet):
    d = api.book(payload=INSULIN, recipient={"name": "Grace"})
    for _ in HAPPY_PATH:
        api.advance(d["id"])

    wrong = api.advance(d["id"], pin="000000", expect=403)
    assert error_code(wrong) == "invalid_pin"
    assert "4 left" in wrong["detail"]["message"]
    assert error_code(api.advance(d["id"], expect=403)) == "invalid_pin"  # missing PIN

    done = api.advance(d["id"], pin=d["recipient_pin"])
    assert done["status"] == "delivered"
    assert done["proof"]["pin_verified"] is True

    events = api.get(f"/v1/deliveries/{d['id']}/events")["data"]
    custody = [e["data"]["action"] for e in events if e["type"] == "delivery.custody"]
    assert custody == ["received_from_merchant", "loaded_on_drone", "delivered_to_recipient"]
    assert [e["data"]["attempts"] for e in events if e["type"] == "delivery.pin_failed"] == [1, 2]


def test_pin_locks_after_five_wrong_attempts(api, fleet):
    d = api.book(payload=INSULIN)
    for _ in HAPPY_PATH:
        api.advance(d["id"])
    for _ in range(5):
        api.advance(d["id"], pin="000000", expect=403)
    locked = api.advance(d["id"], pin=d["recipient_pin"], expect=409)
    assert error_code(locked) == "pin_locked"

    # The drone gives up and flies the package home.
    assert api.advance(d["id"], to="aborted", reason="pin_locked")["status"] == "aborted"
    assert api.advance(d["id"])["status"] == "returned_to_base"


def test_abort_and_retry_path(api, fleet):
    d = api.book()
    for _ in range(3):
        api.advance(d["id"])
    aborted = api.advance(d["id"], to="aborted", reason="wind_gust_exceeds_limit")
    assert aborted["failure_reason"] == "wind_gust_exceeds_limit"
    assert api.advance(d["id"])["status"] == "returned_to_base"
    assert api.advance(d["id"])["status"] == "scheduled"


def test_illegal_test_transition_rejected(api, fleet):
    d = api.book()
    assert error_code(api.advance(d["id"], to="arriving", expect=409)) == "invalid_transition"


def test_final_status_cannot_advance(api, fleet):
    d = api.book()
    api.post(f"/v1/deliveries/{d['id']}/cancel")
    assert error_code(api.advance(d["id"], expect=409)) == "invalid_state"


def test_no_proof_before_delivery(api, fleet):
    d = api.book()
    r = api.get(f"/v1/deliveries/{d['id']}/proof.svg", expect=404)
    assert error_code(r) == "no_proof"


def test_advance_requires_test_mode_key(api, fleet, client, session, merchant):
    d = api.book()
    live_key = merchant.issue_api_key(test=False)
    session.commit()
    r = client.post(
        f"/v1/test/deliveries/{d['id']}/advance", headers={"Authorization": f"Bearer {live_key}"}
    )
    assert r.status_code == 403
    assert error_code(r.json()) == "test_mode_only"
