"""Managed Postgres on the node (Phase 7).

Panel is source of truth for metadata + encrypted secrets.
Agent runs msm-postgres locally, executes DDL via psycopg2 on 127.0.0.1.
Passwords arrive only in request payload (RAM); never written to agent disk.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import time
from typing import Any

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

from config import settings
from services import docker_service

logger = logging.getLogger(__name__)

ADMIN_USER = "msm_admin"
CONTROL_DB = "msm_control"
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
_PG_CAPS = ["CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE", "DAC_READ_SEARCH"]

_WRITE_KEYWORDS = (
    "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "grant", "revoke", "copy", "vacuum", "analyze", "cluster", "reindex",
    "set", "reset", "begin", "commit", "rollback", "savepoint", "lock",
    "call", "do", "notify", "listen", "unlisten", "refresh", "checkpoint",
)


class PostgresAgentError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def validate_identifier(value: str) -> str:
    cleaned = (value or "").strip().lower()
    if not IDENTIFIER_RE.fullmatch(cleaned):
        raise PostgresAgentError("Invalid PostgreSQL identifier")
    return cleaned


def _db_host() -> str:
    host = (settings.managed_postgres_host or "").strip()
    if host != "127.0.0.1":
        raise PostgresAgentError("Managed PostgreSQL may only bind to 127.0.0.1")
    return host


def _admin_connect(admin_password: str, database: str = CONTROL_DB):
    ensure_internal_postgres(admin_password)
    conn = _connect_with_retry(admin_password, database)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _connect_with_retry(admin_password: str, database: str = CONTROL_DB):
    last_error: Exception | None = None
    for _ in range(30):
        try:
            return psycopg2.connect(
                host=_db_host(),
                port=settings.managed_postgres_port,
                dbname=database,
                user=ADMIN_USER,
                password=admin_password,
                connect_timeout=2,
            )
        except psycopg2.Error as exc:
            last_error = exc
            time.sleep(1)
    raise PostgresAgentError("Managed PostgreSQL did not become ready", status_code=503) from last_error


def _owner_connect(database_name: str, owner_role: str, owner_password: str):
    return psycopg2.connect(
        host=_db_host(),
        port=settings.managed_postgres_port,
        dbname=database_name,
        user=owner_role,
        password=owner_password,
        connect_timeout=5,
    )


def _sanitize_bootstrap_environment(admin_password: str) -> None:
    ready = _connect_with_retry(admin_password)
    ready.close()
    sanitized = docker_service.run_managed_postgres(
        name=settings.managed_postgres_container_name,
        image=settings.managed_postgres_image,
        env=None,
        host_port=settings.managed_postgres_port,
        host_ip=_db_host(),
        data_dir=settings.managed_postgres_data_dir,
        network_name=settings.managed_postgres_network,
        cap_adds=_PG_CAPS,
    )
    if not sanitized.get("ok"):
        raise PostgresAgentError("Could not remove bootstrap credentials from container", status_code=503)
    ready = _connect_with_retry(admin_password)
    ready.close()


def ensure_internal_postgres(admin_password: str) -> dict[str, Any]:
    """Start or create local msm-postgres. admin_password only in memory."""
    if not admin_password:
        raise PostgresAgentError("admin_password is required", status_code=400)

    network_result = docker_service.ensure_network(
        settings.managed_postgres_network, internal=True
    )
    if not network_result.get("ok"):
        raise PostgresAgentError(
            network_result.get("error") or "Could not create PostgreSQL network",
            status_code=503,
        )

    os.makedirs(settings.managed_postgres_data_dir, exist_ok=True)
    container_name = settings.managed_postgres_container_name
    state = docker_service.inspect_managed_state(container_name)

    if state and state.get("status") == "running":
        if state.get("has_bootstrap_secret"):
            _sanitize_bootstrap_environment(admin_password)
        docker_service.ensure_managed_restart_policy(container_name, "unless-stopped")
        return {"ok": True, "status": "running"}

    if state and state.get("status") in {"exited", "created", "paused"}:
        start_result = docker_service.start_managed(container_name)
        if not start_result.get("ok"):
            raise PostgresAgentError(
                start_result.get("error") or "Could not start PostgreSQL container",
                status_code=503,
            )
        if state.get("has_bootstrap_secret"):
            _sanitize_bootstrap_environment(admin_password)
        docker_service.ensure_managed_restart_policy(container_name, "unless-stopped")
        return {"ok": True, "status": "started"}

    result = docker_service.run_managed_postgres(
        name=container_name,
        image=settings.managed_postgres_image,
        env={
            "POSTGRES_USER": ADMIN_USER,
            "POSTGRES_PASSWORD": admin_password,
            "POSTGRES_DB": CONTROL_DB,
        },
        host_port=settings.managed_postgres_port,
        host_ip=_db_host(),
        data_dir=settings.managed_postgres_data_dir,
        network_name=settings.managed_postgres_network,
        cap_adds=_PG_CAPS,
    )
    if not result.get("ok"):
        raise PostgresAgentError(
            result.get("error") or "Could not create PostgreSQL container",
            status_code=503,
        )
    # The official image only needs POSTGRES_PASSWORD during first init. Once
    # the data directory is initialized, recreate without environment secrets
    # so Docker inspect cannot expose the cleartext admin password.
    _sanitize_bootstrap_environment(admin_password)
    docker_service.ensure_managed_restart_policy(container_name, "unless-stopped")
    return {"ok": True, "status": "created"}


def provision(
    *,
    admin_password: str,
    db_name: str,
    owner_role: str,
    owner_password: str,
    user_name: str,
    user_password: str,
    power_user: bool = False,
) -> dict[str, Any]:
    db_name = validate_identifier(db_name)
    owner_role = validate_identifier(owner_role)
    user_name = validate_identifier(user_name)
    if not all([admin_password, owner_password, user_password]):
        raise PostgresAgentError("Passwords required")

    owner_create_stmt = (
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s SUPERUSER").format(sql.Identifier(owner_role))
        if power_user
        else sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE"
        ).format(sql.Identifier(owner_role))
    )

    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(owner_create_stmt, (owner_password,))
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name), sql.Identifier(owner_role)
                )
            )
            cur.execute(
                sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(owner_role)
                )
            )
            cur.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name))
            )
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(user_name)),
                (user_password,),
            )
            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(user_name)
                )
            )
    finally:
        conn.close()

    conn = _admin_connect(admin_password, db_name)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(user_name)
                )
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(sql.Identifier(owner_role), sql.Identifier(user_name))
            )
            cur.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(sql.Identifier(owner_role), sql.Identifier(user_name))
            )
    finally:
        conn.close()

    return {
        "ok": True,
        "database_name": db_name,
        "owner_role": owner_role,
        "user_name": user_name,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
        "is_superuser": power_user,
    }


def create_user(
    *,
    admin_password: str,
    database_name: str,
    user_name: str,
    user_password: str,
) -> dict[str, Any]:
    database_name = validate_identifier(database_name)
    user_name = validate_identifier(user_name)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(user_name)),
                (user_password,),
            )
            cur.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(user_name)
                )
            )
    finally:
        conn.close()
    conn = _admin_connect(admin_password, database_name)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(user_name)
                )
            )
            cur.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(user_name))
            )
            cur.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
                ).format(sql.Identifier(user_name))
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "database_name": database_name,
        "username": user_name,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def rotate_role_password(
    *, admin_password: str, role_name: str, new_password: str
) -> dict[str, Any]:
    role_name = validate_identifier(role_name)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(role_name)),
                (new_password,),
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "username": role_name,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def drop_databases_and_roles(
    *,
    admin_password: str,
    databases: list[str],
    owners: list[str],
    users: list[str],
) -> dict[str, Any]:
    ensure_internal_postgres(admin_password)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            for database in databases:
                name = validate_identifier(database)
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
            for role in list(users) + list(owners):
                r = validate_identifier(role)
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(r)))
    finally:
        conn.close()
    return {"ok": True}


def promote_owner(
    *, admin_password: str, owner_role: str, new_password: str
) -> dict[str, Any]:
    owner_role = validate_identifier(owner_role)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH SUPERUSER LOGIN PASSWORD %s").format(
                    sql.Identifier(owner_role)
                ),
                (new_password,),
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "username": owner_role,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


def demote_owner(*, admin_password: str, owner_role: str) -> dict[str, Any]:
    owner_role = validate_identifier(owner_role)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(owner_role)
                )
            )
    finally:
        conn.close()
    return {"ok": True}


def alter_owner_password(
    *, admin_password: str, owner_role: str, new_password: str
) -> dict[str, Any]:
    owner_role = validate_identifier(owner_role)
    conn = _admin_connect(admin_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(
                    sql.Identifier(owner_role)
                ),
                (new_password,),
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "username": owner_role,
        "host": settings.managed_postgres_container_name,
        "port": 5432,
    }


# ── Owner-scoped query actions ──────────────────────────────────────────────


def list_tables(
    database_name: str, owner_role: str, owner_password: str
) -> list[dict[str, Any]]:
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname, c.relname,
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
                {
                    "schema": row[0],
                    "name": row[1],
                    "row_estimate": row[2],
                    "size_bytes": row[3],
                }
                for row in cur.fetchall()
            ]


def database_stats(
    database_name: str, owner_role: str, owner_password: str
) -> dict[str, Any]:
    started = time.monotonic()
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        latency_ms = int((time.monotonic() - started) * 1000)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  pg_database_size(current_database()) AS size_bytes,
                  (SELECT count(*) FROM information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')) AS table_count,
                  (SELECT count(*) FROM pg_stat_activity
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


def describe_table(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
) -> dict[str, Any]:
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    with _owner_connect(database_name, owner_role, owner_password) as conn:
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
                {
                    "name": row[0],
                    "data_type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[3],
                }
                for row in cur.fetchall()
            ]
            if not columns:
                raise PostgresAgentError("Table not found")
            cur.execute(
                """
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s ORDER BY indexname
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
                  AND tc.table_schema = %s AND tc.table_name = %s
                ORDER BY tc.constraint_name
                """,
                (schema_name, table_name),
            )
            foreign_keys = [
                {
                    "name": row[0],
                    "column_name": row[1],
                    "foreign_table": row[2],
                    "foreign_column": row[3],
                }
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


def create_table(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
    columns: list[dict[str, Any]],
) -> dict[str, Any]:
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    if not columns:
        raise PostgresAgentError("At least one column is required")
    column_sql = []
    for column in columns:
        name = validate_identifier(str(column.get("name") or ""))
        type_key = str(column.get("type") or "").lower()
        if type_key not in ALLOWED_COLUMN_TYPES:
            raise PostgresAgentError("Invalid column type")
        part = sql.SQL("{} {}").format(
            sql.Identifier(name), sql.SQL(ALLOWED_COLUMN_TYPES[type_key])
        )
        if column.get("primary_key"):
            part = part + sql.SQL(" PRIMARY KEY")
        if column.get("not_null"):
            part = part + sql.SQL(" NOT NULL")
        column_sql.append(part)
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name))
            )
            cur.execute(
                sql.SQL("CREATE TABLE {}.{} ({})").format(
                    sql.Identifier(schema_name),
                    sql.Identifier(table_name),
                    sql.SQL(", ").join(column_sql),
                )
            )
    return {"ok": True}


def drop_table(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
) -> dict[str, Any]:
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TABLE {}.{}").format(
                    sql.Identifier(validate_identifier(schema_name or "public")),
                    sql.Identifier(validate_identifier(table_name)),
                )
            )
    return {"ok": True}


def read_rows(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
    limit: int,
    offset: int,
    search: str | None = None,
) -> dict[str, Any]:
    limit = min(max(limit, 1), settings.managed_postgres_row_limit)
    offset = max(offset, 0)
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema_name, table_name),
            )
            columns = [row[0] for row in cur.fetchall()]
            if not columns:
                raise PostgresAgentError("Table not found")
            query = sql.SQL("SELECT * FROM {}.{}").format(
                sql.Identifier(schema_name), sql.Identifier(table_name)
            )
            params: list[Any] = []
            if search:
                like = f"%{search[:128]}%"
                clauses = [
                    sql.SQL("CAST({} AS TEXT) ILIKE %s").format(sql.Identifier(column))
                    for column in columns
                ]
                query += sql.SQL(" WHERE ") + sql.SQL(" OR ").join(clauses)
                params.extend([like] * len(columns))
            query += sql.SQL(" LIMIT %s OFFSET %s")
            params.extend([limit, offset])
            cur.execute(query, tuple(params))
            rows = [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
            return {"columns": columns, "rows": rows, "limit": limit, "offset": offset}


def _validate_extension_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    if not IDENTIFIER_RE.fullmatch(cleaned):
        raise PostgresAgentError("Invalid extension name")
    if cleaned not in settings.trusted_extensions_set():
        raise PostgresAgentError(f"Extension '{cleaned}' is not allowed")
    return cleaned


def list_extensions(
    database_name: str, owner_role: str, owner_password: str
) -> list[dict[str, Any]]:
    conn = _owner_connect(database_name, owner_role, owner_password)
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


def install_extension(
    database_name: str, owner_role: str, owner_password: str, name: str
) -> dict[str, Any]:
    ext = _validate_extension_name(name)
    conn = _owner_connect(database_name, owner_role, owner_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(sql.Identifier(ext))
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def drop_extension(
    database_name: str, owner_role: str, owner_password: str, name: str
) -> dict[str, Any]:
    ext = _validate_extension_name(name)
    conn = _owner_connect(database_name, owner_role, owner_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP EXTENSION IF EXISTS {}").format(sql.Identifier(ext))
            )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _split_sql_statements(text: str) -> list[str]:
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
    database_name: str,
    owner_role: str,
    owner_password: str,
    statement: str,
    limit: int,
) -> dict[str, Any]:
    cleaned = (statement or "").strip()
    if not cleaned:
        raise PostgresAgentError("SQL must not be empty")
    statements = _split_sql_statements(cleaned)
    if not statements:
        raise PostgresAgentError("No executable SQL statements found")
    row_limit = min(max(limit, 1), settings.managed_postgres_row_limit)
    timeout_ms = settings.managed_postgres_statement_timeout_ms
    has_write = any(not _is_read_only(s) for s in statements)
    results: list[dict[str, Any]] = []

    conn = _owner_connect(database_name, owner_role, owner_password)
    try:
        if has_write:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (timeout_ms,))
                for stmt in statements:
                    start = time.monotonic()
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
                        entry["duration_ms"] = int((time.monotonic() - start) * 1000)
                        results.append(entry)
                conn.commit()
        else:
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = %s", (timeout_ms,))
                for stmt in statements:
                    start = time.monotonic()
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
                        entry["duration_ms"] = int((time.monotonic() - start) * 1000)
                        results.append(entry)
                conn.commit()
    finally:
        conn.close()

    return {
        "statements": results,
        "total_duration_ms": sum((r.get("duration_ms") or 0) for r in results),
        "statement_timeout_ms": timeout_ms,
    }


def dispatch_query(action: str, payload: dict[str, Any]) -> Any:
    """Dispatch owner/admin query actions. Passwords never logged."""
    act = (action or "").strip().lower()
    dbn = payload.get("database_name") or ""
    owner = payload.get("owner_role") or ""
    opw = payload.get("owner_password") or ""

    if act == "list_tables":
        return list_tables(dbn, owner, opw)
    if act == "stats":
        return database_stats(dbn, owner, opw)
    if act == "describe_table":
        return describe_table(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
        )
    if act == "create_table":
        return create_table(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
            payload.get("columns") or [],
        )
    if act == "drop_table":
        return drop_table(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
        )
    if act == "read_rows":
        return read_rows(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
            int(payload.get("limit") or 50),
            int(payload.get("offset") or 0),
            payload.get("search"),
        )
    if act == "execute_sql":
        return execute_sql(
            dbn, owner, opw,
            payload.get("sql") or "",
            int(payload.get("limit") or 100),
        )
    if act == "list_extensions":
        return list_extensions(dbn, owner, opw)
    if act == "install_extension":
        return install_extension(dbn, owner, opw, payload.get("name") or "")
    if act == "drop_extension":
        return drop_extension(dbn, owner, opw, payload.get("name") or "")
    raise PostgresAgentError(f"Unknown query action: {act}")


def dump_databases(*, admin_password: str, database_names: list[str]) -> dict[str, str]:
    """pg_dump per DB via docker exec. Returns {db_name: sql_text} (no passwords in output)."""
    if not database_names:
        return {}
    ensure_internal_postgres(admin_password)
    container = settings.managed_postgres_container_name
    result: dict[str, str] = {}
    for db_name in database_names:
        name = validate_identifier(db_name)
        cmd_in_container = (
            "pg_dump "
            "--format=plain --no-owner --no-acl --clean --if-exists "
            f"--dbname={shlex.quote(name)} "
            f"--username={shlex.quote(ADMIN_USER)}"
        )
        exec_result = docker_service.exec_in_managed(
            container,
            ["sh", "-c", cmd_in_container],
            timeout=180,
            environment={"PGPASSWORD": admin_password},
        )
        if not exec_result.get("ok"):
            raise PostgresAgentError(
                f"pg_dump failed for database: {(exec_result.get('error') or '')[:200]}",
                status_code=500,
            )
        result[name] = exec_result.get("stdout") or ""
    return result


def restore_sql(
    *,
    admin_password: str,
    dumps: dict[str, str],
    owners: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Restore SQL text into named databases (admin connection)."""
    if not dumps:
        return {"ok": True, "skipped": True, "reason": "empty dumps"}
    ensure_internal_postgres(admin_password)
    started = time.monotonic()
    database_names = [validate_identifier(name) for name in dumps]
    rollback_dumps = dump_databases(
        admin_password=admin_password,
        database_names=database_names,
    )
    def apply_dump(name: str, sql_text: str) -> None:
        owner = (owners or {}).get(name) or {}
        restore_user = validate_identifier(str(owner.get("owner_role") or ADMIN_USER))
        restore_password = str(owner.get("owner_password") or admin_password)
        result = docker_service.exec_in_managed_stdin(
            settings.managed_postgres_container_name,
            ["psql", "--set", "ON_ERROR_STOP=1", "--username", restore_user, "--dbname", name],
            sql_text,
            environment={"PGPASSWORD": restore_password},
        )
        if not result.get("ok"):
            raise PostgresAgentError(
                f"psql restore failed for database: {(result.get('error') or '')[:200]}",
                status_code=500,
            )

    restored: list[str] = []
    failed: list[dict[str, str]] = []
    for db_name, sql_text in dumps.items():
        try:
            name = validate_identifier(db_name)
            if not (sql_text or "").strip():
                restored.append(name)
                continue
            apply_dump(name, sql_text)
            restored.append(name)
        except Exception as exc:  # noqa: BLE001
            failed.append({"database": db_name, "error": str(exc)[:120]})
    if failed:
        rollback_failed: list[str] = []
        for name in restored:
            previous_sql = rollback_dumps.get(name, "")
            if not previous_sql.strip():
                continue
            try:
                apply_dump(name, previous_sql)
            except Exception:
                rollback_failed.append(name)
        if rollback_failed:
            raise PostgresAgentError(
                "Restore failed and rollback requires manual intervention for: "
                + ", ".join(rollback_failed),
                status_code=500,
            )
        raise PostgresAgentError(
            "Restore failed; previously restored databases were rolled back: "
            + "; ".join(f"{f['database']}: {f['error']}" for f in failed),
            status_code=500,
        )
    return {
        "ok": True,
        "databases": restored,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
