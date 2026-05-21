import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Server, Permission, User
from dependencies import get_current_user, verify_csrf, require_server_permission
from games import get_plugin

router = APIRouter(prefix="/api/config", tags=["config"])





@router.get("/{server_id}/files")
def list_config_files(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    require_server_permission(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    return plugin.get_config_files()


@router.get("/{server_id}/files/{file_name}")
def get_config_file(server_id: int, file_name: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    require_server_permission(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    files = plugin.get_config_files()
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
def update_config_file(server_id: int, file_name: str, content: str, db: Session = Depends(get_db), user: User = Depends(get_current_user), _: None = Depends(verify_csrf)) -> dict:
    require_server_permission(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    files = plugin.get_config_files()
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


@router.get("/{server_id}/schema")
def get_config_schema(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    require_server_permission(user, server_id, db, "can_edit_config")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    plugin = get_plugin(server.game_type)
    if not plugin:
        raise HTTPException(status_code=400, detail="Spiel-Typ nicht unterstützt")
    schema = plugin.get_config_schema()
    return [field.model_dump() for field in schema]
