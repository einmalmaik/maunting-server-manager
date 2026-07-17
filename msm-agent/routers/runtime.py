from fastapi import APIRouter, HTTPException, File, UploadFile
from pydantic import BaseModel, Field, field_validator

import os
import shutil
import tempfile
import tarfile
import subprocess
import threading
import time

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


@router.post("/update")
def update_agent(file: UploadFile = File(...)) -> dict:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        agent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        with tempfile.TemporaryDirectory() as extract_dir:
            with tarfile.open(tmp_path, "r:gz") as tar:
                tar.extractall(path=extract_dir)

            src_agent_dir = os.path.join(extract_dir, "msm-agent")
            if os.path.isdir(src_agent_dir):
                for item in os.listdir(src_agent_dir):
                    s = os.path.join(src_agent_dir, item)
                    d = os.path.join(agent_dir, item)
                    if os.path.isdir(s):
                        if item in (".git", "venv", ".env", "certs", "servers"):
                            continue
                        shutil.copytree(s, d, dirs_exist_ok=True)
                    else:
                        shutil.copy2(s, d)

        # Pip-Dependencies aktualisieren
        venv_pip = os.path.join(agent_dir, "venv", "bin", "pip")
        requirements_txt = os.path.join(agent_dir, "requirements.txt")
        if os.path.isfile(venv_pip) and os.path.isfile(requirements_txt):
            subprocess.run([venv_pip, "install", "-r", requirements_txt], check=False)

        def restart_service():
            time.sleep(1)
            subprocess.run(["sudo", "systemctl", "restart", "msm-agent.service"], check=False)

        threading.Thread(target=restart_service, daemon=True).start()

        return {"ok": True, "message": "Agent-Update erfolgreich gestartet"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update des Agents fehlgeschlagen: {str(e)}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
