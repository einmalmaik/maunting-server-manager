from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class E2eeBlindEnvelope(Base):
    """Zero-Metadata blinde Relais-Mailbox für E2EE Direkt- und Team-Chats.

    CRITICAL PRIVACY & SECURITY INVARIANTS:
    - NO USER LINKAGE: Enthält NIEMALS user_id, recipient_id, team_id, IP-Adressen oder Absenderdaten.
    - BLINDE ADRESSIERUNG: Adressierung erfolgt ausschließlich über den blind_mailbox_id Hash,
      den nur die beteiligten Parteien kryptographisch aus ihren Schlüsseln berechnen können.
    - DIS ENVELOPE: Der Server speichert und leitet reine DIS-Ciphertext-Umschläge weiter.
      Kein Klartext und keine Metadaten existieren in der Datenbank.
    """

    __tablename__ = "e2ee_blind_envelopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    blind_mailbox_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ciphertext_envelope: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )
