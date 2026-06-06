"""Dropbox-Backup-Provider.

Nutzt das offizielle Dropbox Python-SDK. Auth via App-Key + App-Secret +
manuell generierter Refresh-Token (Standard server-zu-server Pattern,
OAuth-Flow einmalig in der Dropbox-App-Konsole).

Sync-API, wird vom Backup-Service in ``asyncio.to_thread`` gewrappt.

Path-Layout (Konvention, gleich wie local/s3/sftp):

    /<base_path>/<server_id>/<filename>              (Daten, z. B. server_42_...tar.gz.enc)
    /<base_path>/<server_id>/<filename>.meta.json   (Meta-File daneben)

Dropbox-Konvention: Pfade MÜSSEN mit ``/`` beginnen. Default base_path
ist ``/msm-backups`` (anpassbar ueber ``MSM_BACKUP_DROPBOX_PATH``).

Security:
  - Refresh-Token / App-Secret / App-Key werden **nicht** geloggt, nicht
    in Fehlermeldungen/Dumps geschrieben.
  - Fehlertexte sind generisch ("Upload fehlgeschlagen" — kein Pfad,
    kein Token).
  - Path-Traversal-Schutz: ``remote_key`` muss relativ sein, kein ``..``.
  - Adapter sieht nur Chiffretext (Verschluesselung im Caller, ADR-0013).
  - Constructor validiert alle Felder (leer / Format).

Bekannte Limitierung v1:
  - ``files_upload`` ist Single-Shot. Per Dropbox-Docs max 150 MB pro
    Aufruf. Fuer groessere Backups wird ``ProviderError("Upload
    fehlgeschlagen")`` geworfen. Chunked-Upload via
    ``upload_session_start`` ist eine zukuenftige Erweiterung —
    dokumentiert im ADR.
"""
import logging
import posixpath
from pathlib import Path
from typing import Iterator, Optional

import dropbox
from dropbox import Dropbox
from dropbox.exceptions import ApiError, AuthError, DropboxException
from dropbox.files import FileMetadata, WriteMode

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"  # Backup "<key>.enc" → Meta "<key>.meta.json"

# Dropbox-API-Fehler, die wir als Provider-Fehler klassifizieren.
# AuthError separat gefangen → spaeter evtl. mit Retry-Logic.
_DROPBOX_ERRORS: tuple[type[BaseException], ...] = (
    AuthError,
    ApiError,
    DropboxException,
    OSError,
    IOError,
)


class DropboxProvider(BackupProvider):
    """Backup-Adapter fuer Dropbox."""

    name = "dropbox"

    # Per Dropbox-Docs: single-shot files_upload kappt bei 150 MB.
    # Chunked-Upload (upload_session_start) ist eine zukuenftige
    # Erweiterung — siehe ADR-0010.
    SINGLE_UPLOAD_LIMIT_BYTES = 150 * 1024 * 1024

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        refresh_token: str,
        base_path: str = "/msm-backups",
    ) -> None:
        if not app_key:
            raise ProviderError("Dropbox App-Key nicht konfiguriert")
        if not app_secret:
            raise ProviderError("Dropbox App-Secret nicht konfiguriert")
        if not refresh_token:
            raise ProviderError("Dropbox Refresh-Token nicht konfiguriert")
        if not base_path:
            raise ProviderError("Dropbox-Basispfad nicht konfiguriert")
        if not base_path.startswith("/"):
            raise ProviderError("Dropbox-Basispfad muss mit / beginnen")

        self.app_key = app_key
        self.app_secret = app_secret
        self.refresh_token = refresh_token
        # base_path normalisieren: kein trailing slash, immer mit / anfangend
        self.base_path = "/" + posixpath.normpath(base_path).lstrip("/")

        # Dropbox-Client: SDK handhabt Token-Refresh intern (ueber
        # oauth2_refresh_token). Wir uebergeben KEINEN access_token —
        # SDK holt/erneuert ihn bei jedem Call.
        self._client = Dropbox(
            app_key=app_key,
            app_secret=app_secret,
            oauth2_refresh_token=refresh_token,
        )

    # ── private helpers ───────────────────────────────────────────────────

    def _full_path(self, remote_key: str) -> str:
        """Berechnet den vollen Dropbox-Pfad, mit Traversal-Check.

        remote_key: ``<server_id>/<filename>`` (z. B. ``42/server.tar.gz.enc``)
        Voller Pfad: ``/<base_path>/<server_id>/<filename>``
        """
        if not remote_key:
            raise ProviderError("Ungueltiger Backup-Key")
        if remote_key.startswith("/"):
            raise ProviderError("Ungueltiger Backup-Key")
        if ".." in Path(remote_key).parts:
            raise ProviderError("Ungueltiger Backup-Key")
        full = posixpath.normpath(posixpath.join(self.base_path, remote_key))
        # Bounds-Check: full muss unter base_path bleiben
        if full != self.base_path and not full.startswith(self.base_path + "/"):
            raise ProviderError("Backup-Key ausserhalb des erlaubten Bereichs")
        return full

    @staticmethod
    def _meta_key(remote_key: str) -> str:
        """Mappt Daten-Key auf Meta-Key (Konvention: <key>.meta.json)."""
        return remote_key + META_SUFFIX

    @staticmethod
    def _is_not_found(api_error: ApiError) -> bool:
        """Prueft ob ein ``ApiError`` ein 'path/not_found' Fehler ist.

        Dropbox-Fehler sind tagged unions: ``e.error.is_path()`` prueft
        ob es ein Path-Fehler ist, ``e.error.get_path()`` liefert den
        ``LookupError``, und ``lookup.is_not_found()`` ist True bei
        404-pfaden. Wir nutzen das fuer idempotente Operationen
        (delete, list) wo 'not found' = OK ist.
        """
        try:
            err = api_error.error
            if not err.is_path():
                return False
            lookup = err.get_path()
            return lookup.is_not_found()
        except AttributeError:
            return False

    def _iter_meta_entries(
        self, entries: list
    ) -> Iterator[FileMetadata]:
        """Filtert file entries mit .meta.json Endung."""
        for entry in entries:
            if isinstance(entry, FileMetadata) and entry.name.endswith(META_SUFFIX):
                yield entry

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft Credentials + Refresh + base_path existent.

        Side-Effect: legt ``base_path`` an, falls noch nicht existent
        (idempotente ``files_create_folder_v2``). Macht die Install-Flow
        einfacher.
        """
        try:
            # 1) Auth-Check: erzwingt Token-Refresh
            self._client.users_get_current_account()
        except _DROPBOX_ERRORS:
            return False

        # 2) base_path existent/erstellbar
        try:
            self._client.files_get_metadata(self.base_path)
            return True
        except ApiError as e:
            if not self._is_not_found(e):
                return False
        except _DROPBOX_ERRORS:
            return False
        # not_found → Ordner anlegen
        try:
            self._client.files_create_folder_v2(self.base_path)
            return True
        except _DROPBOX_ERRORS:
            return False

    def upload(
        self,
        local_path: Path,
        remote_key: str,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> BackupLocation:
        if not local_path.is_file():
            raise ProviderError("Lokale Datei existiert nicht")
        full = self._full_path(remote_key)
        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0

        if size_bytes > self.SINGLE_UPLOAD_LIMIT_BYTES:
            # Bewusst hart: lieber klare Fehlermeldung als stille
            # Korruption. Chunked-Upload ist eine zukuenftige Erweiterung.
            raise ProviderError(
                f"Datei zu gross fuer Single-Shot-Upload "
                f"({size_bytes} > {self.SINGLE_UPLOAD_LIMIT_BYTES} Bytes). "
                "Chunked-Upload noch nicht implementiert — siehe ADR-0010."
            )

        try:
            # Dropbox legt Parent-Ordner beim Upload automatisch an —
            # kein mkdir-p noetig (im Gegensatz zu SFTP).
            with open(local_path, "rb") as f:
                data = f.read()
            self._client.files_upload(
                data,
                full,
                mode=WriteMode.overwrite,
                autorename=False,
                mute=True,
            )
        except _DROPBOX_ERRORS as e:
            logger.warning("Dropbox-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

        # Dropbox-SDK hat keinen nativen Progress-Callback fuer single-shot
        # files_upload. Wir reporten einmalig am Ende mit der finalen
        # Dateigroesse (gleiche Semantik wie LocalProvider).
        if progress_cb:
            progress_cb(size_bytes)
        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        full = self._full_path(remote_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # files_download liefert (FileMetadata, requests.Response) —
            # Response.content hat die Bytes.
            _, resp = self._client.files_download(full)
            data = resp.content
        except _DROPBOX_ERRORS as e:
            logger.warning("Dropbox-Download fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e

        local_path.write_bytes(data)
        if progress_cb:
            progress_cb(len(data))

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- und Meta-Datei. Idempotent (fehlend = ok)."""
        try:
            full = self._full_path(remote_key)
            meta_full = self._full_path(self._meta_key(remote_key))
        except ProviderError:
            # Malformed key — idempotenter Cleanup toleriert das
            return

        for path in (full, meta_full):
            try:
                self._client.files_delete_v2(path)
            except ApiError as e:
                # not_found ist OK (idempotent), andere API-Fehler → raise
                if not self._is_not_found(e):
                    logger.warning(
                        "Dropbox-Delete fehlgeschlagen: %s", type(e).__name__
                    )
                    raise ProviderError("Loeschen fehlgeschlagen") from e
            except _DROPBOX_ERRORS as e:
                logger.warning("Dropbox-Delete fehlgeschlagen: %s", type(e).__name__)
                raise ProviderError("Loeschen fehlgeschlagen") from e

    def list_metadata(self) -> list[BackupMetadata]:
        """Listet alle ``*.meta.json`` unter ``base_path`` und parsed jedes.

        Pagination via ``files_list_folder_continue`` (Dropbox-Default
        500 Entries/Page). Kaputte / nicht-parsebare Meta-Files werden
        uebersprungen (kein Raise). 'base_path not found' → leere Liste.
        """
        results: list[BackupMetadata] = []
        try:
            resp = self._client.files_list_folder(
                self.base_path, recursive=True
            )
        except ApiError as e:
            # base_path not_found → keine Backups (akzeptabel)
            if self._is_not_found(e):
                return []
            logger.warning("Dropbox-List fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Liste fehlgeschlagen") from e
        except _DROPBOX_ERRORS as e:
            logger.warning("Dropbox-List fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Liste fehlgeschlagen") from e

        try:
            self._collect_meta(resp.entries, results)
            while resp.has_more:
                resp = self._client.files_list_folder_continue(resp.cursor)
                self._collect_meta(resp.entries, results)
        except _DROPBOX_ERRORS as e:
            logger.warning(
                "Dropbox-List-Pagination fehlgeschlagen: %s", type(e).__name__
            )
            raise ProviderError("Liste fehlgeschlagen") from e

        return results

    def _collect_meta(
        self, entries: list, results: list[BackupMetadata]
    ) -> None:
        """Laedt jede Meta-Datei, parsed, faengt Fehler pro Eintrag."""
        for entry in self._iter_meta_entries(entries):
            try:
                _, resp = self._client.files_download(entry.path_lower)
                raw = resp.content.decode("utf-8")
                results.append(BackupMetadata.from_json(raw))
            except (IOError, OSError, ValueError, TypeError, UnicodeDecodeError) as e:
                # Generischer Skip-Log ohne Pfad-Leak
                logger.warning(
                    "Ueberspringe kaputte Backup-Metadaten: %s",
                    type(e).__name__,
                )
                continue
            except _DROPBOX_ERRORS as e:
                # Netzwerk/Auth-Fehler beim Meta-Download → skip
                logger.warning(
                    "Ueberspringe kaputte Backup-Metadaten (Download): %s",
                    type(e).__name__,
                )
                continue
