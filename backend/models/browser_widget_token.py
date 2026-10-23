"""Gerätegebundene Tokens für das Wetter-Homescreen-Widget.

Das Widget läuft ohne geöffnete App und braucht dafür einen eigenen,
eng begrenzten Zugang. Es ist **kein** Kopplungs-Token: es gilt nur für
`POST /api/browser/wetter/widget`, wird nur als Hash gespeichert und läuft
nach 30 Tagen ab.
"""

from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class BrowserWidgetToken(Base):
    __tablename__ = "browser_widget_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    scope: Mapped[str] = mapped_column(String(32), default="browser.wetter")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
