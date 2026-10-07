from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from traveai.auth import AuthContext, CurrentAuth
from traveai.db import get_session
from traveai.errors import ApiError
from traveai.models import WebhookEndpoint, WebhookMessage
from traveai.schemas.webhook import (
    WebhookEndpointCreate,
    WebhookEndpointList,
    WebhookEndpointOut,
    WebhookEndpointWithSecret,
    WebhookMessageList,
    WebhookMessageOut,
)
from traveai.webhooks.sender import UnsafeUrlError, check_url
from traveai.webhooks.signing import generate_secret

router = APIRouter(prefix="/v1/webhook_endpoints", tags=["webhooks"])

SessionDep = Annotated[Session, Depends(get_session)]
MAX_ENDPOINTS = 10


def _owned(session: Session, auth: AuthContext, endpoint_id: str) -> WebhookEndpoint:
    endpoint = session.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.merchant_id != auth.merchant.id:
        raise ApiError(status.HTTP_404_NOT_FOUND, "webhook_endpoint_not_found", "Not found")
    return endpoint


@router.post("", response_model=WebhookEndpointWithSecret, status_code=status.HTTP_201_CREATED)
def create(
    body: WebhookEndpointCreate, auth: CurrentAuth, session: SessionDep
) -> WebhookEndpointWithSecret:
    """Register a URL for event notifications. The response's `secret` signs every request
    (header `TraveAI-Signature`); store it, it is not shown again."""
    url = str(body.url)
    livemode = not auth.test_mode
    if livemode and not url.startswith("https://"):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_url", "Live webhook URLs must use https"
        )
    try:
        check_url(url, livemode=False)  # scheme/host now; live IP checks happen on each send
    except UnsafeUrlError as exc:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_url", str(exc)) from None
    count = session.scalar(
        select(func.count()).where(WebhookEndpoint.merchant_id == auth.merchant.id)
    )
    if count >= MAX_ENDPOINTS:
        raise ApiError(
            status.HTTP_409_CONFLICT, "too_many_endpoints", f"Limit is {MAX_ENDPOINTS} endpoints"
        )

    endpoint = WebhookEndpoint(
        merchant_id=auth.merchant.id,
        url=url,
        secret=generate_secret(),
        enabled_events=body.enabled_events,
        description=body.description,
        livemode=livemode,
    )
    session.add(endpoint)
    session.commit()
    return WebhookEndpointWithSecret(
        **WebhookEndpointOut.from_model(endpoint).model_dump(), secret=endpoint.secret
    )


@router.get("", response_model=WebhookEndpointList)
def list_endpoints(auth: CurrentAuth, session: SessionDep) -> WebhookEndpointList:
    rows = session.scalars(
        select(WebhookEndpoint)
        .where(WebhookEndpoint.merchant_id == auth.merchant.id)
        .order_by(WebhookEndpoint.created_at)
    )
    return WebhookEndpointList(data=[WebhookEndpointOut.from_model(e) for e in rows])


@router.get("/{endpoint_id}", response_model=WebhookEndpointOut)
def retrieve(endpoint_id: str, auth: CurrentAuth, session: SessionDep) -> WebhookEndpointOut:
    return WebhookEndpointOut.from_model(_owned(session, auth, endpoint_id))


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(endpoint_id: str, auth: CurrentAuth, session: SessionDep) -> Response:
    """Stop sending to this URL. Pending messages are dropped."""
    session.delete(_owned(session, auth, endpoint_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{endpoint_id}/rotate_secret", response_model=WebhookEndpointWithSecret)
def rotate_secret(
    endpoint_id: str, auth: CurrentAuth, session: SessionDep
) -> WebhookEndpointWithSecret:
    """Replace the signing secret (e.g. if it leaked). Takes effect on the next send."""
    endpoint = _owned(session, auth, endpoint_id)
    endpoint.secret = generate_secret()
    session.commit()
    return WebhookEndpointWithSecret(
        **WebhookEndpointOut.from_model(endpoint).model_dump(), secret=endpoint.secret
    )


@router.get("/{endpoint_id}/messages", response_model=WebhookMessageList)
def messages(endpoint_id: str, auth: CurrentAuth, session: SessionDep) -> WebhookMessageList:
    """The last 50 notifications for this URL, with attempt results. For debugging."""
    endpoint = _owned(session, auth, endpoint_id)
    rows = session.scalars(
        select(WebhookMessage)
        .where(WebhookMessage.endpoint_id == endpoint.id)
        .order_by(WebhookMessage.created_at.desc(), WebhookMessage.id.desc())
        .limit(50)
    )
    return WebhookMessageList(data=[WebhookMessageOut.from_model(m) for m in rows])
