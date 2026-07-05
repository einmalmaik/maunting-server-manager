from __future__ import annotations

import time
from typing import Any

from psycopg2 import sql
from sqlalchemy.engine.url import make_url

from config import settings
from database import engine
from services.postgres_service import _is_read_only, _split_sql_statements

ROW_LIMIT = 500
STATEMENT_TIMEOUT_MS = 5000


def _ensure_postgresql() -> None:
    backend = make_url(settings.database_url).get_backend_name()
    if not backend.startswith("postgresql"):
        raise ValueError("Panel-Datenbankverwaltung ist nur für PostgreSQL-Konfigurationen verfügbar.")


def _connect():
    _ensure_postgresql()
    conn = engine.raw_connection()
    try:
        conn.autocommit = False
    except Exception:
        pass
    return conn


def stats() -> dict[str, Any]:
    started = time.monotonic()
    conn = _connect()
    try:
        latency_ms = int((time.monotonic() - started) * 1000)
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
    finally:
        conn.close()
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


def list_tables() -> list[dict[str, Any]]:
    conn = _connect()
    try:
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
    finally:
        conn.close()


def describe_table(schema_name: str, table_name: str) -> dict[str, Any]:
    conn = _connect()
    try:
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
                SELECT pg_total_relation_size((quote_ident(%s) || '.' || quote_ident(%s))::regclass),
                       GREATEST(c.reltuples::bigint, 0)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                """,
                (schema_name, table_name, schema_name, table_name),
            )
            size_row = cur.fetchone()
    finally:
        conn.close()
    return {
        "schema": schema_name,
        "name": table_name,
        "columns": columns,
        "indexes": indexes,
        "foreign_keys": foreign_keys,
        "size_bytes": size_row[0] if size_row else None,
        "row_estimate": size_row[1] if size_row else None,
    }


def read_rows(schema_name: str, table_name: str, limit: int, offset: int, search: str | None = None) -> dict[str, Any]:
    limit = min(max(limit, 1), ROW_LIMIT)
    offset = max(offset, 0)
    table = describe_table(schema_name, table_name)
    columns = [column["name"] for column in table["columns"]]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            query = sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema_name), sql.Identifier(table_name))
            params: list[Any] = []
            if search:
                clauses = [sql.SQL("CAST({} AS TEXT) ILIKE %s").format(sql.Identifier(col)) for col in columns]
                query += sql.SQL(" WHERE ") + sql.SQL(" OR ").join(clauses)
                params.extend([f"%{search[:128]}%"] * len(columns))
            query += sql.SQL(" LIMIT %s OFFSET %s")
            cur.execute(query, tuple(params + [limit, offset]))
            rows = [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
    finally:
        conn.close()
    return {"columns": columns, "rows": rows, "limit": limit, "offset": offset, "row_count": table["row_estimate"]}


def execute_sql(statement: str, limit: int) -> dict[str, Any]:
    cleaned = (statement or "").strip()
    if not cleaned:
        raise ValueError("SQL darf nicht leer sein.")
    statements = _split_sql_statements(cleaned)
    if not statements:
        raise ValueError("Keine ausfuehrbaren SQL-Statements gefunden.")
    row_limit = min(max(limit, 1), ROW_LIMIT)
    has_write = any(not _is_read_only(s) for s in statements)
    results: list[dict[str, Any]] = []
    started_total = time.monotonic()
    conn = _connect()
    try:
        if not has_write:
            conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
            for stmt in statements:
                started = time.monotonic()
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
                    entry["duration_ms"] = int((time.monotonic() - started) * 1000)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                    results.append(entry)
                    break
                results.append(entry)
            conn.commit()
    finally:
        conn.close()
    return {
        "statements": results,
        "total_duration_ms": int((time.monotonic() - started_total) * 1000),
        "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
    }
