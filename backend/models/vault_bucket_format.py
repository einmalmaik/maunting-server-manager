from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBucketFormat(Base):
    """Die aelteste App, die einen Tresor-Bucket noch beschreiben darf.

    Eine App vor 09/2026 baute jeden Eintrag beim Speichern aus einer festen
    Feldliste neu und verwarf alles andere: Papierkorb, Archiv, Dateiverweise.
    Sobald ein Bucket solche Eintraege traegt, meldet der Client das hier an,
    und der Server nimmt von aelteren Apps keinen Sync mehr an.

    Wie ``vault_blind_buckets`` ohne Kontobezug.
    """

    __tablename__ = "vault_bucket_formats"

    bucket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    min_client_format: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
