#!/usr/bin/env python3
"""Credential-safe pg_dump helper for install/update scripts."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit


def _read_env_value(path: Path, key: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        current_key, value = line.split("=", 1)
        if current_key.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def create_dump(env_file: Path, output: Path) -> None:
    database_url = _read_env_value(env_file, "MSM_DATABASE_URL")
    normalized = database_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError("MSM_DATABASE_URL ist keine PostgreSQL-Verbindung.")
    if not parsed.hostname or not parsed.path.strip("/") or not parsed.username:
        raise RuntimeError("MSM_DATABASE_URL ist unvollständig.")

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump ist nicht installiert.")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        pg_dump,
        "--format=custom",
        "--no-password",
        "--host", parsed.hostname,
        "--port", str(parsed.port or 5432),
        "--username", unquote(parsed.username),
        "--dbname", parsed.path.strip("/"),
        "--file", str(output),
    ]
    process_env = os.environ.copy()
    process_env["PGPASSWORD"] = unquote(parsed.password or "")
    result = subprocess.run(
        command,
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump fehlgeschlagen (Exit {result.returncode}).")
    if not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError("pg_dump hat keine verwendbare Sicherung erzeugt.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        create_dump(Path(args.env_file), Path(args.output))
    except Exception as exc:
        print(f"FEHLER: PostgreSQL-Sicherung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    print("PostgreSQL-Sicherung erfolgreich geprüft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
