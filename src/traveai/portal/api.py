"""JSON endpoints behind the merchant portal (cookie sessions, not API keys).

Delivery and webhook actions call the same functions as the public API, with an AuthContext
for the logged-in user's merchant, so the portal can't drift from the API's rules.
"""

from datetime import datetime
from functools import lru_cache
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from traveai.api import deliveries as deliveries_api
from traveai.api import webhooks as webhooks_api
from traveai.auth import AuthContext
from traveai.config import get_settings
from traveai.db import get_session
from traveai.deps import get_now
from traveai.domain.delivery_status import DeliveryStatus
from traveai.domain.enums import MerchantCategory
from traveai.errors import ApiError
from traveai.models import ApiKey, Delivery, Merchant, User
from traveai.models.base import utcnow
from traveai.portal.auth import (
    MIN_PASSWORD_LENGTH,
    CurrentUser,
    PortalContext,
    check_login,
    end_session,
    hash_password,
    start_session,
)
from traveai.portal.usage import usage_summary
from traveai.ratelimit import MemoryRateLimiter, RateLimiter, RedisRateLimiter
from traveai.schemas.delivery import DeliveryCancel, DeliveryList, DeliveryOut, EventList
from traveai.schemas.webhook import (
    WebhookEndpointCreate,
    WebhookEndpointList,
    WebhookEndpointWithSecret,
    WebhookMessageList,
)

router = APIRouter(prefix="/portal/api", include_in_schema=False)

SessionDep = Annotated[Session, Depends(get_session)]
NowDep = Annotated[datetime, Depends(get_now)]
Mode = Literal["test", "live"]
LOGIN_ATTEMPTS_PER_MINUTE = 10


@lru_cache
def get_login_limiter() -> RateLimiter:
    """Tighter limit for sign-up and login (password guessing)."""
    settings = get_settings()
    if settings.redis_url:
        import redis

        return RedisRateLimiter(redis.Redis.from_url(settings.redis_url), LOGIN_ATTEMPTS_PER_MINUTE)
    return MemoryRateLimiter(LOGIN_ATTEMPTS_PER_MINUTE)


def _throttle(request: Request, limiter: Annotated[RateLimiter, Depends(get_login_limiter)]):
    client = request.client.host if request.client else "unknown"
    decision = limiter.hit(f"login:{client}")
    if not decision.allowed:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            f"Too many attempts; try again in {decision.reset_s} s",
            headers=decision.headers(),
        )


def _api_auth(ctx: PortalContext, mode: Mode = "test") -> AuthContext:
    if mode == "live" and not ctx.merchant.live_enabled:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "live_not_enabled",
            "Live mode unlocks once we've verified your business",
        )
    return AuthContext(merchant=ctx.merchant, test_mode=mode == "test")


def _me(ctx: PortalContext) -> dict:
    m = ctx.merchant
    return {
        "user": {"name": ctx.user.name, "email": ctx.user.email, "role": ctx.user.role},
        "merchant": {
            "id": m.id,
            "name": m.name,
            "category": m.category.value,
            "live_enabled": m.live_enabled,
        },
        "csrf_token": ctx.portal_session.csrf_token,
    }


# --- accounts -------------------------------------------------------------------------------


class SignupBody(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    category: MerchantCategory
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class LoginBody(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


@router.post("/signup", status_code=status.HTTP_201_CREATED, dependencies=[Depends(_throttle)])
def signup(body: SignupBody, response: Response, session: SessionDep, now: NowDep) -> dict:
    if not get_settings().portal_signup_enabled:
        raise ApiError(status.HTTP_403_FORBIDDEN, "signup_disabled", "Sign-up is by invitation")
    email = body.email.lower()
    if session.scalar(select(User).where(User.email == email)):
        raise ApiError(
            status.HTTP_409_CONFLICT, "email_taken", "An account with this email already exists"
        )
    merchant = Merchant(name=body.business_name.strip(), category=body.category)
    user = User(
        merchant=merchant,
        email=email,
        name=body.name.strip(),
        password_hash=hash_password(body.password),
    )
    session.add_all([merchant, user])
    first_key = merchant.issue_api_key(test=True, label="First test key")
    try:
        session.flush()
    except IntegrityError:  # same email raced in
        session.rollback()
        raise ApiError(
            status.HTTP_409_CONFLICT, "email_taken", "An account with this email already exists"
        ) from None
    portal_session = start_session(session, user, response, now)
    session.commit()
    return {**_me(PortalContext(user, merchant, portal_session)), "first_test_key": first_key}


@router.post("/login", dependencies=[Depends(_throttle)])
def login(body: LoginBody, response: Response, session: SessionDep, now: NowDep) -> dict:
    user = check_login(session, body.email, body.password)
    if user is None:
        # Same answer for unknown email and wrong password: no account discovery.
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Wrong email or password"
        )
    portal_session = start_session(session, user, response, now)
    session.commit()
    return _me(PortalContext(user, user.merchant, portal_session))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(ctx: CurrentUser, response: Response, session: SessionDep) -> Response:
    end_session(session, ctx.portal_session, response)
    session.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(ctx: CurrentUser) -> dict:
    return _me(ctx)


# --- API keys -------------------------------------------------------------------------------


class KeyCreate(BaseModel):
    mode: Mode = "test"
    label: str | None = Field(default=None, max_length=60)


def _key_out(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "label": k.label,
        "prefix": k.key_prefix,
        "mode": "test" if k.is_test else "live",
        "created_at": k.created_at,
        "revoked_at": k.revoked_at,
    }


@router.get("/keys")
def list_keys(ctx: CurrentUser, session: SessionDep) -> dict:
    keys = session.scalars(
        select(ApiKey).where(ApiKey.merchant_id == ctx.merchant.id).order_by(ApiKey.created_at)
    )
    return {"data": [_key_out(k) for k in keys]}


@router.post("/keys", status_code=status.HTTP_201_CREATED)
def create_key(body: KeyCreate, ctx: CurrentUser, session: SessionDep) -> dict:
    _api_auth(ctx, body.mode)  # live needs a verified business
    plaintext = ctx.merchant.issue_api_key(test=body.mode == "test", label=body.label)
    session.commit()
    key = ctx.merchant.api_keys[-1]
    return {**_key_out(key), "secret": plaintext}


@router.post("/keys/{key_id}/revoke")
def revoke_key(key_id: str, ctx: CurrentUser, session: SessionDep) -> dict:
    key = session.get(ApiKey, key_id)
    if key is None or key.merchant_id != ctx.merchant.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "key_not_found", "API key not found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        session.commit()
    return _key_out(key)


# --- deliveries (same logic as the API) -----------------------------------------------------


@router.get("/deliveries", response_model=DeliveryList)
def list_deliveries(
    ctx: CurrentUser,
    session: SessionDep,
    status_: Annotated[list[DeliveryStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=64, description="Order # or delivery id")] = None,
    created_gte: datetime | None = None,
    created_lt: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    starting_after: str | None = None,
) -> DeliveryList:
    auth = _api_auth(ctx)
    if q and q.startswith("del_"):
        d = session.get(Delivery, q)
        found = d is not None and d.merchant_id == ctx.merchant.id
        return DeliveryList(data=[DeliveryOut.from_model(d)] if found else [], has_more=False)
    return deliveries_api.list_deliveries(
        auth=auth,
        session=session,
        status_=status_,
        external_reference=q,
        created_gte=created_gte,
        created_lt=created_lt,
        limit=limit,
        starting_after=starting_after,
    )


@router.get("/deliveries/{delivery_id}", response_model=DeliveryOut)
def delivery(delivery_id: str, ctx: CurrentUser, session: SessionDep) -> DeliveryOut:
    return deliveries_api.retrieve(delivery_id, _api_auth(ctx), session)


@router.get("/deliveries/{delivery_id}/events", response_model=EventList)
def delivery_events(delivery_id: str, ctx: CurrentUser, session: SessionDep) -> EventList:
    return deliveries_api.events(delivery_id, _api_auth(ctx), session)


@router.post("/deliveries/{delivery_id}/cancel", response_model=DeliveryOut)
def cancel_delivery(
    delivery_id: str, ctx: CurrentUser, session: SessionDep, now: NowDep
) -> DeliveryOut:
    return deliveries_api.cancel(
        delivery_id, _api_auth(ctx), session, now, DeliveryCancel(reason="canceled_in_portal")
    )


# --- webhooks (same logic as the API) -------------------------------------------------------


class PortalWebhookCreate(WebhookEndpointCreate):
    mode: Mode = "test"


@router.get("/webhooks", response_model=WebhookEndpointList)
def webhooks(ctx: CurrentUser, session: SessionDep) -> WebhookEndpointList:
    return webhooks_api.list_endpoints(_api_auth(ctx), session)


@router.post(
    "/webhooks", response_model=WebhookEndpointWithSecret, status_code=status.HTTP_201_CREATED
)
def create_webhook(
    body: PortalWebhookCreate, ctx: CurrentUser, session: SessionDep
) -> WebhookEndpointWithSecret:
    payload = WebhookEndpointCreate(**body.model_dump(exclude={"mode"}))
    return webhooks_api.create(payload, _api_auth(ctx, body.mode), session)


@router.delete("/webhooks/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(endpoint_id: str, ctx: CurrentUser, session: SessionDep) -> Response:
    return webhooks_api.delete(endpoint_id, _api_auth(ctx), session)


@router.post("/webhooks/{endpoint_id}/rotate_secret", response_model=WebhookEndpointWithSecret)
def rotate_webhook_secret(
    endpoint_id: str, ctx: CurrentUser, session: SessionDep
) -> WebhookEndpointWithSecret:
    return webhooks_api.rotate_secret(endpoint_id, _api_auth(ctx), session)


@router.get("/webhooks/{endpoint_id}/messages", response_model=WebhookMessageList)
def webhook_messages(endpoint_id: str, ctx: CurrentUser, session: SessionDep) -> WebhookMessageList:
    return webhooks_api.messages(endpoint_id, _api_auth(ctx), session)


# --- usage ----------------------------------------------------------------------------------


@router.get("/usage")
def usage(
    ctx: CurrentUser,
    session: SessionDep,
    now: NowDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> dict:
    return usage_summary(session, ctx.merchant.id, now, days)
