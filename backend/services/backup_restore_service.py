"""Ein Backup einspielen — die eine Implementierung.

Herausgeloest aus `routers/backups.py`, wo die vollstaendige Orchestrierung im
Endpunkt stand: rund dreihundert Zeilen S3-Download, Entschluesselung,
Lifecycle-Sperre, Container-Stop, sicheres Entpacken, Postgres-Restore und
Rollback. Dasselbe Muster wie bei
`services/server_deletion_service.py` — der Router ist nur noch ihr HTTP-Rand.

Der Anlass ist nicht Ordnungsliebe. Die KI bekommt ein Werkzeug zum Einspielen,
und die Vorgabe des Betreibers lautet: **kein eigener Pfad.** Waere die Logik im
Router geblieben, haette das KI-Werkzeug sie nachbauen muessen — und ein
Nachbau, der die Reihenfolge nicht kennt, waere gefaehrlich. Die Reihenfolge
hier ist naemlich Absicht:

**S3-Download und Entschluesselung laufen VOR dem Container-Stop.** Scheitert
das Passwort oder ist S3 nicht erreichbar, laeuft der Server unveraendert
weiter. Wer zuerst stoppt und dann herunterlaedt, hat im Fehlerfall einen
gestoppten Server und kein Backup.

Die Funktion wirft `HTTPException` — sie ist die Schnittstelle des Panels, und
die Statuscodes (400 falsches Passwort, 409 Server belegt, 502 Cloud nicht
verfuegbar) sind Teil des Vertrags mit der Oberflaeche. Der KI-Pfad uebersetzt
sie in einen Fehlercode des Vorschlags.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
import shutil
import tarfile

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Backup, Server, User
from services.actor_context import ActorContext
from services.permission_service import has_server_permission


logger = logging.getLogger(__name__)


def _safe_extract_backup_tar(archive_path: str, destination: str) -> None:
    """Extract a backup tar without allowing paths or links to escape install_dir."""
    dest = os.path.abspath(destination)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = member.name
            if not name or "\x00" in name or os.path.isabs(name):
                raise ValueError("Unsicheres Backup-Archiv")
            target = os.path.abspath(os.path.join(dest, name))
            if os.path.commonpath([dest, target]) != dest:
                raise ValueError("Unsicheres Backup-Archiv")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError("Unsicheres Backup-Archiv")
        archive.extractall(dest, members=members, filter="data")


def restore_server_backup(
    db: Session, *, server_id: int, backup_id: int, actor: ActorContext
) -> dict:
    """Stellt ein Backup wieder her (von lokal oder S3).

    Stoppt den Docker-Container VOR dem Extrahieren — sonst greift der laufende
    Server-Prozess auf Dateien zu, die wir gerade ersetzen, und das install_dir
    kann nicht atomar ersetzt werden. Container wird NICHT automatisch wieder
    gestartet; das übernimmt der Nutzer (UI bietet Start-Button).

    Restore-Quellen (Prioritaet):
    1. Lokale Datei existiert → bestehende Restore-Logik (unveraendert).
       Wenn .enc: zuerst DIS-Entschluesselung zu temp tar.gz (vor Container-Stop).
    2. Lokale Datei fehlt, s3_key vorhanden → S3-Download (+ ggf. DIS-Decrypt
       fuer legacy .tar.gz) lokal speichern, dann wie 1.
       Download/Decrypt erfolgt VOR dem Container-Stop, damit bei Fehlern
       (S3 unreachable, falsches Passwort) der Server unberührt bleibt.
    3. Weder lokal noch S3 → 404.

    Verwendet denselben Lifecycle-Lock wie Start/Stop/Restart (non-blocking:
    concurrent Restore → 409). Der DIS-Backup-Key wird immer invalidiert
    (try/finally in fetch_backup_from_s3 / decrypt_local_backup_for_restore).
    """
    principal = (
        db.query(User)
        .filter(User.id == actor.user.id, User.is_active.is_(True))
        .first()
    )
    if principal is None or not has_server_permission(
        db, principal, server_id, "server.backups.restore"
    ):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    server = db.query(Server).filter(Server.id == server_id).first()
    backup = db.query(Backup).filter(Backup.id == backup_id, Backup.server_id == server_id).first()
    if not server or not backup:
        raise HTTPException(status_code=404, detail="Server oder Backup nicht gefunden")

    local_exists = os.path.exists(backup.filename)
    if not local_exists and not backup.s3_key:
        # Weder lokale Datei noch S3-Backup → 404 (kein State-Change).
        raise HTTPException(status_code=404, detail="Backup-Datei nicht gefunden")

    # Phase 6: remote node + S3 → agent-direct restore (no panel data plane)
    node = getattr(server, "node", None)
    is_remote = bool(node is not None and not getattr(node, "is_local", False))
    use_agent_s3_restore = is_remote and bool(backup.s3_key)

    from services.server_lifecycle_service import get_server_lifecycle_lock, guardian_recovery_suspension_lease

    lock = get_server_lifecycle_lock(server.id)
    # Non-blocking acquire: concurrent Restore / Lifecycle-Op → 409.
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Server ist belegt — eine andere Operation läuft")

    # tar_path: Pfad zum tar.gz das extrahiert wird.
    # Bei .enc Backups: temp tar.gz nach DIS-Entschluesselung.
    # Bei .tar.gz Backups: backup.filename direkt.
    # decrypt_tmp_dir: muss am Ende aufgeraeumt werden (nur bei .enc Pfad).
    tar_path: str = backup.filename
    decrypt_tmp_dir: str | None = None

    try:
        with guardian_recovery_suspension_lease(db, server, "lifecycle-restore"):
            db.refresh(server)

            if use_agent_s3_restore:
                from games.base import container_name_for
                from services import docker_service
                from services.backup_orchestrator import restore_via_agent_s3
                from services.backup_crypto_service import BackupDecryptionError, BackupCryptoError
                from services.backup_service import set_active_backup_status, clear_active_backup_status
                from services.node_client import NodeClientError
                from services.s3_service import S3NotConfiguredError, S3OperationError

                # Stop container on the remote node before agent extracts
                container = container_name_for(server.id)
                try:
                    if docker_service.is_running(container, node=node):
                        docker_service.stop(container, timeout=30, node=node)
                    docker_service.remove(container, force=True, node=node)
                except Exception:
                    logger.warning(
                        "Container-Stop vor Agent-Restore (Server %s) fehlgeschlagen — fortsetzen",
                        server_id,
                    )

                set_active_backup_status(server_id, "restoring", backup.size_mb)
                try:
                    restore_via_agent_s3(server, backup, db)
                except BackupDecryptionError:
                    raise HTTPException(
                        status_code=400,
                        detail="Entschlüsselung fehlgeschlagen: falsches Passwort oder manipuliertes Backup",
                    )
                except (S3NotConfiguredError, S3OperationError, NodeClientError, BackupCryptoError):
                    raise HTTPException(status_code=502, detail="Cloud-Backup nicht verfügbar")
                except Exception:
                    logger.warning(
                        "Agent-S3-Restore fehlgeschlagen (Server %s, Backup %s)",
                        server_id, backup_id,
                    )
                    raise HTTPException(status_code=500, detail="Wiederherstellung fehlgeschlagen")
                finally:
                    clear_active_backup_status(server_id)

                server.status = "stopped"
                server.status_message = "Wiederhergestellt (Remote-Node)"
                db.commit()
                return {"message": "Backup wiederhergestellt", "server_id": server_id, "backup_id": backup_id}

            # S3-Restore: Download (+ ggf. Decrypt) VOR Container-Stop.
            # Bei Fehlern bleibt install_dir unveraendert und der Container laeuft weiter.
            if not local_exists:
                from services.backup_orchestrator import fetch_backup_from_s3
                from services.s3_service import S3NotConfiguredError, S3OperationError
                from services.backup_crypto_service import BackupDecryptionError, BackupCryptoError
                try:
                    fetch_backup_from_s3(backup, db)
                except BackupDecryptionError:
                    # Falsches Passwort oder manipulierter Stream — klare User-Meldung.
                    raise HTTPException(
                        status_code=400,
                        detail="Entschlüsselung fehlgeschlagen: falsches Passwort oder manipuliertes Backup",
                    )
                except (S3NotConfiguredError, S3OperationError):
                    # S3 nicht erreichbar / Objekt fehlt — klarer Fehler.
                    raise HTTPException(
                        status_code=502,
                        detail="Cloud-Backup nicht verfügbar",
                    )
                except BackupCryptoError:
                    # DIS nicht erreichbar oder anderer DIS-Fehler.
                    raise HTTPException(
                        status_code=502,
                        detail="Cloud-Backup nicht verfügbar",
                    )
                except Exception:
                    logger.warning(
                        "S3-Restore fehlgeschlagen (Server %s, Backup %s)",
                        server_id, backup_id,
                    )
                    raise HTTPException(status_code=500, detail="Wiederherstellung fehlgeschlagen")

            # Lokale .enc-Entschluesselung VOR Container-Stop (VAL-FIX-004).
            # Bei falschem Passwort / DIS-Fehler bleibt der Server unberuehrt.
            if backup.filename.endswith(".enc"):
                from services.backup_orchestrator import decrypt_local_backup_for_restore
                from services.backup_crypto_service import BackupDecryptionError, BackupCryptoError
                try:
                    tar_path = decrypt_local_backup_for_restore(backup.filename)
                    decrypt_tmp_dir = os.path.dirname(tar_path)
                except BackupDecryptionError:
                    raise HTTPException(
                        status_code=400,
                        detail="Entschlüsselung fehlgeschlagen: falsches Passwort oder manipuliertes Backup",
                    )
                except BackupCryptoError:
                    raise HTTPException(
                        status_code=502,
                        detail="Verschlüsselungs-Service nicht verfügbar",
                    )
                except Exception:
                    logger.warning(
                        "Lokale .enc-Entschluesselung fehlgeschlagen (Server %s, Backup %s)",
                        server_id, backup_id,
                    )
                    raise HTTPException(status_code=500, detail="Wiederherstellung fehlgeschlagen")

            # Container stoppen, falls er läuft — Bind-Mount-Konsistenz
            from games.base import container_name_for
            from services import docker_service
            container = container_name_for(server.id)
            if docker_service.is_running(container, node=node):
                docker_service.stop(container, timeout=30, node=node)
            # Force-Remove, damit das install_dir nicht von einem (gestoppten) Container
            # beansprucht bleibt und der Container beim nächsten Start frisch kommt
            remove_result = docker_service.remove(container, force=True, node=node)
            if not remove_result.get("ok"):
                raise HTTPException(status_code=503, detail="Container konnte vor Restore nicht entfernt werden")

            # Live-Status für Restore (Estimate = Größe des zu restore-nden Backups)
            from services.backup_service import set_active_backup_status, clear_active_backup_status
            set_active_backup_status(server_id, "restoring", backup.size_mb)

            old_backup: str | None = None
            remote_restore_pending = False
            try:
                from services.backup_paths import read_backup_scope_from_archive

                scope, _manifest = read_backup_scope_from_archive(tar_path)
                if is_remote:
                    from services.node_client import NodeClient

                    NodeClient.from_node(node).files_restore_archive(server.id, tar_path)
                    remote_restore_pending = True
                elif scope == "selective":
                    os.makedirs(server.install_dir, exist_ok=True)
                    _safe_extract_backup_tar(tar_path, server.install_dir)
                else:
                    if os.path.exists(server.install_dir):
                        old_backup = f"{server.install_dir}_pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                        shutil.move(server.install_dir, old_backup)
                    os.makedirs(server.install_dir, exist_ok=True)
                    _safe_extract_backup_tar(tar_path, server.install_dir)
            except Exception:
                # Best-effort Rollback: Der Server bleibt danach stopped/error statt
                # mit halb extrahierten Dateien als running markiert zu werden.
                if old_backup and os.path.exists(old_backup):
                    try:
                        if os.path.exists(server.install_dir):
                            shutil.rmtree(server.install_dir)
                        shutil.move(old_backup, server.install_dir)
                    except OSError:
                        pass
                server.status = "error"
                server.status_message = "Wiederherstellung fehlgeschlagen"
                db.commit()
                clear_active_backup_status(server_id)
                raise HTTPException(status_code=500, detail="Wiederherstellung fehlgeschlagen")
            finally:
                clear_active_backup_status(server_id)

            # Postgres-Restore (v1.4.4 / M5-Fix): wenn das Backup Postgres-Dumps
            # enthaelt (.msm/postgres/<db_name>.sql pro DB oder Legacy .msm/postgres.sql),
            # wird jeder Dump in seine zugehoerige DB eingespielt.
            # VAL-FIX-008: DB-Restore-Fehler werden an die API gemeldet (nicht nur
            # geloggt) — der Server wird NICHT als erfolgreich restored markiert.
            # VAL-FIX-009: Jeder Dump wird nur in seine zugehoerige DB restored.
            try:
                from services.backup_paths import read_pg_dump_from_archive

                pg_dumps = read_pg_dump_from_archive(tar_path)
                if pg_dumps:
                    from services import postgres_service as _pg

                    result = _pg.restore_pg_dump_from_archive(db, server.id, pg_dumps)
                    if result.get("ok") and not result.get("skipped"):
                        logger.info(
                            "Postgres-Restore fuer Server %s: %s DBs in %sms",
                            server.id,
                            len(result.get("databases", [])),
                            result.get("duration_ms"),
                        )
                        if is_remote:
                            try:
                                from services.node_client import NodeClient

                                NodeClient.from_node(node).files_delete(server.id, ".msm/postgres")
                            except Exception:
                                logger.warning("Remote Postgres-Dump-Cleanup fuer Server %s fehlgeschlagen", server.id)
                    elif result.get("skipped"):
                        logger.debug(
                            "Postgres-Restore skipped: %s",
                            result.get("reason", "unbekannt"),
                        )
            except Exception as exc:
                # VAL-FIX-008: DB-Restore-Fehler blockiert den erfolgreichen
                # Restore-Status. Der Server wird als error markiert, und der
                # API-Fehler wird an den User gemeldet (kein stillschweigendes
                # "stopped" mehr).
                logger.warning(
                    "Postgres-Restore fuer Server %s fehlgeschlagen: %s",
                    server.id, exc,
                )
                if remote_restore_pending:
                    try:
                        from services.node_client import NodeClient

                        NodeClient.from_node(node).files_rollback_restore(server.id)
                    except Exception:
                        logger.error("Remote Datei-Rollback fuer Server %s fehlgeschlagen", server.id)
                elif old_backup and os.path.exists(old_backup):
                    try:
                        if os.path.exists(server.install_dir):
                            shutil.rmtree(server.install_dir)
                        shutil.move(old_backup, server.install_dir)
                    except OSError:
                        logger.error("Lokaler Datei-Rollback fuer Server %s fehlgeschlagen", server.id)
                server.status = "error"
                server.status_message = "Datenbank-Wiederherstellung fehlgeschlagen"
                db.commit()
                clear_active_backup_status(server_id)
                raise HTTPException(
                    status_code=500,
                    detail="Wiederherstellung fehlgeschlagen: Datenbank-Restore fehlerhaft",
                )

            if remote_restore_pending:
                from services.node_client import NodeClient

                NodeClient.from_node(node).files_finalize_restore(server.id)
            elif old_backup and os.path.exists(old_backup):
                shutil.rmtree(old_backup, ignore_errors=True)

            # Status zuruecksetzen -- Server ist jetzt installiert/stopped, nicht running
            server.status = "stopped"
            server.status_message = None
            db.commit()
    finally:
        # Lock IMMER freigeben (Erfolg, Fehler, HTTPException) — kein Deadlock.
        lock.release()
        # Temp-dir der .enc-Entschluesselung immer aufraeumen (VAL-FIX-004).
        # Entfernt das temp tar.gz (Plaintext liegt nur temporaer vor).
        if decrypt_tmp_dir:
            shutil.rmtree(decrypt_tmp_dir, ignore_errors=True)

    return {"message": "Backup wiederhergestellt"}
