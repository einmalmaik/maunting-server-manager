from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..permissions import P_RCON_SEND, require_perm
from ..rcon import RconError, send_rcon_command
from ..server_layout import get_server_base_dir, read_config_ini
from .deps import require_server

router = APIRouter()


class RconCommandBody(BaseModel):
    command: str

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 512:
            raise ValueError("Command must be 1-512 characters.")
        return value


@router.post("/rcon/command")
def run_rcon_command(
    body: RconCommandBody,
    server_name: str = Depends(require_server),
    _user: Any = require_perm(P_RCON_SEND),
) -> Any:
    base_dir = get_server_base_dir(server_name)
    _config_path, values = read_config_ini(base_dir)
    if values.get("rcon_enabled", "false").lower() not in {"true", "1", "yes"}:
        raise HTTPException(status_code=400, detail="RCON is disabled for this server.")
    host = values.get("rcon_host") or "127.0.0.1"
    try:
        port = int(values.get("rconport") or "25575")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid RCON port in config.ini.") from exc
    password = values.get("rcon_password") or ""
    try:
        response = send_rcon_command(host, port, password, body.command)
    except RconError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"response": response}
