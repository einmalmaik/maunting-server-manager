"""Ausgehende Webhook-Subscriptions (MSM -> Drittsystem).

Ein einzelner MSM-Server kann mehrere Subscriptions haben, jede mit eigener
URL, eigenem Secret und eigenem Event-Filter. Typischer Use-Case:

  - Discord-Bot erwartet eine URL wie
    http://localhost:5173/api/webhooks/server-panel/<uuid>?secret=...
    und will Server-Status (online/offline + player_count) sehen.
  - Internes Monitoring (z. B. Uptime-Kuma, Grafana) erwartet ein
    generisches JSON-Post-Endpoint mit Status- und Metrik-Feldern.

Beide werden hier identisch behandelt — der Versand ist generisch, die
Interpretation des Payload liegt beim Empfaenger.
"""
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, Boolean, Index
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class WebhookSubscription(Base):
    """Eine ausgehende Webhook-Konfiguration.

    Felder:
      - server_id:        An welche MSM-Server-ID gebunden
      - target_url:       Vollstaendige URL (in der Regel vom Empfaenger bereitgestellt)
      - secret_hash:      SHA-256(secret) als Hex. Klartext wird NIE gespeichert.
                          Beim Versand wird das Klartext-Secret als Header
                          `X-Webhook-Secret` mitgesendet — der Empfaenger
                          verifiziert es selbst.
      - secret_hint:      Letzte 4 Zeichen des Klartext-Secrets (UX-Erinnerung)
      - enabled:          Vom User schaltbar
      - event_filter:     Komma-getrennte Liste von Event-Typen, die gesendet
                          werden sollen. Leer/None = "alle senden".
                          Beispiele: "status_change,player_update" oder leer.
      - last_delivery_status:    "ok" | "failed" | "pending" | "skipped"
      - last_delivery_at:        Zeitpunkt der letzten Zustellung (oder None)
      - last_response_code:       HTTP-Status der letzten Antwort (oder None)
    """
    __tablename__ = "webhook_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Anzeige/Identifikation, user-vergeben
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Wohin geschickt wird
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Auth gegen den Empfaenger (Header X-Webhook-Secret)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # DIS-verschluesselter Klartext-Secret (AES-256-GCM, AAD msm:webhook:{id}:secret)
    # Wird beim Versand entschluesselt. Ersetzt den frueheren In-Memory-Store.
    secret_encrypted: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    # Aktivierung
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Welche Events feuern diese Subscription?
    # Komma-getrennte Strings wie "status_change,player_update". Leer = alle.
    event_filter: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Last-delivery Snapshot fuer UI
    last_delivery_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_webhook_subs_server_enabled", "server_id", "enabled"),
    )
