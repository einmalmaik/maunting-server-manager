from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from services import runtime_service

router = APIRouter(prefix="/runtime", tags=["runtime"])
logger = logging.getLogger(__name__)

MAX_UPDATE_UPLOAD_SIZE = 100 * 1024 * 1024
MAX_UPDATE_FILE_COUNT = 10_000
MAX_UPDATE_EXTRACTED_SIZE = 512 * 1024 * 1024
UPDATE_CHUNK_SIZE = 1024 * 1024
PIP_TIMEOUT_SECONDS = 300
_PRESERVED_AGENT_ENTRIES = frozenset({".git", ".env", "certs", "servers", "venv"})


class UpdateUploadTooLargeError(ValueError):
    pass


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


def _copy_bounded(source: BinaryIO, destination: BinaryIO, *, limit: int) -> None:
    total = 0
    while chunk := source.read(UPDATE_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            raise UpdateUploadTooLargeError("Agent update archive exceeds the upload limit")
        destination.write(chunk)


def _safe_member_path(member: tarfile.TarInfo) -> PurePosixPath:
    if "\\" in member.name:
        raise ValueError("Agent update archive contains an unsafe path")
    path = PurePosixPath(member.name)
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if path.is_absolute() or not parts or ".." in parts:
        raise ValueError("Agent update archive contains an unsafe path")
    normalized = PurePosixPath(*parts)
    if normalized.parts[0] != "msm-agent":
        raise ValueError("Agent update archive must contain only msm-agent/")
    return normalized


def _extract_update_archive(archive_path: Path, extract_dir: Path) -> Path:
    seen: set[PurePosixPath] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        extracted_size = 0
        validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        for member in archive:
            if len(validated) >= MAX_UPDATE_FILE_COUNT:
                raise ValueError("Agent update archive contains too many files")
            path = _safe_member_path(member)
            if path in seen:
                raise ValueError("Agent update archive contains duplicate paths")
            seen.add(path)
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError("Agent update archive contains unsupported entries")
            extracted_size += member.size
            if extracted_size > MAX_UPDATE_EXTRACTED_SIZE:
                raise ValueError("Agent update archive expands beyond the size limit")
            validated.append((member, path))

        for member, path in validated:
            destination = extract_dir.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(exist_ok=True)
                os.chmod(destination, member.mode & 0o755)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Agent update archive contains an unreadable file")
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=UPDATE_CHUNK_SIZE)
            os.chmod(destination, member.mode & 0o755)

    source_dir = extract_dir / "msm-agent"
    if not (source_dir / "main.py").is_file() or not (source_dir / "requirements.txt").is_file():
        raise ValueError("Agent update archive is incomplete")
    return source_dir


def _install_dependencies(agent_dir: Path, source_dir: Path) -> None:
    pip = agent_dir / "venv" / "bin" / "pip"
    requirements = source_dir / "requirements.txt"
    if not pip.is_file():
        raise RuntimeError("Agent virtual environment is unavailable")
    subprocess.run(
        [str(pip), "install", "-r", str(requirements)],
        check=True,
        timeout=PIP_TIMEOUT_SECONDS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _replace_agent_tree(agent_dir: Path, source_dir: Path) -> None:
    """Replace deployed source entries and remove stale files, preserving runtime state."""
    parent = agent_dir.parent
    incoming = Path(tempfile.mkdtemp(prefix=".msm-agent-incoming-", dir=parent))
    backup = Path(tempfile.mkdtemp(prefix=".msm-agent-backup-", dir=parent))
    installed: list[Path] = []
    backed_up: list[tuple[Path, Path]] = []
    completed = False
    try:
        for item in source_dir.iterdir():
            if item.name in _PRESERVED_AGENT_ENTRIES:
                continue
            target = incoming / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

        current_names = {
            item.name for item in agent_dir.iterdir() if item.name not in _PRESERVED_AGENT_ENTRIES
        }
        incoming_names = {item.name for item in incoming.iterdir()}
        for name in sorted(current_names | incoming_names):
            current = agent_dir / name
            if current.exists() or current.is_symlink():
                saved = backup / name
                os.replace(current, saved)
                backed_up.append((saved, current))
            replacement = incoming / name
            if replacement.exists() or replacement.is_symlink():
                os.replace(replacement, current)
                installed.append(current)
        completed = True
    except Exception:
        for path in reversed(installed):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for saved, original in reversed(backed_up):
            if saved.exists() or saved.is_symlink():
                os.replace(saved, original)
        raise
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
        if completed or not any(backup.iterdir()):
            shutil.rmtree(backup, ignore_errors=True)


def _schedule_restart() -> None:
    def restart_service() -> None:
        time.sleep(1)
        try:
            subprocess.run(
                ["sudo", "systemctl", "restart", "msm-agent.service"],
                check=True,
                timeout=30,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            logger.error("Agent update installed, but service restart failed")

    threading.Thread(target=restart_service, daemon=True).start()


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
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
            os.fchmod(tmp.fileno(), 0o600)
            _copy_bounded(file.file, tmp, limit=MAX_UPDATE_UPLOAD_SIZE)
            tmp_path = Path(tmp.name)

        agent_dir = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as extract_dir:
            source_dir = _extract_update_archive(tmp_path, Path(extract_dir))
            _install_dependencies(agent_dir, source_dir)
            _replace_agent_tree(agent_dir, source_dir)
        _schedule_restart()
        return {
            "ok": True,
            "message": "Agent-Update installiert; Neustart geplant",
            "restart_status": "scheduled",
        }
    except UpdateUploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except (ValueError, tarfile.TarError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Dependency installation timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=500, detail="Dependency installation failed") from exc
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Update des Agents fehlgeschlagen: {type(exc).__name__}: {str(exc)}") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
