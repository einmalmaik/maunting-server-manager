"""GCS-Backup-Provider (Google Cloud Storage).

Nutzt das offizielle ``google-cloud-storage`` Python-SDK. Auth via
Service-Account-JSON-Datei (Pfad in ``.env``, vom User mit chmod 600
angelegt). Service-Account braucht ``roles/storage.objectAdmin`` auf
den Bucket.

Sync-API, wird vom Backup-Service in ``asyncio.to_thread`` gewrappt.

Path-Layout (Konvention, gleich wie local/s3/sftp/dropbox):

    <path_prefix>/<server_id>/<filename>              (Daten, z. B. server_42_...tar.gz.enc)
    <path_prefix>/<server_id>/<filename>.meta.json   (Meta-File daneben)

GCS-Pfade sind flach (kein fuehrender ``/``, kein abschliessender
``/``). Default-Prefix ist ``msm-backups`` (anpassbar ueber
``MSM_BACKUP_GCS_PATH_PREFIX``).

Security:
  - Service-Account-JSON wird **nicht** gelesen ausser vom
    ``google.cloud.storage.Client`` (kein eigenes Parsing).
  - Fehlertexte sind generisch ("Upload fehlgeschlagen" — kein
    Bucket-Name, kein Pfad, kein Key-Leak).
  - Path-Traversal-Schutz: ``remote_key`` muss relativ sein, kein
    ``..``, voller Pfad bleibt unter ``path_prefix``.
  - Idempotente delete()-Operation (GCS loescht nicht-existente
    Objekte ohne Fehler).
  - Adapter sieht nur Chiffretext (Verschluesselung im Caller, ADR-0013).
  - Constructor validiert alle Felder.

Bekannte Limitierung v1:
  - ``Blob.upload_from_filename`` ist Single-Shot, bei grossen Files
    > 5 GB sollte ``Blob.chunk_size`` + resumable Upload genutzt
    werden. Die meisten Game-Server-Backups liegen unter 1 GB, daher
    akzeptabel. ADR dokumentiert dies.
"""
import logging
import posixpath
from pathlib import Path
from typing import Iterator, Optional

from google.api_core import exceptions as gcs_exceptions
from google.cloud import storage as gcs
from google.cloud.storage.client import Client as GcsClient
from google.cloud.storage.bucket import Bucket

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"

# Generische GCS-Fehler, die wir als Provider-Fehler klassifizieren.
# ``GoogleAPICallError`` ist die gemeinsame Basisklasse aller
# google-cloud-storage Exceptions (NotFound, Forbidden, etc. erben davon).
_GCS_ERRORS: tuple[type[BaseException], ...] = (
    gcs_exceptions.GoogleAPICallError,
    gcs_exceptions.NotFound,
    gcs_exceptions.Forbidden,
    gcs_exceptions.Unauthorized,
    gcs_exceptions.ServiceUnavailable,
    OSError,
    IOError,
)


class GCSProvider(BackupProvider):
    """Backup-Adapter fuer Google Cloud Storage."""

    name = "gcs"

    def __init__(
        self,
        bucket: str,
        sa_file_path: str,
        path_prefix: str = "msm-backups",
    ) -> None:
        if not bucket:
            raise ProviderError("GCS-Bucket nicht konfiguriert")
        if not sa_file_path:
            raise ProviderError("GCS Service-Account-Datei nicht konfiguriert")
        if not path_prefix:
            raise ProviderError("GCS-Pfad-Prefix nicht konfiguriert")
        # path_prefix normalisieren: kein fuehrender/trailing slash
        self.path_prefix = path_prefix.strip("/")
        if not self.path_prefix:
            raise ProviderError("GCS-Pfad-Prefix ungueltig")

        self.bucket_name = bucket
        self.sa_file_path = sa_file_path

        # GCS-Client: Auth via Service-Account-JSON. ``from_service_account_file``
        # liest die JSON-Datei und konfiguriert credentials + project_id.
        # Wir uebergeben den Pfad nur an google-cloud-storage — kein eigenes
        # Parsing (sicher gegen Path-Traversal auf das JSON).
        try:
            self._client: GcsClient = gcs.Client.from_service_account_json(
                sa_file_path
            )
        except (ValueError, OSError) as e:
            # ValueError = JSON kaputt / falsches Format, OSError = Datei nicht
            # lesbar. Beides wird zu generischem ProviderError.
            logger.warning("GCS-Client-Init fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("GCS-Credentials ungueltig") from e

        # Bucket-Handle (lazy) — wird bei jedem Call genutzt.
        self._bucket: Bucket = self._client.bucket(bucket)

    # ── private helpers ───────────────────────────────────────────────────

    def _full_key(self, remote_key: str) -> str:
        """Berechnet den vollen GCS-Objektnamen, mit Traversal-Check.

        remote_key: ``<server_id>/<filename>`` (z. B. ``42/server.tar.gz.enc``)
        Voller Pfad: ``<path_prefix>/<server_id>/<filename>``
        """
        if not remote_key:
            raise ProviderError("Ungueltiger Backup-Key")
        if remote_key.startswith("/"):
            raise ProviderError("Ungueltiger Backup-Key")
        if ".." in Path(remote_key).parts:
            raise ProviderError("Ungueltiger Backup-Key")
        full = posixpath.normpath(posixpath.join(self.path_prefix, remote_key))
        # Bounds-Check: full muss unter path_prefix bleiben
        if full != self.path_prefix and not full.startswith(
            self.path_prefix + "/"
        ):
            raise ProviderError("Backup-Key ausserhalb des erlaubten Bereichs")
        return full

    @staticmethod
    def _meta_key(remote_key: str) -> str:
        """Mappt Daten-Key auf Meta-Key (Konvention: <key>.meta.json)."""
        return remote_key + META_SUFFIX

    def _iter_meta_blobs(
        self, blobs: Iterator
    ) -> Iterator:
        """Filtert Blob-Objekte mit .meta.json Endung.

        GCS listet alles unter dem Prefix (inkl. 'Subfolder'-Marker,
        falls welche existieren — wir filtern die raus).
        """
        for blob in blobs:
            name = getattr(blob, "name", "")
            if not name:
                continue
            # GCS kann Folder-Marker liefern (size=0, endet mit /) — skip
            if name.endswith("/"):
                continue
            if name.endswith(META_SUFFIX):
                yield blob

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft Credentials + Bucket-Erreichbarkeit.

        list_blobs(max_results=1) ist ein minimal-invasiver Check: er
        validiert die Credentials (sonst 401/403) und dass der Bucket
        existiert (sonst 404). Wir lesen KEINE Daten.
        """
        try:
            # exists() macht HEAD-Request auf den Bucket — schnell, kein Listing
            return self._bucket.exists(retry=None)
        except _GCS_ERRORS:
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
        full = self._full_key(remote_key)
        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0

        blob = self._bucket.blob(full)
        try:
            # GCS-SDK hat keinen per-call Progress-Callback in
            # upload_from_filename (anders als boto3). Wir reporten daher
            # einmalig am Ende mit der finalen Dateigroesse — gleiche
            # Semantik wie LocalProvider/DropboxProvider.
            if progress_cb:
                # Chunksize fuer resumable Upload: 5 MB — klein genug
                # fuer saubere Retries bei Abbruch, gross genug fuer
                # Throughput bei grossen Backups.
                blob.chunk_size = 5 * 1024 * 1024
            blob.upload_from_filename(
                str(local_path),
                retry=None,
            )
        except _GCS_ERRORS as e:
            logger.warning("GCS-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

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
        full = self._full_key(remote_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._bucket.blob(full)
        try:
            blob.download_to_filename(
                str(local_path),
                retry=None,
            )
        except gcs_exceptions.NotFound as e:
            # Spezifischer Fehlertext: 404 ist eine klare Information,
            # die der Restore-Pfad braucht (um "Backup fehlt im Provider"
            # von "Netzwerk-Fehler" zu unterscheiden).
            raise ProviderError("Download fehlgeschlagen") from e
        except _GCS_ERRORS as e:
            logger.warning("GCS-Download fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e

        if progress_cb:
            try:
                size = local_path.stat().st_size
            except OSError:
                size = 0
            progress_cb(size)

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- und Meta-Blob. Idempotent (fehlend = ok)."""
        try:
            full = self._full_key(remote_key)
            meta_full = self._full_key(self._meta_key(remote_key))
        except ProviderError:
            # Malformed key — idempotenter Cleanup toleriert das
            return

        for name in (full, meta_full):
            blob = self._bucket.blob(name)
            try:
                blob.delete(retry=None)
            except gcs_exceptions.NotFound:
                # 404 = ok (idempotent)
                continue
            except _GCS_ERRORS as e:
                logger.warning("GCS-Delete fehlgeschlagen: %s", type(e).__name__)
                raise ProviderError("Loeschen fehlgeschlagen") from e

    def list_metadata(self) -> list[BackupMetadata]:
        """Listet alle ``*.meta.json`` unter ``path_prefix`` und parsed jedes.

        GCS paginiert via ``list_blobs`` (auto-pagination, gibt einen
        Iterator ueber alle Pages). Kaputte / nicht-parsebare Meta-Files
        werden uebersprungen (kein Raise).
        """
        results: list[BackupMetadata] = []
        try:
            blobs = self._client.list_blobs(
                self.bucket_name,
                prefix=self.path_prefix + "/",
            )
        except _GCS_ERRORS as e:
            logger.warning("GCS-List fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Liste fehlgeschlagen") from e

        for blob in self._iter_meta_blobs(blobs):
            # Vollstaendigen Pfad extrahieren (relativ zu path_prefix)
            try:
                raw = blob.download_as_text(retry=None)
                results.append(BackupMetadata.from_json(raw))
            except (
                gcs_exceptions.GoogleAPICallError,
                ValueError,
                TypeError,
                UnicodeDecodeError,
            ) as e:
                # Generischer Skip-Log ohne Pfad/Bucket-Leak
                logger.warning(
                    "Ueberspringe kaputte Backup-Metadaten: %s",
                    type(e).__name__,
                )
                continue
            except OSError as e:
                logger.warning(
                    "Ueberspringe kaputte Backup-Metadaten (I/O): %s",
                    type(e).__name__,
                )
                continue

        return results
