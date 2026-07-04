"""Backup Orchestrator — orchestriert Server-Backup-Erstellung (lokal + S3).

Erweitert die bestehende lokale Backup-Erstellung um einen verschluesselten
S3-Upload (Best-Effort). Der lokale tar.gz-Snapshot bleibt Primary; S3 ist
das verschluesselte Off-Site-Backup.

Datenfluss:
1. Lokales tar.gz erstellen (bestehende Logik aus backup_service.run_backup)
2. Wenn S3 konfiguriert + Backup-Passwort gesetzt:
   a. Backup-Passwort entschluesseln via DIS
   b. Backup-Key initialisieren via DIS (init_key)
   c. Lokale Datei streamen -> DIS encrypt-stream -> S3 upload_stream
   d. s3_key, s3_bucket, encrypted=True im Backup-Record speichern
   e. Backup-Key invalidieren (immer via try/finally)
3. Bei S3/DIS-Fehler: Warning-Log (keine Secrets), lokales Backup bleibt,
   s3_key=null, encrypted=False.

Sicherheits-Invarianten:
- Keine Secrets (Passwort, Pfade) in Logs.
- Key wird immer invalidiert (try/finally), bei Erfolg und bei Fehler.
- S3-Ausfall blockiert nicht die lokale Backup-Erstellung.
- DIS-Ausfall blockiert S3-Upload, lokales Backup bleibt.
- Concurrent Backups verwenden separate key_ids (jeder Aufruf erzeugt eigenen Key).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# S3-Object-Key-Schema: msm-backups/servers/{id}/server_{id}_{timestamp}_{backup_id}.enc
_S3_KEY_PREFIX = "msm-backups/servers"
_ENCRYPTION_ALGORITHM = "AES-256-GCM"


def create_server_backup(
    server_id: int,
    db: Session,
    *,
    name: str | None = None,
    timeout_seconds: int = 600,
) -> "Backup":
    """Erstellt lokales Backup + verschluesselten S3-Upload (wenn konfiguriert).

    Gibt den Backup-Record zurueck. Wirft bei lokalem Backup-Fehler (wie run_backup).
    S3/DIS-Fehler werden NICHT propagiert (Best-Effort, Warning-Log).
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_service import run_backup

    # Vorab pruefen ob S3-Upload moeglich ist (Intent bestimmt Manifest-Erweiterung).
    s3_eligible = (
        BackupConfigService.is_s3_configured()
        and BackupConfigService.is_backup_password_set()
    )

    if BackupConfigService.is_s3_configured() and not BackupConfigService.is_backup_password_set():
        logger.warning(
            "S3 konfiguriert aber kein Backup-Passwort gesetzt — Server %s: nur lokales Backup",
            server_id,
        )

    # 1. Lokales tar.gz erstellen (bestehende Logik).
    # Wenn S3-eligible: Manifest mit encrypted=true + algorithm erstellen.
    backup = run_backup(
        server_id,
        db,
        name=name,
        timeout_seconds=timeout_seconds,
        encrypted=s3_eligible,
        encryption_algorithm=_ENCRYPTION_ALGORITHM if s3_eligible else None,
    )

    # 2. S3-Upload (Best-Effort).
    if not s3_eligible:
        return backup

    _upload_to_s3(backup, db, server_id)
    return backup


def _upload_to_s3(backup, db: Session, server_id: int) -> None:
    """Verschluesselt die lokale Backup-Datei und laedt sie zu S3 hoch.

    Best-Effort: bei Fehlern wird s3_key=null belassen und nur gewarnt.
    Key wird immer invalidiert (try/finally).
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import BackupCryptoService
    from services.s3_service import S3Service, S3NotConfiguredError, S3OperationError

    local_path = backup.filename
    if not os.path.exists(local_path):
        logger.warning(
            "S3-Upload skipped — lokale Datei fehlt (Server %s, Backup %s)",
            server_id, backup.id,
        )
        return

    key_id: str | None = None
    try:
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        # S3-Object-Key: msm-backups/servers/{id}/server_{id}_{timestamp}_{backup_id}.enc
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3_key = f"{_S3_KEY_PREFIX}/{server_id}/server_{server_id}_{timestamp}_{backup.id}.enc"

        # Stream: lokale Datei -> DIS encrypt-stream -> S3 upload_stream.
        # Keine temp verschluesselte Datei auf der Platte (reines Streaming).
        encrypted_stream = BackupCryptoService.encrypt_file_stream(local_path, key_id)
        S3Service.upload_stream(encrypted_stream, s3_key)

        # Erfolg: s3_key, s3_bucket, encrypted=True speichern.
        bucket = BackupConfigService.get_s3_config().get("bucket") or ""
        backup.s3_key = s3_key
        backup.s3_bucket = bucket
        backup.encrypted = True
        db.commit()
        db.refresh(backup)

        logger.info(
            "S3-Upload erfolgreich (Server %s, Backup %s)",
            server_id, backup.id,
        )
    except S3NotConfiguredError:
        logger.warning(
            "S3 nicht konfiguriert — Upload skipped (Server %s, Backup %s)",
            server_id, backup.id,
        )
    except (S3OperationError, Exception) as exc:
        # Generische Warning ohne Secrets (kein Passwort, keine Pfade, keine Credentials).
        # S3/DIS-Fehler blockieren nicht das lokale Backup.
        logger.warning(
            "S3-Upload fehlgeschlagen (Server %s, Backup %s): %s — lokales Backup bleibt",
            server_id, backup.id, type(exc).__name__,
        )
    finally:
        # Key IMMER invalidieren (Erfolg und Fehler) — kein Key-Leak.
        if key_id:
            try:
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Server %s, Backup %s)",
                    server_id, backup.id,
                )


def upload_backup_to_cloud(backup, db: Session, server_id: int) -> bool:
    """Laedt ein bestehendes lokales Backup verschluesselt zu S3 hoch.

    Idempotent: wenn bereits hochgeladen (s3_key + encrypted=True), wird kein
    Re-Upload durchgefuehrt (Rueckgabe True).

    Returns True wenn Backup in S3 ist (Erfolg oder bereits vorhanden),
    False bei Upload-Fehler.
    """
    # Idempotenz: bereits hochgeladen → kein Re-Upload.
    if backup.s3_key and backup.encrypted:
        return True

    _upload_to_s3(backup, db, server_id)
    return bool(backup.s3_key and backup.encrypted)


def fetch_backup_from_s3(backup, db: Session) -> None:
    """Laedt ein Backup von S3 herunter, entschluesselt es via DIS und speichert es lokal.

    Wird vom Restore-Endpoint verwendet, wenn die lokale Datei fehlt aber ein
    s3_key vorhanden ist. Nach erfolgreichem Aufruf existiert die Datei unter
    ``backup.filename`` und die bestehende Restore-Logik (Container stoppen,
    Extrahieren, DB-Reset) kann ausgefuehrt werden.

    Key-Lifecycle: init_key vor Download/Decrypt, invalidate_key immer danach
    (try/finally — auch bei Fehler).

    Wirft bei:
    - S3NotConfiguredError / S3OperationError: S3-Fehler (Provider nicht erreichbar, Objekt fehlt)
    - BackupDecryptionError: Entschluesselung fehlgeschlagen (falsches Passwort / manipuliert)
    - BackupCryptoError: DIS nicht erreichbar oder anderer DIS-Fehler

    Der Caller (Router) fangt diese ab und gibt klare User-Meldungen zurueck.
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import BackupCryptoService
    from services.s3_service import S3Service

    key_id: str | None = None
    try:
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        # S3-Download → DIS decrypt-stream → lokale Datei.
        # StreamBody.iter_chunks() liefert einen Iterator[bytes], den httpx
        # direkt an DIS weiterstreamt (kein Puffern der ganzen Datei im Speicher).
        body = S3Service.download_stream(backup.s3_key)
        BackupCryptoService.decrypt_to_file(body.iter_chunks(), key_id, backup.filename)

        logger.info(
            "S3-Restore: Download + Decrypt erfolgreich (Backup %s)",
            backup.id,
        )
    finally:
        # Key IMMER invalidieren (Erfolg und Fehler) — kein Key-Leak.
        if key_id:
            try:
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Backup %s)",
                    backup.id,
                )
