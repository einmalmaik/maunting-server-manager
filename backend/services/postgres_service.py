from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from sqlalchemy.orm import Session

from config import settings
from models import PostgresDatabase, PostgresGrant, PostgresUser, Server
from services import docker_service
from services.auth_service import AuthService
from services.docker_service import PortPublish, VolumeBind
from services.panel_settings_service import PanelSettingsService

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
    encrypted = AuthService.encrypt_2fa_secret(password)
    PanelSettingsService.set(ADMIN_PASSWORD_KEY, encrypted)
    return encrypted


def _admin_password() -> str:
    return AuthService.decrypt_2fa_secret(_encrypted_admin_password())


def _db_host() -> str:
    host = (settings.managed_postgres_host or "").strip()
    if host != "127.0.0.1":
        raise PostgresServiceError("Managed PostgreSQL darf nur an 127.0.0.1 gebunden werden.")
    return host


def _admin_connect(database: str = CONTROL_DB):
    # ISOLATION_LEVEL_AUTOCOMMIT: CREATE DATABASE / CREATE ROLE muessen ausserhalb einer
    # Transaktion laufen. NICHT als context manager verwenden -- psycopg2's __enter__()
    # sendet sonst implizit BEGIN, und ein danach gesetztes autocommit wirkt nicht mehr.
    conn = psycopg2.connect(
        host=_db_host(),
        port=settings.managed_postgres_port,
        dbname=database,
        user=ADMIN_USER,
        password=_admin_password(),
        connect_timeout=5,
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _owner_connect(database: PostgresDatabase):
    return psycopg2.connect(
        host=_db_host(),
        port=settings.managed_postgres_port,
        dbname=database.name,
        user=database.owner_role,
        password=AuthService.decrypt_2fa_secret(database.owner_password_encrypted),
        connect_timeout=5,
    )


def _execute_admin(statement: Any, params: tuple[Any, ...] = (), database: str = CONTROL_DB) -> None:
    conn = _admin_connect(database)
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
    finally:
        conn.close()


def ensure_internal_postgres() -> None:
    network_result = docker_service.ensure_network(settings.managed_postgres_network, internal=True)
    if not network_result.get("ok"):
        raise PostgresServiceError(network_result.get("error") or "PostgreSQL-Netz konnte nicht erstellt werden.")

    os.makedirs(settings.managed_postgres_data_dir, exist_ok=True)
    _encrypted_admin_password()

    state = docker_service.inspect_state(settings.managed_postgres_container_name)
    if state and state.get("status") == "running":
        return

    result = docker_service.run_container(
        name=settings.managed_postgres_container_name,
        image=settings.managed_postgres_image,
        env={
            "POSTGRES_USER": ADMIN_USER,
            "POSTGRES_PASSWORD": _admin_password(),
            "POSTGRES_DB": CONTROL_DB,
        },
        ports=[
            PortPublish(
                host_port=settings.managed_postgres_port,
                container_port=5432,
                protocol="tcp",
                host_ip=_db_host(),
            )
        ],
        volumes=[VolumeBind(settings.managed_postgres_data_dir, "/var/lib/postgresql/data", read_only=False)],
        read_only_rootfs=False,
        # Container haengt an zwei Netzen: default-bridge fuer das host-loopback-Binding
        # 127.0.0.1:<port> (Panel-Backend verbindet sich via psycopg2) UND
        # msm-internal fuer DNS-aufloesbaren Zugriff aus Game-Server-Containern
        # ("msm-postgres:5432"). msm-internal ist internal=True und hat damit keinen
        # externen Ingress - die 127.0.0.1-Binding bleibt der einzige externe Pfad.
        extra_networks=[settings.managed_postgres_network],
        startup_check_seconds=2.0,
        # Postgres-Entrypoint braucht CHOWN/FOWNER fuer initdb-chown der data-Files
        # und SETUID/SETGID fuer den Wechsel auf den postgres-User.
        # DAC_OVERRIDE/DAC_READ_SEARCH ermoeglichen Zugriff auf Files mit 0700/0600.
        cap_adds=["CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE", "DAC_READ_SEARCH"],
    )
    if not result.get("ok"):
        raise PostgresServiceError(result.get("error") or "PostgreSQL-Container konnte nicht gestartet werden.")


def server_extra_networks(db: Session, server_id: int) -> list[str]:
    exists = db.query(PostgresDatabase.id).filter(PostgresDatabase.server_id == server_id).first()
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
        "databases": db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).order_by(PostgresDatabase.id).all(),
        "users": db.query(PostgresUser).filter(PostgresUser.server_id == server_id).order_by(PostgresUser.id).all(),
    }


def provision_server_databases(db: Session, server: Server, count: int) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("Mindestens eine PostgreSQL-Datenbank ist erforderlich.")
    ensure_internal_postgres()
    credentials: list[dict[str, Any]] = []
    try:
        existing = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server.id).count()
        for offset in range(1, count + 1):
            db_name, owner_role, user_name = _next_names(server.id, existing + offset)
            credentials.append(_create_database_and_user(db, server.id, db_name, owner_role, user_name))
        db.commit()
        return credentials
    except Exception:
        db.rollback()
        try:
            drop_server_resources(db, server.id)
        except Exception:
            db.rollback()
        raise


def _create_database_and_user(
    db: Session,
    server_id: int,
    db_name: str,
    owner_role: str,
    user_name: str,
) -> dict[str, Any]:
    db_name = _validate_identifier(db_name)
    owner_role = _validate_identifier(owner_role)
    user_name = _validate_identifier(user_name)
    owner_password = _generate_password()
    user_password = _generate_password()

    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(owner_role)
                ),
                (owner_password,),
            )
            conn.commit()
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name), sql.Identifier(owner_role)))
            conn.commit()
            # GRANT CREATE auf die DB: der Owner darf damit "trusted" Extensions installieren
            # (pgcrypto, pg_trgm, citext, ...). Nicht-trusted Extensions (postgis etc.)
            # bleiben unerreichbar, weil weder Owner noch User Superuser sind.
            cur.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db_name), sql.Identifier(owner_role)))
            conn.commit()
            cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name)))
            conn.commit()
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(user_name)
                ),
                (user_password,),
            )
            conn.commit()
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db_name), sql.Identifier(user_name)))
            conn.commit()
    finally:
        conn.close()

    conn = _admin_connect(db_name)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(user_name)))
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(
                    sql.Identifier(owner_role), sql.Identifier(user_name)
                )
            )
            cur.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(
                    sql.Identifier(owner_role), sql.Identifier(user_name)
                )
            )
    finally:
        conn.close()

    database = PostgresDatabase(
        server_id=server_id,
        name=db_name,
        owner_role=owner_role,
        owner_password_encrypted=AuthService.encrypt_2fa_secret(owner_password),
    )
    user = PostgresUser(server_id=server_id, username=user_name, password_mask=_mask_secret(user_password))
    db.add(database)
    db.add(user)
    db.flush()
    db.add(PostgresGrant(server_id=server_id, database_id=database.id, user_id=user.id, privilege="read_write"))
    db.flush()
    return {
        "database_id": database.id,
        "database_name": db_name,
        "username": user_name,
        "password": user_password,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def create_database(db: Session, server_id: int, name: str | None = None) -> dict[str, Any]:
    ensure_internal_postgres()
    next_index = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).count() + 1
    generated_db, generated_owner, generated_user = _next_names(server_id, next_index)
    db_name = _validate_identifier(name or generated_db)
    credential = _create_database_and_user(db, server_id, db_name, generated_owner, generated_user)
    db.commit()
    return credential


def create_user(db: Session, server_id: int, database_id: int, username: str | None = None) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    next_index = db.query(PostgresUser).filter(PostgresUser.server_id == server_id).count() + 1
    user_name = _validate_identifier(username or f"msm_s{server_id}_u{next_index}")
    password = _generate_password()
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(user_name)
                ),
                (password,),
            )
            conn.commit()
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database.name), sql.Identifier(user_name)))
            conn.commit()
    finally:
        conn.close()
    conn = _admin_connect(database.name)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(sql.Identifier(user_name)))
            cur.execute(
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(user_name)
                )
            )
            cur.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(user_name)
                )
            )
    finally:
        conn.close()
    user = PostgresUser(server_id=server_id, username=user_name, password_mask=_mask_secret(password))
    db.flush()
    db.add(PostgresGrant(server_id=server_id, database_id=database.id, user_id=user.id, privilege="read_write"))
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
    user = db.query(PostgresUser).filter(PostgresUser.server_id == server_id, PostgresUser.id == user_id).first()
    if not user:
        raise ValueError("Datenbank-User wurde fuer diesen Server nicht gefunden.")
    password = _generate_password()
    _execute_admin(
        sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(user.username)),
        (password,),
    )
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
    _drop_database_and_roles([database.name], [database.owner_role], [user.username for user in users])
    for user in users:
        db.delete(user)
    db.delete(database)
    db.commit()


def delete_user(db: Session, server_id: int, user_id: int) -> None:
    user = db.query(PostgresUser).filter(PostgresUser.server_id == server_id, PostgresUser.id == user_id).first()
    if not user:
        raise ValueError("Datenbank-User wurde fuer diesen Server nicht gefunden.")
    _drop_database_and_roles([], [], [user.username])
    db.delete(user)
    db.commit()


def _drop_database_and_roles(databases: list[str], owners: list[str], users: list[str]) -> None:
    ensure_internal_postgres()
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            for database in databases:
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (database,))
                conn.commit()
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
                conn.commit()
            for role in users + owners:
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
                conn.commit()
    finally:
        conn.close()


def drop_server_resources(db: Session, server_id: int) -> None:
    resources = list_resources(db, server_id)
    databases = [item.name for item in resources["databases"]]
    owners = [item.owner_role for item in resources["databases"]]
    users = [item.username for item in resources["users"]]
    if databases or owners or users:
        _drop_database_and_roles(databases, owners, users)
    db.query(PostgresGrant).filter(PostgresGrant.server_id == server_id).delete(synchronize_session=False)
    db.query(PostgresUser).filter(PostgresUser.server_id == server_id).delete(synchronize_session=False)
    db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id).delete(synchronize_session=False)
    db.commit()


def list_tables(db: Session, server_id: int, database_id: int) -> list[dict[str, Any]]:
    database = _database_row(db, server_id, database_id)
    with _owner_connect(database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY table_schema, table_name
                """
            )
            return [{"schema": row[0], "name": row[1]} for row in cur.fetchall()]


def create_table(db: Session, server_id: int, database_id: int, schema_name: str, table_name: str, columns: list[dict[str, Any]]) -> None:
    database = _database_row(db, server_id, database_id)
    schema_name = _validate_identifier(schema_name or "public")
    table_name = _validate_identifier(table_name)
    if not columns:
        raise ValueError("Mindestens eine Spalte ist erforderlich.")
    column_sql = []
    for column in columns:
        name = _validate_identifier(str(column.get("name") or ""))
        type_key = str(column.get("type") or "").lower()
        if type_key not in ALLOWED_COLUMN_TYPES:
            raise ValueError("Ungueltiger Spaltentyp.")
        part = sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(ALLOWED_COLUMN_TYPES[type_key]))
        if column.get("primary_key"):
            part = part + sql.SQL(" PRIMARY KEY")
        if column.get("not_null"):
            part = part + sql.SQL(" NOT NULL")
        column_sql.append(part)
    with _owner_connect(database) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))
            cur.execute(
                sql.SQL("CREATE TABLE {}.{} ({})").format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table_name),
                    sql.SQL(", ").join(column_sql),
                )
            )


def drop_table(db: Session, server_id: int, database_id: int, schema_name: str, table_name: str) -> None:
    database = _database_row(db, server_id, database_id)
    with _owner_connect(database) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TABLE {}.{}").format(
                    sql.Identifier(_validate_identifier(schema_name or "public")),
                    sql.Identifier(_validate_identifier(table_name)),
                )
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
    limit = min(max(limit, 1), settings.managed_postgres_row_limit)
    offset = max(offset, 0)
    schema_name = _validate_identifier(schema_name or "public")
    table_name = _validate_identifier(table_name)
    with _owner_connect(database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            )
            columns = [row[0] for row in cur.fetchall()]
            if not columns:
                raise ValueError("Tabelle wurde nicht gefunden.")
            query = sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema_name), sql.Identifier(table_name))
            params: list[Any] = []
            if search:
                like = f"%{search[:128]}%"
                clauses = [sql.SQL("CAST({} AS TEXT) ILIKE %s").format(sql.Identifier(column)) for column in columns]
                query += sql.SQL(" WHERE ") + sql.SQL(" OR ").join(clauses)
                params.extend([like] * len(columns))
            query += sql.SQL(" LIMIT %s OFFSET %s")
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            rows = [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
            return {"columns": columns, "rows": rows, "limit": limit, "offset": offset}


def _split_sql_statements(text: str) -> list[str]:
    """Split a SQL script into individual statements.

    Respectiert:
    - Single-Quoted-String-Literale ('...'' mit '' als Escape)
    - Double-Quoted-Identifier ("...")
    - Dollar-Quoted-Strings ($$...$$ oder $tag$...$tag$)
    - Line-Comments (-- ...) und Block-Comments (/* ... */)
    - Leere Statements werden uebersprungen
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(text)
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag: str | None = None
    paren_depth = 0

    def flush() -> None:
        stmt = "".join(buf).strip()
        # Fuehrende Line- und Block-Kommentare entfernen, damit Statements wie
        # "-- Kommentar\nSELECT 1" als reines "SELECT 1" in der Liste landen.
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
                # '' ist Escape in Postgres, bleibt im String
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
        # Normal-Modus
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
            # Versuche einen Dollar-Tag zu lesen: $[A-Za-z_][A-Za-z0-9_]*$ | $$
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

    # Letztes Statement (ohne schliessendes ;)
    flush()
    return statements


_WRITE_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "vacuum",
    "analyze",
    "cluster",
    "reindex",
    "set",
    "reset",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "lock",
    "call",
    "do",
    "notify",
    "listen",
    "unlisten",
    "refresh",
    "checkpoint",
)


def _is_read_only(stmt: str) -> bool:
    """Heuristik: ist ein Statement read-only (SELECT/WITH/SHOW/EXPLAIN/VALUES)?

    EXPLAIN wird als read-only behandelt, sofern das gewrappte Statement selbst
    read-only ist (sonst kann EXPLAIN ANALYZE tatsaechlich schreiben).
    """
    stripped = stmt.lstrip()
    if not stripped:
        return True
    # Kommentar-Präfixe entfernen
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
        # Heuristik: zweites Wort gibt das gewrappte Statement an. Ist dieses
        # ein Write-Keyword (INSERT/UPDATE/DELETE/...), dann ist der Explain-Write
        # ebenfalls ein Write.
        if len(tokens) >= 2 and tokens[1].lower().split("(")[0] in _WRITE_KEYWORDS:
            return False
        return True
    return head not in _WRITE_KEYWORDS


def execute_sql(db: Session, server_id: int, database_id: int, statement: str, limit: int) -> dict[str, Any]:
    """Multi-Statement SQL execution, similar to psql.

    Returns per-statement results plus total duration and the applied
    statement_timeout. Read-only scripts run in a READ ONLY transaction;
    any write keyword switches to the default read/write mode.
    """
    database = _database_row(db, server_id, database_id)
    cleaned = (statement or "").strip()
    if not cleaned:
        raise ValueError("SQL darf nicht leer sein.")
    statements = _split_sql_statements(cleaned)
    if not statements:
        raise ValueError("Keine ausfuehrbaren SQL-Statements gefunden.")
    row_limit = min(max(limit, 1), settings.managed_postgres_row_limit)
    timeout_ms = settings.managed_postgres_statement_timeout_ms
    has_write = any(not _is_read_only(s) for s in statements)

    results: list[dict[str, Any]] = []
    import time as _time

    conn = _owner_connect(database)
    try:
        if has_write:
            # Schreib-Statements in normaler Transaktion; bei Fehler Rollback
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (timeout_ms,))
                for stmt in statements:
                    start = _time.monotonic()
                    entry: dict[str, Any] = {
                        "statement": stmt,
                        "columns": [],
                        "rows": [],
                        "row_count": None,
                        "status": None,
                        "error": None,
                        "duration_ms": None,
                    }
                    try:
                        cur.execute(stmt)
                        if cur.description:
                            entry["columns"] = [desc[0] for desc in cur.description]
                            entry["rows"] = [
                                dict(zip(entry["columns"], row, strict=False))
                                for row in cur.fetchmany(row_limit)
                            ]
                        entry["row_count"] = cur.rowcount
                        entry["status"] = cur.statusmessage
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                        results.append(entry)
                        break
                    else:
                        entry["duration_ms"] = int((_time.monotonic() - start) * 1000)
                        results.append(entry)
                conn.commit()
        else:
            # Read-only-Statements in einer einzigen READ ONLY Transaktion
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (timeout_ms,))
                for stmt in statements:
                    start = _time.monotonic()
                    entry = {
                        "statement": stmt,
                        "columns": [],
                        "rows": [],
                        "row_count": None,
                        "status": None,
                        "error": None,
                        "duration_ms": None,
                    }
                    try:
                        cur.execute(stmt)
                        if cur.description:
                            entry["columns"] = [desc[0] for desc in cur.description]
                            entry["rows"] = [
                                dict(zip(entry["columns"], row, strict=False))
                                for row in cur.fetchmany(row_limit)
                            ]
                        entry["row_count"] = cur.rowcount
                        entry["status"] = cur.statusmessage
                    except Exception as exc:  # noqa: BLE001
                        conn.rollback()
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                        results.append(entry)
                        break
                    else:
                        entry["duration_ms"] = int((_time.monotonic() - start) * 1000)
                        results.append(entry)
                conn.commit()
    finally:
        conn.close()

    return {
        "statements": results,
        "total_duration_ms": sum((r.get("duration_ms") or 0) for r in results),
        "statement_timeout_ms": timeout_ms,
    }


def _validate_extension_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    if not IDENTIFIER_RE.fullmatch(cleaned):
        raise ValueError("Ungueltiger Extension-Name.")
    if cleaned not in settings.trusted_postgres_extensions:
        raise ValueError(f"Extension '{cleaned}' ist nicht erlaubt.")
    return cleaned


def list_extensions(db: Session, server_id: int, database_id: int) -> list[dict[str, Any]]:
    database = _database_row(db, server_id, database_id)
    conn = _owner_connect(database)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.extname, e.extversion, c.relname IS NOT NULL AS is_default
                FROM pg_extension e
                LEFT JOIN pg_depend d ON d.objid = e.oid AND d.deptype = 'e'
                LEFT JOIN pg_class c ON c.oid = d.refobjid AND c.relname = 'pg_available_extensions'
                ORDER BY e.extname
                """
            )
            return [
                {"name": name, "version": version, "trusted": bool(is_default)}
                for name, version, is_default in cur.fetchall()
            ]
    finally:
        conn.close()


def install_extension(db: Session, server_id: int, database_id: int, name: str) -> None:
    database = _database_row(db, server_id, database_id)
    ext = _validate_extension_name(name)
    conn = _owner_connect(database)
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext)))
        conn.commit()
    finally:
        conn.close()


def drop_extension(db: Session, server_id: int, database_id: int, name: str) -> None:
    database = _database_row(db, server_id, database_id)
    ext = _validate_extension_name(name)
    conn = _owner_connect(database)
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP EXTENSION IF EXISTS {}").format(sql.Identifier(ext)))
        conn.commit()
    finally:
        conn.close()
