"""
Zentrale Backup-Service für MSM.

Single Source of Truth für alle Backup-Operationen (manuell, Auto-Start, Scheduler).
Führt tar.gz des install_dir oder blueprint-definierter Pfade aus, schreibt DB-Record und führt sofort
Retention-Cleanup aus.

Timeouts konfigurierbar:
- Manuell: default 600s (große Welten)
- Scheduler: 300s (nicht zu lange blocken)

KISS: keine neuen Abstraktionen, einfache subprocess + DB, keine partial-State-Leaks.
Deutsche Kommentare passend zum Projekt-Stil.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from services import postgres_service
from services.backup_paths import (
    BACKUP_MANIFEST_ARCNAME,
    backup_plan_for_server,
    create_full_backup_tar,
    create_selective_backup_tar,
    read_pg_dump_bytes_from_archive,
    read_backup_scope_from_archive,
)

logger = logging.getLogger(__name__)

# Live-Status Tracking für Backup/Restore (KISS: module dict, kein Redis, kein neues Model).
# Note on concurrency (Issue 2 defense): unsynchronized; races possible on same server_id across threads (uvicorn + APScheduler).
# Acceptable for ephemeral UX banner only (last-writer wins, resets on process restart). Adding Lock would violate KISS/no-new-complexity (see AGENTS, architecture.md "no global state without compelling reason").
_active_backups: dict[int, dict] = {}


def run_backup(
    server_id: int,
    db: Session,
    *,
    name: str | None = None,
    timeout_seconds: int = 600,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> "Backup":
    """
    Führt ein vollständiges Backup aus + DB-Record + sofortigen Retention-Cleanup.

    Gibt den neuen Backup-Record zurück.
    Wirft bei Fehlern (kein Server, kein install_dir, tar-Fehler/Timeout) → Caller
    behandelt (z. B. HTTP 4xx/5xx oder Warning-Log für Auto).

    Garantiert: Bei Tar-Fehler wird keine DB-Record angelegt und keine
    partiellen Dateien im Backup-Verzeichnis hinterlassen.
    """
    from models import Backup, Server  # Inline-Import gegen Zyklen (wie in scheduler_service)

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise ValueError(f"Server {server_id} nicht gefunden")

    if not os.path.isdir(server.install_dir):
        # Generische Nachricht (kein Leak von install_dir in Exception-String / HTTP-Details)
        raise FileNotFoundError("Server-Verzeichnis existiert nicht. Ist der Server installiert?")

    # Live-Status + Estimate vom letzten Backup (für UX-Banner)
    from models import Backup  # für Estimate
    last = db.query(Backup).filter(Backup.server_id == server_id).order_by(Backup.created_at.desc()).first()
    est = last.size_mb if last else None

    backup_dir = f"/opt/msm/backups/{server_id}"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Nur server_id + Timestamp im Dateinamen — verhindert Path-Traversal über server.name
    # (name bleibt im DB-Feld "name" für UI-Anzeige erhalten). KISS + Security.
    filename = f"server_{server_id}_{timestamp}.tar.gz"
    filepath = os.path.join(backup_dir, filename)

    # Tar ausfuehren (voller install_dir oder Blueprint backup.includePaths)
    plan = backup_plan_for_server(server)
    # Postgres-Integration: wenn der Server postgres_enabled hat (oder
    # PostgresDatabase-Records vorhanden sind), wird vor dem tar ein pg_dump
    # erzeugt und als ``.msm/postgres.sql`` ins Archiv gepackt. Das ist
    # Stand der v1.4.4 -- ohne Blueprints-Config ist jede Server-DB
    # automatisch im Backup.
    pg_dump_bytes: bytes = b""
    try:
        from models import PostgresDatabase as _PgDb
        has_pg = (
            db.query(_PgDb.id).filter(_PgDb.server_id == server_id).first() is not None
        )
    except Exception:
        has_pg = False
    if has_pg:
        try:
            pg_dump_bytes, _pg_sha, _pg_dbs = (
                postgres_service.backup_pg_dump_for_archive(db, server_id)
            )
        except Exception as exc:
            # Postgres-Fehler darf das ganze Backup NICHT blockieren -- der
            # User hat dann immerhin sein install_dir-Snapshot. Wir loggen
            # laut und fahren ohne pg_dump fort.
            logger.warning(
                "Postgres-Dump fuer Backup Server %s fehlgeschlagen: %s",
                server_id, exc,
            )

    try:
        set_active_backup_status(server_id, "creating", est)
        if plan.scope == "selective":
            # selective: nur Blueprint-Pfade -- nichts am Full-Tar-Aufruf
            # geaendert fuer v1.4.4; pg_dump fuer selective-Server waere
            # verfuegbar, aber wir respektieren das Blueprint-Scope-Konzept.
            # Hinweis: Blueprint-Operatoren koennen spaeter ``excludePgDump``
            # im Blueprint-Manifest ergaenzen, falls noetig.
            create_selective_backup_tar(
                filepath,
                server.install_dir,
                plan.include_paths,
                server_id=server_id,
                encrypted=encrypted,
                encryption_algorithm=encryption_algorithm,
            )
        else:
            create_full_backup_tar(
                filepath,
                server.install_dir,
                pg_dump_bytes=pg_dump_bytes or None,
                server_id=server_id,
                encrypted=encrypted,
                encryption_algorithm=encryption_algorithm,
            )
        size_mb = os.path.getsize(filepath) // (1024 * 1024)
    except subprocess.TimeoutExpired as e:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error("Backup-Timeout für Server %s nach %ss", server_id, timeout_seconds)
        clear_active_backup_status(server_id)
        raise RuntimeError(
            f"Backup fehlgeschlagen (Timeout nach {timeout_seconds}s)"
        ) from e
    except Exception as e:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error("Backup fehlgeschlagen für Server %s (details redacted for security)", server_id)
        clear_active_backup_status(server_id)
        raise RuntimeError("Backup fehlgeschlagen") from e

    # DB + Retention nach erfolgreichem Tar. Bei DB-Fehler: Best-Effort Cleanup der Tar-Datei
    # (verhindert Orphan .tar.gz ohne Record). Kein volles 2PC (KISS, keine neue Komplexität).
    try:
        backup = Backup(
            server_id=server_id,
            filename=filepath,
            size_mb=size_mb,
            name=name or None,
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
        # Post-tar DB/Retention Fehler → Tar entfernen, um Orphan zu vermeiden
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error("Backup DB/Retention fehlgeschlagen für Server %s (Tar bereinigt, details redacted for security)", server_id)
        clear_active_backup_status(server_id)
        raise RuntimeError("Backup fehlgeschlagen") from e

    # Nur nicht-sensible IDs + Metadaten loggen (kein full filepath / server.name im INFO-Log)
    logger.debug("Backup DB record created id=%s server=%s", backup.id, server_id)
    clear_active_backup_status(server_id)
    return backup


def cleanup_old_backups(
    server_id: int, db: Session, *, keep: int | None = None
) -> None:
    """
    Löscht alte Backups über dem Retention-Limit (File + DB-Record).

    Wenn keep=None → wird aus Server.backup_retention_count gelesen (Default 5).
    Commitet am Ende.
    """
    from models import Backup, Server  # Inline-Import

    if keep is None:
        server = db.query(Server).filter(Server.id == server_id).first()
        keep = server.backup_retention_count if server else 5

    # Älteste zuerst löschen (offset nach sort desc)
    old = (
        db.query(Backup)
        .filter(Backup.server_id == server_id)
        .order_by(Backup.created_at.desc())
        .offset(keep)
        .all()
    )
    for b in old:
        # S3-Delete (best-effort, nur wenn s3_key vorhanden).
        # Ein S3-Fehler bricht die Retention nicht ab (Warning-Log, keine Secrets).
        if b.s3_key:
            try:
                from services.s3_service import S3Service
                S3Service.delete_object(b.s3_key)
            except Exception as e:
                logger.warning(
                    "S3-Delete fehlgeschlagen (Backup %s): %s",
                    b.id, type(e).__name__,
                )
        if os.path.exists(b.filename):
            try:
                os.remove(b.filename)
            except OSError as e:
                # Kein voller Pfad im Log (data min)
                logger.warning("Konnte Backup-Datei für Server %s (id=%s) nicht löschen: %s", server_id, b.id, e)
        db.delete(b)

    if old:
        db.commit()
        logger.info(
            "Alte Backups aufgeräumt für Server %s (behalten: %s, gelöscht: %s)",
            server_id,
            keep,
            len(old),
        )


def set_active_backup_status(server_id: int, operation: str, estimated_size_mb: int | None = None) -> None:
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
