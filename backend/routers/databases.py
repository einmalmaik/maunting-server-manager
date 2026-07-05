from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from config import settings
from models import PostgresDatabase, PostgresUser, Server, User
from schemas.postgres import (
    PostgresBootstrapRequest,
    PostgresConfirmRequest,
    PostgresCreateDatabaseRequest,
    PostgresCreateTableRequest,
    PostgresCreateUserRequest,
    PostgresDatabaseRequest,
    PostgresDropTableRequest,
    PostgresDatabaseStats,
    PostgresDumpRequest,
    PostgresExtensionDropRequest,
    PostgresExtensionInfo,
    PostgresExtensionRequest,
    PostgresPowerUserDemoteRequest,
    PostgresPowerUserResponse,
    PostgresResourcesResponse,
    PostgresRestoreRequest,
    PostgresRotatePasswordResponse,
    PostgresRowsRequest,
    PostgresRowsResponse,
    PostgresSqlRequest,
    PostgresSqlResponse,
    PostgresTableInfo,
    PostgresTableRequest,
    PostgresTableListItem,
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
    import traceback
    traceback.print_exc()
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
) -> dict[str, list[PostgresTableListItem]]:
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return {"tables": postgres_service.list_tables(db, server_id, body.database_id)}
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/stats", response_model=PostgresDatabaseStats)
def database_stats(
    server_id: int,
    body: PostgresDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return postgres_service.database_stats(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/tables/info", response_model=PostgresTableInfo)
def describe_table(
    server_id: int,
    body: PostgresTableRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return postgres_service.describe_table(db, server_id, body.database_id, body.schema_name, body.table_name)
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


@router.post("/power-user", response_model=PostgresPowerUserResponse)
def promote_to_power_user(
    server_id: int,
    body: PostgresDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Promote a normal DB owner role to SUPERUSER. Returns one-time credentials."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return postgres_service.promote_owner_to_superuser(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/power-user/rotate", response_model=PostgresPowerUserResponse)
def rotate_power_user_credentials(
    server_id: int,
    body: PostgresDatabaseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Rotate the password of an existing superuser owner role. Returns new one-time credentials."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return postgres_service.rotate_power_user_password(db, server_id, body.database_id)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.delete("/power-user/demote", response_model=PostgresPowerUserResponse)
def demote_from_power_user(
    server_id: int,
    body: PostgresPowerUserDemoteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Demote a superuser owner role back to normal. Confirmation username required."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    if body.confirm_name != body.username:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.demote_owner_from_superuser(db, server_id, body.database_id)
        database = db.query(PostgresDatabase).filter(
            PostgresDatabase.server_id == server_id, PostgresDatabase.id == body.database_id
        ).first()
        if not database:
            raise ValueError("Datenbank wurde fuer diesen Server nicht gefunden.")
        return PostgresPowerUserResponse(
            username=database.owner_role,
            password="",
            host=settings.managed_postgres_container_name,
            port=5432,
            database_name=database.name,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/sql", response_model=PostgresSqlResponse)
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


# ── pg_dump / psql round-trip fuer phpMyAdmin-aehnlichen Export/Import ──────


@router.post("/export")
def export_databases(
    server_id: int,
    body: PostgresDumpRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> Response:
    """Streamt ein ``pg_dump``-SQL aller Server-DBs als ``application/sql``.

    Dateiname: ``msm-server-<id>-<created_at>.sql``. SHA256 in ``X-MSM-Dump-SHA256``
    Header (UI/CLI kann Integritaet pruefen). Permission: ``server.databases.admin``.

    KISS: das SQL wird als Memory-String gebaut (nicht Streaming), weil die
    Stripe-Groesse bei Server-DBs im einstelligen MB-Bereich bleibt -- Backup-
    Streaming waere ein anderer Patch, kommt mit v1.5.x.
    """
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        sql_text, db_names, size_bytes, sha, dur_ms = (
            postgres_service.dump_server_databases(db, server_id)
        )
    except Exception as exc:
        raise _service_error(exc) from exc

    if not db_names:
        raise HTTPException(status_code=400, detail="Server hat keine Postgres-Datenbanken.")

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"msm-server-{server_id}-{timestamp}.sql"
    payload = sql_text.encode("utf-8")
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(len(payload)),
        "X-MSM-Dump-SHA256": sha,
        "X-MSM-Dump-Duration-MS": str(dur_ms),
        "X-MSM-Dump-DB-Names": ",".join(db_names),
        "X-MSM-Dump-Size": str(size_bytes),
    }
    return Response(content=payload, media_type="application/sql; charset=utf-8", headers=headers)


@router.post("/import")
def import_database(
    server_id: int,
    body: PostgresRestoreRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Stellt ein uebermitteltes ``application/sql``-Dump wieder her.

    Verhalten: SQL wird in ALLE DBs des Servers geschrieben (DROP+CREATE via
    ``--clean``-Semantik). Permission: ``server.databases.admin``.

    Sicherheit: kein SQL-Pass-through, das ist psycopg2-verifiziertes DDL/DML
    auf bereits-authentifizierten Server-eigenen DBs. Groessen-Limit von
    200MB schuetzt vor Memory-Bomben (im PostgresRestoreRequest-Schema).
    """
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        return postgres_service.restore_sql_to_server_dbs(db, server_id, body.sql)
    except Exception as exc:
        raise _service_error(exc) from exc
