"""
Zentrale Backup-Service für MSM.

Single Source of Truth für alle Backup-Operationen (manuell, Auto-Start, Scheduler).
Führt tar.gz des kompletten install_dir aus, schreibt DB-Record und führt sofort
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

logger = logging.getLogger(__name__)


def run_backup(
    server_id: int,
    db: Session,
    *,
    name: str | None = None,
    timeout_seconds: int = 600,
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

    backup_dir = f"/opt/msm/backups/{server_id}"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Nur server_id + Timestamp im Dateinamen — verhindert Path-Traversal über server.name
    # (name bleibt im DB-Feld "name" für UI-Anzeige erhalten). KISS + Security.
    filename = f"server_{server_id}_{timestamp}.tar.gz"
    filepath = os.path.join(backup_dir, filename)

    # Tar ausführen (voller install_dir, .tar.gz, -C . für relative Pfade)
    tar_ok = False
    try:
        subprocess.run(
            ["tar", "-czf", filepath, "-C", server.install_dir, "."],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
            env={
                **os.environ,
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
        )
        tar_ok = True
        size_mb = os.path.getsize(filepath) // (1024 * 1024)
    except subprocess.TimeoutExpired as e:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error(
            "Backup-Timeout für Server %s nach %ss: %s",
            server_id,
            timeout_seconds,
            e,
        )
        raise RuntimeError(
            f"Backup fehlgeschlagen (Timeout nach {timeout_seconds}s)"
        ) from e
    except Exception as e:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error("Backup fehlgeschlagen für Server %s: %s", server_id, e)
        raise RuntimeError(f"Backup fehlgeschlagen: {e}") from e

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
        except Exception as e:
            logger.warning(
                "Retention-Cleanup nach Backup %s (Server %s) fehlgeschlagen: %s",
                backup.id,
                server_id,
                e,
            )
    except Exception as e:
        # Post-tar DB/Retention Fehler → Tar entfernen, um Orphan zu vermeiden
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass
        logger.error("Backup DB/Retention fehlgeschlagen für Server %s (Tar bereinigt): %s", server_id, e)
        raise RuntimeError(f"Backup fehlgeschlagen: {e}") from e

    # Nur nicht-sensible IDs + Metadaten loggen (kein full filepath / server.name im INFO-Log)
    logger.debug("Backup DB record created id=%s server=%s", backup.id, server_id)
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
