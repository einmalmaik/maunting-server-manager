"""Pydantic-Schemas fuer Server-CRUD.

Phase-2-Validierung:
- ``public_bind_ip`` muss eine echte IPv4-Adresse sein und darf NICHT
  ``0.0.0.0`` sein (verhindert die Docker-UFW-Falle bei unkontrollierten
  Bindings). ``127.0.0.1`` bleibt erlaubt, aber das Frontend bietet ihn
  bewusst nur als "lokal/Test"-Option an.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


def _validate_bind_ip(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        addr = ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise ValueError(f"'{value}' ist keine gueltige IPv4-Adresse.") from exc
    if str(addr) == "0.0.0.0":
        raise ValueError(
            "0.0.0.0 ist als public_bind_ip nicht erlaubt — bitte eine konkrete "
            "Host-IP aus dem Interfaces-Dropdown waehlen (Anti-Docker-UFW-Leak)."
        )
    return str(addr)


# cpu_limit_percent erlaubt Werte > 100 (200 % = 2 Cores). Limit 3200 % = 32 Cores
# als pragmatische Obergrenze.


class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    game_type: str = Field(..., pattern=r"^[a-z0-9_]+$")
    auto_restart: bool = False
    restart_interval_hours: int | None = Field(None, ge=1, le=168)
    restart_time_utc: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    cpu_limit_percent: int | None = Field(None, ge=10, le=3200)
    ram_limit_mb: int | None = Field(None, ge=512)
    disk_limit_gb: int | None = Field(None, ge=1)

    # Ports — leer lassen für automatische Vergabe
    game_port: int | None = Field(None, ge=1024, le=65535)
    query_port: int | None = Field(None, ge=1024, le=65535)
    rcon_port: int | None = Field(None, ge=1024, le=65535)

    # Host-IP, an die die Container-Ports gebunden werden. Wenn leer, vergibt
    # der Router automatisch die erste Public-IP des Hosts (siehe
    # network_interfaces_service.default_bind_ip).
    public_bind_ip: str | None = Field(None, max_length=64)

    @field_validator("public_bind_ip")
    @classmethod
    def _check_bind_ip(cls, v: str | None) -> str | None:
        return _validate_bind_ip(v)


class ServerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    auto_restart: bool | None = None
    restart_interval_hours: int | None = Field(None, ge=1, le=168)
    restart_time_utc: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$")
    cpu_limit_percent: int | None = Field(None, ge=10, le=3200)
    ram_limit_mb: int | None = Field(None, ge=512)
    disk_limit_gb: int | None = Field(None, ge=1)
    game_port: int | None = Field(None, ge=1024, le=65535)
    query_port: int | None = Field(None, ge=1024, le=65535)
    rcon_port: int | None = Field(None, ge=1024, le=65535)
    public_bind_ip: str | None = Field(None, max_length=64)

    @field_validator("public_bind_ip")
    @classmethod
    def _check_bind_ip(cls, v: str | None) -> str | None:
        return _validate_bind_ip(v)


class ServerResponse(BaseModel):
    id: int
    name: str
    game_type: str
    install_dir: str
    container_name: str | None
    status: str
    status_message: str | None
    auto_restart: bool
    restart_interval_hours: int | None
    restart_time_utc: str | None
    cpu_limit_percent: int | None
    ram_limit_mb: int | None
    disk_limit_gb: int | None
    disk_usage_mb: int | None
    game_port: int | None
    query_port: int | None
    rcon_port: int | None
    public_bind_ip: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class ServerStatusResponse(BaseModel):
    id: int
    status: str
    status_message: str | None
    cpu_percent: float | None
    ram_mb: int | None
    disk_mb: int | None
    uptime_seconds: int | None
    # Soft-Limits (auch wenn ohne Limit-Wert anzeigen) — Frontend kann
    # belegt/limit-Verhältnis darstellen und Frei-Speicher des Hosts zeigen.
    cpu_limit_percent: int | None = None
    ram_limit_mb: int | None = None
    disk_limit_gb: int | None = None
    disk_used_mb: int | None = None
    disk_free_mb: int | None = None

    # Update availability for frontend badge (wired from plugin checks).
    # Defaults ensure schema is robust; actual values populated in router.
    server_file_update_available: bool = False
    server_file_update_reason: str | None = None
    mod_updates_available: list[dict] = Field(default_factory=list)
