"""Unauthenticated health check."""

from __future__ import annotations

from fastapi import APIRouter

from config import settings
from services import docker_service

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    docker_ok = False
    try:
        docker_ok = docker_service.ping()
    except Exception:
        docker_ok = False
    return {
        "status": "ok",
        "version": settings.agent_version,
        "docker_connected": docker_ok,
    }
