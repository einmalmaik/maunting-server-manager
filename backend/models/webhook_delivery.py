"""Audit + Live-Feed der ausgehenden Webhook-Zustellungen.

Wir speichern hier explizit nur Metadaten + Payload-Hash, niemals das
Klartext-Secret. Der vollstaendige Payload wird als JSON-Text abgelegt,
damit der Live-Feed im Panel den exakt versendeten Body anzeigen kann
(Debugging fuer den User).

Geplant/Implementiert:
  - status:        "ok" | "failed" | "pending" | "skipped"
  - response_code: HTTP-Status des Empfaengers (None wenn Connection-Error)
  - error:         Fehlertext (gekuerzt, ohne sensitive Daten)
  - payload_hash:  SHA-256 des Payload-Body als Integritaets-Beweis
"""
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class WebhookDelivery(Base):
    """Ein einzelner Zustell-Versuch (outbound)."""
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Was verschickt wurde
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Resultat
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_webhook_deliveries_sub_sent", "subscription_id", "sent_at"),
        Index("ix_webhook_deliveries_server_sent", "server_id", "sent_at"),
    )
