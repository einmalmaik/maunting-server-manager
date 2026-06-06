from datetime import datetime, timezone

from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
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

    # ── Cloud-Provider-Integration (Schritt 7) ──
    # Additive Spalten fuer die Backup-Cloud-Redesign-Migration.
    # Alte Records (vor Cloud-Enable) haben provider="local" und
    # remote_key=None — die Logik in backup_service.py faellt fuer
    # diese Records auf das alte Verhalten zurueck.
    #
    # provider: Welcher Storage-Provider das Backup haelt
    #   ("local" | "s3" | "sftp" | "dropbox" | "gcs" | "azure")
    # remote_key: Wo das Backup im Provider liegt, relativ zum
    #   Provider-Namespace. Format: "<server_id>/<filename>" (ohne
    #   fuehrenden /; jeder Provider ergaenzt seinen base_path /
    #   container / prefix). Fuer local-Records ist das Feld None.
    # metadata_json: JSON-serialisierte BackupMetadata (public
    #   fields: server_name, game_type, limits, ports, ...). Wird
    #   beim Restore genutzt um server.cpu/ram/disk/public_bind_ip
    #   und die Port-Rollen wiederherzustellen. None fuer sehr alte
    #   Records, die vor Cloud-Enable angelegt wurden.
    provider: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    remote_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    server: Mapped["Server"] = relationship("Server", back_populates="backups")
