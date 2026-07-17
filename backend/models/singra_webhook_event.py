"""Deduplication store for inbound Singra support-widget webhooks."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SingraWebhookEvent(Base):
    __tablename__ = "singra_webhook_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )