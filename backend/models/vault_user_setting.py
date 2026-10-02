from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultUserSetting(Base):
    """Welcher Tresor-Bucket zu welchem Konto gehoert, und das Salz dazu.

    Das Konto steht hier nur als ``konto_index``: ein HMAC der Kontonummer aus
    dem DIS-Sidecar (``vault_service.tresor_konto``). Wer nur die Datenbank
    liest, sieht nicht, welchem Konto ein Tresor gehoert; der Server erfaehrt
    es, solange eine angemeldete Anfrage laeuft. Bis 01.10.2026 stand die
    ``user_id`` im Klartext darin.

    SECURITY INVARIANTS:
    - Ein Bucket gehoert hoechstens einem Konto (``bucket_id`` unique).
    - Ein Konto hat hoechstens einen Bucket (``konto_index`` unique).
    """

    __tablename__ = "vault_user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Leer nur fuer Altbestand, bis der Nachzug beim Start oder das erste Lesen
    # ihn setzt.
    konto_index: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    # Altbestand von vor dem 01.10.2026. Wird beim Umstellen auf NULL gesetzt
    # und nie mehr geschrieben.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bucket_id: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=True,
    )
    kdf_salt: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_now,
        onupdate=_now,
        nullable=False,
    )
