import logging
import re
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import Mod, Server, User
from schemas import ModResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from games import get_plugin, updater, _append_console_log
from services.install_update_lock_service import (
    release_install_update_lock,
    acquire_install_update_lock_blocking,
)
from services.mod_install_status_service import (
    INSTALL_RUNNING,
    mark_mod_failed,
    mark_mod_installed,
    mark_mod_installing,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mods", tags=["mods"])

_MOD_UPDATE_CHECK_CACHE: dict[int, float] = {}
_MOD_UPDATE_CHECK_TTL_SECONDS = 300
_MOD_ACTIONS = {"install", "update", "reinstall"}
_WORKSHOP_ID_RE = re.compile(r"^\d{1,20}$")


def _safe_error(value: object) -> str:
    text = str(value or "Installation fehlgeschlagen").strip()
    return " ".join(text.split())[:500]


def _validate_workshop_id(workshop_id: str) -> str:
    value = str(workshop_id or "").strip()
    if not _WORKSHOP_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="errors.invalid_workshop_id")
    return value


def _mark_update_candidates(db: Session, server_id: int, updates: list[dict]) -> None:
    changed = False
    for update in updates:
        workshop_id = str(update.get("workshop_id") or "")
        action = str(update.get("action") or "update")
        if not workshop_id or action not in {"install", "update"}:
            continue
        mod = (
            db.query(Mod)
            .filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id)
            .first()
        )
        if not mod or mod.install_status == INSTALL_RUNNING:
            continue
        mod.install_status = "pending"
        mod.install_action = action
        mod.install_progress = 0
        mod.install_eta_seconds = None
        mod.install_error = None
        mod.update_status = "missing" if action == "install" else "outdated"
        mod.update_reason = str(update.get("reason") or action)
        changed = True
    if changed:
        db.commit()


def _refresh_mod_update_availability(db: Session, server: Server, plugin, *, force: bool = False) -> list[dict]:
    if not plugin or not getattr(plugin, "supports_mods", False):
        return []
    now = time.time()
    if not force and now - _MOD_UPDATE_CHECK_CACHE.get(server.id, 0) < _MOD_UPDATE_CHECK_TTL_SECONDS:
        return []
    _MOD_UPDATE_CHECK_CACHE[server.id] = now
    try:
        updates = plugin.check_for_mod_updates(server)
    except Exception as exc:
        logger.warning("Mod-Update-Check fehlgeschlagen fuer Server %s: %s", server.id, exc)
        return []
    if updates:
        _mark_update_candidates(db, server.id, updates)
    return updates


def _server_has_active_mod_jobs(db: Session, server_id: int) -> bool:
    return (
        db.query(Mod.id)
        .filter(
            Mod.server_id == server_id,
            Mod.install_status.in_((INSTALL_RUNNING, "pending")),
        )
        .limit(1)
        .first()
        is not None
    )


def install_mod_bg(server_id: int, workshop_id: str, action: str = "install", remote_updated: str | None = None):
    # Lock zuerst — ohne offene DB-Session warten (verhindert QueuePool-Timeout bei vielen Mods).
    acquire_install_update_lock_blocking(server_id, "mod_install")
    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            logger.error("Server %s nicht gefunden in Background Task", server_id)
            mark_mod_failed(server_id, workshop_id, "Server nicht gefunden")
            return
        plugin = get_plugin(server.game_type)
        if not plugin or not plugin.supports_mods:
            mark_mod_failed(server_id, workshop_id, "Steam Workshop nicht in diesem Spiel aktiviert")
            return

        try:
            mark_mod_installing(server.id, workshop_id, action)
            if action == "reinstall":
                try:
                    plugin.cleanup_mod(server, workshop_id)
                    _append_console_log(server.id, f"[MSM] Mod {workshop_id} Re-Install: Cache + Artefakte bereinigt\n")
                except Exception as ce:
                    _append_console_log(server.id, f"[MSM] Mod {workshop_id} Re-Install Cleanup Warnung: {ce}\n")
            result = plugin.install_mod(server, workshop_id)
            success = isinstance(result, dict) and result.get("ok", True) is not False and "error" not in result
            if success:
                updater.update_mod_metadata_after_success(server.id, workshop_id, remote_updated)
                mark_mod_installed(server.id, workshop_id)
            else:
                if isinstance(result, dict):
                    err = result.get("error") or ("; ".join(result.get("errors") or []) if result.get("errors") else None)
                else:
                    err = result
                mark_mod_failed(server.id, workshop_id, _safe_error(err))
        finally:
            release_install_update_lock(server.id)
    except Exception as exc:
        logger.exception(
            "Fehler bei Hintergrund-Mod-Installation für Server %s (workshop_id: %s)",
            server_id,
            workshop_id,
        )
        mark_mod_failed(server_id, workshop_id, _safe_error(exc))
        release_install_update_lock(server_id)
    finally:
        db.close()


def reinstall_all_mods_bg(server_id: int, workshop_ids: list[str]) -> None:
    acquire_install_update_lock_blocking(server_id, "mod_reinstall_all")
    try:
        db = SessionLocal()
        try:
            server = db.query(Server).filter(Server.id == server_id).first()
            if not server:
                logger.error("Server %s nicht gefunden (reinstall-all)", server_id)
                for wid in workshop_ids:
                    mark_mod_failed(server_id, wid, "Server nicht gefunden")
                return
            plugin = get_plugin(server.game_type)
            if not plugin or not plugin.supports_mods:
                for wid in workshop_ids:
                    mark_mod_failed(server_id, wid, "Steam Workshop nicht in diesem Spiel aktiviert")
                return

            _append_console_log(
                server.id,
                f"[MSM] Neuinstallation für {len(workshop_ids)} Workshop-Mod(s) gestartet (nacheinander).\n",
            )
            ok_count = 0
            for wid in workshop_ids:
                mark_mod_installing(server.id, wid, "reinstall")
                try:
                    plugin.cleanup_mod(server, wid)
                except Exception as exc:
                    _append_console_log(
                        server.id,
                        f"[MSM] Mod {wid} Cleanup-Warnung: {exc}\n",
                    )
                try:
                    result = plugin.install_mod(server, wid)
                except Exception as exc:
                    mark_mod_failed(server.id, wid, _safe_error(exc))
                    _append_console_log(server.id, f"[MSM] Mod {wid}: fehlgeschlagen — {exc}\n")
                    continue
                success = (
                    isinstance(result, dict)
                    and result.get("ok", True) is not False
                    and "error" not in result
                )
                if success:
                    updater.update_mod_metadata_after_success(server.id, wid, None)
                    mark_mod_installed(server.id, wid)
                    ok_count += 1
                    _append_console_log(server.id, f"[MSM] Mod {wid}: neu installiert\n")
                else:
                    if isinstance(result, dict):
                        err = result.get("error") or (
                            "; ".join(result.get("errors") or []) if result.get("errors") else None
                        )
                    else:
                        err = str(result)
                    mark_mod_failed(server.id, wid, _safe_error(err))
                    _append_console_log(server.id, f"[MSM] Mod {wid}: fehlgeschlagen — {err}\n")

            try:
                plugin.update_modlist(server)
            except Exception as exc:
                logger.warning("Modlist-Update nach reinstall-all fehlgeschlagen: %s", exc)

            _append_console_log(
                server.id,
                f"[MSM] Neuinstallation abgeschlossen ({ok_count}/{len(workshop_ids)} erfolgreich).\n",
            )
        finally:
            db.close()
    except Exception:
        logger.exception("reinstall-all fehlgeschlagen für Server %s", server_id)
        for wid in workshop_ids:
            mark_mod_failed(server_id, wid, "Neuinstallation abgebrochen")
    finally:
        release_install_update_lock(server_id)


@router.get("/{server_id}", response_model=list[ModResponse])
def list_mods(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "server.mods.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        plugin = get_plugin(server.game_type)
        # Kein schwerer Workshop-Check während laufender/pending Installs (Polling + SteamCMD = Pool-Timeout).
        if not _server_has_active_mod_jobs(db, server_id):
            _refresh_mod_update_availability(db, server, plugin)
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()


@router.post("/{server_id}", response_model=ModResponse)
def subscribe_mod(
    server_id: int,
    workshop_id: str,
    background_tasks: BackgroundTasks,
    name: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    require_server_permission(user, server_id, db, "server.mods.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)

    workshop_id = _validate_workshop_id(workshop_id)
    existing = db.query(Mod).filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Mod bereits abonniert")

    max_order = db.query(Mod).filter(Mod.server_id == server_id).count()
    mod = Mod(
        server_id=server_id,
        workshop_id=workshop_id,
        name=name,
        load_order=max_order,
        auto_update=True,
        install_status="pending" if plugin and plugin.supports_mods else "installed",
        install_action="install" if plugin and plugin.supports_mods else None,
        install_progress=0 if plugin and plugin.supports_mods else 100,
        update_status="missing" if plugin and plugin.supports_mods else "up_to_date",
        update_reason="missing" if plugin and plugin.supports_mods else None,
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)

    # Mod via SteamCMD installieren (in den Hintergrund auslagern, um nicht zu blockieren)
    if plugin and plugin.supports_mods:
        background_tasks.add_task(install_mod_bg, server.id, workshop_id)

    return mod


@router.post("/{server_id}/check-updates", response_model=list[ModResponse])
def check_mod_updates(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    require_server_permission(user, server_id, db, "server.mods.read")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    _refresh_mod_update_availability(db, server, plugin, force=True)
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()


@router.post("/{server_id}/{mod_id}/install", response_model=ModResponse)
def install_existing_mod(
    server_id: int,
    mod_id: int,
    background_tasks: BackgroundTasks,
    action: str = "reinstall",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    require_server_permission(user, server_id, db, "server.mods.write")
    if action not in _MOD_ACTIONS:
        raise HTTPException(status_code=400, detail="Ungueltige Mod-Aktion")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Steam Workshop nicht in diesem Spiel aktiviert")
    mod = db.query(Mod).filter(Mod.id == mod_id, Mod.server_id == server_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Mod nicht gefunden")
    _validate_workshop_id(mod.workshop_id)
    if mod.install_status == INSTALL_RUNNING:
        raise HTTPException(status_code=409, detail="Mod-Installation läuft bereits")

    remote_updated = mod.last_updated.isoformat() if action == "reinstall" and mod.last_updated else None
    if action == "update":
        updates = _refresh_mod_update_availability(db, server, plugin, force=True)
        for update in updates:
            if str(update.get("workshop_id")) == str(mod.workshop_id):
                remote_updated = update.get("remote_updated")
                break
        db.refresh(mod)
        if not (
            (mod.install_status == "pending" and mod.install_action == "update")
            or mod.update_status == "outdated"
        ):
            raise HTTPException(status_code=400, detail="Kein Mod-Update verfügbar")

    mod.install_status = "pending"
    mod.install_action = action
    mod.install_progress = 0
    mod.install_eta_seconds = None
    mod.install_error = None
    db.commit()
    db.refresh(mod)

    background_tasks.add_task(install_mod_bg, server.id, mod.workshop_id, action, remote_updated)
    return mod


@router.post("/{server_id}/reinstall-all", response_model=list[ModResponse])
def reinstall_all_mods(
    server_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    require_server_permission(user, server_id, db, "server.mods.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin or not plugin.supports_mods:
        raise HTTPException(status_code=400, detail="Steam Workshop nicht in diesem Spiel aktiviert")

    mods = (
        db.query(Mod)
        .filter(Mod.server_id == server_id)
        .order_by(Mod.load_order.asc())
        .all()
    )
    if not mods:
        raise HTTPException(status_code=400, detail="Keine Mods zum Neuinstallieren")

    if any(m.install_status == INSTALL_RUNNING for m in mods):
        raise HTTPException(status_code=409, detail="Mod-Installation läuft bereits")

    workshop_ids: list[str] = []
    for mod in mods:
        _validate_workshop_id(mod.workshop_id)
        mod.install_status = "pending"
        mod.install_action = "reinstall"
        mod.install_progress = 0
        mod.install_eta_seconds = None
        mod.install_error = None
        workshop_ids.append(mod.workshop_id)
    db.commit()

    background_tasks.add_task(reinstall_all_mods_bg, server.id, workshop_ids)
    return (
        db.query(Mod)
        .filter(Mod.server_id == server_id)
        .order_by(Mod.load_order.asc())
        .all()
    )


@router.post("/{server_id}/abort-installs", response_model=list[ModResponse])
def abort_mod_installs(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Hängende Mod-Installationen zurücksetzen (UI-Status + Install-Lock)."""
    require_server_permission(user, server_id, db, "server.mods.write")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    from services.install_update_lock_service import force_release_install_update_lock
    from services.mod_install_status_service import mark_mod_failed, mark_mod_installed

    mods = db.query(Mod).filter(Mod.server_id == server_id).all()
    changed = False
    for mod in mods:
        if mod.install_status == INSTALL_RUNNING:
            mark_mod_failed(
                server_id,
                mod.workshop_id,
                "Installation abgebrochen — bitte erneut starten (Server-Konsole prüfen).",
            )
            changed = True
        elif mod.install_status == "pending" and mod.install_action == "reinstall":
            mark_mod_installed(server_id, mod.workshop_id)
            changed = True
    force_release_install_update_lock(server_id)
    if changed:
        _append_console_log(
            server_id,
            "[MSM] Mod-Installationen manuell zurückgesetzt (abort-installs).\n",
        )
    return (
        db.query(Mod)
        .filter(Mod.server_id == server_id)
        .order_by(Mod.load_order.asc())
        .all()
    )


@router.patch("/{server_id}/{mod_id}", response_model=ModResponse)
def update_mod(server_id: int, mod_id: int, load_order: int | None = None, auto_update: bool | None = None, enabled: bool | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)):
    # Reorder/auto_update gehoeren zu server.mods.write; enable/disable zu server.mods.toggle.
    # Wenn alle Felder None sind, ist es effektiv ein Read -> server.mods.read.
    if enabled is not None:
        require_server_permission(user, server_id, db, "server.mods.toggle")
    if load_order is not None or auto_update is not None:
        require_server_permission(user, server_id, db, "server.mods.write")
    if enabled is None and load_order is None and auto_update is None:
        require_server_permission(user, server_id, db, "server.mods.read")
    mod = db.query(Mod).filter(Mod.id == mod_id, Mod.server_id == server_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Mod nicht gefunden")
    if load_order is not None:
        mod.load_order = load_order
    if auto_update is not None:
        mod.auto_update = auto_update
    if enabled is not None:
        mod.enabled = enabled
    db.commit()
    db.refresh(mod)
    # Write updated modlist to game config (no-op fuer Blueprints mit
    # modInjection!=file; Helper kennt die Regeln aus der Blueprint).
    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        plugin = get_plugin(server.game_type)
        if plugin and plugin.supports_mods:
            plugin.update_modlist(server)
    return mod


@router.delete("/{server_id}/{mod_id}")
def unsubscribe_mod(server_id: int, mod_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "server.mods.write")
    mod = db.query(Mod).filter(Mod.id == mod_id, Mod.server_id == server_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Mod nicht gefunden")
    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        plugin = get_plugin(server.game_type)
        if plugin and plugin.supports_mods:
            try:
                result = plugin.cleanup_mod(server, mod.workshop_id)
                if isinstance(result, dict) and result.get("ok") is False:
                    raise RuntimeError("cleanup_failed")
            except Exception as exc:
                logger.warning(
                    "Mod-Cleanup fehlgeschlagen fuer Server %s, Mod %s error_type=%s",
                    server_id,
                    mod.workshop_id,
                    type(exc).__name__,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Mod-Dateien konnten nicht entfernt werden",
                ) from exc
    db.delete(mod)
    db.commit()
    if server:
        plugin = get_plugin(server.game_type)
        if plugin and plugin.supports_mods:
            plugin.update_modlist(server)
    return {"message": "Mod entfernt"}


@router.post("/{server_id}/reorder", response_model=list[ModResponse])
def reorder_mods(server_id: int, order: list[int], db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)):
    require_server_permission(user, server_id, db, "server.mods.write")
    mods = db.query(Mod).filter(Mod.server_id == server_id).all()
    mod_map = {m.id: m for m in mods}
    if len(order) != len(mod_map) or len(set(order)) != len(order) or set(order) != set(mod_map):
        raise HTTPException(status_code=400, detail="Ungueltige Mod-Ladereihenfolge")
    for idx, mod_id in enumerate(order):
        mod_map[mod_id].load_order = idx
    db.commit()
    # Write updated modlist to game config (Helper ist Blueprint-driven).
    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        plugin = get_plugin(server.game_type)
        if plugin and plugin.supports_mods:
            plugin.update_modlist(server)
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()
