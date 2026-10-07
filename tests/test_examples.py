"""Run the scripts in examples/ against the app, with the simulator flying in place of real
waiting. Keeps the examples working as the API evolves."""

import sys
import threading
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path

import httpx
import pytest

from traveai.deps import get_sleep
from traveai.rules.airspace import default_airspace
from traveai.sim.simulator import SimContext, Simulator
from traveai.webhooks.signing import sign

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import pharmacy_prescription  # noqa: E402
import restaurant_meal  # noqa: E402
import webhook_receiver  # noqa: E402


@pytest.fixture
def merchant_client(client, api_key):
    client.headers["Authorization"] = f"Bearer {api_key}"
    return client


@pytest.fixture
def fly(session, world):
    sim = Simulator(SimContext(weather=world, airspace=default_airspace()))

    def _fly(seconds: float = 30) -> None:
        for _ in range(int(seconds / 5)):
            sim.step(session, world.now, 5)
            world.now += timedelta(seconds=5)

    return _fly


def test_pharmacy_example_delivers_with_pin(merchant_client, fleet, fly):
    lines: list[str] = []
    final = pharmacy_prescription.run(merchant_client, wait=lambda _s: fly(30), log=lines.append)

    assert final["status"] == "delivered"
    assert final["proof"]["pin_verified"] is True
    assert final["external_reference"] == "RX-20931"
    assert any("PIN accepted" in line for line in lines)
    assert lines[-1] == (
        "Chain of custody: received_from_merchant -> loaded_on_drone -> delivered_to_recipient"
    )


def test_restaurant_example_streams_to_delivery(merchant_client, fleet, fly):
    async def sleep(_s: float) -> None:
        fly(30)

    merchant_client.app.dependency_overrides[get_sleep] = lambda: sleep
    lines: list[str] = []
    final = restaurant_meal.run(merchant_client, log=lines.append)

    assert final["status"] == "delivered"
    statuses = [line.strip() for line in lines if line.startswith("  ")]
    assert statuses == ["scheduled", "assigned", "picking_up", "airborne", "arriving", "delivered"]


def test_restaurant_example_forced_failure_retries(merchant_client, fleet, fly):
    async def sleep(_s: float) -> None:
        fly(30)

    merchant_client.app.dependency_overrides[get_sleep] = lambda: sleep
    lines: list[str] = []
    final = restaurant_meal.run(merchant_client, fail="high_wind", log=lines.append)

    statuses = [line.strip() for line in lines if line.startswith("  ")]
    assert "aborted" in statuses and "returned_to_base" in statuses
    assert final["status"] == "delivered"  # the retry made it


def test_restaurant_example_reports_infeasible_quote(merchant_client, fleet, world):
    world.now += timedelta(hours=10)  # 11 pm in Dallas: no night flying
    lines: list[str] = []
    with pytest.raises(RuntimeError, match="not feasible"):
        restaurant_meal.run(merchant_client, log=lines.append)
    assert any("outside_daylight" in line for line in lines)


# --- webhook receiver ----------------------------------------------------------------------


def test_receiver_verify_matches_server_signing():
    body = b'{"id":"evt_1"}'
    header = sign("whsec_abc", 1_760_000_000, body)
    assert webhook_receiver.verify(body, header, "whsec_abc", now=1_760_000_005)
    assert not webhook_receiver.verify(body, header, "whsec_other", now=1_760_000_005)
    assert not webhook_receiver.verify(body + b" ", header, "whsec_abc", now=1_760_000_005)
    assert not webhook_receiver.verify(body, header, "whsec_abc", now=1_760_000_000 + 301)


def test_receiver_accepts_signed_rejects_forged_and_dedupes(capsys):
    server = HTTPServer(("127.0.0.1", 0), webhook_receiver.make_handler("whsec_abc"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/webhooks"
    webhook_receiver.seen_event_ids.clear()
    try:
        body = (
            b'{"id":"evt_9","type":"delivery.arriving",'
            b'"data":{"object":{"id":"del_1","external_reference":"ORDER-1"},"details":{}}}'
        )
        import time

        good = {"TraveAI-Signature": sign("whsec_abc", int(time.time()), body)}
        assert httpx.post(url, content=body, headers=good).status_code == 200
        assert httpx.post(url, content=body, headers=good).status_code == 200  # retry: no-op
        forged = {"TraveAI-Signature": sign("whsec_wrong", int(time.time()), body)}
        assert httpx.post(url, content=body, headers=forged).status_code == 400
    finally:
        server.shutdown()
    out = capsys.readouterr().out
    assert out.count("[ORDER-1] Drone is 1 minute away") == 1
