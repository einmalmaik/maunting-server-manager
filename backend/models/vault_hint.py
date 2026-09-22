from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultHint(Base):
    """Speichert den optionalen Passwort-Hinweis eines Benutzers für seinen Passwort-Manager.
    
    Wird beim Vergessen des Master-Passworts per E-Mail zugestellt.
    Rate-Limit: Maximal 1 Zustellung alle 10 Minuten.

    `hint` haelt den **verschluesselten** Hinweis und ist deshalb `Text`, nicht
    `String(512)`: die 512 sind die Grenze des Klartexts aus `VaultHintSetRequest`,
    und AES-GCM plus Base64 macht daraus rund 720 Zeichen (siehe Migration
    20260922_01).
    """

    __tablename__ = "vault_hints"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    hint: Mapped[str] = mapped_column(Text, nullable=False)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
