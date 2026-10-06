from conftest import TEST_MERCHANT_ID
from traveai.config import Settings
from traveai.models import ApiKey
from traveai.models.base import utcnow
from traveai.security import hash_secret


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_me_requires_api_key(client):
    assert client.get("/v1/me").status_code == 401


def test_me_rejects_wrong_key(client, api_key):
    assert client.get("/v1/me", headers=auth("sk_test_wrong")).status_code == 401


def test_me_returns_merchant_for_valid_key(client, api_key):
    response = client.get("/v1/me", headers=auth(api_key))
    assert response.status_code == 200
    assert response.json() == {
        "merchant_id": TEST_MERCHANT_ID,
        "name": "Unit Test Pharmacy",
        "category": "pharmacy",
        "test_mode": True,
    }


def test_revoked_key_is_rejected(client, session, api_key):
    stored = session.query(ApiKey).one()
    stored.revoked_at = utcnow()
    session.commit()
    assert client.get("/v1/me", headers=auth(api_key)).status_code == 401


def test_only_key_hash_is_stored(session, api_key):
    stored = session.query(ApiKey).one()
    assert stored.key_hash == hash_secret(api_key)
    assert api_key not in (stored.key_hash, stored.key_prefix)


def test_live_key_is_not_test_mode(client, session, merchant):
    key = merchant.issue_api_key(test=False)
    session.commit()
    assert key.startswith("sk_live_")
    assert client.get("/v1/me", headers=auth(key)).json()["test_mode"] is False


def test_seed_key_parsing_ignores_malformed_pairs():
    settings = Settings(_env_file=None, seed_api_keys=" a:m1 , bad, :m2, b: , c:m3 ")
    assert settings.seed_api_key_map == {"a": "m1", "c": "m3"}
