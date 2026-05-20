from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Mod, Server, Permission, User
from routers.auth import get_current_user

router = APIRouter(prefix="/api/mods", tags=["mods"])


def _check_perm(user: User, server_id: int, db: Session) -> None:
    if user.is_owner:
        return
    perm = db.query(Permission).filter(
        Permission.user_id == user.id,
        Permission.server_id == server_id
    ).first()
    if not perm or not perm.can_manage_mods:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")


@router.get("/{server_id}")
def list_mods(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Mod]:
    _check_perm(user, server_id, db)
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()


@router.post("/{server_id}")
def subscribe_mod(server_id: int, workshop_id: str, name: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Mod:
    _check_perm(user, server_id, db)
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
    )
    db.add(mod)
    db.commit()
    db.refresh(mod)
    return mod


@router.patch("/{server_id}/{mod_id}")
def update_mod(server_id: int, mod_id: int, load_order: int | None = None, auto_update: bool | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> Mod:
    _check_perm(user, server_id, db)
    mod = db.query(Mod).filter(Mod.id == mod_id, Mod.server_id == server_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Mod nicht gefunden")
    if load_order is not None:
        mod.load_order = load_order
    if auto_update is not None:
        mod.auto_update = auto_update
    db.commit()
    db.refresh(mod)
    return mod


@router.delete("/{server_id}/{mod_id}")
def unsubscribe_mod(server_id: int, mod_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db)
    mod = db.query(Mod).filter(Mod.id == mod_id, Mod.server_id == server_id).first()
    if not mod:
        raise HTTPException(status_code=404, detail="Mod nicht gefunden")
    db.delete(mod)
    db.commit()
    return {"message": "Mod entfernt"}


@router.post("/{server_id}/reorder")
def reorder_mods(server_id: int, order: list[int], db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Mod]:
    _check_perm(user, server_id, db)
    mods = db.query(Mod).filter(Mod.server_id == server_id).all()
    mod_map = {m.id: m for m in mods}
    for idx, mod_id in enumerate(order):
        if mod_id in mod_map:
            mod_map[mod_id].load_order = idx
    db.commit()
    return db.query(Mod).filter(Mod.server_id == server_id).order_by(Mod.load_order.asc()).all()
