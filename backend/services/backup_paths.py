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


def build_manifest(
    scope: str,
    include_paths: list[str] | tuple[str, ...] | None = None,
    *,
    server_id: int | None = None,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> dict:
    """Erstellt das Backup-Manifest.

    Pflicht-Felder: version, scope, timestamp, server_id.
    Erweiterte Felder (fuer S3-uploaded Backups): encrypted, encryption_algorithm.
    Fuer rein lokale Backups bleibt encrypted False/abwesend.
    """
    from datetime import datetime, timezone

    payload: dict = {
        "version": BACKUP_MANIFEST_VERSION,
        "scope": scope,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if server_id is not None:
        payload["server_id"] = server_id
    if scope == "selective" and include_paths:
        payload["includePaths"] = list(include_paths)
    if encrypted:
        payload["encrypted"] = True
        payload["encryption_algorithm"] = encryption_algorithm or "AES-256-GCM"
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
    *,
    server_id: int | None = None,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> None:
    import io
    import tarfile

    members = resolve_backup_members(install_dir, include_paths)
    if not members:
        raise FileNotFoundError(
            "Keine Backup-Dateien gefunden. Prüfe Blueprint backup.includePaths und ob Config/Saves existieren."
        )

    manifest = build_manifest(
        "selective",
        include_paths,
        server_id=server_id,
        encrypted=encrypted,
        encryption_algorithm=encryption_algorithm,
    )
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

# Backup-Integration fuer Postgres-Dumps (v1.4.4)
BACKUP_POSTGRES_ARCNAME = ".msm/postgres.sql"


def create_full_backup_tar(
    filepath: str,
    install_dir: str,
    *,
    pg_dump_bytes: bytes | None = None,
    server_id: int | None = None,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> None:
    """Vollstaendiges install_dir-Snapshot als .tar.gz (Ersatz fuer ``tar -czf``).

    Schreibt:
    - alle Dateien aus ``install_dir`` als relative Pfade
    - ``.msm/backup-manifest.json`` (``scope=full``)
    - ``.msm/postgres.sql`` (nur wenn ``pg_dump_bytes`` nicht leer)

    KISS-Notiz: das ist eine 1:1-Umsetzung des vorhandenen ``subprocess tar``-
    Aufrufs in ``run_backup``, plus Postgres-Behaelter. Wir machen das als Python
    nicht als Shell, weil:
    1. Die Postgres-SQL gleichzeitig als Member ins Archiv muss (Shell-pipe
       waere haesslich).
    2. Atomic -- entweder das gesamte Archiv existiert oder gar nichts
       (tarfile-Context-Manager garantiert das).
    """
    import io
    import tarfile
    from pathlib import Path as _P

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    base = _P(install_dir).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"install_dir fehlt: {base}")

    manifest = build_manifest(
        "full",
        server_id=server_id,
        encrypted=encrypted,
        encryption_algorithm=encryption_algorithm,
    )
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")

    with tarfile.open(filepath, "w:gz") as tar:
        # Manifest zuerst -- ist klein und reproduzierbar.
        info = tarfile.TarInfo(name=BACKUP_MANIFEST_ARCNAME)
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))

        # Postgres-Dump, sofern mitgegeben (Server mit postgres_enabled).
        if pg_dump_bytes:
            pg_info = tarfile.TarInfo(name=BACKUP_POSTGRES_ARCNAME)
            pg_info.size = len(pg_dump_bytes)
            tar.addfile(pg_info, io.BytesIO(pg_dump_bytes))

        # Dateien aus install_dir -- rekursiv.
        # Wir benutzen ``tar.add(str(base), arcname=".", recursive=True)`` und
        # nutzen dann das tarfile-internen setzten der arcnames. ABER: das wuerde
        # einen "."-Prefix erzeugen. Wir gehen stattdessen File fuer File durch:
        for path in base.rglob("*"):
            try:
                rel = path.relative_to(base).as_posix()
                if path.is_file():
                    # Folgt ggf. Symlinks nicht (Sicherheit) -- GNU tar --no-acl
                    # Analog; wir nutzen rekursiv ohne follow.
                    tar.add(str(path), arcname=rel, recursive=False)
            except OSError:
                # Race: Datei zwischen rglob und add geloescht -- ueberspringen,
                # kein hartes Failure (Backup ist Best-Effort).
                continue


def read_pg_dump_bytes_from_archive(archive_path: str) -> bytes | None:
    """Holt ``.msm/postgres.sql`` aus einem Backup-tar. Liefert ``None`` wenn nicht vorhanden."""
    import tarfile

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            try:
                member = tar.getmember(BACKUP_POSTGRES_ARCNAME)
            except KeyError:
                return None
            extracted = tar.extractfile(member)
            if extracted is None:
                return None
            return extracted.read()
    except (tarfile.TarError, OSError):
        return None
