from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, utcnow
from traveai.models.merchant import Merchant


class User(TimestampMixin, Base):
    """A person who logs in to the merchant portal."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="owner")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    merchant: Mapped[Merchant] = relationship()


class PortalSession(Base):
    """A logged-in browser. The cookie holds a random token; we store only its hash."""

    __tablename__ = "portal_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    user: Mapped[User] = relationship()
