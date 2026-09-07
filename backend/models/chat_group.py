from __future__ import annotations

from datetime import datetime, timezone
import secrets
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_invite_code() -> str:
    return secrets.token_urlsafe(16)


class ChatGroup(Base):
    """Chat-Gruppe / Community für E2EE Gruppen-Chats mit öffentlichen Einladungslinks."""

    __tablename__ = "chat_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_code: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, default=generate_invite_code
    )
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    default_permissions: Mapped[str | None] = mapped_column(
        String(256), nullable=True, default="send_messages,invite_members"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    members: Mapped[list["ChatGroupMember"]] = relationship(
        "ChatGroupMember", back_populates="group", cascade="all, delete-orphan"
    )


class ChatGroupMember(Base):
    """Mitgliedschaft in einer Chat-Gruppe."""

    __tablename__ = "chat_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_chat_group_members_group_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    permissions: Mapped[str | None] = mapped_column(String(256), nullable=True, default=None)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    group: Mapped["ChatGroup"] = relationship("ChatGroup", back_populates="members")
