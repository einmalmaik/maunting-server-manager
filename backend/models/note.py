from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_uid() -> str:
    return str(uuid.uuid4())


class Note(Base):
    """Notiz-Eintrag in MSM.

    Ermöglicht Benutzern und dem KI-Assistenten das Erstellen, Verwalten und
    Strukturieren von Notizen, Aufgaben, Checklisten und Einkaufslisten.
    Kann persönlich oder mit einem Team geteilt sein.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Eindeutige UID für KI- und API-Referenzierung
    note_uid: Mapped[str] = mapped_column(
        String(64), default=_gen_uid, nullable=False, unique=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Semantische Kategorie: personal, shopping, todo, work, idea, meeting
    category: Mapped[str] = mapped_column(
        String(64), default="personal", nullable=False, index=True
    )
    # Design-DNA Farbakzent: primary, emerald, amber, rose, purple, cyan
    color: Mapped[str | None] = mapped_column(String(32), default="primary", nullable=True)

    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    # Sichtbarkeitstyp: "personal" | "team"
    note_type: Mapped[str] = mapped_column(
        String(32), default="personal", nullable=False, index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False, index=True
    )

    # Relationships
    user = relationship("User", lazy="joined")
    team = relationship("Team", lazy="joined")
