from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultEingang(Base):
    """Posteingang eines Tresors: Datensaetze, die ein Geraet bei gesperrtem Tresor ablegt.

    Die Kamera-Sicherung laedt die Dateien schon hoch, kann aber ohne
    Master-Passwort keinen Tresor-Eintrag anlegen. Sie legt hier einen
    Datensatz ab, der mit dem oeffentlichen Schluessel des Tresors
    verschluesselt und vom Geraet unterschrieben ist. Beim naechsten Entsperren
    macht eine App daraus einen Eintrag und loescht den Datensatz.

    Der Server sieht nur Chiffrat, Zeitpunkt und Bucket. Wie die uebrigen
    Tresortabellen ohne Kontobezug (AGENTS.md Punkt 73).
    """

    __tablename__ = "vault_eingang"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bucket_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
