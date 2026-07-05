from datetime import datetime

from pydantic import BaseModel


class PanelBackupCreateRequest(BaseModel):
    """Optionaler Name fuer das Panel-Backup."""
    name: str | None = None


class PanelBackupSettings(BaseModel):
    """Panel-Backup-Settings (Scheduler + Retention).

    Enthaelt bewusst KEINE Secrets (S3-Credentials, Passwort, Salt) —
    nur nicht-sensitive Scheduler/Retention-Konfiguration (VAL-PANEL-SETTINGS-003).
    """
    enabled: bool
    interval_hours: int
    retention_count: int

    class Config:
        from_attributes = True


class PanelBackupSettingsPatch(BaseModel):
    """Partial-PATCH fuer Panel-Backup-Settings.

    Alle Felder optional — nur angegebene Felder werden aktualisiert.
    Validierung (interval_hours > 0, retention_count >= 1) erfolgt im Service.
    """
    enabled: bool | None = None
    interval_hours: int | None = None
    retention_count: int | None = None


class PanelBackupResponse(BaseModel):
    """Panel-Backup-Record ohne sensitive Pfade (local_path, s3_key).

    local_path und s3_key werden bewusst NICHT zurueckgegeben
    (Security: keine internen Pfade nach aussen).
    """
    id: int
    name: str | None
    size_mb: int | None
    db_type: str
    encrypted: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PanelRestorePrepResponse(BaseModel):
    """Response fuer POST /api/panel-backups/{id}/prepare-restore.

    Enthaelt den Pfad zum generierten Restore-Script und deutsche Anweisungen
    fuer den Admin. Keine Secrets im Response (nur Pfad + Text).
    """
    script_path: str
    instructions: str

    class Config:
        from_attributes = True


class PanelBackupListItem(BaseModel):
    """List-Item fuer GET /api/panel-backups (sorted desc by created_at).

    Enthaelt KEINE sensitiven Pfade (local_path, s3_key, s3_bucket).
    s3_status ist ein nicht-sensitiver Status-Indikator:
      - "cloud":  verschluesselt in S3 hochgeladen (encrypted=True, s3_key gesetzt)
      - "local":  nur lokal vorhanden (encrypted=False)
    """
    id: int
    name: str | None
    size_mb: int | None
    db_type: str
    encrypted: bool
    s3_status: str
    created_at: datetime

    class Config:
        from_attributes = True
