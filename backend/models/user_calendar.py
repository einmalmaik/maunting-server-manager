from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserCalendar(Base):
    """Verknüpfter Kalender eines Benutzers für den KI-Assistenten.

    Ermöglicht der KI, Termine abzufragen und neue Termine vorzuschlagen.

    Sicherheit:
      Zugangsdaten und Token werden mit DIS AES-256-GCM verschlüsselt.
    """

    __tablename__ = "user_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Anzeigename (z. B. "Nextcloud Kalender", "Google Termine")
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # "caldav" | "oauth_google" | "oauth_microsoft" | "local_ics"
    provider_type: Mapped[str] = mapped_column(
        String(32), default="caldav", nullable=False
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # CalDAV Konfiguration
    caldav_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    caldav_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # DIS-verschlüsseltes Passwort oder OAuth-Token
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

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
            secret, aad=f"msm:user_calendar:{self.user_id}"
        )

    def get_credentials(self) -> str | None:
        """Entschlüsselt das hinterlegte Passwort oder Token."""
        if not self.credentials_encrypted:
            return None
        from services.dis_client import DisClient

        try:
            return DisClient.decrypt(
                self.credentials_encrypted, aad=f"msm:user_calendar:{self.user_id}"
            )
        except Exception:
            return None
