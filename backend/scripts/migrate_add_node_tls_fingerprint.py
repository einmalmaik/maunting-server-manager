"""Phase 5: add nodes.tls_fingerprint column (idempotent)."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import inspect, text

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from database import Base, engine  # noqa: E402


def run_migration() -> None:
    print("Phase-5 Migration: nodes.tls_fingerprint ...")
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "nodes" not in inspector.get_table_names():
        print("nodes-Tabelle fehlt — create_all sollte sie angelegt haben.")
        Base.metadata.create_all(bind=engine)
        inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("nodes")]
    if "tls_fingerprint" in cols:
        print("tls_fingerprint existiert bereits — nichts zu tun.")
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE nodes ADD COLUMN tls_fingerprint VARCHAR(128)"))
    print("tls_fingerprint Spalte hinzugefuegt.")


if __name__ == "__main__":
    run_migration()
