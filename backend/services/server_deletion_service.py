"""Gemeinsame, autorisierte Server-Loeschung.

Zielpunkt 10 des v4-Plans verlangt einen einzigen Weg pro Fachoperation. Das
Loeschen lag bisher komplett im Router und war damit fuer die Hoster-Anbindung
nicht erreichbar. Diese Schicht ist die eine Implementierung; der Router ist nur
noch ihr HTTP-Rand.

Reihenfolge
-----------
Die pruefbaren, umkehrbaren Schritte laufen zuerst, die unwiderruflichen zuletzt.
Vorher wurden Container, Firewallregeln, Serverdateien, Backups und Logs
geloescht, bevor die PostgreSQL-Bereinigung ueberhaupt versucht wurde — schlug
sie fehl, blieb ein Server im Panel sichtbar, dessen Daten bereits vernichtet
waren.

Fehlerbilder
------------
Nach aussen gehen ausschliesslich stabile Fehlercodes. Rohe `OSError`-Texte
enthalten absolute Hostpfade und Agent-Fehlermeldungen enthalten Datenbank- und
Rollennamen; beides gehoert nicht in eine API-Antwort.
"""

from __future__ import annotations

import logging
import os
import shutil

from fastapi import HTTPException
from sqlalchemy.orm import Session

from games.base import container_name_for
from models import Server, User
from services import audit_service, docker_service, permission_service, postgres_service
from services.actor_context import ActorContext
from services.firewall_service import close_ports
from services.docker_iptables_service import revoke_server as iptables_revoke_server


logger = logging.getLogger(__name__)


def _fail(code: str, status_code: int = 500) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": f"errors.{code}"},
    )


def _console_log_dir(server_id: int) -> str:
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs",
        str(server_id),
    )


def delete_server_completely(db: Session, *, server_id: int, actor: ActorContext) -> dict:
    """Loescht einen Server samt Dateien, Backups und verwalteten Ressourcen.

    Die Berechtigung wird hier erneut geprueft — nicht nur im Router. Damit
    koennen Hoster-Anbindung, Panel und spaetere Automationen denselben Aufruf
    verwenden, ohne dass eine davon die Rechtepruefung umgehen kann.
    """
    principal = (
        db.query(User)
        .filter(User.id == actor.user.id, User.is_active.is_(True))
        .first()
    )
    if principal is None or not permission_service.has_global_permission(
        db, principal, "servers.delete"
    ):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")

    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    node = getattr(server, "node", None)
    install_dir = server.install_dir
    backup_dir = f"/opt/msm/backups/{server.id}"
    is_local = node is None or node.is_local

    # ── 1. Fallible, aber noch umkehrbare Vorbereitung ────────────────────
    # PostgreSQL zuerst: schlaegt der Agent fehl, ist noch nichts vernichtet
    # und der Aufrufer kann es gefahrlos erneut versuchen.
    try:
        postgres_service.drop_server_resources(db, server.id)
    except Exception as exc:
        logger.warning(
            "PostgreSQL-Bereinigung vor Server-Delete fehlgeschlagen (server_id=%s, error=%s)",
            server.id,
            type(exc).__name__,
        )
        raise _fail("server_postgres_cleanup_failed", status_code=503) from exc

    # ── 2. Container entfernen (idempotent, force killt laufende) ─────────
    container = container_name_for(server.id)
    remove_result = docker_service.remove(container, force=True, node=node)
    if not remove_result.get("ok"):
        raise _fail("server_container_remove_failed", status_code=503)

    # ── 3. Firewall- und iptables-Regeln schliessen ───────────────────────
    ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
    close_ports(ports_list, node=node, name=server.name)
    if is_local:
        iptables_revoke_server(server.name, server.public_bind_ip or "", ports_list)

    # ── 4. S3-Objekte vor dem Cascade loeschen ────────────────────────────
    # Nach `db.delete(server)` sind die Backup-Records und damit die S3-Keys
    # weg; die Objekte wuerden sonst dauerhaft verwaisen. Best effort.
    for backup in server.backups:
        if backup.s3_key:
            try:
                from services.s3_service import S3Service

                S3Service.delete_object(backup.s3_key, bucket=backup.s3_bucket)
            except Exception as exc:
                logger.warning(
                    "S3-Delete fehlgeschlagen (Backup %s): %s", backup.id, type(exc).__name__
                )

    # ── 5. Unwiderrufliche Dateiloeschung ─────────────────────────────────
    dir_removed = False
    if not is_local:
        try:
            from services.node_client import NodeClient

            NodeClient.from_node(node).files_delete_server_root(server.id)
            dir_removed = True
        except Exception as exc:
            logger.warning(
                "Server-Verzeichnis konnte auf dem Node nicht geloescht werden "
                "(server_id=%s, error=%s)",
                server.id,
                type(exc).__name__,
            )
            raise _fail("server_directory_delete_failed", status_code=503) from exc
    elif install_dir and os.path.exists(install_dir):
        repair = docker_service.repair_bind_mount_permissions(install_dir)
        if not repair.get("ok"):
            logger.warning(
                "Install-Verzeichnis-Rechte konnten vor Delete nicht normalisiert werden: %s",
                repair.get("error") or "unbekannter Fehler",
            )
        try:
            shutil.rmtree(install_dir)
            dir_removed = True
        except OSError as exc:
            logger.warning(
                "Install-Verzeichnis konnte nicht geloescht werden (server_id=%s, error=%s)",
                server.id,
                type(exc).__name__,
            )
            raise _fail("server_directory_delete_failed") from exc

    backups_removed = False
    if is_local and os.path.exists(backup_dir):
        try:
            shutil.rmtree(backup_dir)
            backups_removed = True
        except OSError as exc:
            logger.warning(
                "Backup-Verzeichnis konnte nicht geloescht werden (server_id=%s, error=%s)",
                server.id,
                type(exc).__name__,
            )
            raise _fail("server_backup_directory_delete_failed") from exc

    if is_local:
        console_log_dir = _console_log_dir(server.id)
        if os.path.exists(console_log_dir):
            try:
                shutil.rmtree(console_log_dir)
            except OSError as exc:
                logger.warning(
                    "Console-Log-Verzeichnis konnte nicht geloescht werden "
                    "(server_id=%s, error=%s)",
                    server.id,
                    type(exc).__name__,
                )
                raise _fail("server_console_log_delete_failed") from exc

    # ── 6. Datenbankzeile entfernen (Cascade raeumt Rechte, Mods, Backups) ─
    audit_service.record_privileged_action(
        db,
        user_id=principal.id,
        action="server.deleted",
        target_type="server",
        target_id=server.id,
        details={"game_type": server.game_type},
        origin=actor.origin,
        correlation_id=actor.correlation_id,
    )
    db.delete(server)
    db.commit()

    from services.scheduler_service import remove_restart_jobs

    remove_restart_jobs(server_id)
    return {
        "message": "Server gelöscht",
        "cleanup": {
            "container_removed": container,
            "dir_removed": install_dir if dir_removed else None,
            "backups_removed": backup_dir if backups_removed else None,
        },
    }
