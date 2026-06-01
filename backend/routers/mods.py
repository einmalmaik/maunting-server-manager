from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import Mod, Server, User
from schemas import ModResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from games import get_plugin
from services.install_update_lock_service import (
    release_install_update_lock,
    acquire_install_update_lock_blocking,
)
from services.mod_install_status_service import mark_mod_failed, mark_mod_installed, mark_mod_installing
from games import updater
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mods", tags=["mods"])


def install_mod_bg(server_id: int, workshop_id: str):
    db = SessionLocal()
    try:
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            logger.error("Server %s nicht gefunden in Background Task", server_id)
            return
        plugin = get_plugin(server.game_type)
        if not plugin or not plugin.supports_mods:
            mark_mod_failed(server_id, workshop_id)
            return

        # Blockierenden Lock erwerben, um parallele SteamCMD-Aufrufe zu serialisieren
        acquire_install_update_lock_blocking(server.id, "mod_install")
        try:
            mark_mod_installing(server.id, workshop_id, "install")
            result = plugin.install_mod(server, workshop_id)
            success = isinstance(result, dict) and result.get("ok", True) is not False and "error" not in result
            if success:
                updater.update_mod_metadata_after_success(server.id, workshop_id)
                mark_mod_installed(server.id, workshop_id)
            else:
                mark_mod_failed(server.id, workshop_id)
        finally:
            release_install_update_lock(server.id)
    except Exception as exc:
        logger.exception("Fehler bei Hintergrund-Mod-Installation für Server %s (workshop_id: %s)", server_id, workshop_id)
    finally:
        db.close()


@router.get("/{server_id}", response_model=list[ModResponse])
def list_mods(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "server.mods.read")
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
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)

    # Mod via SteamCMD installieren (in den Hintergrund auslagern, um nicht zu blockieren)
    if plugin and plugin.supports_mods:
        background_tasks.add_task(install_mod_bg, server.id, workshop_id)

    return mod


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
                    logger.warning("Mod-Cleanup gab False zurück fuer Server %s, Mod %s", server_id, mod.workshop_id)
            except Exception as e:
                logger.warning("Mod-Cleanup fehlgeschlagen fuer Server %s, Mod %s: %s", server_id, mod.workshop_id, e)
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
