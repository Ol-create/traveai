import json
from datetime import timedelta

import pytest

from traveai.models import WebhookMessage, WebhookMessageStatus
from traveai.webhooks.sender import MAX_ATTEMPTS, RETRY_DELAYS, check_url, fan_out, send_due
from traveai.webhooks.signing import SIGNATURE_HEADER, sign, verify

DEEP_ELLUM = {"lat": 32.7843, "lng": -96.7837}
LAKEWOOD = {"lat": 32.8120, "lng": -96.7520}
BURRITO = {"category": "food", "weight_kg": 1.2, "length_cm": 30, "width_cm": 25, "height_cm": 15}
HOOK_URL = "https://merchant.example.com/hooks"


@pytest.fixture
def headers(api_key):
    return {"Authorization": f"Bearer {api_key}"}


class FakeServer:
    """Records webhook calls and answers with the queued status codes (default 200)."""

    def __init__(self):
        self.calls: list[tuple[str, dict, bytes]] = []
        self.responses: list[int | Exception] = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        result = self.responses.pop(0) if self.responses else 200
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def register(client, headers):
    def _register(url=HOOK_URL, expect=201, **body):
        r = client.post("/v1/webhook_endpoints", json={"url": url, **body}, headers=headers)
        assert r.status_code == expect, r.text
        return r.json()

    return _register


@pytest.fixture
def book(client, headers, fleet):
    def _book():
        q = client.post(
            "/v1/quotes",
            json={"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO},
            headers=headers,
        ).json()
        return client.post("/v1/deliveries", json={"quote_id": q["id"]}, headers=headers).json()

    return _book


def pump(session, world, server):
    """One run of the webhook worker."""
    fan_out(session, world.now)
    session.commit()
    send_due(session, world.now, server)


# --- signing -------------------------------------------------------------------------------


def test_signature_roundtrip_and_tamper_detection():
    body = b'{"id":"evt_1"}'
    header = sign("whsec_x", 1_760_000_000, body)
    assert header.startswith("t=1760000000,v1=")
    assert verify(body, header, "whsec_x", now=1_760_000_010)
    assert not verify(b'{"id":"evt_2"}', header, "whsec_x", now=1_760_000_010)  # body changed
    assert not verify(body, header, "whsec_other", now=1_760_000_010)  # wrong secret
    assert not verify(body, header, "whsec_x", now=1_760_000_000 + 301)  # replayed later
    assert not verify(body, "garbage", "whsec_x")


# --- registering endpoints -----------------------------------------------------------------


def test_register_list_and_delete(client, headers, register):
    ep = register(enabled_events=["delivery.*"], description="orders")
    assert ep["id"].startswith("whe_") and ep["secret"].startswith("whsec_")
    assert ep["livemode"] is False

    listed = client.get("/v1/webhook_endpoints", headers=headers).json()["data"]
    assert [e["id"] for e in listed] == [ep["id"]]
    assert "secret" not in listed[0]  # shown only once

    assert client.delete(f"/v1/webhook_endpoints/{ep['id']}", headers=headers).status_code == 204
    assert client.get(f"/v1/webhook_endpoints/{ep['id']}", headers=headers).status_code == 404


def test_register_validation(register):
    register(url="ftp://example.com/x", expect=422)
    register(enabled_events=["order.*"], expect=422)  # matches nothing we send
    register(url="http://localhost:9000/hooks")  # fine in test mode


def test_live_key_needs_https(client, session, merchant):
    live = merchant.issue_api_key(test=False)
    session.commit()
    r = client.post(
        "/v1/webhook_endpoints",
        json={"url": "http://example.com/hooks"},
        headers={"Authorization": f"Bearer {live}"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_url"


def test_live_urls_must_not_reach_internal_networks():
    from traveai.webhooks.sender import UnsafeUrlError

    for url in ("https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x"):
        with pytest.raises(UnsafeUrlError):
            check_url(url, livemode=True)
    check_url("http://localhost/x", livemode=False)  # allowed in test mode


def test_rotate_secret(client, headers, register):
    ep = register()
    rotated = client.post(f"/v1/webhook_endpoints/{ep['id']}/rotate_secret", headers=headers)
    assert rotated.json()["secret"] != ep["secret"]


# --- sending -------------------------------------------------------------------------------


def test_event_is_sent_signed_with_delivery_object(register, book, session, world, server):
    ep = register()
    d = book()
    pump(session, world, server)

    [(url, sent_headers, body)] = server.calls
    assert url == HOOK_URL
    assert verify(body, sent_headers[SIGNATURE_HEADER], ep["secret"], now=world.now.timestamp())
    payload = json.loads(body)
    assert payload["type"] == "delivery.scheduled"
    assert payload["id"] == sent_headers["TraveAI-Event-Id"]
    assert payload["data"]["object"]["id"] == d["id"]
    assert payload["data"]["object"]["status"] == "scheduled"


def test_only_matching_events_are_sent(client, headers, register, book, session, world, server):
    register(enabled_events=["delivery.canceled"])
    d = book()
    client.post(f"/v1/deliveries/{d['id']}/cancel", headers=headers)
    pump(session, world, server)
    assert [json.loads(b)["type"] for _, _, b in server.calls] == ["delivery.canceled"]


def test_events_before_registration_are_not_sent(register, book, session, world, server):
    book()
    pump(session, world, server)  # nobody listening yet
    register()
    pump(session, world, server)
    assert server.calls == []


def test_other_merchants_events_are_not_sent(register, session, world, server, client, fleet):
    from traveai.domain.enums import MerchantCategory
    from traveai.models import Merchant

    register()
    other = Merchant(name="Other", category=MerchantCategory.RESTAURANT)
    session.add(other)
    key = other.issue_api_key()
    session.commit()
    h = {"Authorization": f"Bearer {key}"}
    q = client.post(
        "/v1/quotes",
        json={"pickup": DEEP_ELLUM, "dropoff": LAKEWOOD, "payload": BURRITO},
        headers=h,
    ).json()
    client.post("/v1/deliveries", json={"quote_id": q["id"]}, headers=h)
    pump(session, world, server)
    assert server.calls == []


def test_failed_sends_are_retried_with_backoff(register, book, session, world, server):
    register()
    book()
    server.responses = [500, ConnectionError("refused"), 200]

    pump(session, world, server)
    message = session.query(WebhookMessage).one()
    assert (message.attempts, message.last_error) == (1, "HTTP 500")
    assert message.next_attempt_at == world.now + RETRY_DELAYS[0]

    pump(session, world, server)  # too early: nothing sent
    assert len(server.calls) == 1

    world.now += RETRY_DELAYS[0]
    pump(session, world, server)
    assert message.attempts == 2 and "refused" in message.last_error

    world.now += RETRY_DELAYS[1]
    pump(session, world, server)
    assert message.status == WebhookMessageStatus.SUCCEEDED
    assert message.delivered_at == world.now and message.last_status_code == 200


def test_gives_up_after_all_retries(register, book, session, world, server):
    register()
    book()
    server.responses = [503] * MAX_ATTEMPTS
    for _ in range(MAX_ATTEMPTS):
        pump(session, world, server)
        world.now += timedelta(hours=7)
    message = session.query(WebhookMessage).one()
    assert message.status == WebhookMessageStatus.FAILED
    assert message.attempts == MAX_ATTEMPTS
    pump(session, world, server)
    assert len(server.calls) == MAX_ATTEMPTS


def test_message_log_endpoint(client, headers, register, book, session, world, server):
    ep = register()
    book()
    pump(session, world, server)
    log = client.get(f"/v1/webhook_endpoints/{ep['id']}/messages", headers=headers).json()["data"]
    assert log[0]["status"] == "succeeded" and log[0]["event_type"] == "delivery.scheduled"
