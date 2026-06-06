"""Lokaler Filesystem-Adapter fuer Backup-Storage.

Verhaelt sich exakt wie das bisherige Backup-Verhalten: tar.gz liegt unter
``/opt/msm/backups/<server_id>/...``. Mit Cloud-only-Mode wird er spaeter
nur noch fuer Restore aus dem Legacy-Bestand genutzt, sobald die
Auto-Migration durch ist.

Security:
- Path-Traversal-Schutz: ``remote_key`` darf nie zu einem Pfad ausserhalb
  ``root_dir`` aufloesen (``..``, absolute Pfade).
- Idempotentes ``delete()`` (fehlende Dateien sind ok).
- ``list_metadata()`` ueberspringt kaputte meta.json-Dateien ohne Raise.
- Keine Pfade in Exceptions (generische ``ProviderError``-Texte).
"""
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProviderError,
    ProgressCallback,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"  # Backup "<key>.enc" → Meta "<key>.meta.json"


class LocalProvider(BackupProvider):
    """Backup-Provider der auf das lokale Filesystem schreibt."""

    name = "local"

    def __init__(self, root_dir: str) -> None:
        self.root_dir = Path(root_dir)

    # ── private helpers ───────────────────────────────────────────────────

    def _ensure_root(self) -> Path:
        """Erstellt root_dir falls noetig und gibt den resolved-Pfad zurueck.

        Wird auch fuer Test-Setups unter tmp_path benutzt, wo der Pfad
        existieren muss, bevor upload() ihn anlegt.
        """
        self.root_dir.mkdir(parents=True, exist_ok=True)
        return self.root_dir.resolve()

    def _full_path(self, remote_key: str) -> Path:
        """Mappt remote_key → absoluten Pfad unter root_dir, mit Traversal-Check.

        remote_key-Format: "<server_id>/<filename>" (z. B. "42/server_42_...tar.gz.enc").
        Niemals absoluter Pfad, nie "..".
        """
        if not remote_key or os.path.isabs(remote_key):
            raise ProviderError("Ungueltiger Backup-Key")
        if ".." in Path(remote_key).parts:
            raise ProviderError("Ungueltiger Backup-Key")
        root = self._ensure_root()
        candidate = (self.root_dir / remote_key).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ProviderError("Backup-Key ausserhalb des erlaubten Bereichs")
        return candidate

    def _meta_path(self, data_path: Path) -> Path:
        """Mappt Daten-Pfad auf den Meta-JSON-Pfad daneben.

        Konvention: <remote_key>.enc → <remote_key>.meta.json
        (wir strippen NICHT .enc, sondern haengen nur .meta.json an)
        """
        # data_path: "root/foo/server.tar.gz.enc" → "root/foo/server.tar.gz.enc.meta.json"
        return data_path.with_name(data_path.name + META_SUFFIX)

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Root muss existieren und schreibbar sein."""
        try:
            root = self._ensure_root()
            return os.access(root, os.W_OK)
        except OSError:
            return False

    def upload(
        self,
        local_path: Path,
        remote_key: str,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> BackupLocation:
        dest = self._full_path(remote_key)
        if not local_path.is_file():
            raise ProviderError("Lokale Datei existiert nicht")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # shutil.copy2 haelt Timestamps; kein In-File-Progress in stdlib.
        shutil.copy2(local_path, dest)
        size_bytes = dest.stat().st_size
        if progress_cb:
            progress_cb(size_bytes)
        size_mb = int(size_bytes // (1024 * 1024))
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        src = self._full_path(remote_key)
        if not src.is_file():
            raise ProviderError("Backup-Datei nicht gefunden")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, local_path)
        size_bytes = local_path.stat().st_size
        if progress_cb:
            progress_cb(size_bytes)

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- + Meta-Datei. Idempotent."""
        try:
            data = self._full_path(remote_key)
        except ProviderError:
            # Key ist malformed — wir tolerieren das beim idempotenten Cleanup
            return
        for path in (data, self._meta_path(data)):
            try:
                if path.is_file():
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                # Kein Pfad im Log (Data-Minimization per AGENTS.md §9)
                logger.warning("Konnte Backup-Datei nicht loeschen: %s", e)

    def list_metadata(self) -> list[BackupMetadata]:
        """Scannt root_dir rekursiv nach *.meta.json und parsed jedes.

        Kaputte / nicht-parsebare Dateien werden uebersprungen — sie
        sind moeglicherweise von einer alten, nicht-MSM-Installation
        oder einem partial-Write. Kein Raise.
        """
        try:
            root = self._ensure_root()
        except OSError:
            return []
        results: list[BackupMetadata] = []
        for meta_file in root.rglob("*" + META_SUFFIX):
            if not meta_file.is_file():
                continue
            try:
                raw = meta_file.read_text(encoding="utf-8")
                results.append(BackupMetadata.from_json(raw))
            except (OSError, ValueError, TypeError) as e:
                # Generischer Log, kein Pfad-Leak
                logger.warning("Ueberspringe kaputte Backup-Metadaten: %s", type(e).__name__)
                continue
        return results
