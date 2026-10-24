"""Schlanke FIFO-Zustell-Queue für den E2EE-Messenger.

Jedes Chiffrat wird für ein Zielgerät in diese Warteschlange gelegt.
Sobald das Zielgerät die Nachricht abholt und lokal verarbeitet,
quittiert es den Empfang und der Eintrag wird aus der Queue gelöscht.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class E2eeDeviceQueue(Base):
    """Zustellauftrag in der gerätebezogenen FIFO-Queue."""

    __tablename__ = "e2ee_device_queue"
    __table_args__ = (
        Index("ix_e2ee_queue_target_device", "target_device_id"),
        Index("ix_e2ee_queue_client_uuid", "client_uuid"),
        Index("ix_e2ee_queue_chat", "chat_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_e2ee_devices.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    sender_device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "direct" | "group"
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)    # Empfänger-ID oder Gruppen-ID
    client_uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
