"""Managed Postgres on the node (Phase 7).

Panel is source of truth for metadata + encrypted secrets.
Agent runs msm-postgres locally, executes DDL via psycopg2 on 127.0.0.1.
Passwords arrive only in request payload (RAM); never written to agent disk.

Datenbankserver haben eine eigene Instanz (Container ``msm-srv-<id>``). Fuer
sie traegt die Anfrage ein ``target``; der Router setzt es mit ``ziel(...)``
fuer die Dauer der Anfrage. Ohne Ziel gilt immer der geteilte msm-postgres.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

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


@dataclass(frozen=True)
class Instanzziel:
    """Eigene Instanz eines Datenbankservers statt des geteilten msm-postgres."""

    host: str
    port: int
    container: str


_ZIEL: ContextVar[Instanzziel | None] = ContextVar("postgres_instanzziel", default=None)


def _pruefe_ziel(target: dict[str, Any]) -> Instanzziel:
    from services.network_interfaces_service import list_host_interfaces

    host = str(target.get("host") or "").strip()
    container = str(target.get("container") or "").strip()
    try:
        port = int(target.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    # Nur Adressen dieses Hosts: das Panel darf den Agenten nicht als Sprungbrett
    # zu fremden Datenbanken im Netz benutzen.
    eigene = {"127.0.0.1"} | {iface.ip for iface in list_host_interfaces()}
    if host not in eigene:
        raise PostgresAgentError("Instance host must be an address of this node")
    if not 1024 <= port <= 65535:
        raise PostgresAgentError("Invalid instance port")
    try:
        docker_service.assert_msm_container_name(container)
    except Exception as exc:
        raise PostgresAgentError("Invalid instance container") from exc
    return Instanzziel(host=host, port=port, container=container)


@contextmanager
def ziel(target: dict[str, Any] | None) -> Iterator[None]:
    """Setzt die eigene Instanz fuer die Dauer einer Anfrage (None = msm-postgres)."""
    token = _ZIEL.set(_pruefe_ziel(target) if target else None)
    try:
        yield
    finally:
        _ZIEL.reset(token)


def _db_port() -> int:
    z = _ZIEL.get()
    return z.port if z is not None else settings.managed_postgres_port


def _container() -> str:
    z = _ZIEL.get()
    return z.container if z is not None else settings.managed_postgres_container_name


def _internal_port() -> int:
    """Port, unter dem Container im internen Netz die Instanz erreichen."""
    z = _ZIEL.get()
    return z.port if z is not None else 5432


def _db_host() -> str:
    z = _ZIEL.get()
    if z is not None:
        return z.host
    host = (settings.managed_postgres_host or "").strip()
    if host != "127.0.0.1":
        raise PostgresAgentError("Managed PostgreSQL may only bind to 127.0.0.1")
    return host


def _postgres_data_dir() -> str:
    return str(Path(settings.managed_postgres_data_dir).expanduser().resolve())


def _admin_connect(admin_password: str, database: str = CONTROL_DB):
    ensure_internal_postgres(admin_password)
    conn = _connect_with_retry(admin_password, database)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _connect_with_retry(admin_password: str, database: str = CONTROL_DB):
    last_error: Exception | None = None
    # First initialization of the official image can exceed 30 seconds on a
    # cold disk. Keep the request bounded while allowing that one-time setup.
    for _ in range(60):
        try:
            return psycopg2.connect(
                host=_db_host(),
                port=_db_port(),
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
        port=_db_port(),
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
        data_dir=_postgres_data_dir(),
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
    if _ZIEL.get() is not None:
        # Eine eigene Instanz ist ein Servercontainer; starten und stoppen
        # gehoert dem Server, nicht diesem Aufruf.
        return {"ok": True, "status": "instance"}

    network_result = docker_service.ensure_network(
        settings.managed_postgres_network, internal=True
    )
    if not network_result.get("ok"):
        raise PostgresAgentError(
            network_result.get("error") or "Could not create PostgreSQL network",
            status_code=503,
        )

    data_dir = _postgres_data_dir()
    os.makedirs(data_dir, exist_ok=True)
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
        data_dir=data_dir,
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

    # A server-scoped power user remains owner of only its own database.  The
    # node-local PostgreSQL cluster is shared by multiple managed servers, so a
    # cluster-wide SUPERUSER role would break tenant isolation.
    owner_create_stmt = sql.SQL(
        "CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOCREATEDB NOCREATEROLE"
    ).format(sql.Identifier(owner_role))

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
        "host": _container(),
        "port": _internal_port(),
        # power_user is panel metadata only; roles always stay NOSUPERUSER.
        "power_user": power_user,
        "is_power_user": power_user,
        "cluster_superuser": False,
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
        "host": _container(),
        "port": _internal_port(),
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
        "host": _container(),
        "port": _internal_port(),
    }


def rotate_admin_password(
    *, admin_password: str, new_admin_password: str
) -> dict[str, Any]:
    """Rotiert das Cluster-Admin-Passwort von msm_admin (ALTER ROLE).

    Verbindet nur mit dem alten Passwort — startet keinen Container neu und
    legt keine DB an. Bei nicht laufendem Postgres: 503 (Node ohne Managed DB).
    Gibt niemals Passwoerter zurueck.
    """
    if not (admin_password or "").strip():
        raise PostgresAgentError("admin_password is required", status_code=400)
    if not (new_admin_password or "").strip():
        raise PostgresAgentError("new_admin_password is required", status_code=400)
    if len(new_admin_password) < 16:
        raise PostgresAgentError("new_admin_password must be at least 16 characters", status_code=400)
    if admin_password == new_admin_password:
        raise PostgresAgentError("new_admin_password must differ from current password", status_code=400)

    # Kein ensure_internal_postgres: Rotation darf keinen Bootstrap mit altem Secret ausloesen.
    last_error: Exception | None = None
    conn = None
    for _ in range(10):
        try:
            conn = psycopg2.connect(
                host=_db_host(),
                port=_db_port(),
                dbname=CONTROL_DB,
                user=ADMIN_USER,
                password=admin_password,
                connect_timeout=2,
            )
            break
        except psycopg2.Error as exc:
            last_error = exc
            time.sleep(0.5)
    if conn is None:
        raise PostgresAgentError(
            "Managed PostgreSQL is not available on this node",
            status_code=503,
        ) from last_error

    try:
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH PASSWORD %s").format(sql.Identifier(ADMIN_USER)),
                (new_admin_password,),
            )
    except psycopg2.Error as exc:
        raise PostgresAgentError(
            "Could not rotate managed PostgreSQL admin password",
            status_code=500,
        ) from exc
    finally:
        conn.close()

    # Verifikation: neues Passwort muss verbinden, altes wird hier nicht erneut getestet.
    try:
        verify = psycopg2.connect(
            host=_db_host(),
            port=_db_port(),
            dbname=CONTROL_DB,
            user=ADMIN_USER,
            password=new_admin_password,
            connect_timeout=5,
        )
        verify.close()
    except psycopg2.Error as exc:
        raise PostgresAgentError(
            "Admin password was changed but verification with the new password failed",
            status_code=500,
        ) from exc

    return {"ok": True, "admin_user": ADMIN_USER}


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
                sql.SQL(
                    "ALTER ROLE {} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN PASSWORD %s"
                ).format(
                    sql.Identifier(owner_role)
                ),
                (new_password,),
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "username": owner_role,
        "host": _container(),
        "port": _internal_port(),
        "scope": "database",
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
        "host": _container(),
        "port": _internal_port(),
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
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = %s
                  AND tc.table_name = %s
                """,
                (schema_name, table_name),
            )
            pk_cols = {row[0] for row in cur.fetchall()}

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
                    "primary_key": row[0] in pk_cols,
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


# ── Studio: generisches Ausfuehren ─────────────────────────────────────────
#
# Katalogabfragen und DDL baut das Panel. Der Agent fuehrt nur aus: so braucht
# eine neue Studio-Funktion kein Agent-Update auf jedem Node.

RUN_MODES = {"read", "tx", "autocommit"}
_MAX_NOTICES = 50


def _json_wert(value: Any) -> Any:
    """Werte so zurueckgeben, dass JSON sie ohne Genauigkeitsverlust traegt."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, dict):
        return {str(k): _json_wert(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_wert(v) for v in value]
    return str(value)


def _pg_fehler(exc: Exception, index: int) -> PostgresAgentError:
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) or str(exc).strip().splitlines()[0:1]
    if isinstance(primary, list):
        primary = primary[0] if primary else type(exc).__name__
    detail = getattr(diag, "message_detail", None)
    hint = getattr(diag, "message_hint", None)
    code = getattr(exc, "pgcode", None)
    parts = [f"[{code}] {primary}" if code else str(primary)]
    if detail:
        parts.append(str(detail))
    if hint:
        parts.append(f"Hint: {hint}")
    message = f"Statement {index + 1}: " + " — ".join(parts)
    return PostgresAgentError(message[:2000], status_code=400)


def run_statements(
    *,
    database_name: str,
    identity: str,
    owner_role: str,
    owner_password: str,
    admin_password: str,
    mode: str,
    statements: list[dict[str, Any]],
    row_limit: int,
    timeout_ms: int,
    rollback: bool = False,
) -> dict[str, Any]:
    """Fuehrt Anweisungen des Panels aus.

    ``read``: nur lesende Transaktion. ``tx``: alles oder nichts in einer
    Transaktion (``rollback`` verwirft am Ende, z. B. fuer EXPLAIN ANALYZE).
    ``autocommit``: fuer Anweisungen, die keine Transaktion vertragen
    (``CREATE INDEX CONCURRENTLY``, ``VACUUM``); Abbruch beim ersten Fehler.
    """
    database_name = validate_identifier(database_name)
    if mode not in RUN_MODES:
        raise PostgresAgentError("Invalid run mode")
    if not statements:
        raise PostgresAgentError("No statements")
    row_limit = min(max(int(row_limit), 1), 5000)
    timeout_ms = min(max(int(timeout_ms), 100), 600_000)

    if identity not in {"admin", "owner"}:
        raise PostgresAgentError("Invalid identity")
    try:
        if identity == "admin":
            if not admin_password:
                raise PostgresAgentError("admin_password is required")
            conn = _admin_connect(admin_password, database_name)
            conn.autocommit = False
        else:
            conn = _owner_connect(database_name, validate_identifier(owner_role), owner_password)
    except psycopg2.OperationalError as exc:
        raise PostgresAgentError("PostgreSQL connection failed", status_code=503) from exc

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    try:
        if mode == "autocommit":
            conn.autocommit = True
        elif mode == "read":
            conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (timeout_ms,))
            for index, statement in enumerate(statements):
                text = str(statement.get("sql") or "")
                params = statement.get("params") or None
                begin = time.monotonic()
                try:
                    cur.execute(text, params)
                except psycopg2.Error as exc:
                    if not conn.autocommit:
                        conn.rollback()
                    raise _pg_fehler(exc, index) from exc
                entry: dict[str, Any] = {
                    "columns": [],
                    "rows": [],
                    "row_count": cur.rowcount,
                    "status": cur.statusmessage,
                    "truncated": False,
                    "duration_ms": 0,
                }
                if cur.description:
                    entry["columns"] = [desc[0] for desc in cur.description]
                    fetched = cur.fetchmany(row_limit + 1)
                    entry["truncated"] = len(fetched) > row_limit
                    entry["rows"] = [[_json_wert(v) for v in row] for row in fetched[:row_limit]]
                entry["duration_ms"] = int((time.monotonic() - begin) * 1000)
                results.append(entry)
        if not conn.autocommit:
            if rollback or mode == "read":
                conn.rollback()
            else:
                conn.commit()
    finally:
        notices = [n.strip() for n in list(conn.notices)[-_MAX_NOTICES:]]
        conn.close()
    return {
        "results": results,
        "notices": notices,
        "duration_ms": int((time.monotonic() - started) * 1000),
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
    if act == "update_row":
        return update_row(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
            payload.get("key_conditions") or {},
            payload.get("updates") or {},
        )
    if act == "delete_rows":
        return delete_rows(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
            payload.get("row_conditions") or [],
        )
    if act == "insert_row":
        return insert_row(
            dbn, owner, opw,
            payload.get("schema_name") or "public",
            payload.get("table_name") or "",
            payload.get("row_data") or {},
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


def update_row(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
    key_conditions: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    if not key_conditions or not updates:
        raise PostgresAgentError("Key conditions and updates required")
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        with conn.cursor() as cur:
            set_clauses = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in updates]
            where_clauses = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in key_conditions]
            params = list(updates.values()) + list(key_conditions.values())
            query = (
                sql.SQL("UPDATE {}.{} SET ")
                .format(sql.Identifier(schema_name), sql.Identifier(table_name))
                + sql.SQL(", ").join(set_clauses)
                + sql.SQL(" WHERE ")
                + sql.SQL(" AND ").join(where_clauses)
            )
            cur.execute(query, tuple(params))
            count = cur.rowcount
            conn.commit()
    return {"updated_count": count, "message": f"{count} Zeile(n) aktualisiert"}


def delete_rows(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
    row_conditions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not row_conditions:
        raise PostgresAgentError("Row conditions required")
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    deleted_count = 0
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        with conn.cursor() as cur:
            for cond in row_conditions:
                if not cond:
                    continue
                where_clauses = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in cond]
                params = list(cond.values())
                query = (
                    sql.SQL("DELETE FROM {}.{} WHERE ")
                    .format(sql.Identifier(schema_name), sql.Identifier(table_name))
                    + sql.SQL(" AND ").join(where_clauses)
                )
                cur.execute(query, tuple(params))
                deleted_count += max(cur.rowcount, 0)
            conn.commit()
    return {"deleted_count": deleted_count, "message": f"{deleted_count} Zeile(n) gelöscht"}


def insert_row(
    database_name: str,
    owner_role: str,
    owner_password: str,
    schema_name: str,
    table_name: str,
    row_data: dict[str, Any],
) -> dict[str, Any]:
    if not row_data:
        raise PostgresAgentError("Row data required")
    schema_name = validate_identifier(schema_name or "public")
    table_name = validate_identifier(table_name)
    columns = list(row_data.keys())
    values = list(row_data.values())
    with _owner_connect(database_name, owner_role, owner_password) as conn:
        with conn.cursor() as cur:
            col_sql = [sql.Identifier(col) for col in columns]
            val_sql = [sql.Placeholder() for _ in values]
            query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) RETURNING *").format(
                sql.Identifier(schema_name),
                sql.Identifier(table_name),
                sql.SQL(", ").join(col_sql),
                sql.SQL(", ").join(val_sql),
            )
            cur.execute(query, tuple(values))
            inserted_row = None
            if cur.description:
                cols = [desc[0] for desc in cur.description]
                fetched = cur.fetchone()
                if fetched:
                    inserted_row = dict(zip(cols, fetched, strict=False))
            conn.commit()
    return {"inserted_row": inserted_row, "message": "Zeile eingefügt"}


def dump_databases(*, admin_password: str, database_names: list[str]) -> dict[str, str]:
    """pg_dump per DB via docker exec. Returns {db_name: sql_text} (no passwords in output)."""
    if not database_names:
        return {}
    ensure_internal_postgres(admin_password)
    container = _container()
    result: dict[str, str] = {}
    for db_name in database_names:
        name = validate_identifier(db_name)
        cmd_in_container = (
            "pg_dump "
            "--format=plain --no-owner --no-acl --clean --if-exists --inserts "
            f"--dbname={shlex.quote(name)} "
            f"--port={_internal_port()} "
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
        dump_text = exec_result.get("stdout") or ""
        # PostgreSQL 17 may wrap plain dumps in psql's \restrict commands.
        # Remove only these tool-generated guards. User-controlled restore SQL
        # is still rejected below if any psql meta-command remains.
        result[name] = "\n".join(
            line
            for line in dump_text.splitlines()
            if not re.match(r"^[ \t]*\\(?:un)?restrict(?:[ \t]|$)", line)
        )
    return result


def _reject_psql_meta_commands(sql_text: str) -> None:
    if re.search(r"(?m)^[^\S\r\n]*\\", sql_text or ""):
        raise PostgresAgentError(
            "PostgreSQL restore rejects psql meta-commands",
            status_code=400,
        )


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
        _reject_psql_meta_commands(sql_text)
        owner = (owners or {}).get(name) or {}
        restore_user = validate_identifier(str(owner.get("owner_role") or ADMIN_USER))
        restore_password = str(owner.get("owner_password") or admin_password)
        result = docker_service.exec_in_managed_stdin(
            _container(),
            [
                "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1",
                "--username", restore_user, "--dbname", name,
                "--port", str(_internal_port()),
            ],
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
