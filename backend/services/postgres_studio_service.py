"""PostgreSQL-Studio: Katalog, Daten-Grid, Überwachung, Ausführung.

Alles läuft über ``postgres_service.run`` — der Agent führt nur fertige
Anweisungen aus. Identitäten:

- **owner** (Standard): Rolle der gewählten Datenbank. Alles, was ein Mensch
  geschrieben hat, läuft so.
- **admin**: Superuser des Ziels. Im geteilten Cluster nur für die festen
  Abfragen hier (Sitzungen, Sperren), immer auf die eigene Datenbank
  gefiltert. Clusterweite Pläne (Rollen, Parameter) nur bei eigener Instanz.
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from models import PostgresDatabase, PostgresUser, Server
from schemas import postgres_studio as s
from services import postgres_ddl, postgres_service
from services.node_client import NodeClientError
from services.postgres_ddl import Plan, ident, literal, qualified

ADMIN_ROLE = "msm_admin"
CTID_KEY = "__msm_ctid"
EXPORT_PAGE = 5000
EXPORT_MAX_ROWS = 200_000
COUNT_ESTIMATE_FROM = 100_000
IMPORT_CHUNK = 500
IMPORT_MAX_PARAMS = 60_000


@dataclass
class Kontext:
    server: Server
    database: PostgresDatabase
    dedicated: bool


def kontext(db: Session, server_id: int, database_id: int) -> Kontext:
    server = db.get(Server, server_id)
    if server is None:
        raise ValueError("Server nicht gefunden.")
    database = postgres_service._database_row(db, server_id, database_id)
    return Kontext(server=server, database=database, dedicated=postgres_service.is_database_server(server))


def _run(
    db: Session,
    k: Kontext,
    statements: list[tuple[str, list[Any] | None]],
    *,
    identity: str = "owner",
    mode: str = "read",
    rollback: bool = False,
    row_limit: int = 5000,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return postgres_service.run(
        db,
        k.server,
        database_name=k.database.name,
        statements=statements,
        identity=identity,
        database=k.database,
        mode=mode,
        rollback=rollback,
        row_limit=row_limit,
        timeout_ms=timeout_ms,
    )


def _dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    columns = result.get("columns") or []
    return [dict(zip(columns, row, strict=False)) for row in result.get("rows") or []]


def _alle(db: Session, k: Kontext, statements: list[tuple[str, list[Any] | None]], **kw: Any) -> list[list[dict[str, Any]]]:
    return [_dicts(r) for r in _run(db, k, statements, **kw)["results"]]


def managed_roles(db: Session, server_id: int) -> set[str]:
    owners = {d.owner_role for d in db.query(PostgresDatabase).filter(PostgresDatabase.server_id == server_id)}
    users = {u.username for u in db.query(PostgresUser).filter(PostgresUser.server_id == server_id)}
    return owners | users


# ── Übersicht und Fähigkeiten ──────────────────────────────────────────────


def overview(db: Session, k: Kontext) -> dict[str, Any]:
    info, schemas = _alle(
        db,
        k,
        [
            (
                "SELECT current_setting('server_version_num')::int AS version_num, "
                "current_setting('server_version') AS version, current_user AS role, "
                "current_database() AS database, pg_database_size(current_database()) AS size_bytes",
                None,
            ),
            (
                "SELECT n.nspname AS name, pg_get_userbyid(n.nspowner) AS owner, "
                "has_schema_privilege(n.oid, 'CREATE') AS can_create, "
                "(SELECT count(*) FROM pg_class c WHERE c.relnamespace = n.oid AND c.relkind IN ('r','p')) AS table_count "
                "FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' "
                "ORDER BY n.nspname",
                None,
            ),
        ],
    )
    return {
        "kind": "dedicated" if k.dedicated else "shared",
        "server": info[0] if info else {},
        "schemas": schemas,
    }


# ── Katalog ────────────────────────────────────────────────────────────────

_NOT_EXTENSION_MEMBER = (
    "NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = '{cls}'::regclass "
    "AND d.objid = {obj} AND d.deptype = 'e')"
)


def schema_objects(db: Session, k: Kontext, schema_name: str) -> dict[str, Any]:
    p = [schema_name]
    relations, sequences, enums, functions, triggers = _alle(
        db,
        k,
        [
            (
                "SELECT c.oid::bigint AS oid, c.relname AS name, c.relkind AS kind, "
                "c.reltuples::bigint AS estimated_rows, "
                "CASE WHEN c.relkind IN ('r','p','m','S') THEN pg_total_relation_size(c.oid) END AS size_bytes, "
                "c.relispartition AS is_partition, c.relrowsecurity AS rls_enabled, "
                "obj_description(c.oid, 'pg_class') AS comment, "
                "(SELECT pc.relname FROM pg_inherits i JOIN pg_class pc ON pc.oid = i.inhparent "
                " WHERE i.inhrelid = c.oid LIMIT 1) AS parent "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','S','f') "
                "AND " + _NOT_EXTENSION_MEMBER.format(cls="pg_class", obj="c.oid") + " "
                "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d "
                " WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'i')) "
                "ORDER BY c.relname",
                p,
            ),
            (
                "SELECT q.sequencename AS name, q.last_value, q.start_value, q.increment_by, "
                "q.min_value, q.max_value, q.cycle FROM pg_sequences q "
                "WHERE q.schemaname = %s AND NOT EXISTS (SELECT 1 FROM pg_depend d "
                " WHERE d.classid = 'pg_class'::regclass AND d.deptype = 'i' "
                " AND d.objid = (quote_ident(q.schemaname) || '.' || quote_ident(q.sequencename))::regclass) "
                "ORDER BY q.sequencename",
                p,
            ),
            (
                "SELECT t.typname AS name, array_agg(e.enumlabel::text ORDER BY e.enumsortorder) AS values "
                "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "JOIN pg_enum e ON e.enumtypid = t.oid WHERE n.nspname = %s "
                "GROUP BY t.typname ORDER BY t.typname",
                p,
            ),
            (
                "SELECT p.oid::bigint AS oid, p.proname AS name, "
                "pg_get_function_identity_arguments(p.oid) AS arguments, "
                "pg_get_function_result(p.oid) AS returns, l.lanname AS language, "
                "p.prokind AS kind, p.provolatile AS volatility, p.prosecdef AS security_definer "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "JOIN pg_language l ON l.oid = p.prolang WHERE n.nspname = %s "
                "AND " + _NOT_EXTENSION_MEMBER.format(cls="pg_proc", obj="p.oid") + " "
                "ORDER BY p.proname, 3",
                p,
            ),
            (
                "SELECT t.tgname AS name, c.relname AS table, t.tgenabled <> 'D' AS enabled, "
                "pg_get_triggerdef(t.oid, true) AS definition, pn.nspname AS function_schema, "
                "pp.proname AS function_name "
                "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_proc pp ON pp.oid = t.tgfoid JOIN pg_namespace pn ON pn.oid = pp.pronamespace "
                "WHERE n.nspname = %s AND NOT t.tgisinternal ORDER BY c.relname, t.tgname",
                p,
            ),
        ],
    )
    kinds = {"r": "table", "p": "partitioned_table", "v": "view", "m": "materialized_view", "S": "sequence", "f": "foreign_table"}
    for rel in relations:
        rel["kind"] = kinds.get(rel["kind"], rel["kind"])
    return {
        "schema": schema_name,
        "relations": relations,
        "sequences": sequences,
        "enums": enums,
        "functions": functions,
        "triggers": triggers,
    }


_TABLE_CTE = (
    "WITH t AS (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %s AND c.relname = %s) "
)
_FK_ACTIONS = {"a": "no_action", "r": "restrict", "c": "cascade", "n": "set_null", "d": "set_default"}
_POLICY_COMMANDS = {"r": "select", "a": "insert", "w": "update", "d": "delete", "*": "all"}
_CONSTRAINT_TYPES = {"p": "primary_key", "f": "foreign_key", "u": "unique", "c": "check", "x": "exclusion", "t": "trigger", "n": "not_null"}


def _key_columns(array_col: str, rel_col: str) -> str:
    return (
        f"ARRAY(SELECT a.attname::text FROM unnest({array_col}) WITH ORDINALITY k(num, ord) "
        f"JOIN pg_attribute a ON a.attrelid = {rel_col} AND a.attnum = k.num ORDER BY k.ord)"
    )


def table_details(db: Session, k: Kontext, schema_name: str, table: str) -> dict[str, Any]:
    p = [schema_name, table]
    (relation, columns, constraints, referenced_by, indexes, triggers, policies, partitions, grants, stats) = _alle(
        db,
        k,
        [
            (
                _TABLE_CTE + "SELECT c.oid::bigint AS oid, c.relkind AS kind, c.relrowsecurity AS rls_enabled, "
                "c.relforcerowsecurity AS rls_forced, obj_description(c.oid, 'pg_class') AS comment, "
                "c.reltuples::bigint AS estimated_rows, pg_get_userbyid(c.relowner) AS owner, "
                "CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) END AS partition_key, "
                "CASE WHEN c.relispartition THEN pg_get_expr(c.relpartbound, c.oid) END AS partition_bound, "
                "CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, true) END AS view_definition, "
                "CASE WHEN c.relkind IN ('r','p','m') THEN pg_total_relation_size(c.oid) END AS size_bytes, "
                "CASE WHEN c.relkind IN ('r','p','m') THEN pg_indexes_size(c.oid) END AS index_size_bytes "
                "FROM pg_class c WHERE c.oid = (SELECT oid FROM t)",
                p,
            ),
            (
                _TABLE_CTE + "SELECT a.attnum AS position, a.attname AS name, "
                "format_type(a.atttypid, a.atttypmod) AS type, NOT a.attnotnull AS nullable, "
                "pg_get_expr(d.adbin, d.adrelid) AS default, a.attidentity AS identity, "
                "a.attgenerated AS generated, col_description(a.attrelid, a.attnum) AS comment, "
                "ty.typcategory AS category, "
                "CASE WHEN ty.typtype = 'e' THEN ARRAY(SELECT e.enumlabel::text FROM pg_enum e "
                " WHERE e.enumtypid = ty.oid ORDER BY e.enumsortorder) END AS enum_values "
                "FROM pg_attribute a JOIN pg_type ty ON ty.oid = a.atttypid "
                "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
                "WHERE a.attrelid = (SELECT oid FROM t) AND a.attnum > 0 AND NOT a.attisdropped "
                "ORDER BY a.attnum",
                p,
            ),
            (
                _TABLE_CTE + "SELECT con.conname AS name, con.contype AS type, "
                "pg_get_constraintdef(con.oid, true) AS definition, "
                + _key_columns("con.conkey", "con.conrelid") + " AS columns, "
                "fn.nspname AS ref_schema, fc.relname AS ref_table, "
                + _key_columns("con.confkey", "con.confrelid") + " AS ref_columns, "
                "con.confdeltype AS on_delete, con.confupdtype AS on_update, con.condeferrable AS deferrable "
                "FROM pg_constraint con LEFT JOIN pg_class fc ON fc.oid = con.confrelid "
                "LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace "
                "WHERE con.conrelid = (SELECT oid FROM t) ORDER BY con.contype, con.conname",
                p,
            ),
            (
                _TABLE_CTE + "SELECT con.conname AS name, n.nspname AS schema, c.relname AS table, "
                + _key_columns("con.conkey", "con.conrelid") + " AS columns, "
                + _key_columns("con.confkey", "con.confrelid") + " AS ref_columns "
                "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE con.contype = 'f' AND con.confrelid = (SELECT oid FROM t) ORDER BY 2, 3, 1",
                p,
            ),
            (
                _TABLE_CTE + "SELECT ic.relname AS name, pg_get_indexdef(i.indexrelid) AS definition, "
                "i.indisunique AS unique, i.indisprimary AS primary, i.indisvalid AS valid, "
                "am.amname AS method, pg_relation_size(i.indexrelid) AS size_bytes, st.idx_scan AS scans "
                "FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid "
                "JOIN pg_am am ON am.oid = ic.relam "
                "LEFT JOIN pg_stat_all_indexes st ON st.indexrelid = i.indexrelid "
                "WHERE i.indrelid = (SELECT oid FROM t) ORDER BY ic.relname",
                p,
            ),
            (
                _TABLE_CTE + "SELECT tg.tgname AS name, tg.tgenabled <> 'D' AS enabled, "
                "pg_get_triggerdef(tg.oid, true) AS definition "
                "FROM pg_trigger tg WHERE tg.tgrelid = (SELECT oid FROM t) AND NOT tg.tgisinternal "
                "ORDER BY tg.tgname",
                p,
            ),
            (
                _TABLE_CTE + "SELECT pol.polname AS name, pol.polcmd AS command, pol.polpermissive AS permissive, "
                "CASE WHEN pol.polroles = '{0}'::oid[] THEN ARRAY['PUBLIC'] "
                " ELSE ARRAY(SELECT r.rolname::text FROM pg_roles r WHERE r.oid = ANY(pol.polroles) ORDER BY 1) END AS roles, "
                "pg_get_expr(pol.polqual, pol.polrelid) AS using, "
                "pg_get_expr(pol.polwithcheck, pol.polrelid) AS with_check "
                "FROM pg_policy pol WHERE pol.polrelid = (SELECT oid FROM t) ORDER BY pol.polname",
                p,
            ),
            (
                _TABLE_CTE + "SELECT c.relname AS name, pg_get_expr(c.relpartbound, c.oid) AS bound, "
                "c.reltuples::bigint AS estimated_rows "
                "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = (SELECT oid FROM t) ORDER BY c.relname",
                p,
            ),
            (
                _TABLE_CTE + "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee)::text END AS grantee, "
                "a.privilege_type AS privilege, a.is_grantable AS grantable "
                "FROM pg_class c, aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a "
                "WHERE c.oid = (SELECT oid FROM t) ORDER BY 1, 2",
                p,
            ),
            (
                _TABLE_CTE + "SELECT n_live_tup AS live, n_dead_tup AS dead, last_vacuum, last_autovacuum, "
                "last_analyze, last_autoanalyze, seq_scan, idx_scan, n_tup_ins AS inserted, "
                "n_tup_upd AS updated, n_tup_del AS deleted "
                "FROM pg_stat_all_tables WHERE relid = (SELECT oid FROM t)",
                p,
            ),
        ],
    )
    if not relation:
        raise ValueError(f"Tabelle {schema_name}.{table} nicht gefunden.")
    for con in constraints:
        con["type"] = _CONSTRAINT_TYPES.get(con["type"], con["type"])
        if con["type"] == "foreign_key":
            con["on_delete"] = _FK_ACTIONS.get(con["on_delete"], con["on_delete"])
            con["on_update"] = _FK_ACTIONS.get(con["on_update"], con["on_update"])
        else:
            con["on_delete"] = con["on_update"] = None
    for pol in policies:
        pol["command"] = _POLICY_COMMANDS.get(pol["command"], pol["command"])
    for col in columns:
        col["identity"] = {"a": "always", "d": "by_default"}.get(col["identity"] or "")
        col["generated"] = bool(col["generated"])
    primary = next((c for c in constraints if c["type"] == "primary_key"), None)
    return {
        "schema": schema_name,
        "table": table,
        **relation[0],
        "primary_key": primary["columns"] if primary else [],
        "columns": columns,
        "constraints": constraints,
        "referenced_by": referenced_by,
        "indexes": indexes,
        "triggers": triggers,
        "policies": policies,
        "partitions": partitions,
        "grants": grants,
        "stats": stats[0] if stats else None,
    }


def function_definition(db: Session, k: Kontext, oid: int) -> dict[str, Any]:
    rows = _alle(
        db,
        k,
        [
            (
                "SELECT p.oid::bigint AS oid, n.nspname AS schema, p.proname AS name, "
                "pg_get_function_identity_arguments(p.oid) AS arguments, "
                "pg_get_functiondef(p.oid) AS definition "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.oid = %s::oid AND p.prokind IN ('f','p') "
                "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'",
                [int(oid)],
            )
        ],
    )[0]
    if not rows:
        raise ValueError("Funktion nicht gefunden.")
    return rows[0]


def extensions(db: Session, k: Kontext) -> list[dict[str, Any]]:
    rows = _alle(
        db,
        k,
        [
            (
                "SELECT a.name, a.default_version, a.installed_version, a.comment, "
                "COALESCE(v.trusted, false) AS trusted, COALESCE(v.superuser, true) AS superuser, "
                "(SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                " WHERE e.extname = a.name) AS schema "
                "FROM pg_available_extensions a LEFT JOIN pg_available_extension_versions v "
                "ON v.name = a.name AND v.version = a.default_version ORDER BY a.name",
                None,
            )
        ],
    )[0]
    for row in rows:
        # Geteilter Cluster: der Owner installiert, PostgreSQL erlaubt ihm nur
        # „trusted". Eigene Instanz: der Admin installiert alles Verfügbare.
        row["installable"] = bool(k.dedicated or row["trusted"] or not row["superuser"])
    return rows


def roles(db: Session, k: Kontext) -> list[dict[str, Any]]:
    managed = managed_roles(db, k.server.id)
    base = (
        "SELECT r.rolname::text AS name, r.rolsuper AS superuser, r.rolinherit AS inherit, "
        "r.rolcreaterole AS createrole, r.rolcreatedb AS createdb, r.rolcanlogin AS login, "
        "r.rolreplication AS replication, r.rolbypassrls AS bypassrls, "
        "r.rolconnlimit AS connection_limit, r.rolvaliduntil AS valid_until, "
        "ARRAY(SELECT b.rolname::text FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid "
        " WHERE m.member = r.oid ORDER BY 1) AS member_of "
        "FROM pg_roles r "
    )
    if k.dedicated:
        statement = (base + "WHERE r.rolname !~ '^pg_' ORDER BY r.rolname", None)
    else:
        # Im geteilten Cluster nur die eigenen Rollen — fremde Namen gehen
        # dieses Studio nichts an.
        statement = (base + "WHERE r.rolname::text = ANY(%s) ORDER BY r.rolname", [sorted(managed)])
    rows = _alle(db, k, [statement])[0]
    for row in rows:
        row["managed"] = row["name"] in managed or row["name"] == ADMIN_ROLE
        row["system"] = row["name"] == ADMIN_ROLE
    return rows


def schema_grants(db: Session, k: Kontext, schema_name: str) -> dict[str, Any]:
    objects, schema_level = _alle(
        db,
        k,
        [
            (
                "SELECT c.relname AS object, c.relkind AS kind, "
                "CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee)::text END AS grantee, "
                "a.privilege_type AS privilege, a.is_grantable AS grantable "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, "
                "aclexplode(COALESCE(c.relacl, acldefault((CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END)::\"char\", c.relowner))) a "
                "WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m','S','f') ORDER BY 1, 3, 4",
                [schema_name],
            ),
            (
                "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee)::text END AS grantee, "
                "a.privilege_type AS privilege, a.is_grantable AS grantable "
                "FROM pg_namespace n, aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a "
                "WHERE n.nspname = %s ORDER BY 1, 2",
                [schema_name],
            ),
        ],
    )
    return {"schema": schema_name, "objects": objects, "schema_grants": schema_level}


# ── Instanz (nur eigene) ───────────────────────────────────────────────────


def _nur_instanz(k: Kontext) -> None:
    if not k.dedicated:
        raise ValueError("Nur bei einem eigenen Datenbankserver verfügbar.")


def parameters(db: Session, k: Kontext) -> list[dict[str, Any]]:
    _nur_instanz(k)
    rows = _alle(
        db,
        k,
        [
            (
                "SELECT name, setting, unit, category, short_desc AS description, context, vartype, "
                "min_val, max_val, enumvals, boot_val, reset_val, source, pending_restart "
                "FROM pg_settings WHERE context <> 'internal' ORDER BY category, name",
                None,
            )
        ],
        identity="admin",
    )[0]
    for row in rows:
        row["editable"] = not postgres_ddl.parameter_blocked(row["name"])
    return rows


# ── Überwachung ────────────────────────────────────────────────────────────


def _datenbankfilter(k: Kontext) -> list[Any]:
    # Geteilter Cluster: nur Sitzungen der eigenen Datenbank. Eigene Instanz:
    # alle — sie gehört ganz diesem Server.
    name = None if k.dedicated else k.database.name
    return [name, name]


def sessions(db: Session, k: Kontext) -> list[dict[str, Any]]:
    return _alle(
        db,
        k,
        [
            (
                "SELECT a.pid, a.datname::text AS database, a.usename::text AS role, a.application_name, "
                "a.client_addr::text AS client, a.backend_start, a.xact_start, a.query_start, a.state, "
                "a.wait_event_type, a.wait_event, left(a.query, 4000) AS query, "
                "pg_blocking_pids(a.pid) AS blocked_by, "
                "EXTRACT(EPOCH FROM (clock_timestamp() - a.query_start))::float8 AS query_seconds "
                "FROM pg_stat_activity a WHERE a.backend_type = 'client backend' "
                "AND a.pid <> pg_backend_pid() AND (%s::text IS NULL OR a.datname = %s) "
                "ORDER BY a.query_start NULLS LAST",
                _datenbankfilter(k),
            )
        ],
        identity="admin",
    )[0]


def locks(db: Session, k: Kontext) -> list[dict[str, Any]]:
    return _alle(
        db,
        k,
        [
            (
                "SELECT l.pid, l.locktype, l.mode, l.granted, "
                "CASE WHEN l.relation IS NOT NULL AND l.database = "
                " (SELECT oid FROM pg_database WHERE datname = current_database()) "
                " THEN l.relation::regclass::text END AS relation, "
                "a.usename::text AS role, a.datname::text AS database, a.state, "
                "left(a.query, 2000) AS query, pg_blocking_pids(l.pid) AS blocked_by "
                "FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                "WHERE l.pid <> pg_backend_pid() AND (%s::text IS NULL OR a.datname = %s) "
                "ORDER BY l.granted, l.pid",
                _datenbankfilter(k),
            )
        ],
        identity="admin",
    )[0]


def session_action(db: Session, k: Kontext, pid: int, action: str) -> bool:
    function = {"cancel": "pg_cancel_backend", "terminate": "pg_terminate_backend"}[action]
    # Prüfung und Wirkung in einer Anweisung: keine Lücke, in der die PID
    # einer fremden Sitzung gehören könnte.
    rows = _alle(
        db,
        k,
        [
            (
                f"SELECT {function}(a.pid) AS ok FROM pg_stat_activity a "
                "WHERE a.pid = %s AND a.backend_type = 'client backend' "
                "AND a.pid <> pg_backend_pid() AND (%s::text IS NULL OR a.datname = %s)",
                [int(pid), *_datenbankfilter(k)],
            )
        ],
        identity="admin",
        mode="tx",
    )[0]
    if not rows:
        raise ValueError("Sitzung nicht gefunden.")
    return bool(rows[0]["ok"])


def health(db: Session, k: Kontext) -> dict[str, Any]:
    database, tables, unused, index_io = _alle(
        db,
        k,
        [
            (
                "SELECT blks_hit, blks_read, xact_commit, xact_rollback, deadlocks, temp_files, "
                "temp_bytes, conflicts, pg_database_size(datname) AS size_bytes, stats_reset "
                "FROM pg_stat_database WHERE datname = current_database()",
                None,
            ),
            (
                "SELECT schemaname AS schema, relname AS table, n_live_tup AS live, n_dead_tup AS dead, "
                "CASE WHEN n_live_tup + n_dead_tup > 0 "
                " THEN (n_dead_tup::float8 / (n_live_tup + n_dead_tup)) END AS dead_ratio, "
                "last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, seq_scan, idx_scan, "
                "pg_total_relation_size(relid) AS size_bytes "
                "FROM pg_stat_user_tables ORDER BY n_dead_tup DESC, pg_total_relation_size(relid) DESC LIMIT 100",
                None,
            ),
            (
                "SELECT st.schemaname AS schema, st.relname AS table, st.indexrelname AS index, "
                "st.idx_scan AS scans, pg_relation_size(st.indexrelid) AS size_bytes "
                "FROM pg_stat_user_indexes st JOIN pg_index i ON i.indexrelid = st.indexrelid "
                "WHERE st.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary "
                "ORDER BY pg_relation_size(st.indexrelid) DESC LIMIT 100",
                None,
            ),
            (
                "SELECT COALESCE(sum(idx_blks_hit), 0)::float8 AS hit, "
                "COALESCE(sum(idx_blks_read), 0)::float8 AS read FROM pg_statio_user_indexes",
                None,
            ),
        ],
    )
    stats = database[0] if database else {}

    def ratio(hit: Any, read: Any) -> float | None:
        hit_f, read_f = float(hit or 0), float(read or 0)
        return hit_f / (hit_f + read_f) if hit_f + read_f > 0 else None

    return {
        "database": stats,
        "cache_hit_ratio": ratio(stats.get("blks_hit"), stats.get("blks_read")),
        "index_hit_ratio": ratio(index_io[0]["hit"], index_io[0]["read"]) if index_io else None,
        "tables": tables,
        "unused_indexes": unused,
    }


# ── SQL und EXPLAIN ────────────────────────────────────────────────────────


def run_sql(db: Session, k: Kontext, req: s.StudioSqlRequest) -> dict[str, Any]:
    statements = postgres_service._split_sql_statements(req.sql)
    if not statements:
        raise ValueError("Keine Anweisung gefunden.")
    if len(statements) > 200:
        raise ValueError("Höchstens 200 Anweisungen auf einmal.")
    result = _run(
        db,
        k,
        [(text, None) for text in statements],
        mode="autocommit" if req.autocommit else "tx",
        rollback=req.rollback,
        row_limit=req.row_limit,
    )
    return {**result, "rolled_back": req.rollback}


def explain(db: Session, k: Kontext, req: s.StudioExplainRequest) -> dict[str, Any]:
    statements = postgres_service._split_sql_statements(req.sql)
    if len(statements) != 1:
        raise ValueError("EXPLAIN braucht genau eine Anweisung.")
    if req.analyze:
        # ANALYZE führt die Abfrage aus — am Ende wird alles verworfen.
        text = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statements[0]}"
        result = _run(db, k, [(text, None)], mode="tx", rollback=True)
    else:
        text = f"EXPLAIN (VERBOSE, FORMAT JSON) {statements[0]}"
        result = _run(db, k, [(text, None)], mode="read")
    rows = result["results"][0]["rows"]
    plan = rows[0][0] if rows else None
    if isinstance(plan, str):
        plan = json.loads(plan)
    return {"plan": plan, "analyzed": req.analyze, "duration_ms": result.get("duration_ms", 0)}


# ── Strukturänderungen ─────────────────────────────────────────────────────


def _pruefe_rollen(db: Session, k: Kontext, plan: Plan, op: Any) -> None:
    if not plan.roles:
        return
    names = set(plan.roles)
    if ADMIN_ROLE in names:
        raise ValueError(f"{ADMIN_ROLE} verwaltet das Panel.")
    managed = managed_roles(db, k.server.id)
    if not k.dedicated:
        foreign = sorted(names - managed)
        if foreign:
            raise ValueError(f"Unbekannte Rolle für diesen Server: {', '.join(foreign)}")
        return
    if op.op in {"create_role", "alter_role", "drop_role"}:
        target = op.name
        if target.startswith("pg_") or target.startswith("msm_"):
            raise ValueError("Namen mit pg_ oder msm_ sind reserviert.")
        if target in managed and op.op != "create_role":
            raise ValueError(f"{target} verwaltet das Panel — Passwort und Löschen über die Datenbankverwaltung.")
        memberships = set(getattr(op, "member_of", []) or []) | set(getattr(op, "grant_membership", []) or [])
        blocked = sorted(m for m in memberships if m.startswith("pg_") and m not in postgres_ddl.ALLOWED_SYSTEM_ROLES)
        if blocked:
            raise ValueError(f"Mitgliedschaft nicht erlaubt: {', '.join(blocked)}")


def _pruefe_parameter(db: Session, k: Kontext, plan: Plan) -> None:
    if not plan.parameter:
        return
    rows = _alle(
        db,
        k,
        [("SELECT context FROM pg_settings WHERE name = %s", [plan.parameter])],
        identity="admin",
    )[0]
    if not rows or rows[0]["context"] == "internal":
        raise ValueError(f"Unbekannter oder fester Parameter: {plan.parameter}")


def plan_for(db: Session, k: Kontext, op: Any) -> Plan:
    plan = postgres_ddl.compile_operation(op, dedicated=k.dedicated)
    if plan.scope == "instance":
        _nur_instanz(k)
    _pruefe_rollen(db, k, plan, op)
    _pruefe_parameter(db, k, plan)
    return plan


def plan_response(plan: Plan) -> dict[str, Any]:
    return {
        "statements": plan.preview(),
        "mode": plan.mode,
        "identity": plan.identity,
        "scope": plan.scope,
        "destructive": plan.destructive,
        "permission": plan.permission,
    }


def execute(db: Session, k: Kontext, op: Any) -> dict[str, Any]:
    plan = plan_for(db, k, op)
    result = _run(
        db,
        k,
        [(text, None) for text in plan.statements],
        identity=plan.identity,
        mode=plan.mode,
        row_limit=100,
    )
    return {"plan": plan_response(plan), **result}


# ── Daten-Grid ─────────────────────────────────────────────────────────────
#
# Diese Anweisungen tragen immer eine Parameterliste: psycopg2 setzt dann
# Platzhalter ein, und ein woertliches % in einem Namen muss doppelt stehen.


def _pi(name: str) -> str:
    return ident(name).replace("%", "%%")


def _pq(schema_name: str, name: str) -> str:
    return qualified(schema_name, name).replace("%", "%%")


def _meta(db: Session, k: Kontext, schema_name: str, table: str) -> dict[str, Any]:
    rows = _alle(
        db,
        k,
        [
            (
                _TABLE_CTE + "SELECT c.relkind AS kind, c.reltuples::bigint AS estimated_rows, "
                "ARRAY(SELECT a.attname::text FROM pg_index i JOIN pg_attribute a "
                " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                " WHERE i.indrelid = c.oid AND i.indisprimary "
                " ORDER BY array_position(i.indkey::int2[], a.attnum)) AS primary_key, "
                "ARRAY(SELECT a.attname::text FROM pg_attribute a WHERE a.attrelid = c.oid "
                " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum) AS columns, "
                "ARRAY(SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a WHERE a.attrelid = c.oid "
                " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum) AS types "
                "FROM pg_class c WHERE c.oid = (SELECT oid FROM t)",
                [schema_name, table],
            )
        ],
    )[0]
    if not rows:
        raise ValueError(f"Tabelle {schema_name}.{table} nicht gefunden.")
    meta = rows[0]
    kind = meta["kind"]
    if kind in {"r", "p", "f"} and meta["primary_key"]:
        meta["key"] = list(meta["primary_key"])
    elif kind == "r":
        # Ohne Primärschlüssel trägt die physische Zeilenadresse die Bearbeitung.
        meta["key"] = [CTID_KEY]
    else:
        meta["key"] = []
    meta["editable"] = bool(meta["key"])
    return meta


def _param_wert(value: Any) -> Any:
    """Werte als untypisiertes Literal — PostgreSQL wandelt in den Spaltentyp."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


_OPERATORS = {"eq": "=", "neq": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}


def _where(filters: list[s.RowFilter], columns: list[str]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for f in filters:
        if f.column not in columns:
            raise ValueError(f"Unbekannte Spalte: {f.column}")
        col = _pi(f.column)
        if f.operator in _OPERATORS:
            clauses.append(f"{col} {_OPERATORS[f.operator]} %s")
            params.append(_param_wert(f.value))
        elif f.operator in {"like", "ilike", "not_like"}:
            verb = {"like": "LIKE", "ilike": "ILIKE", "not_like": "NOT LIKE"}[f.operator]
            clauses.append(f"{col}::text {verb} %s")
            params.append(_param_wert(f.value))
        elif f.operator == "is_null":
            clauses.append(f"{col} IS NULL")
        elif f.operator == "not_null":
            clauses.append(f"{col} IS NOT NULL")
        elif f.operator == "in":
            values = f.value if isinstance(f.value, list) else [f.value]
            clauses.append(f"{col}::text = ANY(%s::text[])")
            params.append([_param_wert(v) for v in values])
        elif f.operator == "contains":
            clauses.append(f"{col}::jsonb @> %s::jsonb")
            value = f.value if isinstance(f.value, str) else json.dumps(f.value)
            params.append(value)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _order(sort: list[s.RowSort], columns: list[str], fallback: list[str]) -> str:
    items = []
    for item in sort:
        if item.column not in columns:
            raise ValueError(f"Unbekannte Spalte: {item.column}")
        items.append(f"{_pi(item.column)}{' DESC' if item.descending else ''}")
    if not items:
        items = ["ctid" if c == CTID_KEY else _pi(c) for c in fallback]
    return (" ORDER BY " + ", ".join(items)) if items else ""


def _select_list(meta: dict[str, Any]) -> str:
    return (f"ctid::text AS {_pi(CTID_KEY)}, *") if meta["key"] == [CTID_KEY] else "*"


def rows(db: Session, k: Kontext, req: s.StudioRowsRequest) -> dict[str, Any]:
    meta = _meta(db, k, req.schema_name, req.table)
    table = _pq(req.schema_name, req.table)
    where, params = _where(req.filters, meta["columns"])
    estimate = int(meta["estimated_rows"] or 0)
    use_estimate = not req.filters and estimate >= COUNT_ESTIMATE_FROM
    statements: list[tuple[str, list[Any] | None]] = [
        (
            f"SELECT {_select_list(meta)} FROM {table}{where}"
            f"{_order(req.sort, meta['columns'], meta['key'])} LIMIT {int(req.limit)} OFFSET {int(req.offset)}",
            params,
        )
    ]
    if not use_estimate:
        statements.append((f"SELECT count(*) AS total FROM {table}{where}", params))
    results = _run(db, k, statements, row_limit=req.limit)["results"]
    data = results[0]
    total = estimate if use_estimate else int(results[1]["rows"][0][0])
    return {
        "columns": data["columns"],
        "types": meta["types"],
        "rows": data["rows"],
        "total": total,
        "total_is_estimate": use_estimate,
        "key": meta["key"],
        "editable": meta["editable"],
    }


def _key_clause(meta: dict[str, Any], key: s.StudioRowKey) -> tuple[str, list[Any]]:
    if not meta["editable"]:
        raise ValueError("Diese Tabelle hat keinen Zeilenschlüssel — Bearbeitung nur über SQL.")
    if set(key.values) != set(meta["key"]):
        raise ValueError("Zeilenschlüssel passt nicht zur Tabelle.")
    parts = []
    params = []
    for name in meta["key"]:
        parts.append("ctid = %s::tid" if name == CTID_KEY else f"{_pi(name)} = %s")
        params.append(_param_wert(key.values[name]))
    return "(" + " AND ".join(parts) + ")", params


def _returning(meta: dict[str, Any]) -> str:
    return f" RETURNING {_select_list(meta)}"


def _one_row(result: dict[str, Any]) -> dict[str, Any]:
    return {"columns": result["columns"], "row": result["rows"][0] if result["rows"] else None}


def insert_row(db: Session, k: Kontext, req: s.StudioRowInsertRequest) -> dict[str, Any]:
    meta = _meta(db, k, req.schema_name, req.table)
    unknown = [c for c in req.values if c not in meta["columns"]]
    if unknown:
        raise ValueError(f"Unbekannte Spalte: {', '.join(unknown)}")
    table = _pq(req.schema_name, req.table)
    if req.values:
        cols = ", ".join(_pi(c) for c in req.values)
        marks = ", ".join(["%s"] * len(req.values))
        statement = f"INSERT INTO {table} ({cols}) VALUES ({marks}){_returning(meta)}"
        params = [_param_wert(v) for v in req.values.values()]
    else:
        statement, params = f"INSERT INTO {table} DEFAULT VALUES{_returning(meta)}", []
    result = _run(db, k, [(statement, params)], mode="tx", row_limit=1)["results"][0]
    return _one_row(result)


def update_row(db: Session, k: Kontext, req: s.StudioRowUpdateRequest) -> dict[str, Any]:
    meta = _meta(db, k, req.schema_name, req.table)
    unknown = [c for c in req.changes if c not in meta["columns"]]
    if unknown:
        raise ValueError(f"Unbekannte Spalte: {', '.join(unknown)}")
    clause, key_params = _key_clause(meta, req.key)
    sets = ", ".join(f"{_pi(c)} = %s" for c in req.changes)
    params = [_param_wert(v) for v in req.changes.values()] + key_params
    statement = f"UPDATE {_pq(req.schema_name, req.table)} SET {sets} WHERE {clause}{_returning(meta)}"
    result = _run(db, k, [(statement, params)], mode="tx", row_limit=1)["results"][0]
    if not result["rows"]:
        raise ValueError("Zeile nicht mehr vorhanden — bitte neu laden.")
    return _one_row(result)


def delete_rows(db: Session, k: Kontext, req: s.StudioRowDeleteRequest) -> dict[str, Any]:
    meta = _meta(db, k, req.schema_name, req.table)
    clauses: list[str] = []
    params: list[Any] = []
    for key in req.keys:
        clause, key_params = _key_clause(meta, key)
        clauses.append(clause)
        params += key_params
    statement = f"DELETE FROM {_pq(req.schema_name, req.table)} WHERE " + " OR ".join(clauses)
    result = _run(db, k, [(statement, params)], mode="tx", row_limit=1)["results"][0]
    return {"deleted": int(result.get("row_count") or 0)}


def import_rows(db: Session, k: Kontext, req: s.StudioImportRequest) -> dict[str, Any]:
    meta = _meta(db, k, req.schema_name, req.table)
    unknown = [c for c in req.columns if c not in meta["columns"]]
    if unknown:
        raise ValueError(f"Unbekannte Spalte: {', '.join(unknown)}")
    if len(set(req.columns)) != len(req.columns):
        raise ValueError("Spalten doppelt.")
    width = len(req.columns)
    for index, row in enumerate(req.rows):
        if len(row) != width:
            raise ValueError(f"Zeile {index + 1}: {len(row)} Werte statt {width}.")
    table = _pq(req.schema_name, req.table)
    cols = ", ".join(_pi(c) for c in req.columns)
    row_marks = "(" + ", ".join(["%s"] * width) + ")"
    statements: list[tuple[str, list[Any] | None]] = []
    step = max(1, min(IMPORT_CHUNK, IMPORT_MAX_PARAMS // width))
    for start in range(0, len(req.rows), step):
        chunk = req.rows[start : start + step]
        marks = ", ".join([row_marks] * len(chunk))
        params = [_param_wert(v) for row in chunk for v in row]
        statements.append((f"INSERT INTO {table} ({cols}) VALUES {marks}", params))
    # Eine Transaktion: entweder alle Zeilen oder keine.
    results = _run(db, k, statements, mode="tx", row_limit=1)["results"]
    return {"inserted": sum(int(r.get("row_count") or 0) for r in results)}


def export_rows(db: Session, k: Kontext, req: s.StudioExportRequest) -> tuple[bytes, str, bool]:
    meta = _meta(db, k, req.schema_name, req.table)
    table = _pq(req.schema_name, req.table)
    where, params = _where(req.filters, meta["columns"])
    order = _order(req.sort, meta["columns"], meta["key"])
    columns: list[str] = []
    rows_out: list[list[Any]] = []
    truncated = False
    # Ohne stabile Reihenfolge wäre seitenweises Lesen lückenhaft: dann nur eine Seite.
    stable = bool(order)
    offset = 0
    while True:
        statement = f"SELECT * FROM {table}{where}{order} LIMIT {EXPORT_PAGE} OFFSET {offset}"
        page = _run(db, k, [(statement, params)], row_limit=EXPORT_PAGE)["results"][0]
        columns = page["columns"]
        rows_out.extend(page["rows"])
        if len(page["rows"]) < EXPORT_PAGE:
            break
        if not stable or len(rows_out) >= EXPORT_MAX_ROWS:
            truncated = True
            break
        offset += EXPORT_PAGE
    if req.format == "json":
        payload = json.dumps([dict(zip(columns, r, strict=False)) for r in rows_out], ensure_ascii=False, indent=2)
        return payload.encode("utf-8"), "application/json", truncated
    if req.format == "sql":
        table = qualified(req.schema_name, req.table)
        cols = ", ".join(ident(c) for c in columns)
        lines = [
            f"INSERT INTO {table} ({cols}) VALUES ("
            + ", ".join(_sql_wert(v) for v in row)
            + ");"
            for row in rows_out
        ]
        return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"), "application/sql", truncated
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows_out:
        writer.writerow(["" if v is None else json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row])
    return buffer.getvalue().encode("utf-8"), "text/csv", truncated


def _sql_wert(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return literal(json.dumps(value, ensure_ascii=False))
    return literal(value)


# ── Sicherung einer Datenbank ──────────────────────────────────────────────


def _agent(k: Kontext, db: Session, call: str, payload: dict[str, Any], *, admin: bool = False) -> Any:
    verbindung = postgres_service._verbindung(db, k.server, postgres_service._client_for_server_id(db, k.server.id))
    if admin:
        payload = {**payload, "admin_password": verbindung.admin_password}
    try:
        return getattr(verbindung.client, call)(verbindung.mit_ziel(payload))
    except NodeClientError as exc:
        status = getattr(exc, "status_code", None)
        # FastAPI antwortet auf eine unbekannte Route mit genau "Not Found".
        if status == 404 and (exc.message or "").strip() == "Not Found":
            raise postgres_service.PostgresServiceError(
                "Der Agent dieses Nodes kennt diese Funktion noch nicht — bitte den Agent aktualisieren."
            ) from exc
        if status in {400, 404}:
            raise ValueError(exc.message or "PostgreSQL-Fehler") from exc
        raise postgres_service.PostgresServiceError(exc.message or "Agent-Aufruf fehlgeschlagen") from exc


def dump(db: Session, k: Kontext, req: s.StudioDumpRequest) -> tuple[bytes, str]:
    result = _agent(
        k,
        db,
        "postgres_dump_db",
        {
            "database_name": k.database.name,
            "format": req.format,
            "schema_only": req.schema_only,
            "data_only": req.data_only,
            "schemas": list(req.schemas),
            "tables": [t.model_dump() for t in req.tables],
        },
        admin=True,
    )
    data = base64.b64decode(result.get("data_b64") or "")
    return data, hashlib.sha256(data).hexdigest()


def restore(db: Session, k: Kontext, req: s.StudioRestoreRequest) -> dict[str, Any]:
    if req.confirm_name != k.database.name:
        raise ValueError("Bestätigungsname stimmt nicht mit der Datenbank überein.")
    try:
        data = base64.b64decode(req.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Datei ist nicht lesbar (Base64).") from exc
    return _agent(
        k,
        db,
        "postgres_restore_db",
        {
            "database_name": k.database.name,
            "owner_role": k.database.owner_role,
            "owner_password": postgres_service._owner_password(k.database),
            "format": req.format,
            "clean": req.clean,
            "data_b64": req.data_b64,
        },
    ) | {"sha256": hashlib.sha256(data).hexdigest()}


def pending(db: Session, k: Kontext) -> list[dict[str, Any]]:
    rows = _agent(k, db, "postgres_pending", {"server_id": k.server.id})
    return [row for row in rows or [] if row.get("database") == k.database.name]


def apply_pending(db: Session, k: Kontext) -> dict[str, Any]:
    return _agent(
        k,
        db,
        "postgres_restore_db",
        {
            "database_name": k.database.name,
            "owner_role": k.database.owner_role,
            "owner_password": postgres_service._owner_password(k.database),
            "pending_server_id": k.server.id,
        },
    )


def discard_pending(db: Session, k: Kontext) -> None:
    _agent(k, db, "postgres_pending_discard", {"server_id": k.server.id, "database_name": k.database.name})
