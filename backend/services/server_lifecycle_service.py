import asyncio
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from games import get_plugin
from games.base import _append_console_log
from models import Server
from services.docker_iptables_service import accept_server as iptables_accept_server
from services.docker_iptables_service import revoke_server as iptables_revoke_server
from services.firewall_service import close_ports, open_ports
from services.install_update_lock_service import (
    INSTALL_UPDATE_ALREADY_RUNNING,
    release_install_update_lock,
    try_acquire_install_update_lock,
)

logger = logging.getLogger(__name__)

_LIFECYCLE_LOCKS: dict[int, asyncio.Lock] = {}


def get_server_lifecycle_lock(server_id: int) -> asyncio.Lock:
    """Per-Server Lock für ALLE destruktiven Lifecycle-Operationen (start/stop/restart).

    Einheitliche Serialisierung verhindert TOCTOU-Races auf Firewall (UFW close/open)
    und iptables (revoke/accept) sowie Docker-Container-Lifecycle.
    Wird von restart_server_with_updates (manuell + Scheduler) UND start/stop in Routern genutzt.
    KISS: eine Quelle, keine Manager-Klasse, keine neuen Abstraktionen.
    """
    return _LIFECYCLE_LOCKS.setdefault(server_id, asyncio.Lock())


async def restart_server_with_updates(db: Session, server: Server) -> dict:
    """Restartet einen Server über den zentralen Lifecycle-Pfad.

    Der Pfad ist absichtlich klein und wird von manuellem Restart und
    Auto-Restart genutzt, damit Server-Datei-Updates, Mod-Updates, Firewall und
    iptables nicht auseinanderlaufen.

    Verwendet den EINHEITLICHEN _LIFECYCLE_LOCK (via get_server_lifecycle_lock),
    damit start/stop/restart sich gegenseitig serialisieren (keine dual-lock TOCTOU).
    """
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")

    lock = get_server_lifecycle_lock(server.id)
    async with lock:
        db.refresh(server)
        # AUFGABE 4A: transient "restarting" VOR erstem stop() (im Lock)
        server.status = "restarting"
        db.commit()
        if not server.public_bind_ip:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Server hat keine Bind-IP konfiguriert. Bitte im Server-Detail "
                    "eine Public-IP zuweisen, bevor er gestartet wird."
                ),
            )

        server_update: dict = {}
        mod_updates: list[dict] = []
        try:
            plugin.prepare_for_updates(server)
            server_update = plugin.check_for_server_file_update(server)
            mod_updates = plugin.check_for_mod_updates(server)
        except Exception as exc:
            _append_console_log(
                server.id,
                f"[MSM] Updater-Check während Restart fehlgeschlagen (nicht kritisch): {exc}\n",
            )
            logger.warning("Updater-Check beim Restart von Server %s fehlgeschlagen: %s", server.id, exc)
            server_update = {}
            mod_updates = []

        update_lock_acquired = False
        needs_update_job = server_update.get("action") == "update" or bool(mod_updates)
        if needs_update_job:
            update_lock_acquired = try_acquire_install_update_lock(
                server.id, "restart_update"
            )
            if not update_lock_acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": INSTALL_UPDATE_ALREADY_RUNNING,
                        "message": f"errors.{INSTALL_UPDATE_ALREADY_RUNNING}",
                    },
                )
        ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
        try:
            close_ports(ports_list)
            iptables_revoke_server(
                server.name,
                server.public_bind_ip or "",
                ports_list,
            )

            stop_result = await asyncio.to_thread(plugin.stop, server)
            if "error" in stop_result:
                raise HTTPException(status_code=500, detail=stop_result["error"])

            if server_update.get("action") == "update":
                _append_console_log(
                    server.id,
                    f"[MSM] Server-Datei-Update erkannt ({server_update.get('reason')}). "
                    "Update wird vor dem Container-Start ausgeführt.\n",
                )
                update_res = await asyncio.to_thread(plugin.perform_server_file_update, server)
                if not update_res.get("ok", False):
                    _append_console_log(
                        server.id,
                        f"[MSM] Server-Datei-Update fehlgeschlagen (Restart wird fortgesetzt): "
                        f"{update_res.get('error') or update_res}\n",
                    )
                else:
                    _append_console_log(server.id, "[MSM] Server-Datei-Update erfolgreich abgeschlossen.\n")

            if mod_updates:
                _append_console_log(
                    server.id,
                    f"[MSM] {len(mod_updates)} Workshop-Mod(s) benötigen Update/Installation. "
                    "Download läuft vor dem Container-Start.\n",
                )
                mod_res = await asyncio.to_thread(plugin.perform_workshop_mod_updates, server)
                if not mod_res.get("ok", False):
                    _append_console_log(
                        server.id,
                        f"[MSM] Workshop-Mod-Update fehlgeschlagen (Restart wird fortgesetzt): "
                        f"{mod_res.get('error') or mod_res}\n",
                    )
        except Exception as exc:
            _append_console_log(
                server.id,
                f"[MSM] Updater-Hook während Restart fehlgeschlagen (nicht kritisch): {exc}\n",
            )
            logger.warning("Updater-Hook beim Restart von Server %s fehlgeschlagen: %s", server.id, exc)
        finally:
            if update_lock_acquired:
                release_install_update_lock(server.id)

        # Pre-Start-Backup (best-effort, nach Lock, vor docker run)
        if server.backup_on_start:
            from services.backup_service import run_backup
            try:
                run_backup(server.id, db, timeout_seconds=300)
            except Exception:
                logger.warning("Pre-Start-Backup fehlgeschlagen für Server %s (details redacted for security)", server.id)
                # NO Hard-Fail: Server startet trotzdem (best-effort)

        start_result = await asyncio.to_thread(plugin.start, server)
        if "error" in start_result:
            raise HTTPException(status_code=500, detail=start_result["error"])

        ports_list = [(p.port, p.protocol, p.role) for p in server.ports]
        open_ports(server.name, ports_list)
        iptables_accept_server(
            server.name,
            server.public_bind_ip or "",
            ports_list,
        )

        server.status = "running"
        db.commit()
        return {
            "message": "Restart-Befehl gesendet",
            "status": server.status,
            "stop": stop_result,
            "start": start_result,
        }
