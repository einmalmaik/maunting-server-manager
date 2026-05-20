from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..models import Server, User
from ..permissions import P_SERVERS_CREATE, P_SERVERS_DELETE, P_SERVERS_VIEW, require_perm
from ..shell import PanelCommandError, fetch_servers_list, get_server_dir, invoke_core_action, run_manager_command
from .deps import get_current_server, get_db

router = APIRouter()
logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _panel_error_detail(exc: PanelCommandError) -> str:
    result = getattr(exc, "result", None)
    return (getattr(result, "stderr", None) or getattr(result, "stdout", None) or str(exc)) if result else str(exc)

def _translate_clone_error(detail: str, source: str, target: str) -> HTTPException | None:
    detail_lower = detail.lower()

    if "source server" in detail_lower and "not found" in detail_lower:
        return HTTPException(status_code=404, detail=f"Server '{source}' not found.")
    if "target server" in detail_lower and "already exists" in detail_lower:
        return HTTPException(status_code=409, detail=f"Server '{target}' already exists.")
    if "source and target must differ" in detail_lower:
        return HTTPException(status_code=409, detail="Source and target server names must differ.")

    return None


def _validate_server_name_value(v: str) -> str:
    v = v.strip().lower()
    if not _NAME_RE.match(v):
        raise ValueError("Server name must contain only a-z, 0-9, and hyphens, and must not start or end with a hyphen.")
    if len(v) > 64:
        raise ValueError("Server name must be 64 characters or fewer.")
    return v


class ServerCreateBody(BaseModel):
    name: str
    game_id: str = "conan_exiles"

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_server_name_value(v)

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, v: str) -> str:
        from ..config import get_settings
        settings = get_settings()
        if v not in settings.game_managers:
            raise ValueError(f"Unknown game_id: {v!r}. Supported: {list(settings.game_managers)}")
        return v


class ServerCloneBody(BaseModel):
    source: str
    name: str

    @field_validator("source", "name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_server_name_value(v)


# ── List servers ──────────────────────────────────────────────────────────────

@router.get("/servers")
def list_servers(
    user: User = require_perm(P_SERVERS_VIEW),
    current_server: str | None = Depends(get_current_server),
    db: Session = Depends(get_db),
) -> Any:
    try:
        data = fetch_servers_list()
        response = dict(data or {})
        response["current"] = current_server
        # Enrich server entries with DB game_id
        servers_db = {s.name: {"game_id": s.game_id, "manager_path": s.manager_path} for s in db.query(Server).all()}
        for entry in response.get("servers", []):
            name = entry.get("name")
            if name and name in servers_db:
                entry["game_id"] = servers_db[name]["game_id"]
            elif name:
                entry["game_id"] = "conan_exiles"
        return response
    except PanelCommandError as exc:
        detail = _panel_error_detail(exc)
        logger.error("servers list failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to list servers.")


# ── Select active server for the session ──────────────────────────────────────

class ServerSelectBody(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_server_name_value(v)




@router.post("/servers/select")
def select_server(
    request: Request,
    body: ServerSelectBody,
    user: User = require_perm(P_SERVERS_VIEW),
) -> Any:
    if not get_server_dir(body.name).is_dir():
        raise HTTPException(status_code=404, detail=f"Server '{body.name}' not found.")
    request.session["current_server"] = body.name
    return {"ok": True, "current_server": body.name}


# ── Get current server ────────────────────────────────────────────────────────

@router.get("/servers/current")
def get_current_server_route(
    request: Request,
    current_server: str | None = Depends(get_current_server),
    user: User = require_perm(P_SERVERS_VIEW),
) -> Any:
    return {"current_server": current_server}


# ── Legacy layout check ───────────────────────────────────────────────────────

@router.get("/servers/legacy-check")
def legacy_check(user: User = require_perm(P_SERVERS_VIEW)) -> Any:
    try:
        return run_manager_command("panel", "bridge", "legacy-check", expect_json=True)
    except PanelCommandError as exc:
        detail = _panel_error_detail(exc)
        logger.error("legacy-check failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to check legacy layout.")


# ── Create server ─────────────────────────────────────────────────────────────

@router.post("/servers", status_code=201)
def create_server(
    request: Request,
    body: ServerCreateBody,
    user: User = require_perm(P_SERVERS_CREATE),
    db: Session = Depends(get_db),
) -> Any:
    from ..config import get_settings
    settings = get_settings()
    manager_path = settings.resolve_manager_path(body.game_id)
    server_dir = str(get_server_dir(body.name))
    try:
        invoke_core_action("server", "create", body.name, manager_path=manager_path)
        # Persist in DB
        existing = db.query(Server).filter(Server.name == body.name).first()
        if not existing:
            db.add(
                Server(
                    name=body.name,
                    game_id=body.game_id,
                    server_dir=server_dir,
                    manager_path=manager_path,
                )
            )
            db.commit()
        # Select the newly created server automatically
        request.session["current_server"] = body.name
    except PanelCommandError as exc:
        detail = _panel_error_detail(exc)
        if "already exists" in detail.lower():
            raise HTTPException(status_code=409, detail=f"Server '{body.name}' already exists.")
        logger.error("server create failed: %s", detail)
        raise HTTPException(status_code=500, detail="Failed to create server.")
    logger.info("server created and selected: name=%s game=%s by user_id=%s", body.name, body.game_id, user.id)
    return {"ok": True, "name": body.name, "game_id": body.game_id}


@router.post("/servers/clone", status_code=200)
def clone_server(
    request: Request,
    body: ServerCloneBody,
    user: User = require_perm(P_SERVERS_CREATE, P_SERVERS_VIEW),
) -> Any:
    if body.source == body.name:
        raise HTTPException(status_code=409, detail="Source and target server names must differ.")

    source_dir = get_server_dir(body.source)
    target_dir = get_server_dir(body.name)

    if not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Server '{body.source}' not found.")
    if target_dir.is_dir():
        raise HTTPException(status_code=409, detail=f"Server '{body.name}' already exists.")

    try:
        invoke_core_action("server", "clone", body.source, body.name)
        request.session["current_server"] = body.name
    except PanelCommandError as exc:
        detail = _panel_error_detail(exc)
        translated_error = _translate_clone_error(detail, body.source, body.name)
        if translated_error is not None:
            raise translated_error
        logger.error("server clone failed: source=%s target=%s error=%s", body.source, body.name, detail)
        raise HTTPException(status_code=500, detail="Failed to clone server.")

    logger.info("server cloned and selected: source=%s name=%s by user_id=%s", body.source, body.name, user.id)
    return {"ok": True, "source": body.source, "name": body.name, "current_server": body.name}


# ── Delete server ─────────────────────────────────────────────────────────────

@router.delete("/servers/{name}", status_code=200)
def delete_server(
    request: Request,
    name: str,
    user: User = require_perm(P_SERVERS_DELETE),
    db: Session = Depends(get_db),
    current_server: str | None = Depends(get_current_server),
) -> Any:
    name = name.strip().lower()
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=422, detail="Invalid server name.")
    if len(name) > 64:
        raise HTTPException(status_code=422, detail="Invalid server name.")
    if not get_server_dir(name).is_dir():
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found.")
    if current_server == name:
        raise HTTPException(status_code=409, detail="Cannot delete the currently active server. Switch to another server first.")
    try:
        invoke_core_action("server", "delete", "--force", name)
        # Also clean up from DB if it exists there
        try:
            db.query(Server).filter(Server.name == name).delete()
            db.commit()
        except Exception as db_exc:
            db.rollback()
            logger.warning("Optional DB cleanup failed for server %s: %s", name, db_exc)
    except PanelCommandError as exc:
        detail = _panel_error_detail(exc)
        logger.error("server delete failed: name=%s error=%s", name, detail)
        raise HTTPException(status_code=500, detail="Failed to delete server.")
    logger.info("server deleted: name=%s by user_id=%s", name, user.id)
    return {"ok": True, "name": name}


# ── Trigger migration from legacy layout ──────────────────────────────

@router.post("/servers/migrate", status_code=200)
def migrate_server(
    body: ServerCreateBody,
    user: User = require_perm(P_SERVERS_CREATE),
) -> Any:
    try:
        invoke_core_action("migrate", body.name)
    except PanelCommandError as exc:
        detail = getattr(getattr(exc, "result", None), "stderr", None) or str(exc)
        if "not found" in detail.lower() or "no such" in detail.lower():
            raise HTTPException(status_code=404, detail=f"Server '{body.name}' not found.")
        logger.error("migrate failed: %s", detail)
        raise HTTPException(status_code=500, detail="Migration failed.")
    logger.info("legacy migration completed: target=%s by user_id=%s", body.name, user.id)
    return {"ok": True, "name": body.name}


# ── Pterodactyl candidates and migration ───────────────────────────

class PterodactylMigrateBody(BaseModel):
    pterodactyl_path: str
    target_server_name: str
    create_target: bool = True

    @field_validator("target_server_name")
    @classmethod
    def validate_target_name(cls, value: str) -> str:
        name = value.strip().lower()
        if not _NAME_RE.match(name):
            raise ValueError("Invalid server name.")
        if len(name) > 64:
            raise ValueError("Server name is too long.")
        return name


@router.get("/servers/pterodactyl/candidates")
def get_pterodactyl_candidates(
    root_path: str = "/var/lib/pterodactyl/volumes",
    _: User = require_perm(P_SERVERS_VIEW),
) -> Any:
    from ..pterodactyl import scan_pterodactyl_volumes
    return scan_pterodactyl_volumes(root_path)


@router.post("/servers/pterodactyl/migrate")
def migrate_pterodactyl(
    body: PterodactylMigrateBody,
    user: User = require_perm(P_SERVERS_CREATE),
    db: Session = Depends(get_db),
) -> Any:
    from ..pterodactyl import migrate_pterodactyl_server
    try:
        res = migrate_pterodactyl_server(
            pterodactyl_path=body.pterodactyl_path,
            target_server_name=body.target_server_name,
            create_target=body.create_target,
            db_session=db
        )
        logger.info("Pterodactyl migration completed: source=%s target=%s by user_id=%s", body.pterodactyl_path, body.target_server_name, user.id)
        return res
    except Exception as exc:
        logger.error("Pterodactyl migration failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
