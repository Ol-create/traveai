from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from traveai.crypto import decrypt, encrypt
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, str_enum, utcnow


class WebhookEndpoint(TimestampMixin, Base):
    """A merchant URL that receives event notifications."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("whe"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    # Needed in plain form to sign each request, so it is encrypted (not hashed) at rest.
    # Use the `secret` property; it encrypts and decrypts with TRAVEAI_SECRET_KEY.
    secret_encrypted: Mapped[str] = mapped_column(String(255))
    # Event types or patterns: ["*"], ["delivery.*"], ["delivery.delivered", ...]
    enabled_events: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["*"])
    description: Mapped[str | None] = mapped_column(String(200))
    # Created with a live key: must be public HTTPS (no localhost / private networks).
    livemode: Mapped[bool] = mapped_column(default=False)

    messages: Mapped[list["WebhookMessage"]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )

    def __init__(self, *, secret: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if secret is not None:
            self.secret = secret

    @property
    def secret(self) -> str:
        return decrypt(self.secret_encrypted)

    @secret.setter
    def secret(self, value: str) -> None:
        self.secret_encrypted = encrypt(value)


class WebhookMessageStatus(StrEnum):
    PENDING = "pending"  # waiting for its first or next attempt
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # gave up after all retries


class WebhookMessage(Base):
    """One event to be sent to one endpoint, with its delivery attempts."""

    __tablename__ = "webhook_messages"
    __table_args__ = (UniqueConstraint("endpoint_id", "event_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("whm"))
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"))
    event_type: Mapped[str] = mapped_column(String(64))
    # The exact JSON body, frozen when the event happened.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[WebhookMessageStatus] = mapped_column(
        str_enum(WebhookMessageStatus), default=WebhookMessageStatus.PENDING, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    endpoint: Mapped[WebhookEndpoint] = relationship(back_populates="messages")
