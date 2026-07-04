from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, DateTime, ForeignKey, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(Integer, ForeignKey("servers.id"), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # S3-Cloud-Backup-Erweiterung (M1). Alle Felder nullable, damit bestehende
    # lokale Backups unberuehrt bleiben (Migration-Pfad: Default null/False).
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    s3_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    server: Mapped["Server"] = relationship("Server", back_populates="backups")
