from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from traveai.auth import CurrentAuth
from traveai.db import get_session
from traveai.deps import get_airspace, get_now, get_rules_config, get_weather_provider
from traveai.models import Quote
from traveai.rules.airspace import AirspaceMap
from traveai.rules.config import RulesConfig
from traveai.rules.weather import WeatherProvider
from traveai.schemas.quote import QuoteCreate, QuoteOut
from traveai.services.quoting import QuoteContext, QuoteRequest, create_quote

router = APIRouter(prefix="/v1/quotes", tags=["quotes"])

MAX_SCHEDULE_AHEAD = timedelta(days=7)
CLOCK_SKEW = timedelta(minutes=1)


@router.post("", response_model=QuoteOut, status_code=status.HTTP_201_CREATED)
def create(
    body: QuoteCreate,
    auth: CurrentAuth,
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    weather: Annotated[WeatherProvider, Depends(get_weather_provider)],
    airspace: Annotated[AirspaceMap, Depends(get_airspace)],
    config: Annotated[RulesConfig, Depends(get_rules_config)],
) -> QuoteOut:
    """Price and check a delivery before booking it.

    Always returns a quote. If the delivery can't happen right now (weather, airspace, range,
    ...) the quote has `feasible: false` and lists every `reason`. Quotes expire after 5 minutes.
    """
    if body.pickup_at is not None:
        if body.pickup_at < now - CLOCK_SKEW:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "pickup_at is in the past")
        if body.pickup_at > now + MAX_SCHEDULE_AHEAD:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "pickup_at is more than 7 days ahead"
            )

    quote = create_quote(
        session,
        auth.merchant.id,
        QuoteRequest(
            pickup=body.pickup,
            dropoff=body.dropoff,
            payload=body.payload,
            priority=body.priority,
            pickup_at=body.pickup_at,
        ),
        QuoteContext(now=now, weather=weather, airspace=airspace, config=config),
    )
    session.commit()
    return QuoteOut.from_model(quote)


@router.get("/{quote_id}", response_model=QuoteOut)
def retrieve(
    quote_id: str, auth: CurrentAuth, session: Annotated[Session, Depends(get_session)]
) -> QuoteOut:
    quote = session.get(Quote, quote_id)
    # Another merchant's quote looks exactly like a missing one.
    if quote is None or quote.merchant_id != auth.merchant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quote not found")
    return QuoteOut.from_model(quote)
