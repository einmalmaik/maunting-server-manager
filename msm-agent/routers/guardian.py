"""Authenticated Guardian desired/observed state and incident delivery APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from config import settings
from services.agent_operation_coordinator import (
    InvalidServerOperation,
    server_id_from_container_name,
)
from services.guardian_action_registry import action_capabilities
from services.guardian_contract import (
    DIAGNOSTIC_PARSERS,
    GUARDIAN_SCHEMA_VERSION,
    PROBE_TYPES,
)
from services.guardian_service import (
    DesiredStateRejected,
    accept_desired_state,
    acknowledge_incidents,
    list_incidents,
    observed_state,
)


router = APIRouter(tags=["guardian"])


class AcknowledgeIncidentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uuids: list[str] = Field(default_factory=list, max_length=1000)


def _server_id(name: str) -> int:
    try:
        return server_id_from_container_name(name, settings.container_name_prefix)
    except InvalidServerOperation as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_container_name", "message": "invalid managed container name"},
        ) from exc


@router.get("/guardian/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "agent_version": settings.agent_version,
        "guardian_schema_versions": [GUARDIAN_SCHEMA_VERSION],
        "probe_types": sorted(PROBE_TYPES),
        "diagnostic_parsers": sorted(DIAGNOSTIC_PARSERS),
        "recovery_actions": action_capabilities(),
    }


@router.post("/containers/{name}/desired-state")
def set_desired_state(name: str, body: dict[str, Any]) -> dict[str, Any]:
    server_id = _server_id(name)
    try:
        return accept_desired_state(server_id, body)
    except DesiredStateRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/containers/{name}/guardian-state")
def get_guardian_state(name: str) -> dict[str, Any]:
    server_id = _server_id(name)
    try:
        return observed_state(server_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "guardian_state_not_found", "message": "Guardian state not found"},
        ) from exc


@router.get("/containers/{name}/incidents")
def get_incidents(name: str) -> list[dict[str, Any]]:
    return list_incidents(_server_id(name))


@router.post("/containers/{name}/incidents/acknowledge")
def acknowledge(name: str, body: AcknowledgeIncidentsRequest) -> dict[str, Any]:
    server_id = _server_id(name)
    try:
        acknowledged = acknowledge_incidents(server_id, body.uuids)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_incident_uuid", "message": "incident UUID list is invalid"},
        ) from exc
    return {"ok": True, "acknowledged": acknowledged}

