"""PostgreSQL-Studio je Datenbank eines Servers.

Rechte: lesen ``server.databases.read``; Daten ändern ``write``;
Strukturänderungen, SQL-Editor, EXPLAIN, Sitzungen, Parameter ``admin``.
Welches Recht eine Strukturänderung braucht, sagt ihr Plan
(``services.postgres_ddl``) — Vorschau und Ausführung prüfen dasselbe.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models import User
from routers.databases import _audit_db, _service_error
from schemas.postgres_studio import (
    StudioExecuteResponse,
    StudioExplainRequest,
    StudioExportRequest,
    StudioImportRequest,
    StudioOperationRequest,
    StudioPlanResponse,
    StudioRowDeleteRequest,
    StudioRowInsertRequest,
    StudioRowsRequest,
    StudioRowUpdateRequest,
    StudioSessionActionRequest,
    StudioSqlRequest,
    StudioSqlResponse,
)
from services import postgres_studio_service as studio
from services.permission_service import has_server_permission

router = APIRouter(
    prefix="/api/servers/{server_id}/databases/{database_id}/studio",
    tags=["databases"],
)

READ = "server.databases.read"
WRITE = "server.databases.write"
ADMIN = "server.databases.admin"


def _kontext(db: Session, user: User, server_id: int, database_id: int, permission: str) -> studio.Kontext:
    require_server_permission(user, server_id, db, permission)
    try:
        return studio.kontext(db, server_id, database_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _call(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:
        raise _service_error(exc) from exc


# ── Übersicht und Katalog ──────────────────────────────────────────────────


@router.get("")
def overview(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, READ)
    result = _call(studio.overview, db, k)
    result["permissions"] = {
        "read": True,
        "write": has_server_permission(db, user, server_id, WRITE),
        "admin": has_server_permission(db, user, server_id, ADMIN),
    }
    return result


@router.get("/objects")
def schema_objects(
    server_id: int,
    database_id: int,
    schema_name: str = Query("public", alias="schema", min_length=1, max_length=63),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.schema_objects, db, k, schema_name)


@router.get("/table")
def table_details(
    server_id: int,
    database_id: int,
    schema_name: str = Query("public", alias="schema", min_length=1, max_length=63),
    table: str = Query(..., min_length=1, max_length=63),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.table_details, db, k, schema_name, table)


@router.get("/functions/{oid}")
def function_definition(
    server_id: int, database_id: int, oid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.function_definition, db, k, oid)


@router.get("/extensions")
def extensions(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.extensions, db, k)


@router.get("/roles")
def roles(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.roles, db, k)


@router.get("/grants")
def grants(
    server_id: int,
    database_id: int,
    schema_name: str = Query("public", alias="schema", min_length=1, max_length=63),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.schema_grants, db, k, schema_name)


@router.get("/health")
def health(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.health, db, k)


# ── Instanz und Überwachung (admin) ────────────────────────────────────────


@router.get("/parameters")
def parameters(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    return _call(studio.parameters, db, k)


@router.get("/sessions")
def sessions(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    return _call(studio.sessions, db, k)


@router.get("/locks")
def locks(server_id: int, database_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    return _call(studio.locks, db, k)


@router.post("/sessions/{pid}")
def session_action(
    server_id: int,
    database_id: int,
    pid: int,
    body: StudioSessionActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    ok = _call(studio.session_action, db, k, pid, body.action)
    _audit_db(
        db,
        user,
        action=f"postgres.studio.session_{body.action}",
        server_id=server_id,
        details={"database_id": database_id, "pid": pid, "ok": ok},
    )
    return {"ok": ok}


# ── SQL, EXPLAIN, Strukturänderungen ───────────────────────────────────────


@router.post("/sql", response_model=StudioSqlResponse)
def run_sql(
    server_id: int,
    database_id: int,
    body: StudioSqlRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    return _call(studio.run_sql, db, k, body)


@router.post("/explain")
def explain(
    server_id: int,
    database_id: int,
    body: StudioExplainRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, ADMIN)
    return _call(studio.explain, db, k, body)


def _plan(db: Session, user: User, server_id: int, database_id: int, body: StudioOperationRequest):
    k = _kontext(db, user, server_id, database_id, READ)
    plan = _call(studio.plan_for, db, k, body.operation)
    require_server_permission(user, server_id, db, plan.permission)
    return k, plan


@router.post("/preview", response_model=StudioPlanResponse)
def preview(
    server_id: int,
    database_id: int,
    body: StudioOperationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _k, plan = _plan(db, user, server_id, database_id, body)
    return studio.plan_response(plan)


@router.post("/execute", response_model=StudioExecuteResponse)
def execute(
    server_id: int,
    database_id: int,
    body: StudioOperationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k, plan = _plan(db, user, server_id, database_id, body)
    result = _call(studio.execute, db, k, body.operation)
    # Kein SQL im Audit: ein Rollenpasswort stünde sonst darin.
    _audit_db(
        db,
        user,
        action="postgres.studio.execute",
        server_id=server_id,
        details={
            "database_id": database_id,
            "operation": body.operation.op,
            "destructive": plan.destructive,
            "scope": plan.scope,
            "statements": len(plan.statements),
        },
    )
    return result


# ── Daten-Grid ─────────────────────────────────────────────────────────────


@router.post("/rows")
def read_rows(
    server_id: int,
    database_id: int,
    body: StudioRowsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    k = _kontext(db, user, server_id, database_id, READ)
    return _call(studio.rows, db, k, body)


@router.post("/rows/insert")
def insert_row(
    server_id: int,
    database_id: int,
    body: StudioRowInsertRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, WRITE)
    return _call(studio.insert_row, db, k, body)


@router.post("/rows/update")
def update_row(
    server_id: int,
    database_id: int,
    body: StudioRowUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, WRITE)
    return _call(studio.update_row, db, k, body)


@router.post("/rows/delete")
def delete_rows(
    server_id: int,
    database_id: int,
    body: StudioRowDeleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, WRITE)
    return _call(studio.delete_rows, db, k, body)


@router.post("/rows/import")
def import_rows(
    server_id: int,
    database_id: int,
    body: StudioImportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    k = _kontext(db, user, server_id, database_id, WRITE)
    return _call(studio.import_rows, db, k, body)


@router.post("/rows/export")
def export_rows(
    server_id: int,
    database_id: int,
    body: StudioExportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> Response:
    k = _kontext(db, user, server_id, database_id, READ)
    payload, media_type, truncated = _call(studio.export_rows, db, k, body)
    extension = {"text/csv": "csv", "application/json": "json", "application/sql": "sql"}[media_type]
    # Header sind latin-1: alles andere wird zu "_".
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{body.schema_name}.{body.table}") + f".{extension}"
    return Response(
        content=payload,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-MSM-Export-Truncated": "1" if truncated else "0",
            "Cache-Control": "no-store",
        },
    )
