from __future__ import annotations

import logging
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


def _db_host() -> str:
    host = (settings.managed_postgres_host or "").strip()
    if host != "127.0.0.1":
        raise PostgresServiceError("Managed PostgreSQL darf nur an 127.0.0.1 gebunden werden.")
    return host


def _admin_connect(database: str = CONTROL_DB):
    # ISOLATION_LEVEL_AUTOCOMMIT: CREATE DATABASE / CREATE ROLE muessen ausserhalb einer
    # Transaktion laufen. NICHT als context manager verwenden -- psycopg2's __enter__()
    # sendet sonst implizit BEGIN, und ein danach gesetztes autocommit wirkt nicht mehr.
    ensure_internal_postgres()
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
    ensure_internal_postgres()
    return psycopg2.connect(
        host=_db_host(),
        port=settings.managed_postgres_port,
        dbname=database.name,
        user=database.owner_role,
        password=AuthService.decrypt_secret(database.owner_password_encrypted, aad="msm:pg:db:owner"),
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

    container_name = settings.managed_postgres_container_name
    state = docker_service.inspect_state(container_name)
    if state and state.get("status") == "running":
        docker_service.ensure_restart_policy(container_name, "unless-stopped")
        return

    if state and state.get("status") in {"exited", "created", "paused"}:
        start_result = docker_service.start(container_name)
        if not start_result.get("ok"):
            raise PostgresServiceError(
                start_result.get("error") or "PostgreSQL-Container konnte nicht gestartet werden."
            )
        docker_service.ensure_restart_policy(container_name, "unless-stopped")
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
        restart_policy_name="unless-stopped",
    )
    if not result.get("ok"):
        raise PostgresServiceError(result.get("error") or "PostgreSQL-Container konnte nicht gestartet werden.")
    docker_service.ensure_restart_policy(container_name, "unless-stopped")


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


def provision_server_databases(
    db: Session,
    server: Server,
    count: int,
    *,
    power_user: bool = False,
) -> list[dict[str, Any]]:
    if count < 1:
        raise ValueError("Mindestens eine PostgreSQL-Datenbank ist erforderlich.")
    ensure_internal_postgres()
    credentials: list[dict[str, Any]] = []
    try:
        existing = db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server.id).count()
        for offset in range(1, count + 1):
            db_name, owner_role, user_name = _next_names(server.id, existing + offset)
            credentials.append(
                _create_database_and_user(
                    db, server.id, db_name, owner_role, user_name, power_user=power_user
                )
            )
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
    *,
    power_user: bool = False,
) -> dict[str, Any]:
    db_name = _validate_identifier(db_name)
    owner_role = _validate_identifier(owner_role)
    user_name = _validate_identifier(user_name)
    owner_password = _generate_password()
    user_password = _generate_password()

    # Power-User-Modus: Owner-Rolle bekommt Postgres-SUPERUSER. Damit kann der
    # MSM-Server-Owner via psql/Migration-Tool DDL auf Rollen-Ebene machen
    # (z. B. CREATE ROLE fuer Discord-Bot-Global-Roles, GRANT auf andere Rollen).
    # Postgresische Superuser sind system-global -- die Credentials werden NICHT
    # persistiert (One-Time), und die Rolle wird beim DB-Drop mit aufgeraeumt.
    owner_create_stmt = (
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s SUPERUSER").format(sql.Identifier(owner_role))
        if power_user
        else sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE"
        ).format(sql.Identifier(owner_role))
    )

    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(owner_create_stmt, (owner_password,))
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
        owner_password_encrypted=AuthService.encrypt_secret(owner_password, aad="msm:pg:db:owner"),
        is_superuser=power_user,
        power_credentials_issued_at=datetime.now(timezone.utc) if power_user else None,
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
        "is_superuser": power_user,
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
                SELECT n.nspname,
                       c.relname,
                       GREATEST(c.reltuples::bigint, 0) AS row_estimate,
                       pg_total_relation_size(c.oid) AS size_bytes
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY n.nspname, c.relname
                """
            )
            return [
                {"schema": row[0], "name": row[1], "row_estimate": row[2], "size_bytes": row[3]}
                for row in cur.fetchall()
            ]


def database_stats(db: Session, server_id: int, database_id: int) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    import time as _time

    started = _time.monotonic()
    with _owner_connect(database) as conn:
        latency_ms = int((_time.monotonic() - started) * 1000)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  pg_database_size(current_database()) AS size_bytes,
                  (SELECT count(*)
                     FROM information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')) AS table_count,
                  (SELECT count(*)
                     FROM pg_stat_activity
                    WHERE datname = current_database()) AS active_connections,
                  current_setting('max_connections')::int AS max_connections,
                  current_database() AS database_name
                """
            )
            row = cur.fetchone()
    return {
        "status": "healthy",
        "latency_ms": latency_ms,
        "size_bytes": row[0],
        "table_count": row[1],
        "active_connections": row[2],
        "max_connections": row[3],
        "database_name": row[4],
        "engine": "PostgreSQL",
    }


def describe_table(db: Session, server_id: int, database_id: int, schema_name: str, table_name: str) -> dict[str, Any]:
    database = _database_row(db, server_id, database_id)
    schema_name = _validate_identifier(schema_name or "public")
    table_name = _validate_identifier(table_name)
    with _owner_connect(database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            )
            columns = [
                {"name": row[0], "data_type": row[1], "nullable": row[2] == "YES", "default": row[3]}
                for row in cur.fetchall()
            ]
            if not columns:
                raise ValueError("Tabelle wurde nicht gefunden.")
            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s
                ORDER BY indexname
                """,
                (schema_name, table_name),
            )
            indexes = [{"name": row[0], "definition": row[1]} for row in cur.fetchall()]
            cur.execute(
                """
                SELECT tc.constraint_name, kcu.column_name, ccu.table_name, ccu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = %s
                  AND tc.table_name = %s
                ORDER BY tc.constraint_name
                """,
                (schema_name, table_name),
            )
            foreign_keys = [
                {"name": row[0], "column_name": row[1], "foreign_table": row[2], "foreign_column": row[3]}
                for row in cur.fetchall()
            ]
            cur.execute(
                """
                SELECT pg_total_relation_size(%s::regclass),
                       GREATEST(c.reltuples::bigint, 0)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                """,
                (f"{schema_name}.{table_name}", schema_name, table_name),
            )
            size_row = cur.fetchone()
    return {
        "schema": schema_name,
        "name": table_name,
        "columns": columns,
        "indexes": indexes,
        "foreign_keys": foreign_keys,
        "size_bytes": size_row[0] if size_row else None,
        "row_estimate": size_row[1] if size_row else None,
    }


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



def promote_owner_to_superuser(db: Session, server_id: int, database_id: int) -> dict[str, Any]:
    """Promote the existing DB owner role to SUPERUSER and rotate its password.

    Used to upgrade an already-provisioned database (created without the
    power_user flag) so the MSM server owner can do migrations that need
    role-level DDL (CREATE ROLE, GRANT on system catalogs, ...).

    Returns the new one-time password. The panel does NOT persist it.
    """
    database = _database_row(db, server_id, database_id)
    if database.is_superuser:
        raise ValueError("Owner-Rolle hat bereits Superuser-Rechte.")
    new_password = _generate_password()
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH SUPERUSER LOGIN PASSWORD %s").format(
                    sql.Identifier(database.owner_role)
                ),
                (new_password,),
            )
        conn.commit()
    finally:
        conn.close()
    database.is_superuser = True
    database.owner_password_encrypted = AuthService.encrypt_secret(new_password, aad="msm:pg:db:owner")
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
    """Rotate the password of an already-superuser owner role.

    Returns the new one-time password. Use this when the old password was
    forgotten or compromised; the role keeps its SUPERUSER attribute.
    """
    database = _database_row(db, server_id, database_id)
    if not database.is_superuser:
        raise ValueError("Owner-Rolle ist kein Superuser -- erst promote_owner_to_superuser aufrufen.")
    new_password = _generate_password()
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(
                    sql.Identifier(database.owner_role)
                ),
                (new_password,),
            )
        conn.commit()
    finally:
        conn.close()
    database.owner_password_encrypted = AuthService.encrypt_secret(new_password, aad="msm:pg:db:owner")
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
    """Demote an existing superuser owner back to a normal role.

    The owner keeps DB ownership + CREATEDB/CREATEROLE-capability (granted
    via the trusted-extension allowlist), but loses SUPERUSER. Useful
    after a migration is finished.
    """
    database = _database_row(db, server_id, database_id)
    if not database.is_superuser:
        raise ValueError("Owner-Rolle ist kein Superuser.")
    conn = _admin_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(database.owner_role)
                )
            )
        conn.commit()
    finally:
        conn.close()
    database.is_superuser = False
    database.power_credentials_issued_at = None
    db.commit()


# ── phpMyAdmin-aehnlicher Export/Import (pg_dump / psql) ────────────────────
#
# Hintergrund: MSM haengt alle Server-DBs an EINEM geteilten Postgres-Container
# (``settings.managed_postgres_container_name``). Dumps muessen daher alle DBs
# umfassen, die diesem Server gehoeren (Power-User sieht mehrere, Owner eine).
#
# Sicherheit:
# - Nur der Server-Owner darf dumpen / restoren -- Permission wird im Router geprueft.
# - pg_dump laeuft als POSTGRES-Admin (kennt alle DBs), aber wir filtern auf
#   die DB-Liste des Servers.
# - Restore schreibt IMMER in alle Server-eigenen DBs (DROP+CREATE-Schema-Reset).
# - Kein SQL-Pass-through aus User-Sicht direkt in pg_dump-argumente (sichere
#   Identifier-Behandlung ueber psycopg2.sql.Identifier).
#
# Streams: pg_dump produziert SQL als Stream -- wir geben es als ``text/plain``
# an Fastify weiter (siehe Route). Restore liest das SQL als kompletten String,
# weil UI es als file-Upload sendet (keine SQL-Bomb-Gefahr durch Memory-Mitsammen).


def _server_database_names(db: Session, server_id: int) -> list[str]:
    """Liefert alle DB-Namen, die zu diesem Server gehoeren (in lexikografischer
    Reihenfolge fuer reproduzierbare Dumps).

    Wir nutzen die ``PostgresDatabase``-Tabelle als Single Source of Truth --
    das verhindert, dass gleichnamige DBs aus anderen Servern mit eingedumpt
    werden (Container ist geteilt).
    """
    rows = (
        db.query(PostgresDatabase.name)
        .filter(PostgresDatabase.server_id == server_id)
        .order_by(PostgresDatabase.name.asc())
        .all()
    )
    return [r[0] for r in rows]


def dump_server_databases(db: Session, server_id: int) -> tuple[str, list[str], int, str, int]:
    """Wrapper -- delegiert an ``_pg_dump_server_dbs`` (siehe dort).

    Existiert nur als stabiler Public-API-Anker; der gesamte body lebt in
    der echten Implementierung.
    """
    sql, names, size, sha, dur = _pg_dump_server_dbs(db, server_id)
    return sql, names, size, sha, dur


def _pg_dump_server_dbs(db: Session, server_id: int) -> tuple[str, list[str], int, str, int]:
    """Server-seitiger pg_dump ueber docker_service.exec_in auf den geteilten Postgres-Container.

    Liefert (sql_text, db_names, byte_size, sha256_hex, duration_ms). Idempotent:
    jeder Lauf erzeugt eine frische, vollstaendige Kopie aller Server-DBs (mit
    ``--clean``, sodass ein ``psql``-Restore idempotent gegen die DB laufen kann).

    KISS-Note: ``docker_service.exec_in`` hat keine env-Param-API, daher
    packen wir ``PGPASSWORD=...`` in das shell-gequotete Kommando. Da wir
    den container-internen pg_dump-Pfad und das Passwort selbst kontrollieren,
    besteht hier keine Injection-Gefahr.
    """
    import time as _time
    import hashlib
    import shlex

    db_names = _server_database_names(db, server_id)
    if not db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")
    started = _time.monotonic()
    admin_pw = _admin_password()
    container = settings.managed_postgres_container_name

    # Statischer Header -- hilft bei der Restore-Verifikation.
    out_parts: list[str] = [
        "-- MSM Postgres Dump\n",
        f"-- Server ID: {server_id}\n",
        f"-- Databases: {', '.join(db_names)}\n",
        f"-- Generated: {_time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime())}\n",
        "-- Format: pg_dump --format=plain --no-owner --no-acl --clean (RESTORE via psql)\n",
        "\n",
    ]

    # Doppel-quoten fuer die Shell-Eingabe (in-container: sh -c). Passwort und
    # Username sind keine User-Eingaben -- sondern Settings -- daher kein
    # zusaetzliches Escaping noetig, ausser fuer SQLite-Anfuehrungszeichen.
    pw_quoted = admin_pw.replace("'", "'\\''")
    user_quoted = ADMIN_USER.replace("'", "'\\''")

    for db_name in db_names:
        # pg_dump --format=plain --no-owner --no-acl --clean --if-exists
        # pg_dump exit 0 garantiert -- psql ist nicht im Image.
        cmd_in_container = (
            "pg_dump "
            "--format=plain --no-owner --no-acl --clean --if-exists "
            f"--dbname={shlex.quote(db_name)} "
            f"--username={shlex.quote(ADMIN_USER)}"
        )
        full_cmd = ["sh", "-c", f"PGPASSWORD='{pw_quoted}' {cmd_in_container}"]

        result = docker_service.exec_in(container, full_cmd, timeout=180)
        if not result.get("ok"):
            raise RuntimeError(
                f"pg_dump fuer {db_name} fehlgeschlagen: "
                f"{(result.get('error') or result.get('stderr') or '')[:400]}"
            )
        body = result.get("stdout") or ""
        if not body.strip():
            # DB hatte keine schema-qualifizierten Objekte -- ueberspringen.
            continue
        out_parts.append(f"\n-- ===== Database: {db_name} =====\n")
        out_parts.append(body)
        if not body.endswith("\n"):
            out_parts.append("\n")

    sql_text = "".join(out_parts)
    raw_bytes = sql_text.encode("utf-8")
    sha = hashlib.sha256(raw_bytes).hexdigest()
    duration_ms = int((_time.monotonic() - started) * 1000)
    return sql_text, db_names, len(raw_bytes), sha, duration_ms


def restore_sql_to_server_dbs(db: Session, server_id: int, sql_text: str) -> dict[str, Any]:
    """Stellt ein komplettes SQL-Dump in alle Server-eigenen DBs wieder her.

    Verhalten:
    - Fuer JEDE Server-DB wird eine eigene psycopg2-Connection geoeffnet.
    - Innerhalb einer Transaktion: SQL via ``cursor.execute(sql_text)``.
    - Schlaegt fehl, wenn die DB nicht existiert (sollte der User aber restored
      haben -- wir erwarten, dass alle DBs bereits existieren).

    Returns: ``{"ok": True, "databases": [names], "bytes": int, "duration_ms": int}``
    """
    import time as _time
    db_names = _server_database_names(db, server_id)
    if not db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")
    started = _time.monotonic()
    bytes_in = len(sql_text.encode("utf-8"))

    import psycopg2

    host = settings.managed_postgres_host or "127.0.0.1"
    port = settings.managed_postgres_port
    admin_pw = _admin_password()

    failed: list[dict[str, str]] = []
    for db_name in db_names:
        try:
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=db_name,
                user=ADMIN_USER,
                password=admin_pw,
                connect_timeout=5,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(sql_text)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as exc:
            failed.append({"database": db_name, "error": str(exc)})

    duration_ms = int((_time.monotonic() - started) * 1000)
    if failed:
        # Sammelfehler, damit der UI weiss welche DBs nicht gingen
        raise RuntimeError(
            "Restore teilweise fehlgeschlagen: "
            + "; ".join(f"{f['database']}: {f['error'][:120]}" for f in failed)
        )
    return {
        "ok": True,
        "databases": db_names,
        "bytes": bytes_in,
        "duration_ms": duration_ms,
    }


def backup_pg_dump_for_archive(db: Session, server_id: int) -> dict[str, bytes]:
    """Erzeugt separate pg_dump-Blobs pro Server-DB fuer die Backup-Integration.

    Liefert ``dict[db_name -> sql_bytes]`` — der Caller packt jeden Dump als
    ``.msm/postgres/<db_name>.sql`` ins Backup-tar. Bei Servern ohne DBs wird
    ein leeres dict geliefert.

    VAL-FIX-009: Jede DB bekommt ihren eigenen Dump, sodass beim Restore jede
    DB nur ihren eigenen Dump erhaelt (keine Cross-Kontamination).
    """
    db_names = _server_database_names(db, server_id)
    if not db_names:
        return {}
    return _pg_dump_server_dbs_per_db(db, server_id, db_names)


def _pg_dump_server_dbs_per_db(
    db: Session, server_id: int, db_names: list[str]
) -> dict[str, bytes]:
    """Erzeugt separate pg_dump-Ausgaben pro DB ueber docker_service.exec_in.

    Liefert ``dict[db_name -> sql_bytes]``. Wirft bei pg_dump-Fehler ( Caller
    muss entscheiden: hartes Backup-Fehlschlagen oder partial).
    """
    import hashlib
    import shlex

    admin_pw = _admin_password()
    container = settings.managed_postgres_container_name
    pw_quoted = admin_pw.replace("'", "'\\''")

    result: dict[str, bytes] = {}
    for db_name in db_names:
        cmd_in_container = (
            "pg_dump "
            "--format=plain --no-owner --no-acl --clean --if-exists "
            f"--dbname={shlex.quote(db_name)} "
            f"--username={shlex.quote(ADMIN_USER)}"
        )
        full_cmd = ["sh", "-c", f"PGPASSWORD='{pw_quoted}' {cmd_in_container}"]

        exec_result = docker_service.exec_in(container, full_cmd, timeout=180)
        if not exec_result.get("ok"):
            raise RuntimeError(
                f"pg_dump fuer {db_name} fehlgeschlagen: "
                f"{(exec_result.get('error') or exec_result.get('stderr') or '')[:400]}"
            )
        body = exec_result.get("stdout") or ""
        if not body.strip():
            # DB hatte keine schema-qualifizierten Objekte — leerer Dump.
            result[db_name] = b""
            continue
        result[db_name] = body.encode("utf-8")

    return result


def restore_pg_dump_from_archive(
    db: Session, server_id: int, dumps: dict[str, bytes]
) -> dict[str, Any]:
    """Stellt separate per-DB Dumps in die zugehoerigen Server-DBs wieder her.

    VAL-FIX-009: Jeder Dump wird NUR in seine zugehoerige DB restored — keine
    Cross-Kontamination (DB A's Dump wird nicht in DB B eingespielt).

    Parameter:
      dumps: ``dict[db_name -> sql_bytes]`` aus dem Backup-Archiv.

    Verhalten:
    - Leeres dict → skipped (kein Postgres-Dump im Backup).
    - Dumps fuer DBs die nicht mehr existieren → uebersprungen (kein Fehler).
    - Restore-Fehler → RuntimeError (Caller muss als harten Fehler behandeln).

    Returns: ``{"ok": True, "databases": [names], "duration_ms": int}`` oder
    ``{"ok": True, "skipped": True, "reason": "..."}`` bei leerem dict.
    """
    import time as _time

    if not dumps:
        return {"ok": True, "skipped": True, "reason": "Backup enthaelt keinen Postgres-Dump"}

    server_db_names = set(_server_database_names(db, server_id))
    if not server_db_names:
        raise ValueError("Server hat keine Postgres-Datenbanken.")

    started = _time.monotonic()
    host = settings.managed_postgres_host or "127.0.0.1"
    port = settings.managed_postgres_port
    admin_pw = _admin_password()

    restored: list[str] = []
    failed: list[dict[str, str]] = []

    for db_name, sql_bytes in dumps.items():
        # _legacy-Schluessel: kombiniert alter Dump ohne Sektions-Marker.
        # Wird in alle Server-DBs eingespielt (altes Verhalten, Backward-Compat).
        if db_name == "_legacy":
            for target_name in server_db_names:
                _restore_sql_to_single_db(
                    host, port, target_name, admin_pw,
                    sql_bytes.decode("utf-8", errors="replace"),
                    failed,
                )
                restored.append(target_name)
            continue

        # Dump fuer eine DB die nicht mehr existiert → ueberspringen.
        if db_name not in server_db_names:
            logger.warning(
                "Restore: Dump fuer DB '%s' existiert nicht mehr im Server %s — uebersprungen",
                db_name, server_id,
            )
            continue

        if not sql_bytes.strip():
            # Leerer Dump (DB hatte keine Objekte) — nichts zu tun.
            restored.append(db_name)
            continue

        sql_text = sql_bytes.decode("utf-8", errors="replace")
        _restore_sql_to_single_db(host, port, db_name, admin_pw, sql_text, failed)
        restored.append(db_name)

    duration_ms = int((_time.monotonic() - started) * 1000)
    if failed:
        raise RuntimeError(
            "Restore teilweise fehlgeschlagen: "
            + "; ".join(f"{f['database']}: {f['error'][:120]}" for f in failed)
        )
    return {
        "ok": True,
        "databases": restored,
        "duration_ms": duration_ms,
    }


def _restore_sql_to_single_db(
    host: str,
    port: int,
    db_name: str,
    admin_pw: str,
    sql_text: str,
    failed: list[dict[str, str]],
) -> None:
    """Stellt ein SQL-Dump in eine einzelne DB wieder her.

    Fehler werden in ``failed`` gesammelt (Caller entscheidet ueber Behandlung).
    """
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=db_name,
            user=ADMIN_USER,
            password=admin_pw,
            connect_timeout=5,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:
        failed.append({"database": db_name, "error": str(exc)})


def _clean_pg_dump_for_restore(sql_text: str) -> str:
    """Bereinigt einen ``pg_dump --clean --if-exists``-Stream fuer
    ``cursor.execute()`` auf einer bereits offenen DB-Connection.

    Was raus muss:
    - ``CREATE DATABASE <name>`` -- wir sind bereits in der DB.
    - ``\\\\connect <name>`` -- psql-meta-Befehl, in cursor.execute() nicht erlaubt.
    - ``\\\\restrict <token>`` und ``\\\\unrestrict <token>`` -- schuetzen den
      gesamten Dump-Block. Wir entfernen BEIDE -- kein Problem, weil die
      Tabellen danach mit expliziten INSERTs gefuellt werden und unsere
      Connection als Postgres-Admin bereits Vollzugriff hat.
    - Reine Kommentar-Banner (``-- ...``-Zeilen), die nicht der Section-Markierung
      ``-- ===== Database: ... =====`` dienen -- spart Speicher und reduziert
      Parser-Last.

    KISS: zeilenbasiert, keine Regex-Magie.
    """
    skip_until: str | None = None
    out: list[str] = []
    for raw in sql_text.split("\n"):
        line = raw.rstrip("\r")
        stripped = line.lstrip()
        if skip_until is not None:
            # Restrict-Block: Zeilen bis '\\unrestrict' weglassen
            if stripped.startswith("\\unrestrict"):
                skip_until = None
            continue
        if stripped.startswith("\\restrict"):
            skip_until = "\\unrestrict"
            continue
        if not stripped:
            # Leerzeilen drinnen behalten wir klein
            if out and out[-1] != "":
                out.append("")
            continue
        if stripped.startswith("CREATE DATABASE "):
            continue
        if stripped.startswith("\\connect "):
            continue
        # Allgemeine Banner-Kommentare (-- ...) weg -- spart Platz, kein Inhalt.
        # Wichtig: ``-- ===== Database: ... =====`` ebenfalls weg, das ist nur
        # Sektions-Trenner.
        if stripped.startswith("--"):
            continue
        out.append(line)
    # Doppelleerzeilen am Ende reduzieren
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)
