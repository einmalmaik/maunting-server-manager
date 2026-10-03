from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultSicherungszugang(Base):
    """Zugang der Kamera-Sicherung, die auf dem Telefon ohne offene App laeuft.

    Er darf nur Blobs hochladen und Datensaetze in den Posteingang legen, nur
    fuer einen Bucket (``services/vault_sicherung_service.py``). Das Token
    steht hier nur als SHA-256, Konto und Sitzungsfamilie nur als HMAC aus dem
    Sidecar, wie in den uebrigen Tresortabellen (AGENTS.md Punkt 73).
    """

    __tablename__ = "vault_sicherungszugaenge"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    konto_index: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    familie_index: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
