from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.dis_text import DisText


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatStory(Base):
    """Temporäre Status-Stories im Messenger (24h Gültigkeit).
    
    Tied to friendships - sichtbar für den Ersteller und bestätigte Freunde.
    Kann Text (mit Farb-Gradient) oder Bild/Medien mit Bildunterschrift enthalten.
    """

    __tablename__ = "chat_stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nicht Ende-zu-Ende verschluesselt: das Backend liest Text und Bild, um
    # sie Freunden bzw. bei oeffentlichem Profil allen Konten zu zeigen. In
    # der Datenbank stehen sie trotzdem nur als Chiffrat (bis 29.09.2026
    # Klartext, waehrend der Editor "Ende-zu-Ende verschluesselt" anzeigte).
    content: Mapped[str] = mapped_column(DisText(aad="msm:social:chat_stories.content"), nullable=False)
    media_url: Mapped[str | None] = mapped_column(DisText(aad="msm:social:chat_stories.media_url"), nullable=True)
    background: Mapped[str] = mapped_column(String(64), default="gradient-1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship("User")
