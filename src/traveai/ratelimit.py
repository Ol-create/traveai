"""Per-API-key rate limiting (fixed one-minute windows).

In memory by default, which is exact for a single API server. With several servers behind a
load balancer, set TRAVEAI_REDIS_URL so they share one counter per key.
"""

import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from traveai.config import get_settings

WINDOW_S = 60


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int  # seconds until the window resets

    def headers(self) -> dict[str, str]:
        h = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            h["Retry-After"] = str(self.reset_s)
        return h


class RateLimiter(Protocol):
    def hit(self, key: str, now: float | None = None) -> Decision: ...


class NoLimit:
    def hit(self, key: str, now: float | None = None) -> Decision:
        return Decision(allowed=True, limit=0, remaining=0, reset_s=0)


class MemoryRateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> Decision:
        now = time.time() if now is None else now
        window = int(now // WINDOW_S)
        with self._lock:
            # Forget finished windows so memory stays bounded.
            for old in [k for k in self._counts if k[1] < window]:
                del self._counts[old]
            count = self._counts.get((key, window), 0) + 1
            self._counts[(key, window)] = count
        return _decide(self.limit, count, now)


class RedisRateLimiter:
    def __init__(self, client, limit: int) -> None:
        self.client = client
        self.limit = limit

    def hit(self, key: str, now: float | None = None) -> Decision:
        now = time.time() if now is None else now
        redis_key = f"traveai:rl:{key}:{int(now // WINDOW_S)}"
        pipe = self.client.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, WINDOW_S * 2)
        count, _ = pipe.execute()
        return _decide(self.limit, int(count), now)


def _decide(limit: int, count: int, now: float) -> Decision:
    reset_s = WINDOW_S - int(now % WINDOW_S)
    return Decision(
        allowed=count <= limit, limit=limit, remaining=max(0, limit - count), reset_s=reset_s
    )


@lru_cache
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    if settings.rate_limit_per_minute <= 0:
        return NoLimit()
    if settings.redis_url:
        import redis  # optional dependency: pip install -e ".[redis]"

        return RedisRateLimiter(
            redis.Redis.from_url(settings.redis_url), settings.rate_limit_per_minute
        )
    return MemoryRateLimiter(settings.rate_limit_per_minute)
