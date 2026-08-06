from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from dependencies import require_global, verify_csrf
from models import User
from schemas.postgres import (
    PostgresDatabaseStats,
    PostgresDeleteRowsRequest,
    PostgresInsertRowRequest,
    PostgresRowsRequest,
    PostgresRowsResponse,
    PostgresSqlRequest,
    PostgresSqlResponse,
    PostgresTableInfo,
    PostgresTableListItem,
    PostgresTableRequest,
    PostgresUpdateRowRequest,
)
from services import panel_database_service

router = APIRouter(prefix="/api/panel/database", tags=["panel-database"])


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    err_str = str(exc)
    if "foreign key constraint" in err_str.lower() or "foreignkeyviolation" in err_str.lower():
        return HTTPException(
            status_code=400,
            detail="Zeile kann nicht gelöscht werden: Sie wird von einer anderen Tabelle referenziert (Fremdschlüssel-Einschränkung).",
        )
    return HTTPException(status_code=400, detail=f"Datenbank-Fehler: {err_str}")


@router.get("/stats", response_model=PostgresDatabaseStats)
def database_stats(_: User = Depends(require_global("panel.database.read"))):
    try:
        return panel_database_service.stats()
    except Exception as exc:
        raise _service_error(exc) from exc


@router.get("/tables/list")
def list_tables(_: User = Depends(require_global("panel.database.read"))) -> dict[str, list[PostgresTableListItem]]:
    try:
        return {"tables": panel_database_service.list_tables()}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/tables/info", response_model=PostgresTableInfo)
def describe_table(
    body: PostgresTableRequest,
    _: User = Depends(require_global("panel.database.read")),
):
    try:
        return panel_database_service.describe_table(body.schema_name, body.table_name)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/rows", response_model=PostgresRowsResponse)
def read_rows(
    body: PostgresRowsRequest,
    _: User = Depends(require_global("panel.database.read")),
):
    try:
        return panel_database_service.read_rows(
            body.schema_name,
            body.table_name,
            body.limit,
            body.offset,
            body.search,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/rows/update")
def update_row(
    body: PostgresUpdateRowRequest,
    _: User = Depends(require_global("panel.database.admin")),
    __: None = Depends(verify_csrf),
):
    try:
        return panel_database_service.update_row(
            body.schema_name,
            body.table_name,
            body.key_conditions,
            body.updates,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/rows/delete")
def delete_rows(
    body: PostgresDeleteRowsRequest,
    _: User = Depends(require_global("panel.database.admin")),
    __: None = Depends(verify_csrf),
):
    try:
        return panel_database_service.delete_rows(
            body.schema_name,
            body.table_name,
            body.row_conditions,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/rows/insert")
def insert_row(
    body: PostgresInsertRowRequest,
    _: User = Depends(require_global("panel.database.admin")),
    __: None = Depends(verify_csrf),
):
    try:
        return panel_database_service.insert_row(
            body.schema_name,
            body.table_name,
            body.row_data,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/sql", response_model=PostgresSqlResponse)
def execute_sql(
    body: PostgresSqlRequest,
    _: User = Depends(require_global("panel.database.admin")),
    __: None = Depends(verify_csrf),
):
    try:
        return panel_database_service.execute_sql(body.sql, body.limit)
    except Exception as exc:
        raise _service_error(exc) from exc
