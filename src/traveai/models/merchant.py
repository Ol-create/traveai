from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from traveai.domain.enums import MerchantCategory
from traveai.ids import new_id
from traveai.models.base import Base, TimestampMixin, UTCDateTime, str_enum
from traveai.security import TEST_KEY_PREFIX, display_prefix, generate_api_key, hash_secret


class Merchant(TimestampMixin, Base):
    """A business that sends deliveries through the API (pharmacy, restaurant, ...)."""

    __tablename__ = "merchants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("merch"))
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[MerchantCategory] = mapped_column(str_enum(MerchantCategory))

    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )

    def issue_api_key(self, *, test: bool = True, plaintext: str | None = None) -> str:
        """Create a new API key for this merchant. Returns the plaintext key: show it once,
        only its hash is stored."""
        key = plaintext or generate_api_key(test=test)
        self.api_keys.append(
            ApiKey(
                key_hash=hash_secret(key),
                key_prefix=display_prefix(key),
                is_test=key.startswith(TEST_KEY_PREFIX),
            )
        )
        return key


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("key"))
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    is_test: Mapped[bool] = mapped_column(default=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    merchant: Mapped[Merchant] = relationship(back_populates="api_keys")
