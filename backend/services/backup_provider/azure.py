"""Azure-Blob-Backup-Provider.

Nutzt das offizielle ``azure-storage-blob`` Python-SDK. Auth via
**Connection-String** (kein Azure-AD-Setup, simpelster Self-Hosted-
Pfad). Connection-String liegt in ``.env`` mit ``chmod 600``.

Sync-API, wird vom Backup-Service in ``asyncio.to_thread`` gewrappt.

Path-Layout (Konvention, gleich wie local/s3/sftp/dropbox/gcs):

    <container>/<path_prefix>/<server_id>/<filename>              (Daten)
    <container>/<path_prefix>/<server_id>/<filename>.meta.json   (Meta)

Azure hat **Container** als top-level-Namespace (vergleichbar mit
einem Bucket in S3/GCS). Default-Container: ``msm-backups``
(anpassbar ueber ``MSM_BACKUP_AZURE_CONTAINER``).
Path-Prefix innerhalb des Containers ist optional (default: leer).
Der Container wird beim ersten ``test_connection`` erstellt, falls
er noch nicht existiert (idempotenter ``create_container``).

Live-Progress:
  Anders als boto3, aber wie ``google-cloud-storage`` hat
  ``azure-storage-blob`` einen nativen Progress-Hook fuer Upload:
  ``upload_blob(data, ..., progress_hook=fn)`` mit Signatur
  ``fn(bytes_transferred, total_bytes)``. Wir nutzen ihn direkt.

  Fuer Download bietet das SDK **keinen** per-call Hook, aber
  ``download_blob()`` liefert einen ``StorageStreamDownloader``, den
  wir per ``readinto(file_wrapper)`` konsumieren. Der Wrapper zaehlt
  Bytes beim ``write()`` und ruft unseren kumulativen progress_cb
  auf. Gleiche Semantik wie bei allen anderen Providern.

  Ohne ``progress_cb`` nutzen wir den Single-Shot-Pfad
  (``upload_blob``/``download_blob`` ohne Hook/Wrapper) — kein
  Overhead fuer den Fallback-Pfad.

Security:
  - Connection-String wird **nicht** geloggt, nicht in Fehlermeldungen.
  - Constructor validiert alle Felder (Connection-String-Format
    wird vom SDK validiert; ungueltige Strings erzeugen
    ValueError → generischer ProviderError).
  - Fehlertexte sind generisch ("Upload fehlgeschlagen" — kein
    Account-Name, kein Container, kein Pfad).
  - Path-Traversal-Schutz: ``remote_key`` muss relativ sein, kein
    ``..``, voller Pfad bleibt unter ``path_prefix``.
  - Idempotente delete()-Operation (Azure toleriert fehlende Blobs).
  - Adapter sieht nur Chiffretext (Verschluesselung im Caller, ADR-0013).
"""
import logging
import posixpath
from pathlib import Path
from typing import Iterator, Optional

from azure.core import exceptions as azure_exceptions
from azure.storage.blob import (
    BlobServiceClient,
    ContainerClient,
)

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"

# Generische Azure-Fehler, die wir als Provider-Fehler klassifizieren.
# ``AzureError`` ist die gemeinsame Basisklasse aller
# azure-storage-blob Exceptions.
_AZURE_ERRORS: tuple[type[BaseException], ...] = (
    azure_exceptions.AzureError,
    azure_exceptions.ResourceNotFoundError,
    azure_exceptions.ResourceExistsError,
    azure_exceptions.ClientAuthenticationError,
    azure_exceptions.ServiceRequestError,
    OSError,
    IOError,
)


class _ProgressFileWrapper:
    """File-Wrapper fuer Download mit Live-Progress.

    ``readinto(stream)`` aus ``StorageStreamDownloader`` ruft
    ``stream.write(data)`` fuer jeden empfangenen Chunk auf. Wir
    zaehlen Bytes beim ``write()`` und rufen ``progress_cb``
    kumulativ auf.

    Azure-SDK chunked automatisch (~4 MB pro Chunk je nach
    Service-Config). Bei kleinen Files: ein einziger write() mit
    allen Bytes → ein Progress-Call am Ende. Bei grossen Files:
    viele write()-Calls → Live-Progress.
    """

    def __init__(
        self, file_obj, progress_cb: ProgressCallback
    ) -> None:
        self._file_obj = file_obj
        self._progress_cb = progress_cb
        self._bytes_written = 0

    def write(self, data: bytes) -> int:
        n = self._file_obj.write(data)
        self._bytes_written += n
        self._progress_cb(self._bytes_written)
        return n


class AzureProvider(BackupProvider):
    """Backup-Adapter fuer Azure Blob Storage."""

    name = "azure"

    def __init__(
        self,
        connection_string: str,
        container: str = "msm-backups",
        path_prefix: str = "",
        account_name: str = "",
    ) -> None:
        if not connection_string:
            raise ProviderError("Azure Connection-String nicht konfiguriert")
        if not container:
            raise ProviderError("Azure-Container nicht konfiguriert")

        # Minimal-Format-Check: ein gueltiger Connection-String enthaelt
        # mindestens "AccountName=" und "AccountKey=". Der vollstaendige
        # Check passiert im SDK (from_connection_string), aber wir geben
        # frueh einen klaren Fehler.
        if "AccountName=" not in connection_string:
            raise ProviderError("Azure Connection-String ungueltig")
        if "AccountKey=" not in connection_string:
            raise ProviderError("Azure Connection-String ungueltig")

        # path_prefix normalisieren: kein fuehrender/trailing slash
        self.path_prefix = path_prefix.strip("/")
        self.container_name = container
        self.account_name = account_name  # nur fuer Diagnostics / Logging

        try:
            self._service: BlobServiceClient = (
                BlobServiceClient.from_connection_string(connection_string)
            )
        except (ValueError, azure_exceptions.AzureError) as e:
            logger.warning(
                "Azure-Client-Init fehlgeschlagen: %s", type(e).__name__
            )
            raise ProviderError("Azure-Credentials ungueltig") from e

        self._container: ContainerClient = self._service.get_container_client(
            container
        )

    # ── private helpers ───────────────────────────────────────────────────

    def _full_blob_name(self, remote_key: str) -> str:
        """Berechnet den vollen Azure-Blob-Namen, mit Traversal-Check.

        remote_key: ``<server_id>/<filename>``
        Voller Pfad: ``<path_prefix>/<server_id>/<filename>`` (oder nur
        ``<server_id>/<filename>`` wenn path_prefix leer).
        """
        if not remote_key:
            raise ProviderError("Ungueltiger Backup-Key")
        if remote_key.startswith("/"):
            raise ProviderError("Ungueltiger Backup-Key")
        if ".." in Path(remote_key).parts:
            raise ProviderError("Ungueltiger Backup-Key")
        if self.path_prefix:
            full = posixpath.normpath(
                posixpath.join(self.path_prefix, remote_key)
            )
            if full != self.path_prefix and not full.startswith(
                self.path_prefix + "/"
            ):
                raise ProviderError(
                    "Backup-Key ausserhalb des erlaubten Bereichs"
                )
            return full
        # path_prefix leer → nur normalize + check
        return posixpath.normpath(remote_key)

    @staticmethod
    def _meta_key(remote_key: str) -> str:
        """Mappt Daten-Key auf Meta-Key (Konvention: <key>.meta.json)."""
        return remote_key + META_SUFFIX

    def _iter_meta_blobs(
        self, blobs: Iterator
    ) -> Iterator:
        """Filtert Blob-Properties mit .meta.json Endung."""
        for blob in blobs:
            name = getattr(blob, "name", "")
            if not name:
                continue
            # Folder-Marker (size=0, endet mit /) — skip
            if name.endswith("/"):
                continue
            if name.endswith(META_SUFFIX):
                yield blob

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft Credentials + Container-Erreichbarkeit.

        ``exists()`` macht HEAD-Request auf den Container. Wenn der
        Container fehlt, wird er idempotent angelegt (typical fuer
        frische Azure-Setups — Backup-User hat nicht zwingend
        Container-Erstellung vorab gemacht).
        """
        try:
            if self._container.exists():
                return True
            # Container existiert nicht → versuchen anzulegen
            self._container.create_container()
            return True
        except _AZURE_ERRORS:
            return False

    def upload(
        self,
        local_path: Path,
        remote_key: str,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> BackupLocation:
        """Laedt eine (bereits verschluesselte) Datei in den Container.

        Mit ``progress_cb``: native Azure-``progress_hook`` mit
        kumulativer Byte-Anzahl pro Hook-Call (Azure hookt pro
        uebertragenen Block, ~4 MB bei Standard-Config).

        Ohne ``progress_cb``: single-shot via ``upload_blob`` ohne
        Hook (kein Overhead fuer Auto-Migration o. ae.).
        """
        if not local_path.is_file():
            raise ProviderError("Lokale Datei existiert nicht")
        full = self._full_blob_name(remote_key)
        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0

        # Container-Existenz sicherstellen (idempotent)
        try:
            if not self._container.exists():
                self._container.create_container()
        except _AZURE_ERRORS as e:
            logger.warning("Azure-Container-Create fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

        blob_client = self._container.get_blob_client(full)
        try:
            with open(local_path, "rb") as f:
                if progress_cb is None:
                    blob_client.upload_blob(
                        f,
                        blob_type="BlockBlob",
                        length=size_bytes,
                        overwrite=True,
                    )
                else:
                    # Azure-Hook: (bytes_transferred, total_bytes) -> None
                    # Wir mappen auf unseren kumulativen Counter.
                    def hook(done: int, _total: Optional[int]) -> None:
                        progress_cb(done)

                    blob_client.upload_blob(
                        f,
                        blob_type="BlockBlob",
                        length=size_bytes,
                        overwrite=True,
                        progress_hook=hook,
                    )
        except _AZURE_ERRORS as e:
            logger.warning("Azure-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        """Laedt eine Datei aus dem Container herunter.

        Mit ``progress_cb``: ``StorageStreamDownloader.readinto()`` mit
        File-Wrapper, der Bytes beim ``write()`` zaehlt und
        ``progress_cb`` kumulativ ruft.

        Ohne ``progress_cb``: single-shot via ``readall()``.
        """
        full = self._full_blob_name(remote_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob_client = self._container.get_blob_client(full)

        try:
            stream = blob_client.download_blob()
        except _AZURE_ERRORS as e:
            logger.warning(
                "Azure-Download fehlgeschlagen: %s", type(e).__name__
            )
            raise ProviderError("Download fehlgeschlagen") from e

        try:
            with open(local_path, "wb") as f:
                if progress_cb is None:
                    # Single-Shot: alle Bytes am Stueck lesen
                    data = stream.readall()
                    f.write(data)
                else:
                    # Stream mit Progress: readinto in einer Schleife bis
                    # EOF (Azure-SDK liefert pro Call einen Block, nicht
                    # alle Bytes). Pro readinto-Call feuert der Wrapper
                    # den progress_cb mit der kumulativen Byte-Anzahl.
                    wrapper = _ProgressFileWrapper(f, progress_cb)
                    while stream.readinto(wrapper) > 0:
                        pass
        except _AZURE_ERRORS as e:
            logger.warning(
                "Azure-Download fehlgeschlagen: %s", type(e).__name__
            )
            raise ProviderError("Download fehlgeschlagen") from e
        except OSError as e:
            logger.warning("Azure-Download I/O-Fehler: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- und Meta-Blob. Idempotent (fehlend = ok)."""
        try:
            full = self._full_blob_name(remote_key)
            meta_full = self._full_blob_name(self._meta_key(remote_key))
        except ProviderError:
            return

        for name in (full, meta_full):
            blob_client = self._container.get_blob_client(name)
            try:
                blob_client.delete_blob()
            except azure_exceptions.ResourceNotFoundError:
                # 404 = ok (idempotent)
                continue
            except _AZURE_ERRORS as e:
                logger.warning(
                    "Azure-Delete fehlgeschlagen: %s", type(e).__name__
                )
                raise ProviderError("Loeschen fehlgeschlagen") from e

    def list_metadata(self) -> list[BackupMetadata]:
        """Listet alle ``*.meta.json`` unter ``path_prefix`` und parsed jedes.

        Pagination via ``ItemPaged`` (automatisch). Kaputte / nicht-
        parsebare Meta-Files werden uebersprungen (kein Raise).
        """
        results: list[BackupMetadata] = []
        try:
            blobs = self._container.list_blobs(
                name_starts_with=(
                    self.path_prefix + "/"
                    if self.path_prefix
                    else None
                )
            )
        except _AZURE_ERRORS as e:
            logger.warning("Azure-List fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Liste fehlgeschlagen") from e

        for blob in self._iter_meta_blobs(blobs):
            try:
                blob_client = self._container.get_blob_client(blob.name)
                raw = blob_client.download_blob().readall().decode("utf-8")
                results.append(BackupMetadata.from_json(raw))
            except (
                azure_exceptions.AzureError,
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
