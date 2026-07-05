"""Panel Backup Service — Panel Self-Backup (MSM-Datenbank + Configs).

Erstellt Panel-Backups bestehend aus:
1. DB-Dump der zentralen MSM-Datenbank (pg_dump fuer PostgreSQL,
   sqlite3 .dump fuer SQLite-Dev)
2. Config-Dateien (.env, install.sh, Caddyfile.template, msm.service.template,
   msm-update.service, msm-update.timer, update.sh — fehlende werden mit
   Warning skipped)
3. manifest.json mit Metadaten (timestamp, msm_version, db_type, config_list)
4. tar.gz lokal speichern (/opt/msm/backups/panel/)
5. Wenn S3 konfiguriert + Backup-Passwort gesetzt: verschluesseln + S3-Upload
6. PanelBackup-Record in DB speichern
7. Retention-Cleanup (lokal + S3 + DB, best-effort S3)

Datenfluss:
   pg_dump/sqlite3 -> msm_db.sql
   Config-Dateien -> configs/<name>
   manifest.json
   -> tar.gz lokal
   -> (wenn S3+Passwort) DIS encrypt-stream -> S3 upload_stream
   -> PanelBackup-Record
   -> Retention

Sicherheits-Invarianten:
- Keine Secrets (Passwort, Pfade, DB-Dump-Inhalt) in Logs.
- pg_dump-Fehler: kein PanelBackup-Record, keine partielle tar.gz, Temp cleaned.
- S3/DIS-Fehler blockieren NICHT das lokale Backup (Best-Effort, Warning-Log).
- Backup-Key wird immer invalidiert (try/finally) — bei Erfolg und bei Fehler.
- Config-Dateien werden mit relativen Pfaden (configs/<name>) ins Archiv
  gepackt (keine absoluten Pfade im tar).
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from config import settings

logger = logging.getLogger(__name__)

# Config-Dateien, die ins Panel-Backup aufgenommen werden (sofern vorhanden).
# Fehlende Dateien werden mit Warning skipped (Backup laeuft weiter).
_CONFIG_FILES: tuple[str, ...] = (
    ".env",
    "install.sh",
    "Caddyfile.template",
    "msm.service.template",
    "msm-update.service",
    "msm-update.timer",
    "update.sh",
)

# Archiv-interne Pfade
_ARC_DB_DUMP = "msm_db.sql"
_ARC_MANIFEST = "manifest.json"
_ARC_CONFIG_PREFIX = "configs"

# S3-Object-Key-Schema: msm-backups/panel/panel_{timestamp}_{id}.enc
_S3_KEY_PREFIX = "msm-backups/panel"

# Panel-Backup-Settings (panel_settings): Scheduler + Retention.
_KEY_ENABLED = "backup.panel_enabled"
_KEY_INTERVAL = "backup.panel_interval_hours"
_KEY_RETENTION = "backup.panel_retention_count"

_DEFAULT_ENABLED = False
_DEFAULT_INTERVAL = 24
_DEFAULT_RETENTION = 7

# Timeout fuer pg_dump / sqlite3 (Sekunden).
_DUMP_TIMEOUT = 300


# ── Oeffentliche API ─────────────────────────────────────────────────────


def create_panel_backup(db: Session, *, name: str | None = None) -> "PanelBackup":
    """Erstellt ein Panel-Backup (DB-Dump + Configs + S3-Upload).

    Gibt den PanelBackup-Record zurueck.

    Wirft bei pg_dump/sqlite3-Fehler (kein partieller Backup-Record, Temp
    wird bereinigt). S3/DIS-Fehler werden NICHT propagiert (Best-Effort).
    """
    from models import PanelBackup  # Inline-Import gegen Zyklen

    db_type = _detect_db_type()
    config_dir = _get_config_dir()
    backup_dir = _get_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)

    # Pruefen ob lokales Backup verschluesselt werden soll (Passwort gesetzt).
    from services.backup_config_service import BackupConfigService
    password_set = BackupConfigService.is_backup_password_set()
    if not password_set:
        logger.warning(
            "Kein Backup-Passwort gesetzt — Panel-Backup wird als plaintext gespeichert (backward compat)"
        )

    # Temp-Verzeichnis fuer den rohen DB-Dump (vor dem tar.gz). Wird am Ende
    # (egal ob Erfolg oder Fehler) bereinigt.
    tmp_dir = os.path.join(backup_dir, f".tmp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}")
    os.makedirs(tmp_dir, exist_ok=True)

    # Bei lokaler Verschluesselung: weiteres 0700 temp-dir fuer das Plaintext tar.gz
    enc_tmp_dir: str | None = None

    try:
        # 1. DB-Dump erstellen (pg_dump oder sqlite3 .dump).
        # Bei Fehlern wird kein Backup angelegt (atomic — kein partieller State).
        db_dump_bytes = _dump_database(db_type)
        if not db_dump_bytes:
            raise RuntimeError("Panel-Backup: DB-Dump war leer")

        # 2. Config-Dateien sammeln (vorhandene, fehlende mit Warning skippen).
        config_list, config_blobs = _collect_config_files(config_dir)

        # 3. manifest.json erstellen.
        manifest = _build_manifest(db_type, config_list)
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        # 4. tar.gz erstellen (in temp-dir wenn Verschluesselung, sonst direkt im backup_dir).
        # Timestamp mit Mikrosekunden — verhindert Kollisionen bei schnellen
        # aufeinanderfolgenden Backups (gleiche Sekunde).
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        tar_filename = f"panel_{timestamp}.tar.gz"

        if password_set:
            # 0700 temp-dir fuer Plaintext tar.gz (VAL-FIX-002)
            import tempfile as _tf
            enc_tmp_dir = _tf.mkdtemp(prefix="msm_panel_enc_", dir=backup_dir)
            try:
                os.chmod(enc_tmp_dir, 0o700)
            except OSError:
                pass
            tar_path = os.path.join(enc_tmp_dir, tar_filename)
        else:
            tar_path = os.path.join(backup_dir, tar_filename)

        _write_archive(
            tar_path,
            db_dump_bytes=db_dump_bytes,
            manifest_bytes=manifest_bytes,
            config_blobs=config_blobs,
        )

        if password_set:
            # 0600 Permissions auf Plaintext tar.gz
            try:
                os.chmod(tar_path, 0o600)
            except OSError:
                pass
            # tar.gz via DIS zu .enc verschluesseln (VAL-FIX-001)
            # Best-Effort: bei DIS-Fehler Fall back zu plaintext tar.gz
            enc_filename = f"panel_{timestamp}.enc"
            enc_path = os.path.join(backup_dir, enc_filename)
            try:
                _encrypt_panel_local_backup(tar_path, enc_path)
                # Plaintext tar.gz sicher loeschen
                try:
                    os.remove(tar_path)
                except OSError:
                    pass
                local_path = enc_path
            except Exception as enc_exc:
                # DIS nicht erreichbar: lokales Backup als plaintext (Best-Effort).
                logger.warning(
                    "Lokale Verschluesselung fehlgeschlagen (%s) — Panel-Backup als plaintext (backward compat)",
                    type(enc_exc).__name__,
                )
                # tar.gz in backup_dir verschieben
                plain_path = os.path.join(backup_dir, f"panel_{timestamp}.tar.gz")
                try:
                    shutil.move(tar_path, plain_path)
                except OSError:
                    pass
                local_path = plain_path
        else:
            local_path = tar_path

        size_mb = os.path.getsize(local_path) // (1024 * 1024)

        # 5. PanelBackup-Record persistieren (vor S3-Upload, damit die ID fuer
        # den S3-Key verfuegbar ist). s3_key/encrypted werden nach Upload gesetzt.
        backup = PanelBackup(
            name=name or None,
            local_path=local_path,
            size_mb=size_mb,
            db_type=db_type,
            encrypted=False,
        )
        db.add(backup)
        db.commit()
        db.refresh(backup)

        # 6. S3-Upload (Best-Effort). Bei Erfolg: s3_key/encrypted setzen.
        _maybe_upload_to_s3(backup, db, local_path, timestamp)

        logger.info(
            "Panel-Backup erstellt (id=%s, db_type=%s, size_mb=%s)",
            backup.id, backup.db_type, backup.size_mb,
        )

        # 7. Retention-Cleanup (lokal + S3 + DB, best-effort).
        try:
            cleanup_old_panel_backups(db)
        except Exception as exc:
            logger.warning(
                "Panel-Backup Retention fehlgeschlagen: %s", type(exc).__name__
            )

        return backup

    except Exception:
        # Bei Fehler (insb. pg_dump): partielle Dateien bereinigen.
        # Kein PanelBackup-Record wurde persistiert (atomic — wirft vor commit).
        logger.error("Panel-Backup-Erstellung fehlgeschlagen (details redacted for security)")
        raise
    finally:
        # Temp-Verzeichnis immer bereinigen (auch bei Erfolg — tar.gz liegt ja
        # bereits im backup_dir bzw. .enc).
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # 0700 temp-dir fuer Plaintext tar.gz immer bereinigen (VAL-FIX-002)
        if enc_tmp_dir:
            shutil.rmtree(enc_tmp_dir, ignore_errors=True)


def get_panel_backup_settings() -> dict:
    """Gibt Panel-Backup-Settings zurueck (mit Defaults bei fehlenden Werten).

    Returns: {"enabled": bool, "interval_hours": int, "retention_count": int}

    Defaults (VAL-PANEL-SETTINGS-001): enabled=False, interval_hours=24,
    retention_count=7. Ungueltige/fehlende Werte werden auf Defaults gesetzt.
    """
    from services.panel_settings_service import PanelSettingsService

    enabled_raw = PanelSettingsService.get(_KEY_ENABLED)
    enabled = _parse_bool(enabled_raw, _DEFAULT_ENABLED)

    interval = _parse_int(
        PanelSettingsService.get(_KEY_INTERVAL), _DEFAULT_INTERVAL
    )
    retention = _parse_int(
        PanelSettingsService.get(_KEY_RETENTION), _DEFAULT_RETENTION
    )

    return {
        "enabled": enabled,
        "interval_hours": interval,
        "retention_count": retention,
    }


def update_panel_backup_settings(
    *,
    enabled: bool | None = None,
    interval_hours: int | None = None,
    retention_count: int | None = None,
) -> dict:
    """Aktualisiert Panel-Backup-Settings (partial PATCH).

    Nur angegebene Felder werden geschrieben (partial PATCH,
    VAL-PANEL-SETTINGS-002). Validierung:
      - interval_hours > 0
      - retention_count >= 1
    Bei ungueltigen Werten wird ValueError geworfen (Router gibt 400 zurueck).

    Returns: aktualisierte Settings (get_panel_backup_settings).
    """
    from services.panel_settings_service import PanelSettingsService

    if enabled is not None:
        PanelSettingsService.set(_KEY_ENABLED, "true" if enabled else "false")

    if interval_hours is not None:
        if interval_hours <= 0:
            raise ValueError("interval_hours muss groesser als 0 sein")
        PanelSettingsService.set(_KEY_INTERVAL, str(int(interval_hours)))

    if retention_count is not None:
        if retention_count < 1:
            raise ValueError("retention_count muss mindestens 1 sein")
        PanelSettingsService.set(_KEY_RETENTION, str(int(retention_count)))

    return get_panel_backup_settings()


def _parse_bool(raw: str, default: bool) -> bool:
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _parse_int(raw: str, default: int) -> int:
    if not raw:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def cleanup_old_panel_backups(db: Session, *, keep: int | None = None) -> None:
    """Loescht alte Panel-Backups ueber dem Retention-Limit (lokal + S3 + DB).

    Best-Effort S3-Delete: ein S3-Fehler bricht die Retention nicht ab.
    Wenn keep=None wird aus panel_settings gelesen (Default 7).
    """
    from models import PanelBackup  # Inline-Import gegen Zyklen

    if keep is None:
        from services.panel_settings_service import PanelSettingsService
        raw = PanelSettingsService.get(_KEY_RETENTION)
        try:
            keep = int(raw) if raw else _DEFAULT_RETENTION
        except (ValueError, TypeError):
            keep = _DEFAULT_RETENTION
        if keep < 1:
            keep = _DEFAULT_RETENTION

    # Aelteste zuerst loeschen (offset nach sort desc).
    old = (
        db.query(PanelBackup)
        .order_by(PanelBackup.created_at.desc())
        .offset(keep)
        .all()
    )
    for b in old:
        # S3-Delete (best-effort, nur wenn s3_key vorhanden).
        if b.s3_key:
            try:
                from services.s3_service import S3Service
                S3Service.delete_object(b.s3_key, bucket=b.s3_bucket)
            except Exception as exc:
                logger.warning(
                    "S3-Delete fehlgeschlagen (Panel-Backup %s): %s",
                    b.id, type(exc).__name__,
                )
        # Lokale Datei loeschen (best-effort).
        if b.local_path and os.path.exists(b.local_path):
            try:
                os.remove(b.local_path)
            except OSError as exc:
                logger.warning(
                    "Konnte Panel-Backup-Datei nicht loeschen (id=%s): %s",
                    b.id, type(exc).__name__,
                )
        db.delete(b)

    if old:
        db.commit()
        logger.info(
            "Panel-Backup Retention: behalten=%s, geloescht=%s", keep, len(old)
        )


# ── S3-Upload (Best-Effort) ──────────────────────────────────────────────


def delete_panel_backup(db: Session, backup_id: int) -> bool:
    """Loescht ein Panel-Backup aus lokalem FS, S3 und DB (Best-Effort S3).

    Reihenfolge: S3 (best-effort) -> lokal (best-effort) -> DB-Row.
    S3-Fehler blockieren NICHT das lokale Loeschen (Warning-Log, keine Secrets).
    Idempotent auf fehlender lokaler Datei (lokal-Delete skipped still loescht DB).
    Idempotent auf fehlendem S3-Objekt (S3 delete_object ist idempotent).

    Returns: True wenn ein Record geloescht wurde, False wenn id nicht existiert
    (idempotent auf nicht-existenter ID — kein Fehler).
    """
    from models import PanelBackup  # Inline-Import gegen Zyklen

    backup = db.query(PanelBackup).filter(PanelBackup.id == backup_id).first()
    if backup is None:
        # Idempotent: nicht-existente ID ist kein Fehler.
        return False

    # 1. S3-Delete (best-effort, nur wenn s3_key vorhanden).
    if backup.s3_key:
        try:
            from services.s3_service import S3Service
            S3Service.delete_object(backup.s3_key, bucket=backup.s3_bucket)
        except Exception as exc:
            # Generische Warning — keine Secrets, kein Pfad-Leak.
            logger.warning(
                "S3-Delete fehlgeschlagen (Panel-Backup %s): %s — lokales Loeschen wird fortgesetzt",
                backup_id, type(exc).__name__,
            )

    # 2. Lokale Datei loeschen (best-effort). Fehlende Datei ist OK (idempotent).
    if backup.local_path:
        try:
            if os.path.exists(backup.local_path):
                os.remove(backup.local_path)
        except OSError as exc:
            logger.warning(
                "Konnte Panel-Backup-Datei nicht loeschen (id=%s): %s — DB-Row wird trotzdem entfernt",
                backup_id, type(exc).__name__,
            )

    # 3. DB-Row entfernen (immer, auch wenn FS/S3-Fehler).
    db.delete(backup)
    db.commit()

    logger.info("Panel-Backup geloescht (id=%s)", backup_id)
    return True


# ── Panel-Restore-Vorbereitung (M4) ──────────────────────────────────────


class PanelRestoreError(Exception):
    """Panel-Restore-Vorbereitung fehlgeschlagen (generisch, keine Secrets)."""


class PanelRestoreNotFoundError(PanelRestoreError):
    """Panel-Backup mit der angegebenen ID existiert nicht."""


class PanelRestoreNoArchiveError(PanelRestoreError):
    """Keine Archiv-Quelle verfuegbar (lokal fehlt, kein s3_key)."""


class PanelRestoreDecryptError(PanelRestoreError):
    """Entschluesselung fehlgeschlagen (falsches Passwort / manipulierter Stream)."""


def prepare_panel_restore(backup_id: int, db: Session) -> dict:
    """Bereitet Panel-Restore vor (Download + Decrypt + Script-Generierung).

    Ablauf:
    1. PanelBackup-Record laden (404 wenn nicht gefunden).
    2. Archiv-Quelle ermitteln:
       - Lokale Datei vorhanden: direkt verwenden (kein S3, kein Decrypt).
       - Lokal fehlt, s3_key vorhanden: von S3 downloaden (+ ggf. DIS
         entschluesseln fuer legacy .tar.gz), lokal speichern.
       - Beides fehlt: Fehler (keine Archiv-Quelle).
    3. Wenn .enc: DIS-Entschluesselung zu temp tar.gz (VAL-FIX-004).
    4. Archiv (tar.gz) in Temp-Verzeichnis entpacken (um manifest.json zu lesen).
    5. Restore-Script generieren (bash, self-contained, idempotent).
       Bei .enc: Script enthaelt Decrypt-Schritt via MSM Python-Backend.
    6. Script ausfuehrbar machen (chmod +x).
    7. Temp-Verzeichnisse bereinigen (nur das Script bleibt im backup_dir).
    8. Backup-Key invalidieren (try/finally, bei S3/Decrypt-Pfad).

    Returns: {"script_path": str, "instructions": str}
    """
    from models import PanelBackup  # Inline-Import gegen Zyklen

    backup = db.query(PanelBackup).filter(PanelBackup.id == backup_id).first()
    if backup is None:
        raise PanelRestoreNotFoundError(
            "Panel-Backup nicht gefunden"
        )

    backup_dir = _get_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)

    # Temp-Verzeichnis fuer die Extraktion (wird am Ende bereinigt).
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    tmp_dir = os.path.join(backup_dir, f".restore_tmp_{backup.id}_{ts}")
    # Temp-Verzeichnis fuer .enc-Entschluesselung (wird am Ende bereinigt).
    decrypt_tmp_dir: str | None = None

    key_id: str | None = None
    try:
        # 1. Archiv-Quelle sicherstellen (lokal oder S3+download).
        key_id = _ensure_local_archive(backup, db)

        # 2. Wenn .enc: zu temp tar.gz entschluesseln (VAL-FIX-004).
        is_enc = backup.local_path.endswith(".enc")
        if is_enc:
            from services.backup_crypto_service import (
                BackupCryptoService,
                BackupDecryptionError,
                BackupCryptoError,
            )
            import tempfile as _tf

            decrypt_tmp_dir = _tf.mkdtemp(prefix="msm_panel_restore_")
            try:
                os.chmod(decrypt_tmp_dir, 0o700)
            except OSError:
                pass

            from services.backup_config_service import BackupConfigService
            dec_key_id: str | None = None
            try:
                password = BackupConfigService.get_backup_password()
                salt = BackupConfigService.get_backup_salt()
                dec_key_id = BackupCryptoService.init_key(password, salt)
                tar_name = os.path.basename(backup.local_path).replace(".enc", ".tar.gz")
                tar_path_for_extract = os.path.join(decrypt_tmp_dir, tar_name)
                with open(backup.local_path, "rb") as f:
                    BackupCryptoService.decrypt_to_file(f, dec_key_id, tar_path_for_extract)
                try:
                    os.chmod(tar_path_for_extract, 0o600)
                except OSError:
                    pass
                archive_for_extract = tar_path_for_extract
            except BackupDecryptionError:
                raise PanelRestoreDecryptError("Entschluesselung fehlgeschlagen")
            except BackupCryptoError:
                raise PanelRestoreError("Verschlüsselungs-Service nicht verfügbar")
            finally:
                if dec_key_id:
                    try:
                        BackupCryptoService.invalidate_key(dec_key_id)
                    except Exception:
                        logger.warning(
                            "Key-Invalidierung fehlgeschlagen (Panel-Restore .enc Decrypt)"
                        )
        else:
            archive_for_extract = backup.local_path

        # 3. Archiv (tar.gz) in Temp-Verzeichnis entpacken.
        _extract_archive_to_dir(archive_for_extract, tmp_dir)

        # 4. manifest.json lesen (db_type, config_list).
        manifest = _read_manifest(tmp_dir)
        db_type = manifest.get("db_type", backup.db_type or "postgresql")
        config_list = manifest.get("config_list", [])

        # 5. Restore-Script generieren.
        script_path = _generate_restore_script(
            backup_id=backup.id,
            archive_path=backup.local_path,
            db_type=db_type,
            config_list=config_list,
            timestamp=ts,
            is_encrypted=is_enc,
        )

        # 6. Script ausfuehrbar machen (chmod +x).
        _make_executable(script_path)

        # 7. Anweisungen (Deutsch, mit sudo bash und Warnung).
        instructions = _build_restore_instructions(script_path)

        logger.info(
            "Panel-Restore vorbereitet (backup_id=%s, script generiert, encrypted=%s)",
            backup.id, is_enc,
        )

        return {
            "script_path": script_path,
            "instructions": instructions,
        }
    except PanelRestoreDecryptError:
        raise
    except Exception as exc:
        # Generische Fehlermeldung — keine Secrets/Pfade leaken.
        logger.warning(
            "Panel-Restore-Vorbereitung fehlgeschlagen (backup_id=%s): %s",
            backup.id, type(exc).__name__,
        )
        raise
    finally:
        # Temp-Verzeichnis immer bereinigen (VAL-PANEL-RESTORE-012).
        shutil.rmtree(tmp_dir, ignore_errors=True)
        # Decrypt-temp-dir bereinigen (VAL-FIX-004: temp tar.gz wird geloescht).
        if decrypt_tmp_dir:
            shutil.rmtree(decrypt_tmp_dir, ignore_errors=True)
        # Backup-Key immer invalidieren (VAL-PANEL-RESTORE-004, success und failure).
        if key_id:
            try:
                from services.backup_crypto_service import BackupCryptoService
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Panel-Restore backup_id=%s)",
                    backup.id,
                )


def _ensure_local_archive(backup, db: Session) -> str | None:
    """Stellt sicher, dass das Archiv lokal verfuegbar ist.

    Wenn die lokale Datei existiert: direkt verwenden (kein S3, kein Decrypt).
    Wenn lokal fehlt aber s3_key:
    - .enc Dateiname: S3-Objekt direkt herunterladen (bereits verschluesselt).
      Entschluesselung erfolgt spaeter in prepare_panel_restore.
    - .tar.gz Dateiname (legacy): S3 download + DIS entschluesseln zu .tar.gz.
    Gibt key_id zurueck (oder None wenn kein S3-Pfad oder .enc Direkt-Download).

    Wirft PanelRestoreNoArchiveError wenn keine Quelle verfuegbar.
    Wirft PanelRestoreDecryptError bei Entschluesselungsfehler (legacy Pfad).
    """
    if backup.local_path and os.path.exists(backup.local_path):
        # Lokale Datei vorhanden — direkt verwenden (VAL-PANEL-RESTORE-003).
        logger.info(
            "Panel-Restore: lokale Datei verwendet (backup_id=%s)", backup.id
        )
        return None

    if not backup.s3_key:
        raise PanelRestoreNoArchiveError(
            "Keine Archiv-Quelle verfuegbar (lokal fehlt, kein S3-Key)"
        )

    # Neues Format: .enc Dateiname → S3 direkt herunterladen (kein Decrypt)
    if backup.local_path and backup.local_path.endswith(".enc"):
        from services.s3_service import S3Service
        try:
            body = S3Service.download_stream(backup.s3_key, bucket=backup.s3_bucket)
            with open(backup.local_path, "wb") as f:
                for chunk in body.iter_chunks():
                    f.write(chunk)
            logger.info(
                "Panel-Restore: S3-Download erfolgreich (backup_id=%s, .enc direkt)",
                backup.id,
            )
            return None
        except Exception as exc:
            raise PanelRestoreError(
                "Archiv-Download fehlgeschlagen"
            ) from exc

    # Legacy Pfad: .tar.gz Dateiname → S3 download + DIS decrypt
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import (
        BackupCryptoService,
        BackupDecryptionError,
    )
    from services.s3_service import S3Service

    password = BackupConfigService.get_backup_password()
    salt = BackupConfigService.get_backup_salt()
    key_id = BackupCryptoService.init_key(password, salt)

    try:
        try:
            body = S3Service.download_stream(backup.s3_key, bucket=backup.s3_bucket)
            BackupCryptoService.decrypt_to_file(
                body.iter_chunks(), key_id, backup.local_path
            )
            logger.info(
                "Panel-Restore: S3-Download + Decrypt erfolgreich (backup_id=%s, legacy .tar.gz)",
                backup.id,
            )
            return key_id
        except BackupDecryptionError as exc:
            # Falsches Passwort / manipulierter Stream — klare Fehlermeldung.
            raise PanelRestoreDecryptError(
                "Entschluesselung fehlgeschlagen"
            ) from exc
        except PanelRestoreError:
            raise
        except Exception as exc:
            # S3-Fehler oder anderer Fehler — generisch, keine Secrets.
            raise PanelRestoreError(
                "Archiv-Download fehlgeschlagen"
            ) from exc
    except Exception:
        # Bei jedem Fehler (Decrypt, S3, sonstiges): Key invalidieren
        # bevor der Fehler weitergereicht wird (VAL-PANEL-RESTORE-004).
        try:
            BackupCryptoService.invalidate_key(key_id)
        except Exception:
            logger.warning(
                "Key-Invalidierung nach Fehler fehlgeschlagen (backup_id=%s)",
                backup.id,
            )
        raise


def _extract_archive_to_dir(archive_path: str, target_dir: str) -> None:
    """Entpackt ein Panel-Backup-tar.gz in target_dir."""
    os.makedirs(target_dir, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        # filter='data' verhindert Path-Traversal und gefaehrliche Metadaten
        # (Python 3.12+ Empfehlung, schuetzt vor Tar-Slip).
        try:
            tar.extractall(path=target_dir, filter="data")
        except TypeError:
            # Aeltere Python-Versionen ohne filter-Parameter
            tar.extractall(path=target_dir)


def _read_manifest(extract_dir: str) -> dict:
    """Liest manifest.json aus dem entpackten Archiv.

    Fallback: leeres Dict wenn manifest.json fehlt (alte Archive).
    """
    manifest_path = os.path.join(extract_dir, _ARC_MANIFEST)
    if not os.path.isfile(manifest_path):
        return {}
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _generate_restore_script(
    *,
    backup_id: int,
    archive_path: str,
    db_type: str,
    config_list: list[str],
    timestamp: str,
    is_encrypted: bool = False,
) -> str:
    """Generiert das Restore-Script (bash) und speichert es im backup_dir.

    Das Script ist self-contained: es extrahiert das Archiv zur Laufzeit in
    ein temporares Verzeichnis, stoppt den Panel-Service, sichert die .env,
    stellt die Datenbank und Configs wieder her und startet den Panel neu.

    Bei verschluesselten Backups (is_encrypted=True): das Script enthaelt
    zusaetzlich einen Decrypt-Schritt, der das .enc-Archiv via MSM Python-
    Backend zu einem temp tar.gz entschluesselt (VAL-FIX-004).

    Sicherheits-Invarianten:
    - Keine Plaintext-Secrets im Script (nur Pfad-Referenzen).
    - DB-Verbindungsparameter werden zur Laufzeit aus der .env gelesen
      (vor dem Ueberschreiben), nicht im Script eingebettet.
    - Safety-Copies nutzen eindeutige Zeitstempel (idempotent, VAL-PANEL-RESTORE-011).

    Returns: Pfad zum generierten Script.
    """
    backup_dir = _get_backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    script_filename = f"restore_{backup_id}.sh"
    script_path = os.path.join(backup_dir, script_filename)

    config_dir = _get_config_dir()
    now_iso = datetime.now(timezone.utc).isoformat()

    lines: list[str] = []
    lines.append("#!/bin/bash")
    lines.append("# MSM Panel Restore Script - Generated by MSM Backup System")
    lines.append(f"# Date: {now_iso}")
    lines.append(f"# Backup ID: {backup_id}")
    lines.append("#")
    lines.append("# WARNING: This will stop the panel and replace the database!")
    lines.append(f"# Run as: sudo bash {script_path}")
    lines.append("")
    lines.append("set -euo pipefail")
    lines.append("")
    lines.append(f'ARCHIVE_PATH="{archive_path}"')
    lines.append(f'CONFIG_DIR="{config_dir}"')
    lines.append("")
    lines.append("# Temp directory for extraction (cleaned up on exit)")
    lines.append('RESTORE_DIR=$(mktemp -d)')
    lines.append('trap \'rm -rf "$RESTORE_DIR"\' EXIT')
    lines.append("")

    if is_encrypted:
        # .enc-Backup: zuerst via MSM Python-Backend entschluesseln, dann extrahieren.
        # Der Decrypt-Schritt nutzt BackupCryptoService (DIS Sidecar). Das temp
        # tar.gz wird nach der Extraktion bereinigt (trap erweitert).
        lines.append("# 1. Decrypt encrypted archive (.enc -> temp tar.gz)")
        lines.append('DECRYPT_TAR="$RESTORE_DIR/archive.tar.gz"')
        # MSM Backend-Pfad (Production: /opt/msm/backend)
        lines.append('MSM_BACKEND_DIR="$(dirname "$CONFIG_DIR")/backend"')
        lines.append('if [ ! -d "$MSM_BACKEND_DIR" ]; then')
        lines.append('    echo "MSM Backend nicht gefunden unter $MSM_BACKEND_DIR" >&2')
        lines.append('    exit 1')
        lines.append('fi')
        lines.append('cd "$MSM_BACKEND_DIR"')
        lines.append('venv/bin/python3 -c "\\')
        lines.append('from services.backup_config_service import BackupConfigService\\')
        lines.append('from services.backup_crypto_service import BackupCryptoService\\')
        lines.append('import os, sys\\')
        lines.append('pw = BackupConfigService.get_backup_password()\\')
        lines.append('salt = BackupConfigService.get_backup_salt()\\')
        lines.append('kid = BackupCryptoService.init_key(pw, salt)\\')
        lines.append('try:\\')
        lines.append('    with open(sys.argv[1], \\"rb\\") as f:\\')
        lines.append('        BackupCryptoService.decrypt_to_file(f, kid, sys.argv[2])\\')
        lines.append('finally:\\')
        lines.append('    BackupCryptoService.invalidate_key(kid)\\')
        lines.append('" "$ARCHIVE_PATH" "$DECRYPT_TAR"')
        lines.append('trap \'rm -rf "$RESTORE_DIR"\' EXIT')
        lines.append("")
        lines.append("# 2. Extract decrypted archive")
        lines.append('tar -xzf "$DECRYPT_TAR" -C "$RESTORE_DIR"')
        lines.append("")
        lines.append("# 3. Stop panel service")
        lines.append("systemctl stop msm-panel.service")
        lines.append("")
        lines.append("# 4. Backup current .env (safety copy with unique timestamp)")
    else:
        lines.append("# 1. Extract archive")
        lines.append('tar -xzf "$ARCHIVE_PATH" -C "$RESTORE_DIR"')
        lines.append("")
        lines.append("# 2. Stop panel service")
        lines.append("systemctl stop msm-panel.service")
        lines.append("")
        lines.append("# 3. Backup current .env (safety copy with unique timestamp)")
    lines.append('ENV_BACKUP="$CONFIG_DIR/.env.pre_restore_$(date +%Y%m%d_%H%M%S)"')
    lines.append('if [ -f "$CONFIG_DIR/.env" ]; then')
    lines.append('    cp "$CONFIG_DIR/.env" "$ENV_BACKUP"')
    lines.append('fi')
    lines.append("")

    # Step-Nummern verschieben sich bei .enc (Decrypt + Extract = 2 Schritte extra)
    _step_db = 5 if is_encrypted else 4
    _step_cfg = 6 if is_encrypted else 5
    _step_restart = 7 if is_encrypted else 6

    if db_type == "sqlite3":
        lines.append(f"# {_step_db}. Restore database (SQLite)")
        lines.append("# Load current DATABASE_URL from .env (before overwrite)")
        lines.append('set -a')
        lines.append('. "$CONFIG_DIR/.env" 2>/dev/null || true')
        lines.append('set +a')
        lines.append('DB_PATH="${DATABASE_URL#sqlite:///}"')
        lines.append('sqlite3 "$DB_PATH" < "$RESTORE_DIR/msm_db.sql"')
    else:
        lines.append(f"# {_step_db}. Restore database (PostgreSQL)")
        lines.append("# Load current DATABASE_URL from .env (before overwrite)")
        lines.append('set -a')
        lines.append('. "$CONFIG_DIR/.env" 2>/dev/null || true')
        lines.append('set +a')
        lines.append('psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$RESTORE_DIR/msm_db.sql"')

    lines.append("")
    lines.append(f"# {_step_cfg}. Restore config files")

    if config_list:
        for name in config_list:
            arc = f"{_ARC_CONFIG_PREFIX}/{name}"
            lines.append(f'cp "$RESTORE_DIR/{arc}" "$CONFIG_DIR/{name}"')
    else:
        lines.append("# (no config files in manifest — skipping)")

    lines.append("")
    lines.append(f"# {_step_restart}. Restart panel service")
    lines.append("systemctl start msm-panel.service")
    lines.append("")
    lines.append('echo "Restore complete. Check panel status: systemctl status msm-panel.service"')
    lines.append("")

    content = "\n".join(lines)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return script_path


def _make_executable(script_path: str) -> None:
    """Setzt das Execute-Bit (chmod +x). Auf Windows ein No-op-Seat."""
    try:
        os.chmod(script_path, 0o755)
    except OSError:
        # Windows: chmod ist eingeschraenkt — kein harter Fehler.
        pass


def _build_restore_instructions(script_path: str) -> str:
    """Erstellt deutsche Anweisungen fuer den Admin (mit sudo bash und Warnung).

    Enthaelt:
    - Warnung ueber Service-Stop und Datenverlust (VAL-PANEL-RESTORE-005).
    - sudo bash Befehl (VAL-PANEL-RESTORE-005).
    - Hinweis auf Status-Check nach dem Restore.
    """
    return (
        "Restore-Skript wurde erstellt:\n"
        f"  {script_path}\n"
        "\n"
        "WARNUNG: Dieses Skript stoppt den MSM-Panel-Dienst und "
        "ueberschreibt die Datenbank und Konfigurationsdateien!\n"
        "Sichern Sie aktuelle Daten, bevor Sie fortfahren. "
        "Ein Datenverlust ist moeglich.\n"
        "\n"
        "Fuehren Sie das Skript mit Root-Rechten aus:\n"
        f"  sudo bash {script_path}\n"
        "\n"
        "Nach dem Restore koennen Sie den Panel-Status pruefen:\n"
        "  systemctl status msm-panel.service"
    )


def _maybe_upload_to_s3(
    backup, db: Session, local_path: str, timestamp: str
) -> None:
    """Verschluesselt das Panel-Backup und laedt es zu S3 hoch (Best-Effort).

    Bei Fehlern (S3/DIS) wird s3_key=null belassen und nur gewarnt.
    Backup-Key wird immer invalidiert (try/finally).

    Zwei Pfade:
    - .enc Datei (bereits lokal verschluesselt): direkt zu S3 hochladen,
      kein DIS encrypt noetig (gleiche verschluesselte Bytes).
    - .tar.gz Datei (legacy Plaintext): via DIS encrypt-stream verschluesseln.
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import BackupCryptoService
    from services.s3_service import S3NotConfiguredError, S3OperationError

    if not os.path.exists(local_path):
        logger.warning(
            "S3-Upload skipped — lokale Datei fehlt (Panel-Backup %s)", backup.id
        )
        return

    if not (BackupConfigService.is_s3_configured()
            and BackupConfigService.is_backup_password_set()):
        logger.info(
            "S3 nicht konfiguriert oder kein Passwort — Panel-Backup %s nur lokal",
            backup.id,
        )
        return

    key_id: str | None = None
    try:
        # S3-Object-Key: msm-backups/panel/panel_{timestamp}_{id}.enc
        s3_key = f"{_S3_KEY_PREFIX}/panel_{timestamp}_{backup.id}.enc"

        from services.s3_service import S3Service

        if local_path.endswith(".enc"):
            # Bereits lokal verschluesselt — direkt zu S3 hochladen (kein DIS noetig).
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
            "S3-Upload erfolgreich (Panel-Backup %s)", backup.id
        )
    except S3NotConfiguredError:
        logger.warning(
            "S3 nicht konfiguriert — Upload skipped (Panel-Backup %s)", backup.id
        )
    except (S3OperationError, Exception) as exc:
        # Generische Warning ohne Secrets. S3/DIS-Fehler blockieren nicht lokal.
        logger.warning(
            "S3-Upload fehlgeschlagen (Panel-Backup %s): %s — lokales Backup bleibt",
            backup.id, type(exc).__name__,
        )
    finally:
        # Key IMMER invalidieren (Erfolg und Fehler) — kein Key-Leak.
        # Nur wenn ein Key initialisiert wurde (.tar.gz legacy Pfad).
        if key_id:
            try:
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Panel-Backup %s)", backup.id
                )


# ── DB-Dump ──────────────────────────────────────────────────────────────


def _detect_db_type() -> str:
    """Erkennt den DB-Typ aus der konfigurierten database_url.

    Returns: "postgresql" oder "sqlite3".
    """
    url = (settings.database_url or "").strip()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return "postgresql"
    if url.startswith("sqlite"):
        return "sqlite3"
    # Fallback-Default (Produktion nutzt PostgreSQL)
    return "postgresql"


def _dump_database(db_type: str) -> bytes:
    """Erstellt den DB-Dump und gibt ihn als Bytes zurueck.

    PostgreSQL: pg_dump via subprocess (env PGPASSWORD fuer Passwordless-Auth).
    SQLite (dev): sqlite3 <db_path> .dump via subprocess.

    Wirft bei Fehlern (nicht-leerer Exit-Code) — Caller stellt Atomicity sicher
    (kein partieller Backup-Record).
    """
    if db_type == "postgresql":
        return _pg_dump_postgres()
    if db_type == "sqlite3":
        return _sqlite3_dump()
    raise RuntimeError(f"Unbekannter db_type: {db_type}")


def _pg_dump_postgres() -> bytes:
    """pg_dump der zentralen MSM-PostgreSQL-Datenbank.

    Parst die database_url fuer Verbindungsparameter. Setzt PGPASSWORD env,
    damit das Passwort nicht in der Kommandozeile erscheint (Process-Listing-
    Leak). Keine Secrets in Logs/Exceptions.
    """
    url = settings.database_url
    parsed = urlparse(url)
    db_name = (parsed.path or "/").lstrip("/") or "msm"
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 5432)
    user = parsed.username or "msm"
    password = parsed.password or ""

    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    cmd = [
        "pg_dump",
        "--host", host,
        "--port", port,
        "--username", user,
        "--format", "plain",
        "--no-owner",
        "--no-privileges",
        db_name,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            timeout=_DUMP_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("pg_dump nicht installiert") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("pg_dump Timeout") from exc

    if result.returncode != 0:
        # Kein stderr im Log/Exception (kann Connection-Details enthalten).
        logger.warning("pg_dump fehlgeschlagen (exit=%s)", result.returncode)
        raise RuntimeError("pg_dump fehlgeschlagen")

    dump = result.stdout
    if not dump:
        raise RuntimeError("pg_dump: leerer Dump")
    # pg_dump-Marker (Comment) sollte enthalten sein.
    return dump


def _sqlite3_dump() -> bytes:
    """sqlite3 .dump der zentralen MSM-SQLite-Datenbank (Dev-Modus).

    Extrahiert den Dateipfad aus der database_url (sqlite:///./msm.db -> ./msm.db).
    :memory:-DBs koennen nicht via CLI gedumpt werden — Caller muss das mocken.
    """
    url = settings.database_url
    # sqlite:///path -> path ; sqlite:///:memory: -> :memory:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise RuntimeError("SQLite database_url nicht parsebar")
    db_path = url[len(prefix):]

    if db_path == ":memory:":
        # In-Memory-DB kann nicht via CLI gedumpt werden. Der Caller (Tests)
        # sollte _dump_database mocken. Als Fallback leeren Dump melden.
        raise RuntimeError("sqlite3 .dump fuer :memory: nicht moeglich")

    cmd = ["sqlite3", db_path, ".dump"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_DUMP_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("sqlite3 nicht installiert") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("sqlite3 Timeout") from exc

    if result.returncode != 0:
        logger.warning("sqlite3 .dump fehlgeschlagen (exit=%s)", result.returncode)
        raise RuntimeError("sqlite3 .dump fehlgeschlagen")

    dump = result.stdout
    if not dump:
        raise RuntimeError("sqlite3: leerer Dump")
    return dump


# ── Config-Dateien ───────────────────────────────────────────────────────


def _collect_config_files(config_dir: str) -> tuple[list[str], dict[str, bytes]]:
    """Sammelt vorhandene Config-Dateien aus config_dir.

    Fehlende Dateien werden mit Warning skipped (Backup laeuft weiter).

    Returns: (config_list, config_blobs) — config_list ist die sortierte Liste
    der enthaltenen Dateinamen (fuer manifest.json), config_blobs mapt
    Dateiname -> Dateiinhalt-Bytes (fuer das tar-Archiv).
    """
    config_list: list[str] = []
    config_blobs: dict[str, bytes] = {}

    for name in _CONFIG_FILES:
        path = os.path.join(config_dir, name)
        if not os.path.isfile(path):
            # Fehlende Datei mit Warning skippen (Backup laeuft weiter).
            logger.warning("Panel-Backup: Config-Datei fehlt, skipped: %s", name)
            continue
        try:
            with open(path, "rb") as f:
                config_blobs[name] = f.read()
            config_list.append(name)
        except OSError as exc:
            # Lesefehler: skippen mit Warning (kein harte Abbruch).
            logger.warning(
                "Panel-Backup: Config-Datei nicht lesbar, skipped: %s (%s)",
                name, type(exc).__name__,
            )

    return config_list, config_blobs


# ── Manifest ─────────────────────────────────────────────────────────────


def _build_manifest(db_type: str, config_list: list[str]) -> dict:
    """Erstellt manifest.json mit Metadaten.

    Pflicht-Felder: timestamp (ISO8601), msm_version, db_type, config_list.
    """
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "msm_version": _get_msm_version(),
        "db_type": db_type,
        "config_list": list(config_list),
    }


def _get_msm_version() -> str:
    """Liest die MSM-Version aus /opt/msm/.version oder git describe.

    Fallback: "unknown" (kein harter Fehler — Version ist nur Metadaten).
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5,
            cwd=settings.panel_config_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        version_path = os.path.join(settings.panel_config_dir, ".version")
        with open(version_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        pass
    return "unknown"


# ── Archiv ───────────────────────────────────────────────────────────────


def _write_archive(
    local_path: str,
    *,
    db_dump_bytes: bytes,
    manifest_bytes: bytes,
    config_blobs: dict[str, bytes],
) -> None:
    """Schreibt das tar.gz-Archiv mit manifest, db-dump und configs.

    Archiv-Struktur:
      manifest.json
      msm_db.sql
      configs/<name>
    """
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with tarfile.open(local_path, "w:gz") as tar:
        # manifest.json
        _add_bytes(tar, _ARC_MANIFEST, manifest_bytes)
        # msm_db.sql
        _add_bytes(tar, _ARC_DB_DUMP, db_dump_bytes)
        # configs/<name>
        for name, blob in config_blobs.items():
            _add_bytes(tar, f"{_ARC_CONFIG_PREFIX}/{name}", blob)


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    """Fuegt Bytes als tar-Member hinzu (in-memory, kein temp File)."""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = 0  # reproduzierbare Archive
    tar.addfile(info, io.BytesIO(data))


def _encrypt_panel_local_backup(tar_path: str, enc_path: str) -> None:
    """Verschluesselt ein lokales Panel-Backup tar.gz zu .enc via DIS.

    Stream: tar.gz -> DIS encrypt-stream -> .enc Datei.
    Key-Lifecycle: init_key vor Verschluesselung, invalidate_key immer danach.
    Setzt 0600-Permissions auf die .enc-Datei.
    """
    from services.backup_config_service import BackupConfigService
    from services.backup_crypto_service import BackupCryptoService

    key_id: str | None = None
    try:
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        encrypted_stream = BackupCryptoService.encrypt_file_stream(tar_path, key_id)
        with open(enc_path, "wb") as f:
            for chunk in encrypted_stream:
                f.write(chunk)
        try:
            os.chmod(enc_path, 0o600)
        except OSError:
            pass  # Windows
    finally:
        if key_id:
            try:
                BackupCryptoService.invalidate_key(key_id)
            except Exception:
                logger.warning(
                    "Key-Invalidierung fehlgeschlagen (Panel-Backup lokale Verschluesselung)"
                )


# ── Verzeichnis-Helper ───────────────────────────────────────────────────


def _get_config_dir() -> str:
    """Gibt das Config-Verzeichnis zurueck (Production: /opt/msm)."""
    return settings.panel_config_dir


def _get_backup_dir() -> str:
    """Gibt das Panel-Backup-Verzeichnis zurueck (Production: /opt/msm/backups/panel)."""
    return settings.panel_backup_dir
