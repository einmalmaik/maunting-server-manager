from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from config import settings
from models import PostgresDatabase, PostgresUser, Server, User
from schemas.postgres import (
    PostgresBootstrapRequest,
    PostgresConfirmRequest,
    PostgresConnectionInfo,
    PostgresCreateDatabaseRequest,
    PostgresCreateUserRequest,
    PostgresDatabaseRequest,
    PostgresInstanceNetworkRequest,
    PostgresPowerUserDemoteRequest,
    PostgresPowerUserResponse,
    PostgresResourcesResponse,
    PostgresRevealRequest,
    PostgresRevealResponse,
    PostgresRotatePasswordResponse,
)
from services import audit_service, postgres_instance_service, postgres_service
from services.postgres_service import PostgresServiceError

router = APIRouter(prefix="/api/servers/{server_id}/databases", tags=["databases"])


def _ensure_server(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PostgresServiceError):
        return HTTPException(status_code=503, detail=str(exc))
    err_str = str(exc)
    if "foreign key constraint" in err_str.lower() or "foreignkeyviolation" in err_str.lower():
        return HTTPException(
            status_code=400,
            detail="Zeile kann nicht gelöscht werden: Sie wird von einer anderen Tabelle referenziert (Fremdschlüssel-Einschränkung).",
        )
    return HTTPException(status_code=400, detail=f"PostgreSQL-Fehler: {err_str}")


def _audit_db(
    db: Session,
    user: User,
    *,
    action: str,
    server_id: int,
    details: dict | None = None,
) -> None:
    """Schreibt Audit fuer privilegierte DB-Aktionen (ohne Secrets)."""
    payload = {"server_id": server_id}
    if details:
        payload.update(details)
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action=action,
        target_type="server",
        target_id=server_id,
        details=payload,
        commit=True,
    )


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
        credentials = postgres_service.provision_server_databases(db, server, body.database_count)
        _audit_db(
            db,
            user,
            action="postgres.database.provision",
            server_id=server_id,
            details={"database_count": body.database_count},
        )
        return {"credentials": credentials}
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
        credential = postgres_service.create_database(db, server_id, body.name)
        _audit_db(
            db,
            user,
            action="postgres.database.create",
            server_id=server_id,
            details={"database_name": credential.get("database_name")},
        )
        return {"credential": credential}
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
        db_name = database.name
        postgres_service.delete_database(db, server_id, database_id)
        _audit_db(
            db,
            user,
            action="postgres.database.delete",
            server_id=server_id,
            details={"database_id": database_id, "database_name": db_name},
        )
        return {"message": "Datenbank gelöscht"}
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
        result = postgres_service.rotate_user_password(db, server_id, user_id)
        _audit_db(
            db,
            user,
            action="postgres.user.rotate",
            server_id=server_id,
            details={"username": result.get("username")},
        )
        return result
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
        credential = postgres_service.create_user(db, server_id, body.database_id, body.username)
        _audit_db(
            db,
            user,
            action="postgres.user.create",
            server_id=server_id,
            details={
                "database_id": body.database_id,
                "username": credential.get("username"),
            },
        )
        return {"credential": credential}
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
        raise HTTPException(status_code=400, detail="Bestätigungsname stimmt nicht überein")
    try:
        uname = pg_user.username
        postgres_service.delete_user(db, server_id, user_id)
        _audit_db(
            db,
            user,
            action="postgres.user.delete",
            server_id=server_id,
            details={"username": uname},
        )
        return {"message": "Datenbank-User gelöscht"}
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
    """Issue elevated owner credentials for this database only (not cluster SUPERUSER)."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        result = postgres_service.promote_owner_to_power_user(db, server_id, body.database_id)
        _audit_db(
            db,
            user,
            action="postgres.power_user.promote",
            server_id=server_id,
            details={"database_id": body.database_id, "username": result.get("username")},
        )
        return result
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
    """Rotate password of a database-scoped power-user owner role. One-time credentials."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        result = postgres_service.rotate_power_user_password(db, server_id, body.database_id)
        _audit_db(
            db,
            user,
            action="postgres.power_user.rotate",
            server_id=server_id,
            details={"database_id": body.database_id, "username": result.get("username")},
        )
        return result
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
    """Demote a power-user owner role back to normal owner. Confirmation username required."""
    _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    if body.confirm_name != body.username:
        raise HTTPException(status_code=400, detail="Bestaetigungsname stimmt nicht ueberein")
    try:
        postgres_service.demote_owner_from_power_user(db, server_id, body.database_id)
        database = db.query(PostgresDatabase).filter(
            PostgresDatabase.server_id == server_id, PostgresDatabase.id == body.database_id
        ).first()
        if not database:
            raise ValueError("Datenbank wurde fuer diesen Server nicht gefunden.")
        _audit_db(
            db,
            user,
            action="postgres.power_user.demote",
            server_id=server_id,
            details={"database_id": body.database_id, "username": database.owner_role},
        )
        return PostgresPowerUserResponse(
            username=database.owner_role,
            password="",
            host=settings.managed_postgres_container_name,
            port=5432,
            database_name=database.name,
        )
    except Exception as exc:
        raise _service_error(exc) from exc


# ── pg_dump / psql round-trip fuer phpMyAdmin-aehnlichen Export/Import ──────


# ── Verbindungs-Hub ────────────────────────────────────────────────────────


@router.get("/connection", response_model=PostgresConnectionInfo)
def connection(server_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Host, Port, Datenbanken und Rollen — ohne ein einziges Passwort."""
    server = _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.read")
    try:
        return postgres_service.connection_info(db, server)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/credentials/reveal", response_model=PostgresRevealResponse)
def reveal_credential(
    server_id: int,
    body: PostgresRevealRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Gespeichertes Passwort anzeigen. Jeder Abruf steht im Audit-Log."""
    server = _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        result = postgres_service.reveal_credential(db, server, body.database_id, body.user_id)
    except Exception as exc:
        raise _service_error(exc) from exc
    _audit_db(
        db,
        user,
        action="postgres.credential.reveal",
        server_id=server_id,
        details={"database_id": body.database_id, "username": result["username"]},
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.put("/instance/network", response_model=PostgresConnectionInfo)
def update_instance_network(
    server_id: int,
    body: PostgresInstanceNetworkRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Allowlist und SSL-Pflicht einer eigenen Instanz (gilt sofort)."""
    server = _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        postgres_instance_service.update_network(
            db, server, allowed_cidrs=body.allowed_cidrs, ssl_required=body.ssl_required
        )
        _audit_db(
            db,
            user,
            action="postgres.instance.network",
            server_id=server_id,
            details={
                "allowed_cidrs": postgres_instance_service.cidrs_of(server.postgres_instance),
                "ssl_required": body.ssl_required,
            },
        )
        return postgres_service.connection_info(db, server)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/instance/bootstrap")
def bootstrap_instance(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
):
    """Legt fehlende Datenbanken in der eigenen Instanz an (wiederholbar)."""
    server = _ensure_server(db, server_id)
    require_server_permission(user, server_id, db, "server.databases.admin")
    try:
        created = postgres_instance_service.bootstrap(db, server)
        server.status_message = None
        db.commit()
        _audit_db(
            db,
            user,
            action="postgres.instance.bootstrap",
            server_id=server_id,
            details={"created": created},
        )
        return {"created": created}
    except Exception as exc:
        raise _service_error(exc) from exc

