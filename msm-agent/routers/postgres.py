"""Managed Postgres endpoints for panel proxy (Phase 7).

Passwords arrive only in JSON body and stay in process memory for the request.
Never logged. Never written to agent disk.
"""

from __future__ import annotations

from typing import Any, Literal

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
    rotate_admin_password,
    rotate_role_password,
    run_statements,
    ziel,
)

router = APIRouter(prefix="/postgres", tags=["postgres"])


def _http(exc: PostgresAgentError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


class TargetIn(BaseModel):
    """Eigene Instanz eines Datenbankservers. Fehlt es, gilt msm-postgres."""

    host: str = Field(..., min_length=1, max_length=64)
    port: int = Field(..., ge=1024, le=65535)
    container: str = Field(..., min_length=1, max_length=64)


class _Zielbar(BaseModel):
    target: TargetIn | None = None

    def ziel_dict(self) -> dict[str, Any] | None:
        return self.target.model_dump() if self.target else None


class EnsureIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)


class ProvisionIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    db_name: str = Field(..., min_length=1, max_length=63)
    owner_role: str = Field(..., min_length=1, max_length=63)
    owner_password: str = Field(..., min_length=1)
    user_name: str = Field(..., min_length=1, max_length=63)
    user_password: str = Field(..., min_length=1)
    power_user: bool = False


class CreateUserIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    database_name: str = Field(..., min_length=1, max_length=63)
    user_name: str = Field(..., min_length=1, max_length=63)
    user_password: str = Field(..., min_length=1)


class RotateIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    role_name: str = Field(..., min_length=1, max_length=63)
    new_password: str = Field(..., min_length=1)


class RotateAdminIn(_Zielbar):
    """Cluster-Admin-Rotation: altes und neues Passwort nur im Request-Body."""

    admin_password: str = Field(..., min_length=1)
    new_admin_password: str = Field(..., min_length=16, max_length=256)


class DropIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    databases: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    users: list[str] = Field(default_factory=list)


class QueryIn(_Zielbar):
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
    # Daten-Grid: ohne diese Felder verwarf Pydantic sie still, und jedes
    # Aendern/Loeschen/Einfuegen endete in "Key conditions ... required".
    key_conditions: dict[str, Any] | None = None
    updates: dict[str, Any] | None = None
    row_conditions: list[dict[str, Any]] | None = None
    row_data: dict[str, Any] | None = None


class PromoteIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    owner_role: str = Field(..., min_length=1, max_length=63)
    new_password: str = Field(..., min_length=1)


class DemoteIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    owner_role: str = Field(..., min_length=1, max_length=63)


class DumpIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    database_names: list[str] = Field(default_factory=list)


class RestoreIn(_Zielbar):
    admin_password: str = Field(..., min_length=1)
    dumps: dict[str, str] = Field(default_factory=dict)
    owners: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.post("/ensure")
def postgres_ensure(body: EnsureIn) -> dict[str, Any]:
    try:
        with ziel(body.ziel_dict()):
            return ensure_internal_postgres(body.admin_password)
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/provision")
def postgres_provision(body: ProvisionIn) -> dict[str, Any]:
    try:
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
            return rotate_role_password(
                admin_password=body.admin_password,
                role_name=body.role_name,
                new_password=body.new_password,
            )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/admin/rotate")
def postgres_rotate_admin(body: RotateAdminIn) -> dict[str, Any]:
    """Rotiert msm_admin auf dem Node. Antwort enthaelt keine Passwoerter."""
    try:
        with ziel(body.ziel_dict()):
            return rotate_admin_password(
                admin_password=body.admin_password,
                new_admin_password=body.new_admin_password,
            )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/drop")
def postgres_drop(body: DropIn) -> dict[str, Any]:
    try:
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
            payload = body.model_dump()
            return dispatch_query(body.action, payload)
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/roles/promote")
def postgres_promote(body: PromoteIn) -> dict[str, Any]:
    try:
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
            return demote_owner(
                admin_password=body.admin_password,
                owner_role=body.owner_role,
            )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


@router.post("/roles/rotate-owner")
def postgres_rotate_owner(body: PromoteIn) -> dict[str, Any]:
    try:
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
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
        with ziel(body.ziel_dict()):
            return restore_sql(
                admin_password=body.admin_password,
                dumps=body.dumps,
                owners=body.owners,
            )
    except PostgresAgentError as exc:
        raise _http(exc) from exc


class RunStatementIn(BaseModel):
    sql: str = Field(..., min_length=1, max_length=1_000_000)
    # None: SQL wird nicht formatiert (Editor-SQL mit %). Liste: psycopg2
    # setzt Platzhalter ein, dann muss ein woertliches % doppelt stehen.
    params: list[Any] | None = Field(None, max_length=100_000)


class RunIn(_Zielbar):
    database_name: str = Field(..., min_length=1, max_length=63)
    identity: Literal["owner", "admin"] = "owner"
    owner_role: str = ""
    owner_password: str = ""
    admin_password: str = ""
    mode: Literal["read", "tx", "autocommit"] = "read"
    rollback: bool = False
    statements: list[RunStatementIn] = Field(..., min_length=1, max_length=500)
    row_limit: int = Field(500, ge=1, le=5000)
    timeout_ms: int = Field(5000, ge=100, le=600_000)


@router.post("/run")
def postgres_run(body: RunIn) -> dict[str, Any]:
    """Anweisungen des Panel-Studios ausfuehren (Katalog, DDL, Wartung)."""
    try:
        with ziel(body.ziel_dict()):
            return run_statements(
                database_name=body.database_name,
                identity=body.identity,
                owner_role=body.owner_role,
                owner_password=body.owner_password,
                admin_password=body.admin_password,
                mode=body.mode,
                statements=[st.model_dump() for st in body.statements],
                row_limit=body.row_limit,
                timeout_ms=body.timeout_ms,
                rollback=body.rollback,
            )
    except PostgresAgentError as exc:
        raise _http(exc) from exc
