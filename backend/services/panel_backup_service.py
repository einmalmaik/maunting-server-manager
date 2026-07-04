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

# Default-Retention fuer Panel-Backups (panel_settings: backup.panel_retention_count).
_DEFAULT_RETENTION = 7
_KEY_RETENTION = "backup.panel_retention_count"

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

    # Temp-Verzeichnis fuer den rohen DB-Dump (vor dem tar.gz). Wird am Ende
    # (egal ob Erfolg oder Fehler) bereinigt.
    tmp_dir = os.path.join(backup_dir, f".tmp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}")
    os.makedirs(tmp_dir, exist_ok=True)

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

        # 4. tar.gz lokal speichern.
        # Timestamp mit Mikrosekunden — verhindert Kollisionen bei schnellen
        # aufeinanderfolgenden Backups (gleiche Sekunde).
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"panel_{timestamp}.tar.gz"
        local_path = os.path.join(backup_dir, filename)
        _write_archive(
            local_path,
            db_dump_bytes=db_dump_bytes,
            manifest_bytes=manifest_bytes,
            config_blobs=config_blobs,
        )
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
        # bereits im backup_dir).
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
                S3Service.delete_object(b.s3_key)
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


def _maybe_upload_to_s3(
    backup, db: Session, local_path: str, timestamp: str
) -> None:
    """Verschluesselt das Panel-Backup und laedt es zu S3 hoch (Best-Effort).

    Bei Fehlern (S3/DIS) wird s3_key=null belassen und nur gewarnt.
    Backup-Key wird immer invalidiert (try/finally).
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
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)

        # S3-Object-Key: msm-backups/panel/panel_{timestamp}_{id}.enc
        s3_key = f"{_S3_KEY_PREFIX}/panel_{timestamp}_{backup.id}.enc"

        # Stream: lokale Datei -> DIS encrypt-stream -> S3 upload_stream.
        # Keine temp verschluesselte Datei (reines Streaming).
        from services.s3_service import S3Service
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


# ── Verzeichnis-Helper ───────────────────────────────────────────────────


def _get_config_dir() -> str:
    """Gibt das Config-Verzeichnis zurueck (Production: /opt/msm)."""
    return settings.panel_config_dir


def _get_backup_dir() -> str:
    """Gibt das Panel-Backup-Verzeichnis zurueck (Production: /opt/msm/backups/panel)."""
    return settings.panel_backup_dir
