from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserActivityTime(Base):
    """Aktive Nutzungszeit („Spielzeit“) eines Benutzers.

    Zählt echte administrative und dialogische Interaktionszeit,
    nicht bloßes Offenstehen von Browsertabs.
    """

    __tablename__ = "user_activity_times"
    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_user_activity_times_user_category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "general" | "ai_chat" | "server_admin" | "command_exec"
    category: Mapped[str] = mapped_column(String(32), default="general", nullable=False, index=True)
    seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", backref="activity_times")
