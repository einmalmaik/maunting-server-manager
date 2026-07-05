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

    Lokale Verschluesselung: wenn ein Backup-Passwort gesetzt ist, wird das
    lokale tar.gz via DIS zu .enc verschluesselt (VAL-FIX-001/002/003).
    Ohne Passwort: Plaintext tar.gz (backward compat, Warning-Log).
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_service import run_backup

    # Lokale Verschluesselung wenn Passwort gesetzt (unabhaengig von S3-Config).
    password_set = BackupConfigService.is_backup_password_set()
    s3_configured = BackupConfigService.is_s3_configured()
    s3_eligible = s3_configured and password_set
    encrypt_local = password_set  # VAL-FIX-001: local .enc when password set

    if s3_configured and not password_set:
        logger.warning(
            "S3 konfiguriert aber kein Backup-Passwort gesetzt — Server %s: nur lokales Backup",
            server_id,
        )
    if not password_set:
        logger.warning(
            "Kein Backup-Passwort gesetzt — Server %s: lokales Backup als plaintext (backward compat)",
            server_id,
        )

    # 1. Lokales Backup erstellen (tar.gz oder .enc wenn encrypt_local).
    # encrypted-Flag steuert das Manifest im tar.gz (true wenn verschluesselt).
    backup = run_backup(
        server_id,
        db,
        name=name,
        timeout_seconds=timeout_seconds,
        encrypted=encrypt_local,
        encryption_algorithm=_ENCRYPTION_ALGORITHM if encrypt_local else None,
        encrypt_local=encrypt_local,
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

    Zwei Pfade:
    - .enc Datei (bereits lokal verschluesselt): direkt zu S3 hochladen,
      kein DIS encrypt noetig (gleiche verschluesselte Bytes).
    - .tar.gz Datei (legacy upload-to-cloud): via DIS encrypt-stream
      verschluesseln und hochladen (bestehendes Verhalten).
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
        # S3-Object-Key: msm-backups/servers/{id}/server_{id}_{timestamp}_{backup_id}.enc
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3_key = f"{_S3_KEY_PREFIX}/{server_id}/server_{server_id}_{timestamp}_{backup.id}.enc"

        if local_path.endswith(".enc"):
            # Bereits lokal verschluesselt — direkt zu S3 hochladen (kein DIS noetig).
            # S3-Objekt enthaelt die gleichen verschluesselten Bytes wie die lokale .enc.
            with open(local_path, "rb") as f:
                S3Service.upload_stream(f, s3_key)
        else:
            # Legacy .tar.gz — via DIS encrypt-stream verschluesseln und hochladen.
            password = BackupConfigService.get_backup_password()
            salt = BackupConfigService.get_backup_salt()
            key_id = BackupCryptoService.init_key(password, salt)
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
        # Nur wenn ein Key initialisiert wurde (.tar.gz legacy Pfad).
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
    """Laedt ein Backup von S3 herunter und speichert es lokal.

    Wird vom Restore-Endpoint verwendet, wenn die lokale Datei fehlt aber ein
    s3_key vorhanden ist. Nach erfolgreichem Aufruf existiert die Datei unter
    ``backup.filename`` und die bestehende Restore-Logik kann ausgefuehrt werden.

    Zwei Pfade:
    - .enc Dateiname (neues Format): S3-Objekt ist bereits verschluesselt.
      Direkt herunterladen als .enc — keine DIS-Entschluesselung noetig.
      Die Entschluesselung erfolgt spaeter in der Restore-Logik.
    - .tar.gz Dateiname (legacy): S3-Objekt ist verschluesselt, muss via DIS
      entschluesselt werden um als .tar.gz lokal gespeichert zu werden.

    Key-Lifecycle (nur legacy Pfad): init_key vor Download/Decrypt,
    invalidate_key immer danach (try/finally — auch bei Fehler).

    Wirft bei:
    - S3NotConfiguredError / S3OperationError: S3-Fehler
    - BackupDecryptionError: Entschluesselung fehlgeschlagen (legacy Pfad)
    - BackupCryptoError: DIS nicht erreichbar (legacy Pfad)
    """
    from services.s3_service import S3Service

    # Neues Format: .enc Dateiname → S3-Objekt direkt herunterladen (kein Decrypt)
    if backup.filename.endswith(".enc"):
        body = S3Service.download_stream(backup.s3_key, bucket=backup.s3_bucket)
        with open(backup.filename, "wb") as f:
            for chunk in body.iter_chunks():
                f.write(chunk)
        logger.info(
            "S3-Restore: Download erfolgreich (Backup %s, .enc direkt)",
            backup.id,
        )
        return

    # Legacy Pfad: .tar.gz Dateiname → S3 download + DIS decrypt zu .tar.gz
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import BackupCryptoService

    key_id: str | None = None
    try:
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        # S3-Download → DIS decrypt-stream → lokale .tar.gz Datei.
        body = S3Service.download_stream(backup.s3_key, bucket=backup.s3_bucket)
        BackupCryptoService.decrypt_to_file(body.iter_chunks(), key_id, backup.filename)

        logger.info(
            "S3-Restore: Download + Decrypt erfolgreich (Backup %s, legacy .tar.gz)",
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


def decrypt_local_backup_for_restore(enc_path: str) -> str:
    """Entschluesselt eine lokale .enc-Backup-Datei zu einem temporaeren tar.gz.

    Wird vom Restore-Endpoint verwendet, wenn das lokale Backup .enc ist
    (Passwort war gesetzt bei Backup-Erstellung).

    Ablauf (VAL-FIX-004):
    1. 0700 temp-dir erstellen
    2. .enc -> DIS decrypt-stream -> temp tar.gz (0600 Permissions)
    3. Key invalidieren (try/finally)
    4. Pfad zum temp tar.gz zurueckgeben (Caller muss temp-dir aufraeumen)

    Wirft bei:
    - BackupDecryptionError: falsches Passwort / manipulierter Stream
    - BackupCryptoError: DIS nicht erreichbar

    Returns: Pfad zum temporaeren tar.gz. Caller muss das temp-dir
    (os.path.dirname(return_value)) nach der Extraktion aufraeumen.
    """
    import shutil
    import tempfile

    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import (
        BackupCryptoService,
        BackupDecryptionError,
        BackupCryptoError,
    )

    # 0700 temp-dir fuer das entschluesselte tar.gz
    tmp_dir = tempfile.mkdtemp(prefix="msm_restore_")
    try:
        os.chmod(tmp_dir, 0o700)
    except OSError:
        pass  # Windows

    tar_filename = os.path.basename(enc_path).replace(".enc", ".tar.gz")
    tar_path = os.path.join(tmp_dir, tar_filename)

    key_id: str | None = None
    try:
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        # .enc -> DIS decrypt-stream -> temp tar.gz
        with open(enc_path, "rb") as f:
            BackupCryptoService.decrypt_to_file(f, key_id, tar_path)
        try:
            os.chmod(tar_path, 0o600)
        except OSError:
            pass  # Windows

        logger.info(
            "Lokales .enc Backup entschluesselt fuer Restore (temp tar.gz erstellt)"
        )
        return tar_path
    except BackupDecryptionError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except BackupCryptoError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise BackupCryptoError(f"Entschluesselung fehlgeschlagen: {type(exc).__name__}") from exc
    finally:
        # Key IMMER invalidieren (Erfolg und Fehler) — kein Key-Leak.
        if key_id:
            try:
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Restore Decrypt)"
                )
