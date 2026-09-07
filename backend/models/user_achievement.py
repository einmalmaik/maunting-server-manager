from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserAchievement(Base):
    """Freigeschaltete Errungenschaft eines Benutzers.

    Zentrale Kontobindung: Jede Errungenschaft kann systemweit genau einmal
    je Benutzer errungen werden.
    """

    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "achievement_id", name="uq_user_achievements_user_achievement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    achievement_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", backref="achievements")
