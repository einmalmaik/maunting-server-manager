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
    #: `name`, `description` und `avatar_url` standen hier bis Stufe 6c.
    #:
    #: `20260923_03` hat sie geleert, `20260923_05` hat sie entfernt. Der
    #: Unterschied ist nicht der Bestand — der war nach dem Leeren weg —,
    #: sondern was ein späterer Zweig tun kann: in eine Spalte, die es gibt,
    #: schreibt sich still zurück, was hier nie wieder stehen soll. Ein Feld,
    #: das es nicht gibt, bricht den Bau.
    #:
    #: Name, Beschreibung und Logo liegen im verschlüsselten Gruppenblock
    #: (`gruppenKonfig.ts`), im versiegelten örtlichen Namensspeicher
    #: (`gruppenName.ts`) und in der Einladungskarte unten.
    invite_code: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, default=generate_invite_code
    )
    #: Die Einladungskarte, verschlüsselt (`sv-einladung-v1:…`).
    #:
    #: Name, Beschreibung und Logo, wie ein Eingeladener sie sieht — und zwar
    #: so, dass dieser Server sie nicht lesen kann. Der Schlüssel fällt aus dem
    #: Gruppengeheimnis und steht **hinter der Raute** im Einladungslink; alles
    #: hinter der Raute schickt der Browser nie an einen Server.
    #:
    #: Ohne diese Spalte hinge die Vorschau an `name` und `avatar_url` — genau
    #: den Feldern, die in Stufe 6 verschwinden. Sie ist der Grund, warum die
    #: Einladung das überlebt.
    invite_card: Mapped[str | None] = mapped_column(Text, nullable=True)
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
