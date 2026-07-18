"""Managed Postgres endpoints for panel proxy (Phase 7).

Passwords arrive only in JSON body and stay in process memory for the request.
Never logged. Never written to agent disk.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.postgres_service import (
    PostgresAgentError,
    alter_owner_password,
    create_user,
    demote_owner,
    dispatch_query,
    drop_databases_and_roles,
    dump_databases,
    ensure_internal_postgres,
    promote_owner,
    provision,
    restore_sql,
    rotate_role_password,
)

router = APIRouter(prefix="/postgres", tags=["postgres"])


def _http(exc: PostgresAgentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


class EnsureIn(BaseModel):
    admin_password: str = Field(..., min_length=1)


class ProvisionIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    db_name: str = Field(..., min_length=1, max_length=63)
    owner_role: str = Field(..., min_length=1, max_length=63)
    owner_password: str = Field(..., min_length=1)
    user_name: str = Field(..., min_length=1, max_length=63)
    user_password: str = Field(..., min_length=1)
    power_user: bool = False


class CreateUserIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    database_name: str = Field(..., min_length=1, max_length=63)
    user_name: str = Field(..., min_length=1, max_length=63)
    user_password: str = Field(..., min_length=1)


class RotateIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    role_name: str = Field(..., min_length=1, max_length=63)
    new_password: str = Field(..., min_length=1)


class DropIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    databases: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)


class QueryIn(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    database_name: str = ""
    owner_role: str = ""
    owner_password: str = ""
    schema_name: str | None = None
    table_name: str | None = None
    columns: list[dict[str, Any]] | None = None
    limit: int | None = None
    offset: int | None = None
    search: str | None = None
    sql: str | None = None
    name: str | None = None


class PromoteIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    owner_role: str = Field(..., min_length=1, max_length=63)
    new_password: str = Field(..., min_length=1)


class DemoteIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    owner_role: str = Field(..., min_length=1, max_length=63)


class DumpIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    database_names: list[str] = Field(default_factory=list)


class RestoreIn(BaseModel):
    admin_password: str = Field(..., min_length=1)
    dumps: dict[str, str] = Field(default_factory=dict)
    owners: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post("/ensure")
def postgres_ensure(body: EnsureIn) -> dict[str, Any]:
    try:
        return ensure_internal_postgres(body.admin_password)
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/provision")
def postgres_provision(body: ProvisionIn) -> dict[str, Any]:
    try:
        return provision(
            admin_password=body.admin_password,
            db_name=body.db_name,
            owner_role=body.owner_role,
            owner_password=body.owner_password,
            user_name=body.user_name,
            user_password=body.user_password,
            power_user=body.power_user,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/users/create")
def postgres_create_user(body: CreateUserIn) -> dict[str, Any]:
    try:
        return create_user(
            admin_password=body.admin_password,
            database_name=body.database_name,
            user_name=body.user_name,
            user_password=body.user_password,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/users/rotate")
def postgres_rotate(body: RotateIn) -> dict[str, Any]:
    try:
        return rotate_role_password(
            admin_password=body.admin_password,
            role_name=body.role_name,
            new_password=body.new_password,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/drop")
def postgres_drop(body: DropIn) -> dict[str, Any]:
    try:
        return drop_databases_and_roles(
            admin_password=body.admin_password,
            databases=body.databases,
            owners=body.owners,
            users=body.users,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.delete("/database")
def postgres_delete_database(body: DropIn) -> dict[str, Any]:
    """Alias for drop (phase-7 DELETE /postgres/database)."""
    return postgres_drop(body)


@router.post("/query")
def postgres_query(body: QueryIn) -> Any:
    try:
        payload = body.model_dump()
        return dispatch_query(body.action, payload)
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/roles/promote")
def postgres_promote(body: PromoteIn) -> dict[str, Any]:
    try:
        return promote_owner(
            admin_password=body.admin_password,
            owner_role=body.owner_role,
            new_password=body.new_password,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/roles/demote")
def postgres_demote(body: DemoteIn) -> dict[str, Any]:
    try:
        return demote_owner(
            admin_password=body.admin_password,
            owner_role=body.owner_role,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/roles/rotate-owner")
def postgres_rotate_owner(body: PromoteIn) -> dict[str, Any]:
    try:
        return alter_owner_password(
            admin_password=body.admin_password,
            owner_role=body.owner_role,
            new_password=body.new_password,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/dump")
def postgres_dump(body: DumpIn) -> dict[str, Any]:
    try:
        dumps = dump_databases(
            admin_password=body.admin_password,
            database_names=body.database_names,
        )
        return {"ok": True, "dumps": dumps}
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/restore")
def postgres_restore(body: RestoreIn) -> dict[str, Any]:
    try:
        return restore_sql(
            admin_password=body.admin_password,
            dumps=body.dumps,
            owners=body.owners,
        )
    except PostgresAgentError as exc:
        raise _http(exc) from exc
