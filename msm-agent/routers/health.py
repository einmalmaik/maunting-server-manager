"""Unauthenticated health check."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import settings
from services import docker_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
def health():
    docker_ok = False
    try:
        docker_ok = docker_service.ping()
    except Exception:
        docker_ok = False
    payload = {
        "status": "ok" if docker_ok else "degraded",
        "version": settings.agent_version,
        "docker_connected": docker_ok,
    }
    if not docker_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
