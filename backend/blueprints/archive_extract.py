"""Sicheres Archiv-Entpacken (ZIP + Tar-Familie).

Security-Invarianten:
- Zip-Slip / Path-Traversal via ``../`` wird pro Member abgelehnt.
- Symlinks/Hardlinks/Device-Nodes/FIFOs in Tar werden uebersprungen
  (nicht extrahiert) und ins Log geschrieben.
- Zip-Bomb-Schutz: Decompressed-Groesse darf ``MAX_DECOMPRESSED_BYTES``
  nicht ueberschreiten (10 GiB) + Ratio-Limit pro Member.
- NUL-Byte oder Backslash in Member-Namen wird abgelehnt.

KISS: Reiner Helper, keine Blueprint-Abhaengigkeiten.
"""

from __future__ import annotations

import logging
import os
import tarfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_DECOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
PER_MEMBER_COMPRESSION_RATIO = 100  # entpackt <= 100x rohe Archiv-Groesse pro Member
_CHUNK_BYTES = 1024 * 1024  # 1 MiB

_ALLOWED_EXTRACT_EXTS = (
    ".zip",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".txz",
    ".tar.bz2",
    ".tbz2",
)


class ArchiveExtractError(RuntimeError):
    """Aufrufer-freundlicher Fehler fuer Entpack-Probleme."""


def _resolve_into(base_dir: Path, member_name: str, extract_to: Path | None) -> Path:
    """Loest den Ziel-Pfad auf und stellt sicher, dass er unter ``base_dir`` liegt."""
    if not member_name or member_name.startswith("/") or "\x00" in member_name or "\\" in member_name:
        raise ArchiveExtractError(f"Member '{member_name}' hat unsicheren Pfad.")
    if any(part == ".." for part in member_name.split("/")):
        raise ArchiveExtractError(f"Member '{member_name}' enthält '..' (Zip-Slip).")
    base = base_dir
    if extract_to:
        base = extract_to
    target = (base / member_name).resolve()
    base_real = base_dir.resolve()
    try:
        target.relative_to(base_real)
    except ValueError as exc:
        raise ArchiveExtractError(
            f"Member '{member_name}' würde aus dem erlaubten Verzeichnis entweichen."
        ) from exc
    return target


def _detect_archive_kind(name: str) -> str | None:
    n = name.lower()
    if n.endswith(".zip"):
        return "zip"
    for ext in _ALLOWED_EXTRACT_EXTS:
        if n.endswith(ext):
            return "tar"
    return None


def _extract_zip(zf: zipfile.ZipFile, base_dir: Path, extract_to: Path | None) -> None:
    archive_size = sum(getattr(zi, "compress_size", 0) for zi in zf.infolist())
    decompressed_budget = max(archive_size * PER_MEMBER_COMPRESSION_RATIO, _CHUNK_BYTES)
    decompressed_budget = min(decompressed_budget, MAX_DECOMPRESSED_BYTES)
    written = 0
    for info in zf.infolist():
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and (unix_mode & 0o170000) == 0o120000:
            logger.warning("Zip-Member '%s' ist ein Symlink — wird übersprungen.", info.filename)
            continue
        if info.is_dir():
            target = _resolve_into(base_dir, info.filename, extract_to)
            target.mkdir(parents=True, exist_ok=True)
            continue
        target = _resolve_into(base_dir, info.filename, extract_to)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > decompressed_budget:
                    target.unlink(missing_ok=True)
                    raise ArchiveExtractError(
                        "Decompressed-Größen-Limit überschritten (Zip-Bomb-Schutz)."
                    )
                dst.write(chunk)


def _extract_tar(tf: tarfile.TarFile, base_dir: Path, extract_to: Path | None) -> None:
    archive_size = 0
    try:
        archive_size = os.path.getsize(tf.name) if tf.name else 0
    except OSError:
        archive_size = 0
    decompressed_budget = max(archive_size * PER_MEMBER_COMPRESSION_RATIO, _CHUNK_BYTES)
    decompressed_budget = min(decompressed_budget, MAX_DECOMPRESSED_BYTES)
    written = 0
    for member in tf.getmembers():
        if member.issym() or member.islnk():
            logger.warning("Tar-Member '%s' ist Sym-/Hardlink — wird übersprungen.", member.name)
            continue
        if member.isdev() or member.isfifo():
            logger.warning("Tar-Member '%s' ist Device/FIFO — wird übersprungen.", member.name)
            continue
        target = _resolve_into(base_dir, member.name, extract_to)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        src = tf.extractfile(member)
        if src is None:
            continue
        with src, open(target, "wb") as dst:
            while True:
                chunk = src.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > decompressed_budget:
                    target.unlink(missing_ok=True)
                    raise ArchiveExtractError(
                        "Decompressed-Größen-Limit überschritten (Tar-Bomb-Schutz)."
                    )
                dst.write(chunk)


def safe_extract_archive(archive_path: Path, extract_to: Path, base_dir: Path) -> None:
    """Entpackt ``archive_path`` sicher nach ``extract_to``.

    :param archive_path: Pfad zum Archiv (ZIP oder Tar-Variante).
    :param extract_to: Ziel-Verzeichnis fuer die extrahierten Dateien.
    :param base_dir: Sicherheits-Boundary — kein Member darf ausserhalb landen.
    :raises ArchiveExtractError: bei Sicherheitsverletzungen oder Korruption.
    """
    kind = _detect_archive_kind(archive_path.name)
    if kind is None:
        raise ArchiveExtractError("Nicht unterstütztes Archiv-Format.")

    if kind == "zip":
        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                _extract_zip(zf, base_dir, extract_to)
        except zipfile.BadZipFile as exc:
            raise ArchiveExtractError("Ungültiges ZIP-Archiv.") from exc
    else:
        try:
            with tarfile.open(str(archive_path), "r:*") as tf:
                _extract_tar(tf, base_dir, extract_to)
        except tarfile.TarError as exc:
            raise ArchiveExtractError(f"Tar-Fehler: {exc}") from exc
