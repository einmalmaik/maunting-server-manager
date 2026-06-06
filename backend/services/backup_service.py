"""
Zentrale Backup-Service fuer MSM.

Single Source of Truth fuer alle Backup-Operationen (manuell, Auto-Start, Scheduler).
Fuehrt tar.gz des kompletten install_dir aus, schreibt DB-Record und fuehrt sofort
Retention-Cleanup aus.

Backup-Pipeline (Schritt 7, Cloud-Redesign)
-------------------------------------------
1. Tar.gz erstellen (in /var/tmp/msm-backup-tmp/, **nicht** mehr direkt im
   Backup-Provider-Namespace) — verhindert dass ein halb geschriebener Tar
   schon im Provider sichtbar ist.
2. Optional: Client-seitige AES-256-GCM Verschluesselung (Schluessel aus
   ``MSM_BACKUP_ENCRYPTION_KEY``). Provider sehen nur Chiffretext.
3. Provider-Stage:
   - ``provider == "local"``: Kopie nach ``/opt/msm/backups/{server_id}/``.
     Datei bleibt auf der Platte (heutiges Verhalten fuer local).
   - cloud-Provider (``s3``/``sftp``/``dropbox``/``gcs``/``azure``):
     ``provider.upload(local_path, remote_key, progress_cb=...)``. Live-
     Progress wird in ``_active_backups`` durchgereicht.
4. DB-Record mit ``provider``/``remote_key``/``metadata_json``.
5. Cleanup der Temp-Files in /var/tmp.
6. ``cleanup_old_backups`` (provider-aware) laeuft mit ``keep=retention``.

Timeouts konfigurierbar:
- Manuell: default 600s (große Welten)
- Scheduler: 300s (nicht zu lange blocken)

KISS: keine neuen Abstraktionen, einfache subprocess + DB, keine partial-State-Leaks.
Deutsche Kommentare passend zum Projekt-Stil.
"""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Live-Status Tracking fuer Backup/Restore (KISS: module dict, kein Redis, kein neues Model).
# Note on concurrency (Issue 2 defense): unsynchronized; races possible on same server_id across threads (uvicorn + APScheduler).
# Acceptable for ephemeral UX banner only (last-writer wins, resets on process restart). Adding Lock would violate KISS/no-new-complexity (see AGENTS, architecture.md "no global state without compelling reason").
_active_backups: dict[int, dict] = {}

# Pfad fuer Tar-Temp-Dateien. Wird beim Backup-Start einmal erstellt und am
# Ende des Service wieder aufgeraeumt. NICHT der finale Backup-Speicherort
# (fuer local: /opt/msm/backups/{id}/, fuer cloud: der Provider).
TEMP_BACKUP_DIR = "/var/tmp/msm-backup-tmp"


def run_backup(
    server_id: int,
    db: Session,
    *,
    name: str | None = None,
    timeout_seconds: int = 600,
) -> "Backup":
    """
    Fuehrt ein vollstaendiges Backup aus + DB-Record + sofortigen Retention-Cleanup.

    Provider-Stage (Schritt 7) ist additiv: bei ``provider == "local"`` exakt
    das alte Verhalten (Datei in ``/opt/msm/backups/{id}/``). Bei cloud-Providern
    wird der tar.gz (ggf. verschluesselt) via ``provider.upload`` hochgeladen
    und das lokale Temp-File geloescht.

    Gibt den neuen Backup-Record zurueck.
    Wirft bei Fehlern (kein Server, kein install_dir, tar-Fehler/Timeout,
    Provider-Fehler, Encryption-Fehler) → Caller behandelt (z. B. HTTP 4xx/5xx
    oder Warning-Log fuer Auto).

    Garantiert: Bei Tar-Fehler wird keine DB-Record angelegt und keine
    partiellen Dateien im Backup-Verzeichnis hinterlassen. Bei Provider-Fehler
    wird die DB-Record nicht angelegt und die Temp-Datei aufgeraeumt.
    """
    from models import Backup, Server  # Inline-Import gegen Zyklen (wie in scheduler_service)

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise ValueError(f"Server {server_id} nicht gefunden")

    if not os.path.isdir(server.install_dir):
        # Generische Nachricht (kein Leak von install_dir in Exception-String / HTTP-Details)
        raise FileNotFoundError("Server-Verzeichnis existiert nicht. Ist der Server installiert?")

    # Live-Status + Estimate vom letzten Backup (fuer UX-Banner)
    last = db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).first()
    est = last.size_mb if last else None

    set_active_backup_status(server_id, "creating", est)

    # Provider + Encryption-Key bestimmen (fuer spaetere Stages)
    provider_name = (settings.backup_provider or "local").lower()
    encryption_key = settings.backup_encryption_key or ""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Nur server_id + Timestamp im Dateinamen — verhindert Path-Traversal ueber server.name
    # (name bleibt im DB-Feld "name" fuer UI-Anzeige erhalten). KISS + Security.
    filename = f"server_{server_id}_{timestamp}.tar.gz"

    # Pfad-Wahl:
    # - local-Provider: Tar landet direkt in /opt/msm/backups/{id}/ (heutiges
    #   Verhalten, kein Copy noetig). Encryption schreibt .enc daneben.
    # - cloud-Provider: Tar landet in /var/tmp/msm-backup-tmp/, dann
    #   Provider-Upload, dann Temp-Cleanup. So sieht der Provider keine
    #   halbgeschriebenen Files.
    if provider_name == "local":
        backup_dir = settings.backup_local_dir or "/opt/msm/backups"
        target_dir = f"{backup_dir}/{server_id}"
        os.makedirs(target_dir, exist_ok=True)
        temp_filepath = os.path.join(target_dir, filename)
        temp_is_local = True
    else:
        os.makedirs(TEMP_BACKUP_DIR, exist_ok=True)
        temp_filepath = os.path.join(TEMP_BACKUP_DIR, filename)
        temp_is_local = False

    # Tar ausfuehren (voller install_dir, .tar.gz, -C . fuer relative Pfade)
    try:
        subprocess.run(
            ["tar", "-czf", temp_filepath, "-C", server.install_dir, "."],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
            env={
                **os.environ,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
        )
    except subprocess.TimeoutExpired as e:
        _safe_remove(temp_filepath)
        logger.error("Backup-Timeout fuer Server %s nach %ss", server_id, timeout_seconds)
        clear_active_backup_status(server_id)
        raise RuntimeError(
            f"Backup fehlgeschlagen (Timeout nach {timeout_seconds}s)"
        ) from e
    except Exception as e:
        _safe_remove(temp_filepath)
        logger.error("Backup fehlgeschlagen fuer Server %s (details redacted for security)", server_id)
        clear_active_backup_status(server_id)
        raise RuntimeError("Backup fehlgeschlagen") from e

    # Tar-Datei existiert jetzt. Encryption + Provider-Stage als eine
    # zusammengehoerige Transaktion: Temp-Files werden am Ende (Erfolg
    # oder Fehler) aufgeraeumt. Bei Fehler in einer der Stages wird
    # KEIN DB-Record angelegt.
    enc_filepath: str | None = None  # gesetzt wenn Encryption aktiv war
    final_filepath: str | None = None  # gesetzt fuer local-Provider (Ziel-Pfad)
    remote_key: str | None = None  # gesetzt fuer cloud-Provider

    try:
        # ── Stage 2: Optional Encryption ──
        upload_source: str = temp_filepath
        if encryption_key:
            # Verschlusseltes File landet im gleichen Verzeichnis wie das tar.gz
            # (bei local: Backup-Dir; bei cloud: Temp-Dir) mit .enc Suffix
            enc_filepath = temp_filepath + ".enc"
            from services.backup_encryption import encrypt_file
            encrypt_file(
                Path(temp_filepath),
                Path(enc_filepath),
                encryption_key,
            )
            upload_source = enc_filepath

        try:
            size_bytes = os.path.getsize(upload_source)
        except OSError:
            size_bytes = 0
        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None

        # ── Stage 3: Provider-Upload ──
        # Bei local ist upload_source bereits im Ziel-Verzeichnis (kein Move noetig).
        # Bei cloud: provider.upload mit Progress-Callback in _active_backups.
        if provider_name == "local":
            # Bei local ist final_filepath = upload_source (Tar landet schon dort).
            # Bei Encryption hat das File .enc Suffix, sonst .tar.gz.
            final_filepath = upload_source
        else:
            # Cloud-Provider: remote_key = <server_id>/<filename>
            target_filename = (
                filename + ".enc" if encryption_key else filename
            )
            remote_key = f"{server_id}/{target_filename}"

            # Live-Status auf "uploading" setzen fuer den Provider-Stage
            set_active_backup_status(server_id, "uploading", est)

            from services.backup_provider import get_provider
            provider = get_provider(provider_name)

            # Progress-Callback: schreibt in _active_backups[server_id]
            def _progress_to_active(bytes_done: int) -> None:
                _active_backups[server_id] = {
                    **_active_backups.get(server_id, {}),
                    "operation": "uploading",
                    "bytes_done": bytes_done,
                    "bytes_total": size_bytes,
                    "percent": (
                        int(bytes_done * 100 / size_bytes)
                        if size_bytes
                        else None
                    ),
                    "started_at": _active_backups.get(
                        server_id, {}
                    ).get("started_at"),
                }

            try:
                provider.upload(
                    Path(upload_source),
                    remote_key,
                    progress_cb=_progress_to_active,
                )
            except Exception as e:
                # Provider-Fehler: KEIN DB-Record, Temp-Files werden im finally
                # aufgeraeumt. remote_key nicht in DB, also kein Cleanup noetig.
                logger.warning(
                    "Provider-Upload fehlgeschlagen fuer Server %s (provider=%s, details redacted)",
                    server_id,
                    provider_name,
                )
                raise RuntimeError("Backup fehlgeschlagen") from e

        # ── Stage 4: Metadata-Snapshot (fuer Restore) ──
        # BackupMetadata enthaelt nur public fields (server_name, game_type,
        # limits, ports, panel_version). Sensitive Inhalte (Savegames, Configs)
        # bleiben im verschluesselten tar.gz.
        from services.backup_provider import BackupMetadata
        metadata = _build_backup_metadata(server)

        # ── Stage 5: DB-Record + Retention ──
        # Backup.filename: bei local = final_filepath (heutiges Verhalten),
        # bei cloud = remote_key (Pfad im Provider-Namespace, nicht auf der Platte).
        # Wir behalten die Spalte filename fuer Backward-Compat mit allen
        # bestehenden Stellen (Router, Tests, PATCHNOTES) — bei cloud-Records
        # ist der Wert einfach der Provider-Key.
        try:
            backup = Backup(
                server_id=server_id,
                filename=final_filepath or remote_key,
                size_mb=size_mb,
                name=name or None,
                provider=provider_name,
                remote_key=remote_key,
                metadata_json=metadata.to_json(),
            )
            db.add(backup)
            db.commit()
            db.refresh(backup)

            try:
                cleanup_old_backups(server_id, db, keep=server.backup_retention_count)
            except Exception:
                logger.warning(
                    "Retention-Cleanup nach Backup %s (Server %s) fehlgeschlagen",
                    backup.id,
                    server_id,
                )
        except Exception as e:
            # DB-Insert fehlgeschlagen — bei cloud: Provider-Upload war erfolgreich,
            # aber ohne DB-Record ist das File ein Orphan. Best-Effort Cleanup.
            if provider_name != "local" and remote_key:
                try:
                    from services.backup_provider import get_provider
                    get_provider(provider_name).delete(remote_key)
                except Exception as cleanup_err:
                    logger.warning(
                        "Konnte Orphan-Cloud-Backup nicht loeschen (server=%s, key=%s): %s",
                        server_id,
                        remote_key,
                        cleanup_err,
                    )
            if final_filepath and os.path.exists(final_filepath):
                _safe_remove(final_filepath)
            logger.error(
                "Backup DB/Retention fehlgeschlagen fuer Server %s (details redacted for security)",
                server_id,
            )
            clear_active_backup_status(server_id)
            raise RuntimeError("Backup fehlgeschlagen") from e

    finally:
        # Temp-Files IMMER aufraeumen — Erfolg oder Fehler
        # Bei local: NIE loeschen (final_filepath = upload_source = finaler Speicherort).
        # Bei cloud: temp_filepath + enc_filepath (falls existent) loeschen.
        if not temp_is_local:
            _safe_remove(temp_filepath)
            if enc_filepath:
                _safe_remove(enc_filepath)
        elif encryption_key and enc_filepath:
            # Bei local + Encryption: enc_filepath liegt im Backup-Dir
            # (verschluesselte Version) — das IST das finale File, also
            # nicht loeschen. Aber der Klartext-temp_filepath im gleichen
            # Dir sollte weg.
            if os.path.exists(temp_filepath) and temp_filepath != enc_filepath:
                _safe_remove(temp_filepath)

    # Nur nicht-sensible IDs + Metadaten loggen (kein full filepath / server.name im INFO-Log)
    logger.debug("Backup DB record created id=%s server=%s", backup.id, server_id)
    clear_active_backup_status(server_id)
    return backup


def cleanup_old_backups(
    server_id: int, db: Session, *, keep: int | None = None
) -> None:
    """
    Loescht alte Backups ueber dem Retention-Limit.

    Provider-aware (Schritt 7):
    - ``provider == "local"``: ``os.remove(b.filename)`` (heutiges Verhalten).
    - cloud-Provider: ``provider.delete(remote_key)`` (Daten + Metadata in
      einem Call — Provider kuemmert sich um den Meta-File-Anhang).
    - Records ohne ``remote_key`` (sehr alte Records vor Cloud-Enable) fallen
      auf das alte Verhalten zurueck.

    Wenn keep=None → wird aus Server.backup_retention_count gelesen (Default 5).
    Commitet am Ende.
    """
    from models import Backup, Server  # Inline-Import

    if keep is None:
        server = db.query(Server).filter(Server.id == server_id).first()
        keep = server.backup_retention_count if server else 5

    # Aelteste zuerst loeschen (offset nach sort desc)
    old = (
        db.query(Backup)
        .filter(Backup.server_id == server_id)
        .order_by(Backup.created_at.desc())
        .offset(keep)
        .all()
    )
    for b in old:
        provider_name = (b.provider or "local").lower()
        if provider_name == "local" or not b.remote_key:
            # Local-Provider oder sehr alte Records ohne remote_key: lokales File loeschen
            if b.filename and os.path.exists(b.filename):
                try:
                    os.remove(b.filename)
                except OSError as e:
                    logger.warning(
                        "Konnte Backup-Datei fuer Server %s (id=%s) nicht loeschen: %s",
                        server_id,
                        b.id,
                        e,
                    )
        else:
            # Cloud-Provider: provider.delete loescht Daten + Metadata in einem Call
            try:
                from services.backup_provider import get_provider
                provider = get_provider(provider_name)
                provider.delete(b.remote_key)
            except Exception as e:
                # Provider-Fehler: loggen aber Record trotzdem loeschen, damit
                # der Retention-Cleanup nicht haengenbleibt. Backup-File im
                # Provider bleibt als Orphan, manuelle Cleanup noetig (extrem
                # seltener Edge-Case — Cloud-Creds wechseln etc.).
                logger.warning(
                    "Konnte Cloud-Backup nicht loeschen (server=%s, id=%s, provider=%s): %s",
                    server_id,
                    b.id,
                    provider_name,
                    e,
                )
        db.delete(b)

    if old:
        db.commit()
        logger.info(
            "Alte Backups aufgeraeumt fuer Server %s (behalten: %s, geloescht: %s)",
            server_id,
            keep,
            len(old),
        )


def set_active_backup_status(
    server_id: int, operation: str, estimated_size_mb: int | None = None
) -> None:
    """Setzt Live-Status (aufgerufen von run_backup und restore)."""
    _active_backups[server_id] = {
        "operation": operation,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "estimated_size_mb": estimated_size_mb,
    }


def clear_active_backup_status(server_id: int) -> None:
    """Entfernt Live-Status (auch bei Fehlern)."""
    _active_backups.pop(server_id, None)


def get_active_backup_status(server_id: int) -> dict | None:
    """Liefert Snapshot oder None."""
    return _active_backups.get(server_id)


# ── Helpers (privat) ──────────────────────────────────────────────────────


def _safe_remove(path: str) -> None:
    """Entfernt eine Datei wenn existent; schluckt OSError (Best-Effort)."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _build_backup_metadata(server) -> "BackupMetadata":
    """Baut BackupMetadata-Snapshot aus dem aktuellen Server-Stand.

    Nur public fields (server_name, game_type, limits, ports, panel_version).
    Sensitive Inhalte (Savegames, Configs) bleiben im verschluesselten tar.gz.

    Ports werden ueber die server.ports-Relationship gelesen (kein eigener
    DB-Roundtrip noetig). Format: ``[{"role": "game", "port": 25565,
    "protocol": "udp"}, ...]``.
    """
    from services.backup_provider import BackupMetadata

    # Ports: ueber die server-Relationship (ServerPort-Records)
    ports: list[dict] = []
    try:
        for p in (server.ports or []):
            ports.append(
                {
                    "role": getattr(p, "role", None),
                    "port": getattr(p, "port", None),
                    "protocol": getattr(p, "protocol", None),
                }
            )
    except Exception:
        # server.ports nicht geladen oder kaputt — leerer Ports-List
        # (Restore faellt dann auf Default-Ports zurueck).
        pass

    # Panel-Version: aus /opt/msm/VERSION (vom Installer geschrieben).
    # Optional — wenn File nicht da, leerer String.
    panel_version = ""
    version_file = "/opt/msm/VERSION"
    try:
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                panel_version = f.read().strip()
    except OSError:
        pass

    return BackupMetadata(
        backup_version=1,
        server_id=server.id,
        server_name=server.name or "",
        game_type=server.game_type or "",
        created_at=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        panel_version=panel_version,
        cpu_limit_percent=server.cpu_limit_percent,
        ram_limit_mb=server.ram_limit_mb,
        disk_limit_gb=server.disk_limit_gb,
        public_bind_ip=server.public_bind_ip,
        ports=ports,
        name=None,
        size_mb=None,
    )


# Inline-Import am Ende, um zirkulaere Imports zu vermeiden
from config import settings  # noqa: E402
