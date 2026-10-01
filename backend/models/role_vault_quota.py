"""Speicher der Tresor-Cloud je globaler Rolle.

Ein Konto bekommt den hoechsten Wert seiner Rollen. Hat keine seiner Rollen eine
Zeile, bekommt es keinen Speicher und kann nichts hochladen; anders als beim
KI-Kontingent heisst „nichts hinterlegt“ hier nicht unbegrenzt. Die Aufloesung
steht in ``vault_blob_service.quote_fuer``.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RoleVaultQuota(Base):
    __tablename__ = "role_vault_quotas"

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    quota_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
