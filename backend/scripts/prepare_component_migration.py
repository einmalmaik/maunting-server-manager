#!/usr/bin/env python3
"""Prepare protected environment files for a control-plane migration.

Secret values are read from protected files and never accepted as CLI
arguments or printed.  The target keeps its freshly generated PostgreSQL
credentials while application/DIS secrets remain stable across the cutover.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit


_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_REQUIRED_SECRETS = ("MSM_SECRET_KEY", "MSM_DIS_SALT", "MSM_DIS_SIDECAR_TOKEN")
_TARGET_DATABASE_KEYS = ("MSM_DATABASE_URL", "MSM_DATABASE_URL_ASYNC")


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        raise RuntimeError(f"Environment-Datei fehlt: {path}")
    return path.read_text(encoding="utf-8").splitlines()


def _raw_values(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = _KEY_RE.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def _plain(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return value


def _quoted(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise RuntimeError("Mehrzeilige Environment-Werte werden nicht unterstützt")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _validate_origin(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{label} muss eine HTTPS-Origin ohne Pfad sein")
    return value.rstrip("/")


def _replace_values(lines: list[str], replacements: dict[str, str]) -> list[str]:
    output: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        match = _KEY_RE.match(line.strip())
        if match and match.group(1) in replacements:
            key = match.group(1)
            if key not in replaced:
                output.append(f"{key}={replacements[key]}")
                replaced.add(key)
            continue
        output.append(line)
    if output and output[-1] != "":
        output.append("")
    for key, raw_value in replacements.items():
        if key not in replaced:
            output.append(f"{key}={raw_value}")
    return output


def _atomic_write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_stat = path.stat() if path.exists() else None
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        if existing_stat is not None and hasattr(os, "fchown"):
            os.fchown(descriptor, existing_stat.st_uid, existing_stat.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines).rstrip("\n") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temp_path.unlink(missing_ok=True)
        raise


def disable_local_agent(env_file: Path) -> None:
    lines = _read_lines(env_file)
    updated = _replace_values(lines, {"MSM_LOCAL_AGENT_ENABLED": "false"})
    _atomic_write(env_file, updated)


def merge_target_environment(
    *,
    source_env: Path,
    target_env: Path,
    output_env: Path,
    dis_output: Path,
    api_origin: str,
    frontend_origin: str | None,
) -> None:
    api_origin = _validate_origin(api_origin, "API-Origin")
    if frontend_origin:
        frontend_origin = _validate_origin(frontend_origin, "Frontend-Origin")

    source_lines = _read_lines(source_env)
    target_lines = _read_lines(target_env)
    source_values = _raw_values(source_lines)
    target_values = _raw_values(target_lines)

    for key in _REQUIRED_SECRETS:
        if len(_plain(source_values.get(key, ""))) < 16:
            raise RuntimeError(f"Quellwert {key} fehlt oder ist ungültig")
    for key in _TARGET_DATABASE_KEYS:
        if not _plain(target_values.get(key, "")).startswith("postgresql+"):
            raise RuntimeError(f"Zielwert {key} fehlt oder ist keine PostgreSQL-URL")

    cors_origins = [
        origin.strip().rstrip("/")
        for origin in _plain(source_values.get("MSM_CORS_ALLOWED_ORIGINS", "")).split(",")
        if origin.strip()
    ]
    if frontend_origin and frontend_origin not in cors_origins:
        cors_origins.append(frontend_origin)

    public_panel = frontend_origin or api_origin
    replacements = {
        key: target_values[key] for key in _TARGET_DATABASE_KEYS
    }
    replacements.update(
        {
            "MSM_PANEL_URL": _quoted(public_panel),
            "MSM_API_URL": _quoted(api_origin),
            "MSM_COOKIE_DOMAIN": _quoted(""),
            "MSM_COOKIE_CROSS_SITE": "true" if frontend_origin else "false",
            "MSM_CORS_ALLOWED_ORIGINS": _quoted(",".join(cors_origins)),
            "MSM_SERVE_FRONTEND": "false" if frontend_origin else "true",
            "MSM_LOCAL_AGENT_ENABLED": "false",
            "MSM_LOCAL_AGENT_ENV_FILE": _quoted("/opt/msm/msm-agent/.env"),
            "MSM_SERVERS_DIR": _quoted("/opt/msm/servers"),
            "MSM_PANEL_CONFIG_DIR": _quoted("/opt/msm"),
            "MSM_PANEL_BACKUP_DIR": _quoted("/opt/msm/backups/panel"),
            "MSM_BLUEPRINTS_DIR": _quoted("/opt/msm/blueprints/community"),
            "MSM_DOCKER_HOST": _quoted(""),
        }
    )
    _atomic_write(output_env, _replace_values(source_lines, replacements))

    dis_lines = [
        "# Automatisch für den migrierten MSM-Control-Plane erzeugt.",
        f"MSM_SECRET_KEY={source_values['MSM_SECRET_KEY']}",
        f"MSM_DIS_SALT={source_values['MSM_DIS_SALT']}",
        f"MSM_DIS_SIDECAR_TOKEN={source_values['MSM_DIS_SIDECAR_TOKEN']}",
        "MSM_DIS_SIDECAR_PORT=9100",
        "NODE_ENV=production",
    ]
    _atomic_write(dis_output, dis_lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    disable = subparsers.add_parser("disable-local-agent")
    disable.add_argument("--env-file", required=True)

    merge = subparsers.add_parser("merge-target")
    merge.add_argument("--source-env", required=True)
    merge.add_argument("--target-env", required=True)
    merge.add_argument("--output-env", required=True)
    merge.add_argument("--dis-output", required=True)
    merge.add_argument("--api-origin", required=True)
    merge.add_argument("--frontend-origin")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "disable-local-agent":
            disable_local_agent(Path(args.env_file))
        else:
            merge_target_environment(
                source_env=Path(args.source_env),
                target_env=Path(args.target_env),
                output_env=Path(args.output_env),
                dis_output=Path(args.dis_output),
                api_origin=args.api_origin,
                frontend_origin=args.frontend_origin,
            )
    except Exception as exc:
        print(f"FEHLER: Migrationskonfiguration konnte nicht vorbereitet werden: {exc}", file=sys.stderr)
        return 1
    print("Migrationskonfiguration sicher vorbereitet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
