from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultHint(Base):
    """Speichert den optionalen Passwort-Hinweis eines Benutzers für seinen Passwort-Manager.
    
    Wird beim Vergessen des Master-Passworts per E-Mail zugestellt.
    Rate-Limit: Maximal 1 Zustellung alle 10 Minuten.
    """

    __tablename__ = "vault_hints"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    hint: Mapped[str] = mapped_column(String(512), nullable=False)
    last_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
