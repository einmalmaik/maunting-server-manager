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

Live-Progress:
  Anders als boto3 (S3) bietet ``google-cloud-storage`` keinen
  per-call Progress-Callback in ``upload_from_filename`` /
  ``download_to_file``. Wir loesen das mit zwei Strategien:

  - **Upload mit progress_cb:** manueller Resumable-Upload ueber das
    GCS-Resumable-Protokoll. Eine Upload-Session wird via
    ``Blob.create_resumable_upload_session`` erstellt, dann werden
    die Bytes in 8-MB-Chunks per ``AuthorizedSession.patch`` an die
    Resumable-URL geschickt. Nach jedem Chunk feuern wir den
    Progress-Callback mit der kumulativen Byte-Anzahl. Das ist exakt
    die gleiche Semantik wie der boto3-Callback, der Backup-Service
    kann die Bytes an die bestehende ``_active_backups``-Struktur
    durchreichen ohne Sonderlogik fuer GCS.

  - **Download mit progress_cb:** manueller Stream-Download ueber
    ``AuthorizedSession.get(url, stream=True)`` und Chunk-Read.
    Pro gelesenem Chunk feuern wir den Progress-Callback.

  Ohne progress_cb nutzen wir weiterhin ``upload_from_filename`` /
  ``download_to_filename`` (kein Overhead fuer den Fallback-Pfad).

Security:
  - Service-Account-JSON wird **nicht** gelesen ausser vom
    ``google.cloud.storage.Client`` (kein eigenes Parsing).
  - Fehlertexte sind generisch ("Upload fehlgeschlagen" — kein
    Bucket-Name, kein Pfad, kein Key-Leak).
  - Path-Traversal-Schutz: ``remote_key`` muss relativ sein, kein
    ``..``, voller Pfad bleibt unter ``path_prefix``.
  - Idempotente delete()-Operation (GCS loescht nicht-existente
    Objekte ohne Fehler — NotFound wird abgefangen).
  - Adapter sieht nur Chiffretext (Verschluesselung im Caller, ADR-0013).
  - Constructor validiert alle Felder.
"""
import logging
import posixpath
from pathlib import Path
from typing import Iterator, Optional

from google.api_core import exceptions as gcs_exceptions
from google.auth.transport import requests as google_requests
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

# Resumable-Upload-Chunk-Groesse. 8 MB ist der von Google empfohlene
# Sweet-Spot: klein genug, dass Retries nach Netzwerkabbruch nicht zu
# viel wiederholen, gross genug fuer guten Throughput. GCS akzeptiert
# 256 KB bis 5 GB pro Chunk.
_GCS_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# Download-Stream-Chunk-Groesse fuer Live-Progress.
_GCS_DOWNLOAD_CHUNK_BYTES = 8 * 1024 * 1024

# GCS Resumable-Upload-Status-Codes: 200 = fertig, 308 = mehr Chunks noetig.
_GCS_RESUMABLE_OK = 200
_GCS_RESUMABLE_CONTINUE = 308

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

    def _authorized_session(self):
        """Baut eine AuthorizedSession aus den Client-Credentials.

        Wird fuer den Resumable-Upload- und den Stream-Download-Pfad
        genutzt. google.auth.transport.requests.AuthorizedSession
        handhabt Token-Refresh automatisch (HTTP 401 → refresh → retry).
        """
        return google_requests.AuthorizedSession(
            self._client._credentials,
            refresh_status_codes=[401],
        )

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft Credentials + Bucket-Erreichbarkeit.

        ``exists()`` macht HEAD-Request auf den Bucket — schnell, kein
        Listing, keine Daten. Validiert Credentials (sonst 401/403) und
        Bucket-Existenz (sonst 404).
        """
        try:
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
        """Laedt eine (bereits verschluesselte) Datei in den Bucket.

        Mit ``progress_cb``: Resumable-Upload mit Chunked-Progress
        (8-MB-Chunks, Callback pro Chunk mit kumulativer Byte-Anzahl).

        Ohne ``progress_cb``: Single-Shot-Upload via
        ``upload_from_filename`` (kein Overhead fuer Aufrufer, die
        keinen Live-Progress brauchen, z. B. Auto-Migration).
        """
        if not local_path.is_file():
            raise ProviderError("Lokale Datei existiert nicht")
        full = self._full_key(remote_key)
        try:
            size_bytes = local_path.stat().st_size
        except OSError:
            size_bytes = 0

        blob = self._bucket.blob(full)
        if progress_cb is None:
            # Fallback-Pfad: single-shot, kein Progress
            try:
                blob.upload_from_filename(str(local_path), retry=None)
            except _GCS_ERRORS as e:
                logger.warning(
                    "GCS-Upload fehlgeschlagen: %s", type(e).__name__
                )
                raise ProviderError("Upload fehlgeschlagen") from e
        else:
            # Resumable-Upload mit Chunked-Progress
            self._resumable_upload(
                blob=blob,
                local_path=local_path,
                size_bytes=size_bytes,
                progress_cb=progress_cb,
            )

        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def _resumable_upload(
        self,
        blob: "gcs.Blob",
        local_path: Path,
        size_bytes: int,
        progress_cb: ProgressCallback,
    ) -> None:
        """Manueller Resumable-Upload mit Progress-Callback pro Chunk.

        GCS-Resumable-Protokoll:
          1) ``create_resumable_upload_session`` → Upload-URL
          2) PATCH-Requests mit ``Content-Range: bytes X-Y/Z``
          3) Pro Chunk: GCS antwortet mit 308 (Resume Incomplete) bis
             der finale Chunk 200 OK zurueckgibt.

        Wir feuern nach jedem vollstaendig geschriebenen Chunk den
        Progress-Callback mit der kumulativen Byte-Anzahl. Fehler
        (Netzwerk, Auth, 4xx/5xx) werden in generischen ProviderError
        gewandelt.
        """
        # 1) Resumable Session erstellen
        try:
            upload_url = blob.create_resumable_upload_session(
                content_type="application/octet-stream",
                size=size_bytes,
                retry=None,
                timeout=60,
            )
        except _GCS_ERRORS as e:
            logger.warning(
                "GCS-Resumable-Session fehlgeschlagen: %s",
                type(e).__name__,
            )
            raise ProviderError("Upload fehlgeschlagen") from e

        # 2) AuthorizedSession fuer PATCH-Requests (handhabt Token-Refresh)
        session = self._authorized_session()

        # 3) Bytes in Chunks hochladen
        bytes_sent = 0
        try:
            with open(local_path, "rb") as f:
                while bytes_sent < size_bytes:
                    chunk_size = min(
                        _GCS_UPLOAD_CHUNK_BYTES, size_bytes - bytes_sent
                    )
                    chunk = f.read(chunk_size)
                    if not chunk:
                        # Datei wurde seit dem stat() geschrumpft — Abbruch
                        # als inkonsistenten Zustand werten.
                        raise ProviderError("Upload fehlgeschlagen")

                    content_range = (
                        f"bytes {bytes_sent}-{bytes_sent + len(chunk) - 1}"
                        f"/{size_bytes}"
                    )
                    response = session.patch(
                        upload_url,
                        data=chunk,
                        headers={"Content-Range": content_range},
                        retry=False,
                    )

                    if response.status_code not in (
                        _GCS_RESUMABLE_OK,
                        _GCS_RESUMABLE_CONTINUE,
                    ):
                        # Unerwarteter Statuscode → ProviderError.
                        # response.text koennte GCS-spezifische Details
                        # enthalten, wir geben das NICHT weiter (Security:
                        # kein Pfad/Key/Bucket-Leak).
                        logger.warning(
                            "GCS-Resumable-Upload unerwarteter Status: %d",
                            response.status_code,
                        )
                        raise ProviderError("Upload fehlgeschlagen")

                    bytes_sent += len(chunk)
                    progress_cb(bytes_sent)
        except _GCS_ERRORS as e:
            logger.warning("GCS-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e
        except OSError as e:
            logger.warning("GCS-Upload I/O-Fehler: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        """Laedt eine Datei aus dem Bucket herunter.

        Mit ``progress_cb``: Stream-Download mit Chunked-Progress
        (8-MB-Chunks, Callback pro Chunk mit kumulativer Byte-Anzahl).

        Ohne ``progress_cb``: Single-Shot-Download via
        ``download_to_filename`` (kein Overhead fuer Fallback-Pfad).
        """
        full = self._full_key(remote_key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._bucket.blob(full)

        if progress_cb is None:
            # Fallback-Pfad: single-shot, kein Progress
            try:
                blob.download_to_filename(str(local_path), retry=None)
            except gcs_exceptions.NotFound as e:
                raise ProviderError("Download fehlgeschlagen") from e
            except _GCS_ERRORS as e:
                logger.warning(
                    "GCS-Download fehlgeschlagen: %s", type(e).__name__
                )
                raise ProviderError("Download fehlgeschlagen") from e
            return

        # Stream-Download mit Progress
        self._stream_download(
            blob=blob,
            local_path=local_path,
            progress_cb=progress_cb,
        )

    def _stream_download(
        self,
        blob: "gcs.Blob",
        local_path: Path,
        progress_cb: ProgressCallback,
    ) -> None:
        """Manueller Stream-Download mit Progress-Callback pro Chunk.

        Wir holen die Media-URL (signierte URL oder direkter GCS-
        Endpoint) und streamen die Bytes per AuthorizedSession. Nach
        jedem gelesenen Chunk feuern wir den Progress-Callback mit der
        kumulativen Byte-Anzahl.

        Vorteil gegenueber ``download_to_file(wrapped_file)``: garantiert
        chunked-reads (auch fuer kleine Files), unabhaengig davon wie
        GCS intern liefert.
        """
        # media_link ist die GCS-Media-URL des Blobs. Bei privaten Buckets
        # liefert die URL 401/403 ohne Credentials; die AuthorizedSession
        # setzt den Bearer-Token automatisch.
        media_url = blob.media_link
        if not media_url:
            # Aelterer/ungenutzter Blob ohne media_link — Bucket-Listing
            # via API als Fallback. Sollte selten sein.
            try:
                blob.reload(client=self._client)
            except _GCS_ERRORS as e:
                raise ProviderError("Download fehlgeschlagen") from e
            media_url = blob.media_link
            if not media_url:
                raise ProviderError("Download fehlgeschlagen")

        session = self._authorized_session()
        bytes_written = 0
        try:
            response = session.get(media_url, stream=True, retry=False)
            # 404 = "Backup fehlt im Provider" → ProviderError
            if response.status_code == 404:
                raise ProviderError("Download fehlgeschlagen")
            response.raise_for_status()

            with open(local_path, "wb") as f:
                for chunk in response.iter_content(
                    chunk_size=_GCS_DOWNLOAD_CHUNK_BYTES
                ):
                    if not chunk:
                        continue
                    f.write(chunk)
                    bytes_written += len(chunk)
                    progress_cb(bytes_written)
        except _GCS_ERRORS as e:
            logger.warning(
                "GCS-Download fehlgeschlagen: %s", type(e).__name__
            )
            raise ProviderError("Download fehlgeschlagen") from e
        except OSError as e:
            logger.warning("GCS-Download I/O-Fehler: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e

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
