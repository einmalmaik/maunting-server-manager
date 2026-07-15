"""Managed Postgres — panel proxy (Phase 7).

Source of truth for metadata + DIS-encrypted secrets remains the panel DB.
All SQL and msm-postgres container ops run on the node agent via NodeClient.
No psycopg2 and no direct docker for managed Postgres in this module.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from config import settings
from models import PostgresDatabase, PostgresGrant, PostgresUser, Server
from services.auth_service import AuthService
from services.node_client import NodeClient, NodeClientError
from services.node_service import (
    client_for_server,
    get_local_node,
    client_for_node,
)
from services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)

ADMIN_USER = "msm_admin"
CONTROL_DB = "msm_control"
ADMIN_PASSWORD_KEY = "managed_postgres.admin_password_encrypted"
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ALLOWED_COLUMN_TYPES = {
    "text": "text",
    "varchar": "varchar(255)",
    "integer": "integer",
    "bigint": "bigint",
    "boolean": "boolean",
    "timestamp": "timestamp",
    "jsonb": "jsonb",
}

_WRITE_KEYWORDS = (
    "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "grant", "revoke", "copy", "vacuum", "analyze", "cluster", "reindex",
    "set", "reset", "begin", "commit", "rollback", "savepoint", "lock",
    "call", "do", "notify", "listen", "unlisten", "refresh", "checkpoint",
)


class PostgresServiceError(RuntimeError):
    pass


def _validate_identifier(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if not IDENTIFIER_RE.fullmatch(cleaned):
        raise ValueError("Ungueltiger PostgreSQL-Identifier.")
    return cleaned


def _mask_secret(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


def _generate_password() -> str:
    return secrets.token_urlsafe(32)


def _encrypted_admin_password() -> str:
    encrypted = PanelSettingsService.get(ADMIN_PASSWORD_KEY, "")
    if encrypted:
        return encrypted
    password = _generate_password()
    encrypted = AuthService.encrypt_secret(password, aad="msm:pg:admin")
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, encrypted)
    return encrypted


def _admin_password() -> str:
    return AuthService.decrypt_secret(_encrypted_admin_password(), aad="msm:pg:admin")


def _map_agent_error(exc: Exception) -> Exception:
    if isinstance(exc, NodeClientError):
        return PostgresServiceError(exc.message or "Agent Postgres-Fehler")
    return exc


def _client_for_server_id(db: Session, server_id: int) -> NodeClient:
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise ValueError("Server nicht gefunden.")
    return _client_for_server(db, server)


def _client_for_server(db: Session, server: Server) -> NodeClient:
    try:
        client = client_for_server(server, db)
        if client is not None:
            return client
        # Local single-host: use local node agent when assigned node missing in tests
        local = get_local_node(db)
        if local is not None:
            c = client_for_node(local)
            if c is not None:
                return c
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Node nicht erreichbar") from exc
    raise PostgresServiceError(
        "Kein erreichbarer Node-Agent fuer Managed Postgres. "
        "Weise dem Server einen Node zu und starte den msm-agent."
    )


def ensure_internal_postgres(db: Session | None = None, server: Server | None = None) -> None:
    """Ensure msm-postgres on the target node via agent (no local psycopg2)."""
    try:
        if server is not None and db is not None:
            client = _client_for_server(db, server)
        elif db is not None:
            local = get_local_node(db)
            if local is None:
                logger.warning("ensure_internal_postgres: no local node")
                return
            client = client_for_node(local)
            if client is None:
                logger.warning("ensure_internal_postgres: local agent unavailable")
                return
        else:
            logger.warning("ensure_internal_postgres: no db session")
            return
        client.postgres_ensure(admin_password=_admin_password())
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Agent Postgres ensure failed") from exc


def server_extra_networks(db: Session, server_id: int) -> list[str]:
    exists = (
        db.query(PostgresDatabase.id)
        .filter(PostgresDatabase.server_id == server_id)
        .first()
    )
    return [settings.managed_postgres_network] if exists else []


def _database_row(db: Session, server_id: int, database_id: int) -> PostgresDatabase:
    database = (
        db.query(PostgresDatabase)
        .filter(PostgresDatabase.server_id == server_id, PostgresDatabase.id == database_id)
        .first()
    )
    if not database:
        raise ValueError("Datenbank wurde fuer diesen Server nicht gefunden.")
    return database


def _next_names(server_id: int, index: int) -> tuple[str, str, str]:
    return (f"msm_s{server_id}_db{index}", f"msm_s{server_id}_o{index}", f"msm_s{server_id}_u{index}")


def list_resources(db: Session, server_id: int) -> dict[str, list[Any]]:
    return {
        "databases": db.query(PostgresDatabase)
        .filter(PostgresDatabase.server_id == server_id)
        .order_by(PostgresDatabase.id)
        .all(),
        "users": db.query(PostgresUser)
        .filter(PostgresUser.server_id == server_id)
        .order_by(PostgresUser.id)
        .all(),
    }


def backup_context(db: Session, server_id: int) -> dict[str, Any] | None:
    """Build an ephemeral node-local dump/restore payload without persisting secrets."""
    databases = [row.name for row in list_resources(db, server_id)["databases"]]
    if not databases:
        return None
    rows = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).all()
    owners = {
        row.name: {
            "owner_role": row.owner_role,
            "owner_password": _owner_password(row),
        }
        for row in rows
    }
    return {
        "admin_password": _admin_password(),
        "database_names": databases,
        "owners": owners,
    }


def _owner_password(database: PostgresDatabase) -> str:
    return AuthService.decrypt_secret(
        database.owner_password_encrypted, aad="msm:pg:db:owner"
    )


def _owner_query(
    db: Session,
    server_id: int,
    database: PostgresDatabase,
    action: str,
    **extra: Any,
) -> Any:
    client = _client_for_server_id(db, server_id)
    payload = {
        "action": action,
        "database_name": database.name,
        "owner_role": database.owner_role,
        "owner_password": _owner_password(database),
        **extra,
    }
    try:
        return client.postgres_query(payload)
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Agent query failed") from exc


def provision_server_databases(
    db: Session,
    server: Server,
    count: int,
    *,
    power_user: bool = False,
) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("Mindestens eine PostgreSQL-Datenbank ist erforderlich.")
    ensure_internal_postgres(db, server)
    credentials: list[dict[str, Any]] = []
    attempted_resources: list[tuple[str, str, str]] = []
    try:
        existing = (
            db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server.id).count()
        )
        for offset in range(1, count + 1):
            db_name, owner_role, user_name = _next_names(server.id, existing + offset)
            attempted_resources.append((db_name, owner_role, user_name))
            credentials.append(
                _create_database_and_user(
                    db, server, db_name, owner_role, user_name, power_user=power_user
                )
            )
        db.commit()
        return credentials
    except Exception:
        try:
            client = _client_for_server(db, server)
            client.postgres_drop(
                {
                    "admin_password": _admin_password(),
                    "databases": [item[0] for item in attempted_resources],
                    "owners": [item[1] for item in attempted_resources],
                    "users": [item[2] for item in attempted_resources],
                }
            )
        except Exception:
            logger.warning("PostgreSQL compensation failed for server_id=%s", server.id)
        db.rollback()
        raise


def _create_database_and_user(
    db: Session,
    server: Server,
    db_name: str,
    owner_role: str,
    user_name: str,
    *,
    power_user: bool = False,
) -> dict[str, Any]:
    db_name = _validate_identifier(db_name)
    owner_role = _validate_identifier(owner_role)
    user_name = _validate_identifier(user_name)
    owner_password = _generate_password()
    user_password = _generate_password()
    client = _client_for_server(db, server)
    try:
        client.postgres_provision(
            {
                "admin_password": _admin_password(),
                "db_name": db_name,
                "owner_role": owner_role,
                "owner_password": owner_password,
                "user_name": user_name,
                "user_password": user_password,
                "power_user": power_user,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Provision failed") from exc

    database = PostgresDatabase(
        server_id=server.id,
        name=db_name,
        owner_role=owner_role,
        owner_password_encrypted=AuthService.encrypt_secret(
            owner_password, aad="msm:pg:db:owner"
        ),
        is_superuser=power_user,
        power_credentials_issued_at=datetime.now(timezone.utc) if power_user else None,
    )
    user = PostgresUser(
        server_id=server.id, username=user_name, password_mask=_mask_secret(user_password)
    )
    db.add(database)
    db.add(user)
    db.flush()
    db.add(
        PostgresGrant(
            server_id=server.id,
            database_id=database.id,
            user_id=user.id,
            privilege="read_write",
        )
    )
    db.flush()
    return {
        "database_id": database.id,
        "database_name": db_name,
        "username": user_name,
        "password": user_password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
        "is_superuser": power_user,
    }


def create_database(db: Session, server_id: int, name: str | None = None) -> dict[str, Any]:
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise ValueError("Server nicht gefunden.")
    ensure_internal_postgres(db, server)
    next_index = (
        db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).count() + 1
    )
    generated_db, generated_owner, generated_user = _next_names(server_id, next_index)
    db_name = _validate_identifier(name or generated_db)
    credential = _create_database_and_user(
        db, server, db_name, generated_owner, generated_user
    )
    db.commit()
    return credential


def create_user(
    db: Session, server_id: int, database_id: int, username: str | None = None
) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    next_index = db.query(PostgresUser).filter(PostgresUser.server_id == server_id).count() + 1
    user_name = _validate_identifier(username or f"msm_s{server_id}_u{next_index}")
    password = _generate_password()
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_create_user(
            {
                "admin_password": _admin_password(),
                "database_name": database.name,
                "user_name": user_name,
                "user_password": password,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Create user failed") from exc
    user = PostgresUser(
        server_id=server_id, username=user_name, password_mask=_mask_secret(password)
    )
    db.add(user)
    db.flush()
    db.add(
        PostgresGrant(
            server_id=server_id,
            database_id=database.id,
            user_id=user.id,
            privilege="read_write",
        )
    )
    db.commit()
    return {
        "database_id": database.id,
        "database_name": database.name,
        "username": user_name,
        "password": password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def rotate_user_password(db: Session, server_id: int, user_id: int) -> dict[str, Any]:
    user = (
        db.query(PostgresUser)
        .filter(PostgresUser.server_id == server_id, PostgresUser.id == user_id)
        .first()
    )
    if not user:
        raise ValueError("Datenbank-User wurde fuer diesen Server nicht gefunden.")
    password = _generate_password()
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_rotate_user(
            {
                "admin_password": _admin_password(),
                "role_name": user.username,
                "new_password": password,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Rotate failed") from exc
    user.password_mask = _mask_secret(password)
    user.last_rotated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "username": user.username,
        "password": password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def delete_database(db: Session, server_id: int, database_id: int) -> None:
    database = _database_row(db, server_id, database_id)
    users = [grant.user for grant in database.grants]
    _drop_database_and_roles(
        db,
        server_id,
        [database.name],
        [database.owner_role],
        [user.username for user in users],
    )
    for user in users:
        db.delete(user)
    db.delete(database)
    db.commit()


def delete_user(db: Session, server_id: int, user_id: int) -> None:
    user = (
        db.query(PostgresUser)
        .filter(PostgresUser.server_id == server_id, PostgresUser.id == user_id)
        .first()
    )
    if not user:
        raise ValueError("Datenbank-User wurde fuer diesen Server nicht gefunden.")
    _drop_database_and_roles(db, server_id, [], [], [user.username])
    db.delete(user)
    db.commit()


def _drop_database_and_roles(
    db: Session,
    server_id: int,
    databases: list[str],
    owners: list[str],
    users: list[str],
) -> None:
    if not databases and not owners and not users:
        return
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_drop(
            {
                "admin_password": _admin_password(),
                "databases": databases,
                "owners": owners,
                "users": users,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Drop failed") from exc


def drop_server_resources(db: Session, server_id: int) -> None:
    resources = list_resources(db, server_id)
    databases = [item.name for item in resources["databases"]]
    owners = [item.owner_role for item in resources["databases"]]
    users = [item.username for item in resources["users"]]
    if databases or owners or users:
        _drop_database_and_roles(db, server_id, databases, owners, users)
    db.query(PostgresGrant).filter(PostgresGrant.server_id == server_id).delete(
        synchronize_session=False
    )
    db.query(PostgresUser).filter(PostgresUser.server_id == server_id).delete(
        synchronize_session=False
    )
    db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).delete(
        synchronize_session=False
    )
    db.commit()


def list_tables(db: Session, server_id: int, database_id: int) -> list[dict[str, Any]]:
    database = _database_row(db, server_id, database_id)
    result = _owner_query(db, server_id, database, "list_tables")
    return result if isinstance(result, list) else []


def database_stats(db: Session, server_id: int, database_id: int) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    result = _owner_query(db, server_id, database, "stats")
    return result if isinstance(result, dict) else {}


def describe_table(
    db: Session,
    server_id: int,
    database_id: int,
    schema_name: str,
    table_name: str,
) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    return _owner_query(
        db,
        server_id,
        database,
        "describe_table",
        schema_name=schema_name or "public",
        table_name=table_name,
    )


def create_table(
    db: Session,
    server_id: int,
    database_id: int,
    schema_name: str,
    table_name: str,
    columns: list[dict[str, Any]],
) -> None:
    database = _database_row(db, server_id, database_id)
    _owner_query(
        db,
        server_id,
        database,
        "create_table",
        schema_name=schema_name or "public",
        table_name=table_name,
        columns=columns,
    )


def drop_table(
    db: Session,
    server_id: int,
    database_id: int,
    schema_name: str,
    table_name: str,
) -> None:
    database = _database_row(db, server_id, database_id)
    _owner_query(
        db,
        server_id,
        database,
        "drop_table",
        schema_name=schema_name or "public",
        table_name=table_name,
    )


def read_rows(
    db: Session,
    server_id: int,
    database_id: int,
    schema_name: str,
    table_name: str,
    limit: int,
    offset: int,
    search: str | None = None,
) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    return _owner_query(
        db,
        server_id,
        database,
        "read_rows",
        schema_name=schema_name or "public",
        table_name=table_name,
        limit=limit,
        offset=offset,
        search=search,
    )


def _split_sql_statements(text: str) -> list[str]:
    """Split a SQL script into individual statements (panel_database_service + tests)."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    in_single = in_double = in_line_comment = in_block_comment = False
    dollar_tag: str | None = None
    paren_depth = 0

    def flush() -> None:
        stmt = "".join(buf).strip()
        while stmt:
            if stmt.startswith("--"):
                nl = stmt.find("\n")
                if nl == -1:
                    stmt = ""
                    break
                stmt = stmt[nl + 1 :].lstrip()
                continue
            if stmt.startswith("/*"):
                end = stmt.find("*/")
                if end == -1:
                    stmt = ""
                    break
                stmt = stmt[end + 2 :].lstrip()
                continue
            break
        if stmt:
            statements.append(stmt)
        buf.clear()

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue
        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue
        if dollar_tag is not None:
            buf.append(ch)
            if text.startswith(dollar_tag, i):
                buf.extend(dollar_tag[1:])
                i += len(dollar_tag)
                dollar_tag = None
            else:
                i += 1
            continue
        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue
        if ch == "$":
            j = i + 1
            if j < n and text[j] == "$":
                dollar_tag = "$$"
                buf.append("$$")
                i = j + 1
                continue
            tag_start = j
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            if j < n and text[j] == "$" and j > tag_start:
                dollar_tag = text[i : j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            paren_depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            buf.append(ch)
            i += 1
            continue
        if ch == ";" and paren_depth == 0:
            flush()
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return statements


def _is_read_only(stmt: str) -> bool:
    stripped = stmt.lstrip()
    if not stripped:
        return True
    while stripped.startswith("--") or stripped.startswith("/*"):
        if stripped.startswith("--"):
            nl = stripped.find("\n")
            stripped = stripped[nl + 1 :].lstrip() if nl != -1 else ""
        else:
            end = stripped.find("*/")
            stripped = stripped[end + 2 :].lstrip() if end != -1 else ""
    tokens = stripped.split()
    if not tokens:
        return True
    head = tokens[0].lower()
    if head == "explain":
        if len(tokens) >= 2 and tokens[1].lower().split("(")[0] in _WRITE_KEYWORDS:
            return False
        return True
    return head not in _WRITE_KEYWORDS


def execute_sql(
    db: Session, server_id: int, database_id: int, statement: str, limit: int
) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    return _owner_query(
        db,
        server_id,
        database,
        "execute_sql",
        sql=statement,
        limit=limit,
    )


def _validate_extension_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    if not IDENTIFIER_RE.fullmatch(cleaned):
        raise ValueError("Ungueltiger Extension-Name.")
    if cleaned not in settings.trusted_postgres_extensions:
        raise ValueError(f"Extension '{cleaned}' ist nicht erlaubt.")
    return cleaned


def list_extensions(db: Session, server_id: int, database_id: int) -> list[dict[str, Any]]:
    database = _database_row(db, server_id, database_id)
    result = _owner_query(db, server_id, database, "list_extensions")
    return result if isinstance(result, list) else []


def install_extension(db: Session, server_id: int, database_id: int, name: str) -> None:
    database = _database_row(db, server_id, database_id)
    ext = _validate_extension_name(name)
    _owner_query(db, server_id, database, "install_extension", name=ext)


def drop_extension(db: Session, server_id: int, database_id: int, name: str) -> None:
    database = _database_row(db, server_id, database_id)
    ext = _validate_extension_name(name)
    _owner_query(db, server_id, database, "drop_extension", name=ext)


def promote_owner_to_superuser(db: Session, server_id: int, database_id: int) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    if database.is_superuser:
        raise ValueError("Owner-Rolle hat bereits Superuser-Rechte.")
    new_password = _generate_password()
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_promote(
            {
                "admin_password": _admin_password(),
                "owner_role": database.owner_role,
                "new_password": new_password,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Promote failed") from exc
    database.is_superuser = True
    database.owner_password_encrypted = AuthService.encrypt_secret(
        new_password, aad="msm:pg:db:owner"
    )
    database.power_credentials_issued_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "username": database.owner_role,
        "password": new_password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
        "database_name": database.name,
    }


def rotate_power_user_password(db: Session, server_id: int, database_id: int) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    if not database.is_superuser:
        raise ValueError(
            "Owner-Rolle ist kein Superuser -- erst promote_owner_to_superuser aufrufen."
        )
    new_password = _generate_password()
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_rotate_owner(
            {
                "admin_password": _admin_password(),
                "owner_role": database.owner_role,
                "new_password": new_password,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Rotate owner failed") from exc
    database.owner_password_encrypted = AuthService.encrypt_secret(
        new_password, aad="msm:pg:db:owner"
    )
    database.power_credentials_issued_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "username": database.owner_role,
        "password": new_password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
        "database_name": database.name,
    }


def demote_owner_from_superuser(db: Session, server_id: int, database_id: int) -> None:
    database = _database_row(db, server_id, database_id)
    if not database.is_superuser:
        raise ValueError("Owner-Rolle ist kein Superuser.")
    client = _client_for_server_id(db, server_id)
    try:
        client.postgres_demote(
            {
                "admin_password": _admin_password(),
                "owner_role": database.owner_role,
            }
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Demote failed") from exc
    database.is_superuser = False
    database.power_credentials_issued_at = None
    db.commit()


def _server_database_names(db: Session, server_id: int) -> list[str]:
    rows = (
        db.query(PostgresDatabase.name)
        .filter(PostgresDatabase.server_id == server_id)
        .order_by(PostgresDatabase.name.asc())
        .all()
    )
    return [r[0] for r in rows]


def _restore_owners(db: Session, server_id: int) -> dict[str, dict[str, str]]:
    rows = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).all()
    return {
        row.name: {
            "owner_role": row.owner_role,
            "owner_password": _owner_password(row),
        }
        for row in rows
    }


def dump_server_databases(db: Session, server_id: int) -> tuple[str, list[str], int, str, int]:
    sql_text, names, size, sha, dur = _pg_dump_server_dbs(db, server_id)
    return sql_text, names, size, sha, dur


def _pg_dump_server_dbs(db: Session, server_id: int) -> tuple[str, list[str], int, str, int]:
    db_names = _server_database_names(db, server_id)
    if not db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")
    started = time.monotonic()
    client = _client_for_server_id(db, server_id)
    try:
        resp = client.postgres_dump(
            admin_password=_admin_password(), database_names=db_names
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "pg_dump failed") from exc
    dumps = resp.get("dumps") or {}
    out_parts: list[str] = [
        "-- MSM Postgres Dump\n",
        f"-- Server ID: {server_id}\n",
        f"-- Databases: {', '.join(db_names)}\n",
        f"-- Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
        "-- Format: pg_dump --format=plain --no-owner --no-acl --clean (RESTORE via psql)\n",
        "\n",
    ]
    for db_name in db_names:
        body = dumps.get(db_name) or ""
        if not body.strip():
            continue
        out_parts.append(f"\n-- ===== Database: {db_name} =====\n")
        out_parts.append(body)
        if not body.endswith("\n"):
            out_parts.append("\n")
    sql_text = "".join(out_parts)
    raw_bytes = sql_text.encode("utf-8")
    sha = hashlib.sha256(raw_bytes).hexdigest()
    duration_ms = int((time.monotonic() - started) * 1000)
    return sql_text, db_names, len(raw_bytes), sha, duration_ms


def restore_sql_to_server_dbs(db: Session, server_id: int, sql_text: str) -> dict[str, Any]:
    db_names = _server_database_names(db, server_id)
    if not db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")
    dumps = {name: sql_text for name in db_names}
    client = _client_for_server_id(db, server_id)
    try:
        result = client.postgres_restore(
            admin_password=_admin_password(),
            dumps=dumps,
            owners=_restore_owners(db, server_id),
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "Restore failed") from exc
    return {
        "ok": True,
        "databases": result.get("databases") or db_names,
        "bytes": len(sql_text.encode("utf-8")),
        "duration_ms": result.get("duration_ms") or 0,
    }


def backup_pg_dump_for_archive(db: Session, server_id: int) -> dict[str, bytes]:
    db_names = _server_database_names(db, server_id)
    if not db_names:
        return {}
    client = _client_for_server_id(db, server_id)
    try:
        resp = client.postgres_dump(
            admin_password=_admin_password(), database_names=db_names
        )
    except NodeClientError as exc:
        raise PostgresServiceError(exc.message or "pg_dump failed") from exc
    dumps = resp.get("dumps") or {}
    result: dict[str, bytes] = {}
    for name in db_names:
        body = dumps.get(name) or ""
        result[name] = body.encode("utf-8") if body else b""
    return result


def restore_pg_dump_from_archive(
    db: Session, server_id: int, dumps: dict[str, bytes]
) -> dict[str, Any]:
    if not dumps:
        return {
            "ok": True,
            "skipped": True,
            "reason": "Backup enthaelt keinen Postgres-Dump",
        }
    server_db_names = set(_server_database_names(db, server_id))
    if not server_db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")

    started = time.monotonic()
    text_dumps: dict[str, str] = {}
    restored: list[str] = []

    for db_name, sql_bytes in dumps.items():
        if db_name == "_legacy":
            sql_text = sql_bytes.decode("utf-8", errors="replace")
            for target_name in server_db_names:
                text_dumps[target_name] = sql_text
                restored.append(target_name)
            continue
        if db_name not in server_db_names:
            logger.warning(
                "Restore: Dump fuer DB '%s' existiert nicht mehr im Server %s — uebersprungen",
                db_name,
                server_id,
            )
            continue
        if not sql_bytes.strip():
            restored.append(db_name)
            continue
        text_dumps[db_name] = sql_bytes.decode("utf-8", errors="replace")
        restored.append(db_name)

    if text_dumps:
        client = _client_for_server_id(db, server_id)
        try:
            client.postgres_restore(
                admin_password=_admin_password(),
                dumps=text_dumps,
                owners=_restore_owners(db, server_id),
            )
        except NodeClientError as exc:
            raise PostgresServiceError(exc.message or "Restore failed") from exc

    return {
        "ok": True,
        "databases": restored,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
