"""Ausführung für das PostgreSQL-Studio an der Panel-Datenbank.

Das Studio (`postgres_studio_service`) baut Katalogabfragen, Datenänderungen
und SQL für jede Datenbank gleich. An einer Server-Datenbank führt der Agent
sie aus (`msm-agent/services/postgres_service.run_statements`); die
Panel-Datenbank erreicht nur das Backend selbst. Diese Datei ist das
Gegenstück dazu — dieselben Modi, dieselbe Ergebnisform, keine eigene
Tabellen- oder Zeilenlogik.

Eigene Verbindung je Aufruf statt einer aus dem SQLAlchemy-Pool: ein
``readonly`` oder ``statement_timeout`` bliebe sonst an einer Poolverbindung
hängen und träfe die nächste Anfrage des Panels.
"""

from __future__ import annotations

import time
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Any

import psycopg2
from sqlalchemy.engine.url import make_url

from config import settings
from services.postgres_service import PostgresServiceError

RUN_MODES = {"read", "tx", "autocommit"}
_MAX_NOTICES = 50


class PanelDatabaseError(PostgresServiceError):
    """Nicht erreichbar — wie ein Agent, der nicht antwortet (503)."""


def _url():
    url = make_url(settings.database_url)
    if not url.get_backend_name().startswith("postgresql"):
        raise ValueError("Panel-Datenbankverwaltung ist nur für PostgreSQL-Konfigurationen verfügbar.")
    return url


def database_name() -> str:
    return str(_url().database or "")


def _connect():
    url = _url()
    optionen = {k: v for k, v in url.query.items() if isinstance(v, str)}
    try:
        return psycopg2.connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname=url.database,
            connect_timeout=5,
            **optionen,
        )
    except psycopg2.OperationalError as exc:
        raise PanelDatabaseError("Verbindung zur Panel-Datenbank fehlgeschlagen.") from exc


def _json_wert(value: Any) -> Any:
    """Wie im Agent: JSON trägt jeden Wert ohne Genauigkeitsverlust."""
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


def _fehler(exc: psycopg2.Error, index: int) -> ValueError:
    diag = getattr(exc, "diag", None)
    primary = getattr(diag, "message_primary", None) or (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    parts = [f"[{exc.pgcode}] {primary}" if exc.pgcode else str(primary)]
    if getattr(diag, "message_detail", None):
        parts.append(str(diag.message_detail))
    if getattr(diag, "message_hint", None):
        parts.append(f"Hint: {diag.message_hint}")
    return ValueError((f"Statement {index + 1}: " + " — ".join(parts))[:2000])


def run(
    statements: list[tuple[str, list[Any] | None]],
    *,
    mode: str = "read",
    rollback: bool = False,
    row_limit: int = 500,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """``read``: nur lesend. ``tx``: alles oder nichts (``rollback`` verwirft).
    ``autocommit``: für VACUUM und Co.; Abbruch beim ersten Fehler."""
    if mode not in RUN_MODES:
        raise ValueError("Ungültiger Ausführungsmodus.")
    if not statements:
        raise ValueError("Keine Anweisung.")
    row_limit = min(max(int(row_limit), 1), 5000)
    timeout_ms = min(max(int(timeout_ms or settings.managed_postgres_statement_timeout_ms), 100), 600_000)

    conn = _connect()
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    try:
        if mode == "autocommit":
            conn.autocommit = True
        elif mode == "read":
            conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = %s", (timeout_ms,))
            for index, (text, params) in enumerate(statements):
                begin = time.monotonic()
                try:
                    cur.execute(text, params)
                except psycopg2.Error as exc:
                    if not conn.autocommit:
                        conn.rollback()
                    raise _fehler(exc, index) from exc
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
