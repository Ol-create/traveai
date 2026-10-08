from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from traveai.models.base import Base, UTCDateTime, utcnow


class WorkerLease(Base):
    """Leader election for background workers: only the holder of an unexpired lease runs
    that job, so several copies can be deployed for failover without double work."""

    __tablename__ = "worker_leases"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # "simulator", "webhooks"
    holder: Mapped[str] = mapped_column(String(120))  # host:pid:random
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RuntimeSetting(Base):
    """Settings changeable at runtime and shared by every process (e.g. simulation speed)."""

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
