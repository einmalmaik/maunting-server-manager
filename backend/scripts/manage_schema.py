#!/usr/bin/env python3
"""Initialize or upgrade the PostgreSQL panel schema."""

from __future__ import annotations

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import engine
from services.schema_manager import initialize_or_upgrade_schema


def main() -> int:
    try:
        result = initialize_or_upgrade_schema(engine)
    except Exception as exc:
        print(f"FEHLER: Datenbankschema konnte nicht vorbereitet werden: {exc}", file=sys.stderr)
        return 1
    print(f"Datenbankschema bereit ({result}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
