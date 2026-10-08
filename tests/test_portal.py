from datetime import timedelta

import pytest

from traveai.admin import enable_live, invite_user
from traveai.config import Settings
from traveai.domain.enums import MerchantCategory
from traveai.models import Merchant, User
from traveai.portal.api import get_login_limiter
from traveai.ratelimit import MemoryRateLimiter

PASSWORD = "correct horse battery"
SIGNUP = {
    "business_name": "Uptown Pharmacy",
    "category": "pharmacy",
    "name": "Ada Lovelace",
    "email": "Ada@Example.com",
    "password": PASSWORD,
}
PICKUP = {"lat": 32.7843, "lng": -96.7837}
DROPOFF = {"lat": 32.8120, "lng": -96.7520}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}


class Portal:
    """A logged-in browser: session cookie in the client, CSRF token echoed on writes."""

    def __init__(self, client, me):
        self.client, self.me = client, me

    def get(self, path, **kw):
        return self.client.get(f"/portal/api{path}", **kw)

    def post(self, path, json=None, csrf=True):
        headers = {"X-CSRF-Token": self.me["csrf_token"]} if csrf else {}
        return self.client.post(f"/portal/api{path}", json=json, headers=headers)

    def delete(self, path):
        return self.client.delete(
            f"/portal/api{path}", headers={"X-CSRF-Token": self.me["csrf_token"]}
        )


@pytest.fixture
def signed_up(client):
    r = client.post("/portal/api/signup", json=SIGNUP)
    assert r.status_code == 201, r.text
    return r


@pytest.fixture
def portal(client, signed_up):
    return Portal(client, signed_up.json())


def api_key_headers(key):
    return {"Authorization": f"Bearer {key}"}


# --- sign-up, login, sessions -------------------------------------------------------------


def test_signup_creates_business_login_and_first_key(client, signed_up, session):
    body = signed_up.json()
    assert body["merchant"]["name"] == "Uptown Pharmacy"
    assert body["merchant"]["live_enabled"] is False
    assert body["user"]["email"] == "ada@example.com"  # normalised
    # The first test key works on the public API straight away.
    me = client.get("/v1/me", headers=api_key_headers(body["first_test_key"])).json()
    assert me["merchant_id"] == body["merchant"]["id"] and me["test_mode"] is True

    cookie = signed_up.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    stored = session.query(User).one()
    assert PASSWORD not in stored.password_hash and stored.password_hash.startswith("scrypt$")


def test_signup_validation(client, signed_up, monkeypatch):
    dup = client.post("/portal/api/signup", json={**SIGNUP, "email": "ada@example.com"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "email_taken"
    assert (
        client.post(
            "/portal/api/signup", json={**SIGNUP, "email": "b@example.com", "password": "short"}
        ).status_code
        == 422
    )
    assert (
        client.post("/portal/api/signup", json={**SIGNUP, "email": "not-an-email"}).status_code
        == 422
    )

    monkeypatch.setattr(
        "traveai.portal.api.get_settings",
        lambda: Settings(_env_file=None, portal_signup_enabled=False),
    )
    off = client.post("/portal/api/signup", json={**SIGNUP, "email": "c@example.com"})
    assert off.status_code == 403 and off.json()["detail"]["code"] == "signup_disabled"


def test_login_errors_do_not_reveal_accounts(client, signed_up):
    client.cookies.clear()
    wrong_password = client.post(
        "/portal/api/login", json={"email": SIGNUP["email"], "password": "nope nope nope"}
    )
    unknown_email = client.post(
        "/portal/api/login", json={"email": "who@example.com", "password": PASSWORD}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()

    ok = client.post("/portal/api/login", json={"email": " ADA@example.com ", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["csrf_token"]
    assert client.get("/portal/api/me").status_code == 200


def test_login_is_rate_limited(client, signed_up):
    limiter = MemoryRateLimiter(limit=2)
    client.app.dependency_overrides[get_login_limiter] = lambda: limiter
    bad = {"email": SIGNUP["email"], "password": "wrong password!"}
    assert client.post("/portal/api/login", json=bad).status_code == 401
    assert client.post("/portal/api/login", json=bad).status_code == 401
    blocked = client.post("/portal/api/login", json={**bad, "password": PASSWORD})
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers


def test_writes_need_the_csrf_token(portal):
    no_token = portal.post("/keys", json={"label": "x"}, csrf=False)
    assert no_token.status_code == 403 and no_token.json()["detail"]["code"] == "csrf_failed"
    assert portal.post("/keys", json={"label": "x"}).status_code == 201


def test_logout_ends_the_session_server_side(client, portal):
    stolen_cookie = client.cookies.get("traveai_session")
    assert portal.post("/logout").status_code == 204
    assert client.get("/portal/api/me").status_code == 401
    client.cookies.set("traveai_session", stolen_cookie)  # replaying the old cookie
    assert client.get("/portal/api/me").status_code == 401


def test_sessions_expire(client, portal, world):
    world.now += timedelta(days=8)
    r = client.get("/portal/api/me")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "not_logged_in"


# --- API keys -------------------------------------------------------------------------------


def test_create_list_revoke_keys(client, portal):
    created = portal.post("/keys", json={"label": "Checkout server"}).json()
    assert created["secret"].startswith("sk_test_") and created["label"] == "Checkout server"
    listed = portal.get("/keys").json()["data"]
    assert [k["label"] for k in listed] == ["First test key", "Checkout server"]
    assert all("secret" not in k for k in listed)

    assert client.get("/v1/me", headers=api_key_headers(created["secret"])).status_code == 200
    portal.post(f"/keys/{created['id']}/revoke")
    assert client.get("/v1/me", headers=api_key_headers(created["secret"])).status_code == 401


def test_live_keys_need_a_verified_business(portal, session):
    denied = portal.post("/keys", json={"mode": "live"})
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "live_not_enabled"

    enable_live(session, portal.me["merchant"]["id"])
    session.expire_all()
    live = portal.post("/keys", json={"mode": "live", "label": "Production"}).json()
    assert live["secret"].startswith("sk_live_") and live["mode"] == "live"


# --- deliveries, webhooks, usage -----------------------------------------------------------


def book(client, key, ref=None):
    h = api_key_headers(key)
    q = client.post(
        "/v1/quotes", json={"pickup": PICKUP, "dropoff": DROPOFF, "payload": BURRITO}, headers=h
    ).json()
    body = {"quote_id": q["id"], **({"external_reference": ref} if ref else {})}
    return client.post("/v1/deliveries", json=body, headers=h).json()


def test_deliveries_list_search_detail_cancel(client, portal, fleet, world):
    key = portal.me["first_test_key"]
    a = book(client, key, ref="ORD-1")
    world.now += timedelta(seconds=1)
    b = book(client, key, ref="ORD-2")

    listed = portal.get("/deliveries").json()["data"]
    assert [d["id"] for d in listed] == [b["id"], a["id"]]
    assert [d["id"] for d in portal.get("/deliveries?q=ORD-1").json()["data"]] == [a["id"]]
    assert [d["id"] for d in portal.get(f"/deliveries?q={b['id']}").json()["data"]] == [b["id"]]
    assert portal.get(f"/deliveries/{a['id']}").json()["tracking_url"] == a["tracking_url"]
    assert (
        portal.get(f"/deliveries/{a['id']}/events").json()["data"][0]["type"]
        == "delivery.scheduled"
    )

    canceled = portal.post(f"/deliveries/{a['id']}/cancel").json()
    assert canceled["status"] == "canceled" and canceled["failure_reason"] == "canceled_in_portal"


def test_merchants_only_see_their_own_data(client, portal, fleet, session):
    other = Merchant(name="Other", category="restaurant")
    session.add(other)
    theirs = book(client, other.issue_api_key())
    session.commit()
    assert portal.get("/deliveries").json()["data"] == []
    assert portal.get(f"/deliveries?q={theirs['id']}").json()["data"] == []
    assert portal.get(f"/deliveries/{theirs['id']}").status_code == 404


def test_webhooks_via_portal(portal):
    created = portal.post("/webhooks", json={"url": "http://localhost:9000/hooks"}).json()
    assert created["secret"].startswith("whsec_") and created["livemode"] is False
    assert [e["id"] for e in portal.get("/webhooks").json()["data"]] == [created["id"]]
    rotated = portal.post(f"/webhooks/{created['id']}/rotate_secret").json()
    assert rotated["secret"] != created["secret"]
    assert portal.get(f"/webhooks/{created['id']}/messages").json()["data"] == []
    live = portal.post("/webhooks", json={"url": "https://example.com/h", "mode": "live"})
    assert live.status_code == 403
    assert portal.delete(f"/webhooks/{created['id']}").status_code == 204
    assert portal.get("/webhooks").json()["data"] == []


def test_usage_summary(client, portal, fleet, world):
    key = portal.me["first_test_key"]
    done = book(client, key)
    book(client, key)  # still scheduled
    h = api_key_headers(key)
    for _ in range(5):
        client.post(f"/v1/test/deliveries/{done['id']}/advance", headers=h)

    u = portal.get("/usage?days=7").json()
    assert u["total"] == 2 and u["delivered"] == 1
    assert u["by_status"] == {"delivered": 1, "scheduled": 1}
    assert u["spend_cents"] == done["price"]["amount_cents"]
    assert u["on_time_rate"] == 1.0  # delivered at once, well before the estimate
    assert len(u["daily"]) == 8 and sum(d["deliveries"] for d in u["daily"]) == 2


# --- pages and admin ------------------------------------------------------------------------


def test_portal_page_headers_and_hidden_from_public_docs(client):
    r = client.get("/portal/")
    assert r.status_code == 200 and "TraveAI Portal" in r.text
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert client.get("/portal-assets/app.js").status_code == 200
    assert not [p for p in client.get("/openapi.json").json()["paths"] if p.startswith("/portal")]


def test_invited_user_can_log_in(client, session):
    merchant = Merchant(name="Invite-only Clinic", category=MerchantCategory.HOSPITAL)
    session.add(merchant)
    session.commit()
    user, temp_password = invite_user(session, merchant.id, "Nurse@Clinic.org", "Nurse Joy")
    r = client.post(
        "/portal/api/login", json={"email": "nurse@clinic.org", "password": temp_password}
    )
    assert r.status_code == 200 and r.json()["merchant"]["id"] == merchant.id
