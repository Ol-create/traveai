from conftest import TEST_KEY, TEST_MERCHANT
from traveai.config import Settings


def test_me_requires_api_key(client):
    response = client.get("/v1/me")
    assert response.status_code == 401


def test_me_rejects_wrong_key(client):
    response = client.get("/v1/me", headers={"Authorization": "Bearer sk_test_wrong"})
    assert response.status_code == 401


def test_me_returns_merchant_for_valid_key(client):
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {TEST_KEY}"})
    assert response.status_code == 200
    assert response.json() == {"merchant_id": TEST_MERCHANT, "test_mode": True}


def test_api_key_map_parsing_ignores_malformed_pairs():
    settings = Settings(_env_file=None, api_keys=" a:m1 , bad, :m2, b: , c:m3 ")
    assert settings.api_key_map == {"a": "m1", "c": "m3"}
