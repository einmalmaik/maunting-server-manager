#!/usr/bin/env python3
"""Bridge a current pre-Phase-8 PostgreSQL installation to Alembic."""

from __future__ import annotations

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import Base, engine
import models  # noqa: F401
from services.multi_node_migration_service import ensure_multi_node_schema
from services.schema_manager import initialize_or_upgrade_schema


def main() -> int:
    try:
        # create_all only creates new tables (not columns). This adds the Nodes
        # table to a current legacy PostgreSQL installation; the two legacy
        # columns are added explicitly without requiring DIS or an agent token.
        Base.metadata.create_all(bind=engine)
        ensure_multi_node_schema(engine)
        result = initialize_or_upgrade_schema(engine)
    except Exception as exc:
        print(f"FEHLER: Phase-8-Schemabrücke fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print(f"Phase-8-Schema bereit ({result}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
