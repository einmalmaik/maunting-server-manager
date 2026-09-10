from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatMedia(Base):
    """Speichert clientseitig verschluesselte E2EE-Medienanhaenge und Blobs.

    Sicherheits-Invarianten:
    - Der Server speichert und liefert AUSSCHLIESSLICH verschluesselte Blobs aus.
    - Zugriff auf Medien-URLs erfordert Chat-Mitgliedschaft (DirectChat oder ChatGroup).
    - Externe Abrufe erfolgen ueber signierte, zeitlich limitierte URLs (HMAC + Expiration).
    """

    __tablename__ = "chat_media"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    blind_mailbox_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direct_chat_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("direct_chats.id", ondelete="CASCADE"), nullable=True, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chat_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    uploader_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ciphertext_blob: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), default="application/octet-stream", nullable=False)
    file_name: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    uploader: Mapped["User"] = relationship("User", foreign_keys=[uploader_user_id])
    direct_chat: Mapped["DirectChat | None"] = relationship("DirectChat", foreign_keys=[direct_chat_id])
    group: Mapped["ChatGroup | None"] = relationship("ChatGroup", foreign_keys=[group_id])
