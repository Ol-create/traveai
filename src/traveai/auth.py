from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.db import get_session
from traveai.errors import ApiError
from traveai.models import ApiKey, Merchant
from traveai.ratelimit import RateLimiter, get_rate_limiter
from traveai.security import hash_secret

bearer_scheme = HTTPBearer(auto_error=False, description="Merchant API key, e.g. sk_test_...")


@dataclass(frozen=True)
class AuthContext:
    merchant: Merchant
    test_mode: bool


def require_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    response: Response,
) -> AuthContext:
    """Resolve the calling merchant from the `Authorization: Bearer <api_key>` header.

    Keys are stored as SHA-256 hashes, so we look up the hash; the plaintext never touches the DB.
    Each key is rate limited; every response carries X-RateLimit-* headers.
    """
    unauthorized = ApiError(
        status.HTTP_401_UNAUTHORIZED,
        "invalid_api_key",
        "Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    api_key = session.scalar(
        select(ApiKey).where(
            ApiKey.key_hash == hash_secret(credentials.credentials),
            ApiKey.revoked_at.is_(None),
        )
    )
    if api_key is None:
        raise unauthorized

    decision = limiter.hit(api_key.id)
    if decision.limit:
        if not decision.allowed:
            raise ApiError(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                f"Over {decision.limit} requests per minute; retry in {decision.reset_s} s",
                headers=decision.headers(),
            )
        response.headers.update(decision.headers())
    return AuthContext(merchant=api_key.merchant, test_mode=api_key.is_test)


CurrentAuth = Annotated[AuthContext, Depends(require_api_key)]
