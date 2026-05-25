"""Migration-Tests f\u00fcr den Phase-1 Docker-Cutover.

Simuliert die Legacy-DB-Schema-Situation (`servers.linux_user NOT NULL`) und
verifiziert, dass die Migration in main.py die Spalte sauber entfernt.

Wir nutzen daf\u00fcr eine **frische SQLite-DB auf disk** (nicht :memory:), weil
der Lifespan-Code in main.py mit seinem eigenen Engine arbeitet und nur dann
gegen die gleiche DB l\u00e4uft, wenn die URL persistent ist.
"""
from __future__ import annotations

import os
import tempfile

from sqlalchemy import create_engine, inspect, text


def test_migration_drops_legacy_linux_user_column(monkeypatch):
    """Legacy-Schema: servers hat linux_user NOT NULL. Nach Migration: Spalte weg."""
    tmpdir = tempfile.mkdtemp(prefix="msm-mig-test-")
    db_path = os.path.join(tmpdir, "msm.db")
    db_url = f"sqlite:///{db_path}"

    # 1) Legacy-Schema bauen \u2014 minimal, nur was die Migration anf\u00e4sst.
    legacy_engine = create_engine(db_url)
    with legacy_engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE servers ("
            "  id INTEGER PRIMARY KEY,"
            "  name VARCHAR NOT NULL,"
            "  game_type VARCHAR NOT NULL,"
            "  install_dir VARCHAR NOT NULL,"
            "  linux_user VARCHAR NOT NULL,"
            "  status VARCHAR DEFAULT 'stopped'"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO servers (id, name, game_type, install_dir, linux_user) "
            "VALUES (1, 'legacy', 'dayz', '/opt/msm/servers/legacy', 'msm_srv_1')"
        ))
    legacy_engine.dispose()

    # 2) Migration nachstellen: gleiche Logik wie in main.py lifespan.
    engine = create_engine(db_url)
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("servers")]
    assert "linux_user" in cols  # Pre-condition: Legacy-Schema

    with engine.begin() as conn:
        # Phase-1 Docker-Spalten (idempotent, wie main.py)
        if "container_name" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN container_name VARCHAR(64)"))
        if "public_bind_ip" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN public_bind_ip VARCHAR(64)"))
        if "disk_usage_mb" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN disk_usage_mb INTEGER"))
        # Hier passiert der eigentliche Drop
        if "linux_user" in cols:
            conn.execute(text("ALTER TABLE servers DROP COLUMN linux_user"))

    # 3) Post-condition: linux_user weg, neue Spalten da.
    inspector = inspect(engine)
    new_cols = {c["name"] for c in inspector.get_columns("servers")}
    assert "linux_user" not in new_cols
    assert {"container_name", "public_bind_ip", "disk_usage_mb"}.issubset(new_cols)

    # 4) Alte Daten sind erhalten.
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, name FROM servers WHERE id=1")).first()
        assert row is not None
        assert row.name == "legacy"

    # 5) Neuer INSERT ohne linux_user funktioniert.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO servers (id, name, game_type, install_dir) "
            "VALUES (2, 'docker-only', 'dayz', '/opt/msm/servers/dockeronly')"
        ))
        row = conn.execute(text("SELECT id FROM servers WHERE id=2")).first()
        assert row is not None

    engine.dispose()


def test_migration_idempotent_on_clean_schema():
    """Frische DB ohne linux_user: Migration muss ohne Fehler nochmal laufen."""
    tmpdir = tempfile.mkdtemp(prefix="msm-mig-test-")
    db_path = os.path.join(tmpdir, "msm.db")
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE servers ("
            "  id INTEGER PRIMARY KEY,"
            "  name VARCHAR NOT NULL,"
            "  game_type VARCHAR NOT NULL,"
            "  install_dir VARCHAR NOT NULL,"
            "  container_name VARCHAR(64),"
            "  public_bind_ip VARCHAR(64),"
            "  disk_usage_mb INTEGER"
            ")"
        ))

    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("servers")]
    assert "linux_user" not in cols

    # Migrationen sollen no-op sein
    with engine.begin() as conn:
        if "container_name" not in cols:
            conn.execute(text("ALTER TABLE servers ADD COLUMN container_name VARCHAR(64)"))
        if "linux_user" in cols:
            conn.execute(text("ALTER TABLE servers DROP COLUMN linux_user"))

    # Schema unver\u00e4ndert
    inspector = inspect(engine)
    new_cols = {c["name"] for c in inspector.get_columns("servers")}
    assert "linux_user" not in new_cols
    engine.dispose()


# ── Phase 3 — RBAC: Legacy `permissions` → `server_permissions` ───────────────


def _simulate_legacy_permissions_migration(db_url: str) -> int:
    """Spiegelt 1:1 die Migrations-Logik aus backend/main.py (Phase-3-Block).

    Verwendet Raw-SQL gegen die uebergebene DB-URL und liefert die Anzahl
    migrierter Rows zurueck.
    """
    from datetime import datetime, timezone

    from sqlalchemy import create_engine, inspect, text

    from services.permission_catalog import LEGACY_PERMISSION_MAPPING

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "permissions" in inspector.get_table_names()
    legacy_cols = {c["name"] for c in inspector.get_columns("permissions")}
    select_cols = [c for c in LEGACY_PERMISSION_MAPPING.keys() if c in legacy_cols]
    migrated = 0
    if not select_cols:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE permissions"))
    else:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, user_id, server_id, "
                    + ", ".join(select_cols)
                    + " FROM permissions"
                )
            ).fetchall()
            for row in rows:
                desired_keys: set[str] = set()
                for col in select_cols:
                    if getattr(row, col):
                        desired_keys.update(LEGACY_PERMISSION_MAPPING[col])
                # Sichtbarkeit immer mit-migrieren, damit Users mit nur
                # `can_start`/`can_stop` (ohne can_view_*) nicht aus
                # `list_visible_servers` / `get_server` fliegen.
                if desired_keys:
                    desired_keys.add("server.view")
                for key in desired_keys:
                    exists = conn.execute(
                        text(
                            "SELECT id FROM server_permissions "
                            "WHERE user_id = :uid AND server_id = :sid "
                            "AND permission_key = :key"
                        ),
                        {"uid": row.user_id, "sid": row.server_id, "key": key},
                    ).first()
                    if exists is None:
                        conn.execute(
                            text(
                                "INSERT INTO server_permissions "
                                "(user_id, server_id, permission_key, granted_at) "
                                "VALUES (:uid, :sid, :key, :ts)"
                            ),
                            {
                                "uid": row.user_id,
                                "sid": row.server_id,
                                "key": key,
                                "ts": datetime.now(timezone.utc),
                            },
                        )
                        migrated += 1
            conn.execute(text("DROP TABLE permissions"))
    engine.dispose()
    return migrated


def _make_target_table(db_url: str) -> None:
    """Legt `server_permissions` mit derselben NOT-NULL-Geometrie wie die ORM-DDL an."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE server_permissions ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  server_id INTEGER NOT NULL,"
                "  permission_key VARCHAR(64) NOT NULL,"
                "  granted_at DATETIME NOT NULL,"
                "  granted_by INTEGER"
                ")"
            )
        )
    engine.dispose()


def test_phase3_legacy_permissions_migration_inserts_granted_at():
    """Migration muss `granted_at` setzen (NOT NULL) — kein Crash beim INSERT."""
    from sqlalchemy import create_engine, inspect, text

    tmpdir = tempfile.mkdtemp(prefix="msm-rbac-mig-")
    db_path = os.path.join(tmpdir, "msm.db")
    db_url = f"sqlite:///{db_path}"

    # Ziel-Tabelle (wie nach Base.metadata.create_all)
    _make_target_table(db_url)
    # Legacy-Tabelle mit min. einem can_*-Eintrag.
    legacy_engine = create_engine(db_url)
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE permissions ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  server_id INTEGER NOT NULL,"
                "  can_start BOOLEAN NOT NULL DEFAULT 0,"
                "  can_stop BOOLEAN NOT NULL DEFAULT 0,"
                "  can_view_console BOOLEAN NOT NULL DEFAULT 0"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO permissions (user_id, server_id, can_start, can_stop, can_view_console) "
                "VALUES (7, 42, 1, 1, 1)"
            )
        )
    legacy_engine.dispose()

    migrated = _simulate_legacy_permissions_migration(db_url)
    assert migrated >= 2  # mindestens 'server.view' + 'server.start'/'server.stop'

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT user_id, server_id, permission_key, granted_at "
                "FROM server_permissions ORDER BY permission_key"
            )
        ).fetchall()
    inspector = inspect(engine)
    assert "permissions" not in inspector.get_table_names()
    assert rows, "Migration hat keine Rows angelegt"
    for r in rows:
        assert r.user_id == 7
        assert r.server_id == 42
        assert r.granted_at is not None
    engine.dispose()


def test_phase3_legacy_permissions_migration_handles_no_known_columns():
    """Legacy-Tabelle ohne bekannte can_*-Spalten: einfach droppen, kein Crash."""
    from sqlalchemy import create_engine, inspect, text

    tmpdir = tempfile.mkdtemp(prefix="msm-rbac-mig-empty-")
    db_path = os.path.join(tmpdir, "msm.db")
    db_url = f"sqlite:///{db_path}"

    _make_target_table(db_url)
    legacy_engine = create_engine(db_url)
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE permissions ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  server_id INTEGER NOT NULL,"
                "  some_other_col VARCHAR(32)"
                ")"
            )
        )
    legacy_engine.dispose()

    migrated = _simulate_legacy_permissions_migration(db_url)
    assert migrated == 0

    engine = create_engine(db_url)
    inspector = inspect(engine)
    assert "permissions" not in inspector.get_table_names()
    engine.dispose()


def test_phase3_legacy_permissions_migration_always_adds_server_view():
    """User mit `can_start=1` (ohne can_view_*) muss nach Migration trotzdem
    `server.view` haben — sonst Totalverlust des Server-Zugriffs."""
    from sqlalchemy import create_engine, text

    tmpdir = tempfile.mkdtemp(prefix="msm-rbac-mig-view-")
    db_path = os.path.join(tmpdir, "msm.db")
    db_url = f"sqlite:///{db_path}"

    _make_target_table(db_url)
    legacy_engine = create_engine(db_url)
    with legacy_engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE permissions ("
                "  id INTEGER PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  server_id INTEGER NOT NULL,"
                "  can_start BOOLEAN NOT NULL DEFAULT 0,"
                "  can_stop BOOLEAN NOT NULL DEFAULT 0,"
                "  can_view_console BOOLEAN NOT NULL DEFAULT 0,"
                "  can_view_logs BOOLEAN NOT NULL DEFAULT 0"
                ")"
            )
        )
        # User 9 hatte NUR can_start — in der alten Welt sah er den Server
        # trotzdem im Listing. Ohne `server.view` waere er nach Migration raus.
        conn.execute(
            text(
                "INSERT INTO permissions "
                "(user_id, server_id, can_start, can_stop, can_view_console, can_view_logs) "
                "VALUES (9, 11, 1, 0, 0, 0)"
            )
        )
    legacy_engine.dispose()

    _simulate_legacy_permissions_migration(db_url)

    engine = create_engine(db_url)
    with engine.connect() as conn:
        keys = {
            r.permission_key
            for r in conn.execute(
                text(
                    "SELECT permission_key FROM server_permissions "
                    "WHERE user_id = 9 AND server_id = 11"
                )
            ).fetchall()
        }
    engine.dispose()
    assert "server.start" in keys
    assert "server.view" in keys, (
        "Migration ohne server.view sperrt den User aus list_visible_servers aus"
    )
