from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DirectChat(Base):
    """Repräsentiert eine persistente 1:1-Chatverbindung zwischen zwei Benutzern.

    Ermöglicht das Antworten und Fortführen von Unterhaltungen zwischen
    Nicht-Freunden, sobald der Chat einmal initiiert wurde.
    Konvention: user_a_id < user_b_id (für eindeutigen UniqueConstraint).
    """

    __tablename__ = "direct_chats"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_direct_chats_users"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    user_a_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_b_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blind_mailbox_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    initiated_by_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    user_a: Mapped["User"] = relationship("User", foreign_keys=[user_a_id])
    user_b: Mapped["User"] = relationship("User", foreign_keys=[user_b_id])
    initiator: Mapped["User"] = relationship("User", foreign_keys=[initiated_by_user_id])

    def get_other_user_id(self, current_user_id: int) -> int:
        return self.user_b_id if self.user_a_id == current_user_id else self.user_a_id
