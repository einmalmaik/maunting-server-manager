#!/usr/bin/env python3
"""One-time, fail-closed import from a legacy MSM SQLite DB to PostgreSQL.

The source is opened read-only. The target must not contain MSM data. Only
tables and columns known by the current SQLAlchemy models are copied, in
foreign-key order, inside one PostgreSQL transaction. Secrets remain opaque
encrypted strings; this script never decrypts or prints them.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from typing import Iterable

from sqlalchemy import Integer, create_engine, event, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env", override=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Importiert eine bestehende MSM-SQLite-Datenbank nach PostgreSQL."
    )
    parser.add_argument("--sqlite", required=True, help="Pfad zur bestehenden msm.db")
    parser.add_argument(
        "--postgres-url",
        default=os.getenv("MSM_DATABASE_URL", ""),
        help="PostgreSQL-Ziel; standardmäßig MSM_DATABASE_URL",
    )
    parser.add_argument(
        "--archive-source",
        action="store_true",
        help="Benennt die SQLite-Datei nach erfolgreicher Verifikation in .migrated um",
    )
    return parser.parse_args()


def _sqlite_readonly_engine(path: Path) -> Engine:
    def connect_readonly() -> sqlite3.Connection:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        return sqlite3.connect(uri, uri=True, check_same_thread=False)

    engine = create_engine("sqlite+pysqlite://", creator=connect_readonly)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _known_source_columns(source: Connection, table_name: str, columns: Iterable[str]) -> list[str]:
    source_columns = {
        column["name"] for column in inspect(source).get_columns(table_name)
    }
    return [name for name in columns if name in source_columns]


def _assert_target_empty(target: Connection, table_names: set[str]) -> None:
    existing = set(inspect(target).get_table_names()) & table_names
    populated: list[str] = []
    for table_name in sorted(existing):
        quoted = target.dialect.identifier_preparer.quote(table_name)
        if target.execute(text(f"SELECT 1 FROM {quoted} LIMIT 1")).first() is not None:
            populated.append(table_name)
    if populated:
        raise RuntimeError(
            "PostgreSQL-Ziel enthält bereits MSM-Daten; Import sicher abgebrochen "
            f"({', '.join(populated[:5])})."
        )


def _reset_postgres_sequence(target: Connection, table_name: str, column_name: str) -> None:
    preparer = target.dialect.identifier_preparer
    table_sql = preparer.quote(table_name)
    column_sql = preparer.quote(column_name)
    target.execute(
        text(
            "SELECT setval(pg_get_serial_sequence(:table_name, :column_name), "
            f"COALESCE((SELECT MAX({column_sql}) FROM {table_sql}), 1), "
            f"EXISTS(SELECT 1 FROM {table_sql}))"
        ),
        {"table_name": table_name, "column_name": column_name},
    )


def migrate(sqlite_path: Path, postgres_url: str) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise RuntimeError(f"SQLite-Quelle nicht gefunden: {sqlite_path}")

    from database_policy import validate_panel_database_url

    target_url = validate_panel_database_url(postgres_url)

    # Import all model modules so Base.metadata is complete.
    from database import Base
    import models  # noqa: F401

    source_engine = _sqlite_readonly_engine(sqlite_path)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    copied: dict[str, int] = {}

    try:
        with source_engine.connect() as source:
            source_tables = set(inspect(source).get_table_names())
            if not source_tables:
                raise RuntimeError("SQLite-Quelle enthält keine Tabellen.")

            with target_engine.begin() as target:
                model_tables = {table.name for table in Base.metadata.sorted_tables}
                _assert_target_empty(target, model_tables)
                Base.metadata.create_all(bind=target)

                # Kurzlebige Verifikations-/Enrollment-Daten werden bewusst
                # nicht uebernommen. Alte SQLite-Versionen hatten inkompatible
                # Klartext-Spalten; aktive Sessions/Codes nach einem Update zu
                # verwerfen ist sicherer als sie fehlerhaft zu importieren.
                ephemeral_tables = {
                    "email_verifications",
                    "login_challenges",
                    "node_enrollments",
                }

                for table in Base.metadata.sorted_tables:
                    if table.name in ephemeral_tables:
                        copied[table.name] = 0
                        continue
                    if table.name not in source_tables:
                        continue
                    column_names = _known_source_columns(
                        source, table.name, (column.name for column in table.columns)
                    )
                    if not column_names:
                        continue

                    rows = [
                        dict(row._mapping)
                        for row in source.execute(
                            select(*(table.c[name] for name in column_names))
                        )
                    ]
                    if rows:
                        target.execute(table.insert(), rows)

                    target_count = int(
                        target.execute(select(func.count()).select_from(table)).scalar_one()
                    )
                    if target_count != len(rows):
                        raise RuntimeError(
                            f"Verifikation für Tabelle {table.name} fehlgeschlagen."
                        )
                    copied[table.name] = target_count

                # Sehr alte Installationen speicherten Ports direkt in
                # ``servers``. Diese Werte muessen explizit in das heutige
                # server_ports-Modell ueberfuehrt werden, sonst waeren die
                # Game-Server nach dem Import nicht mehr erreichbar.
                server_columns = (
                    {column["name"] for column in inspect(source).get_columns("servers")}
                    if "servers" in source_tables
                    else set()
                )
                legacy_port_columns = {"game_port", "query_port", "rcon_port"}
                server_ports = Base.metadata.tables["server_ports"]
                if legacy_port_columns & server_columns:
                    existing_port_count = int(
                        target.execute(
                            select(func.count()).select_from(server_ports)
                        ).scalar_one()
                    )
                    if existing_port_count == 0:
                        select_parts = ["id"] + [
                            name
                            for name in ("game_port", "query_port", "rcon_port")
                            if name in server_columns
                        ]
                        rows = source.execute(
                            text(f"SELECT {', '.join(select_parts)} FROM servers")
                        ).mappings()
                        role_protocol = {
                            "game_port": ("game", "udp"),
                            "query_port": ("query", "udp"),
                            "rcon_port": ("rcon", "tcp"),
                        }
                        transformed_ports: list[dict[str, object]] = []
                        for row in rows:
                            for column_name, (role, protocol) in role_protocol.items():
                                port = row.get(column_name)
                                if port:
                                    transformed_ports.append({
                                        "server_id": row["id"],
                                        "role": role,
                                        "port": port,
                                        "protocol": protocol,
                                    })
                        if transformed_ports:
                            target.execute(server_ports.insert(), transformed_ports)
                        copied["server_ports"] = len(transformed_ports)

                # Phase-3-RBAC ersetzte die breite permissions-Tabelle durch
                # einzelne Permission-Keys. Auch diese Berechtigungen duerfen
                # beim Legacy-Import nicht still verloren gehen.
                if "permissions" in source_tables and "server_permissions" not in source_tables:
                    from services.permission_catalog import LEGACY_PERMISSION_MAPPING

                    permission_columns = {
                        column["name"]
                        for column in inspect(source).get_columns("permissions")
                    }
                    mapped_columns = [
                        name for name in LEGACY_PERMISSION_MAPPING if name in permission_columns
                    ]
                    server_permissions = Base.metadata.tables["server_permissions"]
                    existing_permission_count = int(
                        target.execute(
                            select(func.count()).select_from(server_permissions)
                        ).scalar_one()
                    )
                    if mapped_columns and existing_permission_count == 0:
                        rows = source.execute(
                            text(
                                "SELECT user_id, server_id, "
                                + ", ".join(mapped_columns)
                                + " FROM permissions"
                            )
                        ).mappings()
                        transformed_permissions: list[dict[str, object]] = []
                        seen_permissions: set[tuple[int, int, str]] = set()
                        for row in rows:
                            keys: set[str] = set()
                            for column_name in mapped_columns:
                                if row[column_name]:
                                    keys.update(LEGACY_PERMISSION_MAPPING[column_name])
                            if keys:
                                keys.add("server.view")
                            for permission_key in keys:
                                identity = (
                                    int(row["user_id"]),
                                    int(row["server_id"]),
                                    permission_key,
                                )
                                if identity in seen_permissions:
                                    continue
                                seen_permissions.add(identity)
                                transformed_permissions.append({
                                    "user_id": identity[0],
                                    "server_id": identity[1],
                                    "permission_key": permission_key,
                                    "granted_at": datetime.now(timezone.utc),
                                    "granted_by": None,
                                })
                        if transformed_permissions:
                            target.execute(
                                server_permissions.insert(), transformed_permissions
                            )
                        copied["server_permissions"] = len(transformed_permissions)

                for table in Base.metadata.sorted_tables:
                    integer_primary_keys = [
                        column
                        for column in table.primary_key.columns
                        if isinstance(column.type, Integer)
                        and (column.autoincrement is True or column.autoincrement == "auto")
                    ]
                    if len(integer_primary_keys) == 1:
                        _reset_postgres_sequence(
                            target, table.name, integer_primary_keys[0].name
                        )
    finally:
        source_engine.dispose()
        target_engine.dispose()

    return copied


def main() -> int:
    args = _parse_args()
    sqlite_path = Path(args.sqlite).expanduser().resolve()
    marker_path = sqlite_path.with_suffix(sqlite_path.suffix + ".migration-complete")

    source_hash = hashlib.sha256(sqlite_path.read_bytes()).hexdigest() if sqlite_path.is_file() else ""
    if marker_path.is_file():
        marker_hash = marker_path.read_text(encoding="ascii").strip()
        if not source_hash or marker_hash != source_hash:
            print(
                "FEHLER: Vorhandener Migrationsmarker passt nicht zur SQLite-Quelle.",
                file=sys.stderr,
            )
            return 1
        if args.archive_source:
            archive_path = sqlite_path.with_suffix(sqlite_path.suffix + ".migrated")
            if archive_path.exists():
                print(f"FEHLER: Migrationsarchiv existiert bereits: {archive_path}", file=sys.stderr)
                return 1
            try:
                sqlite_path.rename(archive_path)
                marker_path.unlink()
            except OSError as exc:
                print(f"WARNUNG: Import ist abgeschlossen, SQLite-Datei noch gesperrt: {exc}")
                return 0
            print(f"SQLite-Quelle archiviert: {archive_path}")
        return 0

    # database.py must be bound to the PostgreSQL target while model metadata
    # is loaded. SQLite itself is opened separately and read-only.
    if not args.postgres_url:
        print("FEHLER: --postgres-url oder MSM_DATABASE_URL fehlt.", file=sys.stderr)
        return 2
    os.environ["MSM_DATABASE_URL"] = args.postgres_url
    os.environ.pop("MSM_SQLITE_MIGRATION", None)

    try:
        copied = migrate(sqlite_path, args.postgres_url)
    except Exception as exc:
        print(f"FEHLER: SQLite-Import abgebrochen: {exc}", file=sys.stderr)
        return 1

    marker_path.write_text(source_hash, encoding="ascii")

    if args.archive_source:
        archive_path = sqlite_path.with_suffix(sqlite_path.suffix + ".migrated")
        if archive_path.exists():
            print(
                f"FEHLER: Migrationsarchiv existiert bereits: {archive_path}",
                file=sys.stderr,
            )
            return 1
        try:
            sqlite_path.rename(archive_path)
            marker_path.unlink()
        except OSError as exc:
            print(f"WARNUNG: Import ist abgeschlossen, SQLite-Datei noch gesperrt: {exc}")
            total = sum(copied.values())
            print(f"Import erfolgreich verifiziert: {len(copied)} Tabellen, {total} Zeilen.")
            return 0
        print(f"SQLite-Quelle archiviert: {archive_path}")

    total = sum(copied.values())
    print(f"Import erfolgreich verifiziert: {len(copied)} Tabellen, {total} Zeilen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
