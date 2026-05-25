from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Mod, Server, User
from schemas import ModResponse
from dependencies import get_current_user, verify_csrf, require_server_permission
from games import get_plugin

router = APIRouter(prefix="/api/mods", tags=["mods"])





@router.get("/{server_id}", response_model=list[ModResponse])
def list_mods(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_server_permission(user, server_id, db, "server.mods.read")
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()


@router.post("/{server_id}", response_model=ModResponse)
def subscribe_mod(server_id: int, workshop_id: str, name: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)):
    require_server_permission(user, server_id, db, "server.mods.write")
    existing = db.query(Mod).filter(Mod.server_id == server_id, Mod.workshop_id == workshop_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Mod bereits abonniert")

    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    max_order = db.query(Mod).filter(Mod.server_id == server_id).count()
    mod = Mod(
        server_id=server_id,
        workshop_id=workshop_id,
        name=name,
        load_order=max_order,
        auto_update=True,
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)

    # Mod via SteamCMD im Hintergrund installieren
    plugin = get_plugin(server.game_type)
    if plugin and plugin.supports_mods:
        plugin.install_mod(server, workshop_id)

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
    db.delete(mod)
    db.commit()
    return {"message": "Mod entfernt"}


@router.post("/{server_id}/reorder", response_model=list[ModResponse])
def reorder_mods(server_id: int, order: list[int], db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)):
    require_server_permission(user, server_id, db, "server.mods.write")
    mods = db.query(Mod).filter(Mod.server_id == server_id).all()
    mod_map = {m.id: m for m in mods}
    for idx, mod_id in enumerate(order):
        if mod_id in mod_map:
            mod_map[mod_id].load_order = idx
    db.commit()
    # Write updated modlist to game config (Helper ist Blueprint-driven).
    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        plugin = get_plugin(server.game_type)
        if plugin and plugin.supports_mods:
            plugin.update_modlist(server)
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()
