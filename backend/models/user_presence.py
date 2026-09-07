from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserPresence(Base):
    """Online-Status, Geräte-Erkennung und Rich Presence eines Benutzers.

    Status:
    - 'online': Aktiv
    - 'away': Abwesend
    - 'invisible': Offline / Unsichtbar

    Geräte-Erkennung:
    - 'web': 🌐 Web-Panel (Browser / Globus-Symbol)
    - 'desktop': 🖥️ Desktop-App (MSM Desktop / PC-Symbol)
    - 'mobile': 📱 Mobile App (MSS Smart System / Handy-Symbol)
    """

    __tablename__ = "user_presences"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), default="online", nullable=False)
    device_type: Mapped[str] = mapped_column(String(16), default="web", nullable=False)
    custom_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activity_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    activity_detail: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", backref="presence")
