import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Server, Permission, User
from routers.auth import get_current_user

router = APIRouter(prefix="/api/config", tags=["config"])


def _check_perm(user: User, server_id: int, db: Session) -> None:
    if user.is_owner:
        return
    perm = db.query(Permission).filter(
        Permission.user_id == user.id,
        Permission.server_id == server_id
    ).first()
    if not perm or not perm.can_edit_config:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")


# Game-spezifische Config-Dateien (später aus Plugin geladen)
CONFIG_FILES = {
    "conan_exiles_ue5": [
        {"name": "Engine.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/Engine.ini"},
        {"name": "Game.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/Game.ini"},
        {"name": "ServerSettings.ini", "path": "ConanSandbox/Saved/Config/LinuxServer/ServerSettings.ini"},
    ],
    "dayz": [
        {"name": "serverDZ.cfg", "path": "serverDZ.cfg"},
        {"name": "cfgplayerspawn.xml", "path": "cfgplayerspawn.xml"},
        {"name": "cfgeconomy.xml", "path": "cfgeconomy.xml"},
    ],
}


@router.get("/{server_id}/files")
def list_config_files(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    _check_perm(user, server_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return CONFIG_FILES.get(server.game_type, [])


@router.get("/{server_id}/files/{file_name}")
def get_config_file(server_id: int, file_name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    files = CONFIG_FILES.get(server.game_type, [])
    file_info = next((f for f in files if f["name"] == file_name), None)
    if not file_info:
        raise HTTPException(status_code=404, detail="Config-Datei unbekannt")

    full_path = Path(server.install_dir) / file_info["path"]
    if not full_path.exists():
        return {"name": file_name, "path": str(full_path), "content": "", "exists": False}

    try:
        content = full_path.read_text(encoding="utf-8")
        return {"name": file_name, "path": str(full_path), "content": content, "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lesen fehlgeschlagen: {e}")


@router.put("/{server_id}/files/{file_name}")
def update_config_file(server_id: int, file_name: str, content: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    _check_perm(user, server_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    files = CONFIG_FILES.get(server.game_type, [])
    file_info = next((f for f in files if f["name"] == file_name), None)
    if not file_info:
        raise HTTPException(status_code=404, detail="Config-Datei unbekannt")

    full_path = Path(server.install_dir) / file_info["path"]
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        return {"message": "Config gespeichert", "name": file_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schreiben fehlgeschlagen: {e}")
