"""Usage summary for the portal overview."""

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from traveai.domain.delivery_status import DeliveryStatus
from traveai.models import Delivery

ON_TIME_GRACE = timedelta(minutes=5)


def usage_summary(session: Session, merchant_id: str, now: datetime, days: int = 30) -> dict:
    since = now - timedelta(days=days)
    deliveries = session.scalars(
        select(Delivery).where(Delivery.merchant_id == merchant_id, Delivery.created_at >= since)
    ).all()

    by_status = Counter(d.status.value for d in deliveries)
    delivered = [d for d in deliveries if d.status == DeliveryStatus.DELIVERED and d.delivered_at]
    with_estimate = [d for d in delivered if d.estimated_dropoff_at]
    on_time = [d for d in with_estimate if d.delivered_at <= d.estimated_dropoff_at + ON_TIME_GRACE]
    minutes = [(d.delivered_at - d.created_at).total_seconds() / 60 for d in delivered]

    per_day = Counter(d.created_at.date().isoformat() for d in deliveries)
    start = since.date()
    daily = [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "deliveries": per_day.get((start + timedelta(days=i)).isoformat(), 0),
        }
        for i in range(days + 1)
    ]
    return {
        "days": days,
        "total": len(deliveries),
        "by_status": dict(by_status),
        "delivered": len(delivered),
        "spend_cents": sum(d.price_cents for d in delivered),
        "currency": "usd",
        "on_time_rate": round(len(on_time) / len(with_estimate), 3) if with_estimate else None,
        "avg_delivery_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        "daily": daily,
    }
