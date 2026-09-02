from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserMailbox(Base):
    """Verknüpftes E-Mail-Postfach eines Benutzers für den KI-Assistenten.

    Ermöglicht der KI, im Namen des Benutzers E-Mails zu durchsuchen,
    Zusammenfassungen zu erstellen oder Entwürfe zu verfassen.

    Sicherheit:
      Passwörter und OAuth-Refresh-Tokens liegen NIEMALS im Klartext vor,
      sondern werden über DIS AES-256-GCM verschlüsselt gespeichert.
    """

    __tablename__ = "user_mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Anzeigename (z. B. "Hauptpostfach", "Arbeit", "Gmail Privat")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # E-Mail-Adresse
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # "imap_smtp" | "oauth_google" | "oauth_microsoft"
    provider_type: Mapped[str] = mapped_column(
        String(32), default="imap_smtp", nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # IMAP Konfiguration (für Lesezugriff)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, default=993, nullable=True)
    imap_use_ssl: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    imap_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # SMTP Konfiguration (für Sendezugriff)
    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, default=587, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # DIS-verschlüsselte Zugangsdaten (Passwort oder OAuth Refresh-Token)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Benachrichtigungs- & Sync-Einstellungen
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_filter_rules_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def set_credentials(self, secret: str) -> None:
        """Verschlüsselt das Passwort oder Token mit DIS AES-256-GCM."""
        if not secret:
            self.credentials_encrypted = None
            return
        from services.dis_client import DisClient

        self.credentials_encrypted = DisClient.encrypt(
            secret, aad=f"msm:user_mailbox:{self.user_id}"
        )

    def get_credentials(self) -> str | None:
        """Entschlüsselt das hinterlegte Passwort oder Token."""
        if not self.credentials_encrypted:
            return None
        from services.dis_client import DisClient

        try:
            return DisClient.decrypt(
                self.credentials_encrypted, aad=f"msm:user_mailbox:{self.user_id}"
            )
        except Exception:
            return None

    @property
    def notify_filter_rules(self) -> list[dict]:
        if not self.notify_filter_rules_json:
            return []
        try:
            return json.loads(self.notify_filter_rules_json)
        except Exception:
            return []

    @notify_filter_rules.setter
    def notify_filter_rules(self, rules: list[dict] | None) -> None:
        if rules is None:
            self.notify_filter_rules_json = None
        else:
            self.notify_filter_rules_json = json.dumps(rules)
