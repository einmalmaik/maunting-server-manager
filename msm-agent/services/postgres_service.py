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
_PG_CAPS = ["CHOWN", "FOWNER", "SETUID", "SETGID", "DAC_OVERRIDE", "DAC_READ_SEARCH"]


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
                params = statement.get("params")
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
        # PostgreSQL 17 may wrap plain dumps in psql's \restrict commands.
        # Remove only these tool-generated guards. User-controlled restore SQL
        # is still rejected below if any psql meta-command remains.
        result[name] = _ohne_restrict(exec_result.get("stdout") or "")
    return result


def _ohne_restrict(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not re.match(r"^[ \t]*\\(?:un)?restrict(?:[ \t]|$)", line)
    )


DUMP_FORMATS = {"plain": "p", "custom": "c", "tar": "t"}
PENDING_DIR = Path(".msm") / "postgres"


def _muster(*teile: str) -> str:
    """pg_dump-Muster: in doppelten Anfuehrungszeichen gilt jedes Zeichen woertlich."""
    return ".".join('"' + teil.replace('"', '""') + '"' for teil in teile)


def dump_database(
    *,
    admin_password: str,
    database_name: str,
    fmt: str = "plain",
    schema_only: bool = False,
    data_only: bool = False,
    schemas: list[str] | None = None,
    tables: list[tuple[str, str]] | None = None,
) -> bytes:
    """pg_dump einer Datenbank. Argumente als argv, nie durch eine Shell."""
    name = validate_identifier(database_name)
    if fmt not in DUMP_FORMATS:
        raise PostgresAgentError("Invalid dump format")
    if schema_only and data_only:
        raise PostgresAgentError("schema_only and data_only exclude each other")
    ensure_internal_postgres(admin_password)
    command = [
        "pg_dump", f"--format={DUMP_FORMATS[fmt]}", "--no-owner", "--no-acl",
        f"--dbname={name}", f"--port={_internal_port()}", f"--username={ADMIN_USER}",
    ]
    if fmt == "plain":
        # INSERT statt COPY: COPY-Daten enden auf "\." und koennen mit "\"
        # beginnen — der Restore lehnt jede solche Zeile als psql-Befehl ab.
        command.append("--rows-per-insert=500")
        if not data_only:
            command += ["--clean", "--if-exists"]
    if schema_only:
        command.append("--schema-only")
    if data_only:
        command.append("--data-only")
    for schema in schemas or []:
        command.append(f"--schema={_muster(schema)}")
    for schema, table in tables or []:
        command.append(f"--table={_muster(schema, table)}")
    result = docker_service.exec_in_managed(
        _container(), command, timeout=600, environment={"PGPASSWORD": admin_password}, binary=True,
    )
    if not result.get("ok"):
        raise PostgresAgentError(f"pg_dump failed: {(result.get('error') or '')[:300]}", status_code=500)
    data = result.get("stdout_bytes") or b""
    if fmt == "plain":
        data = _ohne_restrict(data.decode("utf-8", errors="replace")).encode("utf-8")
    return data


def restore_database(
    *,
    database_name: str,
    owner_role: str,
    owner_password: str,
    fmt: str,
    data: bytes,
    clean: bool = False,
) -> dict[str, Any]:
    """Spielt einen Dump als Owner ein — alles oder nichts."""
    name = validate_identifier(database_name)
    role = validate_identifier(owner_role)
    if fmt not in DUMP_FORMATS:
        raise PostgresAgentError("Invalid dump format")
    started = time.monotonic()
    port = str(_internal_port())
    if fmt == "plain":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PostgresAgentError("SQL dump is not UTF-8", status_code=400) from exc
        text = _ohne_restrict(text)
        _reject_psql_meta_commands(text)
        command = [
            "psql", "--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--single-transaction",
            "--username", role, "--dbname", name, "--port", port,
        ]
        result = docker_service.exec_in_managed_stdin(
            _container(), command, text, environment={"PGPASSWORD": owner_password}
        )
    else:
        command = [
            "pg_restore", "--no-owner", "--no-acl", "--single-transaction", "--exit-on-error",
            "--username", role, "--dbname", name, "--port", port,
        ]
        if clean:
            command += ["--clean", "--if-exists"]
        result = docker_service.exec_in_managed_stdin(
            _container(), command, data, environment={"PGPASSWORD": owner_password}
        )
    if not result.get("ok"):
        raise PostgresAgentError(f"restore failed: {(result.get('error') or '')[:300]}", status_code=400)
    return {"ok": True, "database": name, "duration_ms": int((time.monotonic() - started) * 1000)}


def _pending_dir(server_id: int | str) -> Path:
    from services.file_service import server_root

    return server_root(server_id) / PENDING_DIR


def pending_dumps(server_id: int | str) -> list[dict[str, Any]]:
    """Dumps, die ein Backup-Restore eines Datenbankservers liegen liess."""
    folder = _pending_dir(server_id)
    if not folder.is_dir():
        return []
    return [
        {"database": path.stem, "size_bytes": path.stat().st_size, "modified": path.stat().st_mtime}
        for path in sorted(folder.glob("*.sql"))
        if path.is_file() and IDENTIFIER_RE.fullmatch(path.stem)
    ]


def pending_dump_path(server_id: int | str, database_name: str) -> Path:
    name = validate_identifier(database_name)
    path = _pending_dir(server_id) / f"{name}.sql"
    if not path.is_file():
        raise PostgresAgentError("No pending dump for this database", status_code=404)
    return path


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
