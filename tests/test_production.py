"""Production hardening: worker leases, encrypted secrets, rate limits, readiness, request ids,
safe 500s and production settings checks."""

from contextlib import nullcontext
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from traveai import crypto
from traveai.config import Settings
from traveai.models import WebhookEndpoint
from traveai.ratelimit import MemoryRateLimiter, RedisRateLimiter, get_rate_limiter
from traveai.workers import (
    LEASE_TTL,
    SIMULATOR,
    SPEED_KEY,
    lease_alive,
    release,
    set_runtime,
    sim_speed,
    tick,
    try_acquire,
)


@pytest.fixture
def h(api_key):
    return {"Authorization": f"Bearer {api_key}"}


# --- worker leases -------------------------------------------------------------------------


def test_only_one_worker_holds_the_lease(session, world):
    now = world.now
    assert try_acquire(session, SIMULATOR, "a", now)
    assert not try_acquire(session, SIMULATOR, "b", now)  # standby
    assert try_acquire(session, SIMULATOR, "a", now + timedelta(seconds=5))  # renewal

    # "a" dies: after the TTL, "b" takes over.
    later = now + timedelta(seconds=5) + LEASE_TTL + timedelta(seconds=1)
    assert try_acquire(session, SIMULATOR, "b", later)
    assert not try_acquire(session, SIMULATOR, "a", later)


def test_release_hands_over_immediately(session, world):
    assert try_acquire(session, SIMULATOR, "a", world.now)
    release(session, SIMULATOR, "a")
    assert try_acquire(session, SIMULATOR, "b", world.now)
    release(session, SIMULATOR, "a")  # not the holder: no effect
    assert lease_alive(session, SIMULATOR, world.now)


def test_tick_runs_the_job_only_for_the_leader(session, world):
    ran: list[str] = []
    factory = lambda: nullcontext(session)  # noqa: E731
    assert tick(factory, SIMULATOR, "a", lambda s, now: ran.append("a"), world.now)
    assert not tick(factory, SIMULATOR, "b", lambda s, now: ran.append("b"), world.now)
    assert ran == ["a"]


def test_simulation_speed_is_shared_through_the_database(session):
    settings = Settings(_env_file=None, sim_speed=5)
    assert sim_speed(session, settings) == 5  # default from settings
    set_runtime(session, SPEED_KEY, 40)
    assert sim_speed(session, settings) == 40
    set_runtime(session, SPEED_KEY, 1000)
    assert sim_speed(session, settings) == 100  # clamped


# --- encrypted webhook secrets -------------------------------------------------------------


def test_webhook_secret_is_encrypted_at_rest(client, h, session):
    created = client.post(
        "/v1/webhook_endpoints", json={"url": "https://example.com/hooks"}, headers=h
    ).json()
    row = session.scalar(select(WebhookEndpoint))
    assert created["secret"] not in row.secret_encrypted
    assert row.secret_encrypted.startswith("gAAAA")  # Fernet token
    assert row.secret == created["secret"]


def test_key_rotation_keeps_old_secrets_readable(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()

    def use_keys(keys: str) -> None:
        monkeypatch.setattr(
            crypto, "get_settings", lambda: Settings(_env_file=None, secret_key=keys)
        )
        crypto.get_cipher.cache_clear()

    try:
        use_keys(old)
        token = crypto.encrypt("whsec_abc")
        use_keys(f"{new},{old}")  # new key first, old still accepted
        assert crypto.decrypt(token) == "whsec_abc"
        rotated = crypto.get_cipher().rotate(token.encode()).decode()
        use_keys(new)  # old key retired
        assert crypto.decrypt(rotated) == "whsec_abc"
        with pytest.raises(crypto.DecryptionError):
            crypto.decrypt(token)
    finally:
        crypto.get_cipher.cache_clear()


# --- rate limits ---------------------------------------------------------------------------


def test_memory_rate_limiter_windows():
    limiter = MemoryRateLimiter(limit=3)
    t = 60 * 16_667 + 20.0  # 20 s into a minute
    results = [limiter.hit("key", t).allowed for _ in range(4)]
    assert results == [True, True, True, False]
    blocked = limiter.hit("key", t)
    assert blocked.remaining == 0 and blocked.reset_s == 40
    assert blocked.headers()["Retry-After"] == "40"
    assert limiter.hit("other-key", t).allowed  # per key
    assert limiter.hit("key", t + 40).allowed  # next minute


class FakeRedis:
    """Just enough of redis-py for the limiter: pipeline().incr().expire().execute()."""

    def __init__(self):
        self.data: dict[str, int] = {}
        self.ttl: dict[str, int] = {}

    def pipeline(self):
        redis, ops = self, []

        class Pipe:
            def incr(self, key):
                ops.append(("incr", key))

            def expire(self, key, seconds):
                ops.append(("expire", key, seconds))

            def execute(self):
                out = []
                for op in ops:
                    if op[0] == "incr":
                        redis.data[op[1]] = redis.data.get(op[1], 0) + 1
                        out.append(redis.data[op[1]])
                    else:
                        redis.ttl[op[1]] = op[2]
                        out.append(True)
                return out

        return Pipe()


def test_redis_rate_limiter_shares_counts_between_servers():
    shared = FakeRedis()
    server_a, server_b = RedisRateLimiter(shared, limit=2), RedisRateLimiter(shared, limit=2)
    t = 1_000_000.0
    assert server_a.hit("k", t).allowed
    assert server_b.hit("k", t).allowed
    assert not server_a.hit("k", t).allowed  # third request overall
    assert all(ttl == 120 for ttl in shared.ttl.values())  # keys expire on their own


def test_api_returns_429_with_headers(client, h):
    client.app.dependency_overrides[get_rate_limiter] = lambda: limiter
    limiter = MemoryRateLimiter(limit=2)
    first = client.get("/v1/me", headers=h)
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"
    client.get("/v1/me", headers=h)
    blocked = client.get("/v1/me", headers=h)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert int(blocked.headers["Retry-After"]) > 0


# --- readiness, request ids, errors --------------------------------------------------------


def test_ready_reports_database_and_workers(client, session, world):
    body = client.get("/ready").json()
    assert body == {
        "status": "ready",
        "database": "ok",
        "workers": {"simulator": "absent", "webhooks": "absent"},
    }
    try_acquire(session, SIMULATOR, "w", world.now)
    assert client.get("/ready").json()["workers"]["simulator"] == "active"


def test_request_id_is_echoed_or_generated(client):
    assert client.get("/health", headers={"X-Request-Id": "abc-123"}).headers["X-Request-Id"] == (
        "abc-123"
    )
    generated = client.get("/health", headers={"X-Request-Id": "bad id with spaces"})
    assert len(generated.headers["X-Request-Id"]) == 32


def test_unhandled_errors_return_safe_json(client, h, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database password is hunter2")  # must not leak

    monkeypatch.setattr("traveai.api.merchants.MeResponse", boom)
    r = client.get("/v1/me", headers=h)
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["code"] == "internal_error"
    assert detail["request_id"] == r.headers["X-Request-Id"]
    assert "hunter2" not in r.text


def test_invalid_key_error_has_stable_code(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer sk_test_nope"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_api_key"
    assert r.headers["WWW-Authenticate"] == "Bearer"


# --- production settings -------------------------------------------------------------------


def test_production_refuses_unsafe_settings():
    unsafe = Settings(
        _env_file=None, env="production", sandbox_controls=True, debug=True, seed_api_keys="k:m"
    )
    with pytest.raises(RuntimeError) as err:
        unsafe.check_production()
    for problem in ("SECRET_KEY", "Postgres", "SANDBOX_CONTROLS", "DEBUG", "SEED_API_KEYS"):
        assert problem in str(err.value)


def test_production_accepts_safe_settings():
    Settings(
        _env_file=None,
        env="production",
        secret_key=Fernet.generate_key().decode(),
        database_url="postgresql+psycopg://traveai@db/traveai",
    ).check_production()
    Settings(_env_file=None).check_production()  # development: no checks
