from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class PanelBackup(Base):
    """Panel Self-Backup Record (MSM-Datenbank + Config-Dateien).

    Speichert Metadaten eines Panel-Backups (pg_dump der zentralen
    MSM-DB + Config-Dateien wie .env, install.sh, Caddyfile.template, ...).

    Felder:
    - id: Primaerschluessel
    - name: Optionaler User-Name fuer das Backup
    - local_path: Absoluter Pfad zur lokalen tar.gz-Datei
    - s3_key: S3-Object-Key (null wenn nicht nach S3 hochgeladen)
    - s3_bucket: S3-Bucket-Name (null wenn nicht nach S3 hochgeladen)
    - encrypted: True wenn verschluesselt in S3 hochgeladen
    - size_mb: Groesse der lokalen tar.gz in MB
    - db_type: Datenbank-Typ ("postgresql"; alte "sqlite3"-Records sind read-only legacy)
    - created_at: Zeitstempel der Erstellung
    """
    __tablename__ = "panel_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    local_path: Mapped[str] = mapped_column(String(512), nullable=False)
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    s3_bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    size_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    db_type: Mapped[str] = mapped_column(String(32), default="postgresql", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
