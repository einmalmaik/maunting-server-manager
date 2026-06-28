from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models import PostgresDatabase, PostgresUser, Server, User
from schemas.postgres import (
    PostgresBootstrapRequest,
    PostgresConfirmRequest,
    PostgresCreateDatabaseRequest,
    PostgresCreateTableRequest,
    PostgresCreateUserRequest,
    PostgresDatabaseRequest,
    PostgresDropTableRequest,
    PostgresExtensionDropRequest,
    PostgresExtensionInfo,
    PostgresExtensionRequest,
    PostgresResourcesResponse,
    PostgresRowsRequest,
    PostgresRowsResponse,
    PostgresRotatePasswordResponse,
    PostgresSqlRequest,
    PostgresTableRequest,
)
from services import postgres_service
from services.postgres_service import PostgresServiceError

router = APIRouter(prefix="/api/servers/{server_id}/databases", tags=["databases"])


def _ensure_server(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PostgresServiceError):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail="PostgreSQL-Operation fehlgeschlagen")


@router.get("", response_model=PostgresResourcesResponse)
def list_databases(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    return postgres_service.list_resources(db, server_id)


@router.post("/bootstrap")
def bootstrap_databases(
    server_id: int,
    body: PostgresBootstrapRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    server = _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return {"credentials": postgres_service.provision_server_databases(db, server, body.database_count)}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("")
def create_database(
    server_id: int,
    body: PostgresCreateDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return {"credential": postgres_service.create_database(db, server_id, body.name)}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.delete("/{database_id}")
def delete_database(
    server_id: int,
    database_id: int,
    body: PostgresConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    database = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id, PostgresDatabase.id == database_id).first()
    if not database:
        raise HTTPException(status_code=404, detail="Datenbank nicht gefunden")
    if body.confirm_name != database.name:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.delete_database(db, server_id, database_id)
        return {"message": "Datenbank geloescht"}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/users/{user_id}/rotate", response_model=PostgresRotatePasswordResponse)
def rotate_user_password(
    server_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return postgres_service.rotate_user_password(db, server_id, user_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/users")
def create_user(
    server_id: int,
    body: PostgresCreateUserRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return {"credential": postgres_service.create_user(db, server_id, body.database_id, body.username)}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.delete("/users/{user_id}")
def delete_user(
    server_id: int,
    user_id: int,
    body: PostgresConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    pg_user = db.query(PostgresUser).filter(PostgresUser.server_id == server_id, PostgresUser.id == user_id).first()
    if not pg_user:
        raise HTTPException(status_code=404, detail="Datenbank-User nicht gefunden")
    if body.confirm_name != pg_user.username:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.delete_user(db, server_id, user_id)
        return {"message": "Datenbank-User geloescht"}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/tables/list")
def list_tables(
    server_id: int,
    body: PostgresDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return {"tables": postgres_service.list_tables(db, server_id, body.database_id)}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/tables")
def create_table(
    server_id: int,
    body: PostgresCreateTableRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.write")
    try:
        postgres_service.create_table(
            db,
            server_id,
            body.database_id,
            body.schema_name,
            body.table_name,
            [column.model_dump() for column in body.columns],
        )
        return {"message": "Tabelle erstellt"}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/tables/drop")
def drop_table(
    server_id: int,
    body: PostgresDropTableRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.write")
    if body.confirm_name != body.table_name:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.drop_table(db, server_id, body.database_id, body.schema_name, body.table_name)
        return {"message": "Tabelle geloescht"}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/rows", response_model=PostgresRowsResponse)
def read_rows(
    server_id: int,
    body: PostgresRowsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return postgres_service.read_rows(
            db,
            server_id,
            body.database_id,
            body.schema_name,
            body.table_name,
            body.limit,
            body.offset,
            body.search,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/extensions", response_model=list[PostgresExtensionInfo])
def install_extension(
    server_id: int,
    body: PostgresExtensionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.write")
    try:
        postgres_service.install_extension(db, server_id, body.database_id, body.name)
        return postgres_service.list_extensions(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/extensions/list", response_model=list[PostgresExtensionInfo])
def list_installed_extensions(
    server_id: int,
    body: PostgresDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return postgres_service.list_extensions(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.delete("/extensions/{extension_name}", response_model=list[PostgresExtensionInfo])
def drop_installed_extension(
    server_id: int,
    extension_name: str,
    body: PostgresExtensionDropRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.write")
    if body.confirm_name != extension_name:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.drop_extension(db, server_id, body.database_id, extension_name)
        return postgres_service.list_extensions(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/sql", response_model=PostgresRowsResponse)
def execute_sql(
    server_id: int,
    body: PostgresSqlRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return postgres_service.execute_sql(db, server_id, body.database_id, body.sql, body.limit)
    except Exception as exc:
        raise _service_error(exc) from exc
