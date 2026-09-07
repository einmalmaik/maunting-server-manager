from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserFriend(Base):
    """Freundschaftsbeziehung zwischen zwei Benutzern.

    Status:
    - 'pending': Anfrage gestellt, wartet auf Bestätigung
    - 'accepted': Bestätigte Freundschaft (beidseitig sichtbar)
    - 'blocked': Blockiert
    """

    __tablename__ = "user_friends"
    __table_args__ = (
        UniqueConstraint("user_id", "friend_id", name="uq_user_friends_user_friend"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    friend_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], backref="friendships_sent")
    friend: Mapped["User"] = relationship("User", foreign_keys=[friend_id], backref="friendships_received")
