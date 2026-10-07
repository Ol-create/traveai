import pytest

from traveai.config import Settings, get_settings
from traveai.sim.runner import control


@pytest.fixture
def h(api_key):
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture(autouse=True)
def reset_control():
    saved = (control.running, control.speed)
    yield
    control.running, control.speed = saved


def with_settings(client, **overrides):
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, **overrides)


# --- sandbox -------------------------------------------------------------------------------


def test_simulator_status(client, h, monkeypatch):
    monkeypatch.setattr("traveai.api.testing.get_settings", lambda: Settings(_env_file=None))
    body = client.get("/v1/test/simulator", headers=h).json()
    assert body["running"] is False  # tests don't start the background loop
    assert body["controls_enabled"] is False


def test_change_speed_when_sandbox_controls_on(client, h, monkeypatch):
    monkeypatch.setattr(
        "traveai.api.testing.get_settings", lambda: Settings(_env_file=None, sandbox_controls=True)
    )
    r = client.patch("/v1/test/simulator", json={"speed": 25}, headers=h)
    assert r.status_code == 200 and r.json()["speed"] == 25
    assert control.speed == 25
    assert client.patch("/v1/test/simulator", json={"speed": 500}, headers=h).status_code == 422


def test_speed_change_refused_when_controls_off(client, h, monkeypatch):
    monkeypatch.setattr("traveai.api.testing.get_settings", lambda: Settings(_env_file=None))
    r = client.patch("/v1/test/simulator", json={"speed": 25}, headers=h)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "sandbox_controls_disabled"


def test_sandbox_needs_test_key(client, session, merchant):
    live = merchant.issue_api_key(test=False)
    session.commit()
    r = client.get("/v1/test/simulator", headers={"Authorization": f"Bearer {live}"})
    assert r.status_code == 403


# --- OpenAPI docs --------------------------------------------------------------------------


@pytest.fixture
def spec(client):
    return client.get("/openapi.json").json()


def test_every_operation_is_documented(spec):
    ops = [(path, op) for path, item in spec["paths"].items() for op in item.values()]
    assert len(ops) >= 25
    ids = [op["operationId"] for _, op in ops]
    assert len(ids) == len(set(ids)), "operation ids must be unique (SDK generation)"
    for path, op in ops:
        assert op.get("summary"), f"{path} has no summary"
        assert op.get("tags"), f"{path} has no tag"


def test_authenticated_operations_document_errors(spec):
    for path, item in spec["paths"].items():
        if path == "/health":
            continue
        for op in item.values():
            assert "401" in op["responses"], f"{path} doesn't document 401"
            assert op.get("security"), f"{path} has no security requirement"
    error = spec["components"]["schemas"]["ErrorResponse"]
    assert error["properties"]["detail"]["$ref"].endswith("ErrorDetail")


def test_landing_page_and_examples(spec):
    assert "## Quickstart" in spec["info"]["description"]
    assert "TraveAI-Signature" in spec["info"]["description"]
    assert {t["name"] for t in spec["tags"]} >= {"quotes", "deliveries", "tracking", "webhooks"}
    schemas = spec["components"]["schemas"]
    for name in ("QuoteCreate", "DeliveryCreate", "WebhookEndpointCreate"):
        assert schemas[name].get("examples"), f"{name} has no request example"


def test_docs_page_served(client):
    assert client.get("/docs").status_code == 200
