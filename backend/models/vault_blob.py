from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBlob(Base):
    """Eine Datei der Tresor-Cloud, wie der Server sie sieht: Chiffrat in Chunks.

    Name, Typ, echte Groesse und Schluessel stehen im Tresor-Eintrag, der auf
    den Blob verweist, und damit nur im Umschlag des Clients. Hier stehen nur
    Kennung, Bucket, Anzahl und Groesse der Chunks (gepolstert) und der Hash
    des Loeschnachweises.

    Der Blob gehoert dem Bucket, nicht dem Konto: welches Konto den Bucket
    hat, steht nur als HMAC in ``vault_user_settings``. Die Quote wird je
    Bucket gezaehlt, ihre Grenze kommt aus den Rollen des angemeldeten Kontos.
    Bis 01.10.2026 stand hier die ``user_id``. Der Chiffrat liegt auf der
    Platte unter ``settings.vault_blob_dir``.
    """

    __tablename__ = "vault_blobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    bucket_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # sha256 des Loeschschluessels, der nur im Tresor-Eintrag steht. Ein
    # abgegriffenes Zugangstoken allein loescht damit keine Datei.
    delete_verifier: Mapped[str] = mapped_column(String(64), nullable=False)
    # offen -> fertig -> geloescht. Geloeschte haelt der Server noch
    # `LOESCHHALTUNG` lang, bevor die Dateien verschwinden; nach Zuruecksetzen
    # und Kontoloeschung nicht (`alle_zur_loeschung`).
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="offen")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
