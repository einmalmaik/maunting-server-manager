from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultBucketTombstone(Base):
    """Ein Tresor-Bucket, der zurueckgesetzt oder mit seinem Konto geloescht wurde.

    Seine Eintraege, Dateien und Nachweise sind weg. Ein anderes Geraet mit
    dem alten Tresor bekaeme sonst einen leeren Bucket vorgesetzt, den es per
    Erstanspruch neu belegt und mit seiner lokalen Kopie fuellt: der geloeschte
    Tresor kaeme zurueck. Jeder Weg, der einen Bucket beansprucht oder
    beschreibt, lehnt einen beerdigten mit 410 ab.

    Wie ``vault_blind_buckets`` ohne Kontobezug.
    """

    __tablename__ = "vault_bucket_tombstones"

    bucket_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
