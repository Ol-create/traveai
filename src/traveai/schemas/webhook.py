from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from traveai.domain.delivery_status import DeliveryStatus, event_type_for
from traveai.models import WebhookEndpoint, WebhookMessage, WebhookMessageStatus
from traveai.models.delivery import CUSTODY_EVENT_TYPE

EVENT_TYPES = sorted(
    {event_type_for(s) for s in DeliveryStatus} | {CUSTODY_EVENT_TYPE, "delivery.pin_failed"}
)


class WebhookEndpointCreate(BaseModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://pharmacy.example.com/traveai/webhooks",
                    "enabled_events": ["delivery.*"],
                    "description": "Order status updates",
                }
            ]
        }
    }

    url: AnyHttpUrl = Field(description="HTTPS URL (http://localhost is fine with test keys).")
    enabled_events: list[str] = Field(
        default_factory=lambda: ["*"],
        min_length=1,
        description='Event types or patterns, e.g. ["*"], ["delivery.*"], ["delivery.delivered"].',
    )
    description: str | None = Field(default=None, max_length=200)

    @field_validator("enabled_events")
    @classmethod
    def _known_events(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            if not any(fnmatchcase(t, pattern) for t in EVENT_TYPES):
                raise ValueError(f"'{pattern}' matches no event type; known: {EVENT_TYPES}")
        return patterns


class WebhookEndpointOut(BaseModel):
    id: str
    object: Literal["webhook_endpoint"] = "webhook_endpoint"
    url: str
    enabled_events: list[str]
    description: str | None
    livemode: bool
    created_at: datetime

    @classmethod
    def from_model(cls, e: WebhookEndpoint) -> "WebhookEndpointOut":
        return cls(
            id=e.id,
            url=e.url,
            enabled_events=e.enabled_events,
            description=e.description,
            livemode=e.livemode,
            created_at=e.created_at,
        )


class WebhookEndpointWithSecret(WebhookEndpointOut):
    secret: str = Field(description="Signing secret. Shown only when created or rotated.")


class WebhookEndpointList(BaseModel):
    object: Literal["list"] = "list"
    data: list[WebhookEndpointOut]


class WebhookMessageOut(BaseModel):
    id: str
    event_id: str
    event_type: str
    status: WebhookMessageStatus
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    created_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_model(cls, m: WebhookMessage) -> "WebhookMessageOut":
        return cls(
            id=m.id,
            event_id=m.event_id,
            event_type=m.event_type,
            status=m.status,
            attempts=m.attempts,
            next_attempt_at=m.next_attempt_at,
            last_status_code=m.last_status_code,
            last_error=m.last_error,
            delivered_at=m.delivered_at,
            created_at=m.created_at,
            payload=m.payload,
        )


class WebhookMessageList(BaseModel):
    object: Literal["list"] = "list"
    data: list[WebhookMessageOut]
