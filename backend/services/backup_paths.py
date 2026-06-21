"""Blueprint-gesteuerte Backup-Pfade (Config + Savegames, kein voller install_dir)."""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BACKUP_MANIFEST_ARCNAME = ".msm/backup-manifest.json"
BACKUP_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class BackupPlan:
    scope: Literal["full", "selective"]
    include_paths: tuple[str, ...] = ()


def _blueprint_for_server(server) -> object | None:
    from games import get_plugin

    plugin = get_plugin(getattr(server, "game_type", "") or "")
    if plugin is None:
        return None
    return plugin.get_blueprint()


def backup_plan_for_server(server) -> BackupPlan:
    bp = _blueprint_for_server(server)
    if bp is None:
        return BackupPlan(scope="full")
    backup = getattr(bp, "backup", None)
    if backup is None or not getattr(backup, "includePaths", None):
        return BackupPlan(scope="full")
    paths = tuple(str(p).strip() for p in backup.includePaths if str(p).strip())
    if not paths:
        return BackupPlan(scope="full")
    return BackupPlan(scope="selective", include_paths=paths)


def resolve_backup_members(install_dir: str, include_paths: list[str] | tuple[str, ...]) -> list[str]:
    """Relative Pfade unter install_dir, die ins Archiv sollen (existierende Dateien/Ordner)."""
    base = Path(install_dir).resolve()
    if not base.is_dir():
        return []

    members: set[str] = set()
    for raw in include_paths:
        pattern = str(raw).strip()
        if not pattern:
            continue
        has_glob = any(ch in pattern for ch in ("*", "?", "["))
        if has_glob:
            hits = glob.glob(str(base / pattern), recursive=True)
            for hit in hits:
                p = Path(hit).resolve()
                try:
                    rel = p.relative_to(base).as_posix()
                except ValueError:
                    continue
                members.add(rel)
            continue

        p = (base / pattern).resolve()
        try:
            p.relative_to(base)
        except ValueError:
            continue
        if p.exists():
            members.add(Path(pattern).as_posix())

    return sorted(members)


def build_manifest(scope: str, include_paths: list[str] | tuple[str, ...] | None = None) -> dict:
    payload: dict = {"version": BACKUP_MANIFEST_VERSION, "scope": scope}
    if scope == "selective" and include_paths:
        payload["includePaths"] = list(include_paths)
    return payload


def read_backup_scope_from_archive(archive_path: str) -> tuple[str, dict | None]:
    import tarfile

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            try:
                member = tar.getmember(BACKUP_MANIFEST_ARCNAME)
            except KeyError:
                return "full", None
            extracted = tar.extractfile(member)
            if extracted is None:
                return "full", None
            data = json.loads(extracted.read().decode("utf-8"))
            scope = str(data.get("scope") or "full")
            if scope not in ("full", "selective"):
                scope = "full"
            return scope, data
    except (tarfile.TarError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "full", None


def create_selective_backup_tar(
    filepath: str,
    install_dir: str,
    include_paths: list[str] | tuple[str, ...],
) -> None:
    import io
    import tarfile

    members = resolve_backup_members(install_dir, include_paths)
    if not members:
        raise FileNotFoundError(
            "Keine Backup-Dateien gefunden. Prüfe Blueprint backup.includePaths und ob Config/Saves existieren."
        )

    manifest = build_manifest("selective", include_paths)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    base = Path(install_dir).resolve()

    with tarfile.open(filepath, "w:gz") as tar:
        info = tarfile.TarInfo(name=BACKUP_MANIFEST_ARCNAME)
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        for rel in members:
            full = base / rel
            if not full.exists():
                continue
            tar.add(str(full), arcname=rel, recursive=True)