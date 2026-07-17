from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from services import runtime_service

router = APIRouter(prefix="/runtime", tags=["runtime"])


class Port(BaseModel):
    port: int = Field(..., ge=1, le=65535)
    protocol: str = Field(..., pattern="^(tcp|udp)$")
    role: str = Field(default="game", min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")


class PortCheckBody(BaseModel):
    ports: list[Port] = Field(..., min_length=1, max_length=32)
    bind_ip: str = Field(default="0.0.0.0", max_length=64)

    @field_validator("bind_ip")
    @classmethod
    def validate_bind_ip(cls, value: str) -> str:
        import ipaddress

        ipaddress.ip_address(value)
        return value


class FirewallBody(BaseModel):
    ports: list[Port] = Field(..., max_length=32)
    server_name: str = Field(default="server", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


@router.post("/ports/check")
def check_ports(body: PortCheckBody) -> dict:
    return runtime_service.ports_available(
        [(item.port, item.protocol) for item in body.ports], body.bind_ip
    )


@router.post("/firewall/{action}")
def update_firewall(action: str, body: FirewallBody) -> dict:
    if action not in {"open", "close"}:
        raise HTTPException(status_code=400, detail="Invalid firewall action")
    result = runtime_service.firewall(
        action,
        [(item.port, item.protocol, item.role) for item in body.ports],
        body.server_name,
    )
    if not result["ok"]:
        raise HTTPException(status_code=503, detail="Node firewall update failed")
    return result


@router.get("/interfaces")
def get_interfaces() -> dict:
    from services import network_interfaces_service
    interfaces = [h.to_dict() for h in network_interfaces_service.list_host_interfaces()]
    return {
        "interfaces": interfaces,
        "default_bind_ip": network_interfaces_service.default_bind_ip(),
    }
