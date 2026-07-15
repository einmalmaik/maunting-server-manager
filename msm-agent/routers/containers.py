"""Docker container management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import docker_service
from services.docker_service import (
    ContainerNameError,
    DockerUnavailableError,
    HardeningError,
)

router = APIRouter(prefix="/containers", tags=["containers"])


class CreateContainerRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    image: str = Field(..., min_length=1, max_length=512)
    command: list[str] | str | None = None
    env: dict[str, str] | None = None
    ports: dict[str, Any] | None = None
    volumes: dict[str, dict[str, str]] | None = None
    cpu_limit_percent: float | None = Field(default=None, ge=0, le=100_000)
    ram_limit_mb: int | None = Field(default=None, ge=0)
    user: str | None = None
    workdir: str | None = None
    network: str | None = None
    # Hardening traps — if clients send these, we reject explicitly
    privileged: bool | None = None
    cap_add: list[str] | None = None
    network_mode: str | None = None


class StopRequest(BaseModel):
    timeout: int | None = Field(default=None, ge=0, le=600)


class ExecRequest(BaseModel):
    command: list[str] = Field(..., min_length=1)


def _map_docker_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ContainerNameError):
        return HTTPException(status_code=400, detail=exc.message)
    if isinstance(exc, HardeningError):
        return HTTPException(status_code=403, detail=exc.message)
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, DockerUnavailableError):
        return HTTPException(status_code=503, detail=exc.message)
    return HTTPException(status_code=500, detail="Internal error")


@router.get("")
def list_containers() -> list[dict[str, Any]]:
    try:
        return docker_service.list_containers()
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.post("")
def create_container(body: CreateContainerRequest) -> dict[str, Any]:
    try:
        return docker_service.create_container(
            name=body.name,
            image=body.image,
            command=body.command,
            env=body.env,
            ports=body.ports,
            volumes=body.volumes,
            cpu_limit_percent=body.cpu_limit_percent,
            ram_limit_mb=body.ram_limit_mb,
            user=body.user,
            workdir=body.workdir,
            network=body.network,
            privileged=body.privileged,
            cap_add=body.cap_add,
            network_mode=body.network_mode,
        )
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.post("/{name}/start")
def start_container(name: str) -> dict[str, Any]:
    try:
        return docker_service.start_container(name)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.post("/{name}/stop")
def stop_container(name: str, body: StopRequest | None = None) -> dict[str, Any]:
    timeout = body.timeout if body else None
    try:
        return docker_service.stop_container(name, timeout=timeout)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.post("/{name}/restart")
def restart_container(name: str, body: StopRequest | None = None) -> dict[str, Any]:
    timeout = body.timeout if body else None
    try:
        return docker_service.restart_container(name, timeout=timeout)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.delete("/{name}")
def delete_container(name: str) -> dict[str, Any]:
    try:
        return docker_service.remove_container(name)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.get("/{name}/stats")
def stats(name: str) -> dict[str, Any]:
    try:
        return docker_service.container_stats(name)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc


@router.post("/{name}/exec")
def exec_command(name: str, body: ExecRequest) -> dict[str, Any]:
    try:
        return docker_service.exec_in_container(name, body.command)
    except Exception as exc:
        raise _map_docker_errors(exc) from exc
