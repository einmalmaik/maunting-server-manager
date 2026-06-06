"""S3-kompatibler Backup-Storage-Adapter.

Spricht die Standard-AWS-S3-API und ist damit kompatibel mit:
- AWS S3
- Hetzner Object Storage (S3-Endpoint)
- Cloudflare R2
- Backblaze B2 (S3-kompatibler Endpoint)
- MinIO (self-hosted)
- Wasabi
- DigitalOcean Spaces (mit S3-Endpoint)

Setzt ``boto3`` voraus (sync-API). Der Backup-Service wrappt die sync-
Methoden in ``asyncio.to_thread``, weil Backup-Operationen ohnehin
nicht im Request-Thread laufen duerfen.

Auth-Modell: Access-Key + Secret-Key im Klartext (in .env, chmod 600).
Kein AWS-IAM-Role-Pattern, weil das auf einem einfachen Self-Hosted-
Server die Komplexitaet nicht rechtfertigt.

Security:
- Provider-Adapter sieht nur Chiffretext (Verschluesselung im Caller).
- Fehlertexte sind generisch (kein Bucket-Name, kein Pfad, kein Key-Leak).
- List-Metadaten ueberspringt kaputte/parse-failende Files ohne Raise.
- Idempotente delete()-Operation (S3 delete_object auf nicht-existente
  Keys ist OK).
"""
import logging
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from .base import (
    BackupLocation,
    BackupMetadata,
    BackupProvider,
    ProgressCallback,
    ProviderError,
)

logger = logging.getLogger(__name__)

META_SUFFIX = ".meta.json"


class S3Provider(BackupProvider):
    """S3-kompatibler Storage-Adapter."""

    name = "s3"

    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str = "",
    ) -> None:
        if not bucket:
            raise ProviderError("S3-Bucket nicht konfiguriert")
        if not access_key or not secret_key:
            raise ProviderError("S3-Credentials fehlen")

        self.bucket = bucket
        # Boto-Client-Config: Connection-Pool + Timeouts
        # request_checksum_calculation="when_required" deaktiviert boto3's
        # automatische CRC32-Trailer (default seit boto3 1.34) — spart
        # CPU/Netzwerk-Overhead und ist kompatibel mit Test-Mockings
        # (moto 5.x). Bei Echt-S3 kein Funktionsverlust, weil S3 Checks
        # serverseitig validiert.
        boto_cfg = BotoConfig(
            connect_timeout=30,
            read_timeout=300,  # 5 Min fuer grosse Downloads
            retries={"max_attempts": 3, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        kwargs: dict = {
            "region_name": region or "us-east-1",
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "config": boto_cfg,
        }
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        self._client = boto3.client("s3", **kwargs)

    # ── private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _meta_key(remote_key: str) -> str:
        """Mappt Daten-Key auf Meta-Key.

        Konvention (gleich wie Local): <key>.enc → <key>.meta.json
        """
        return remote_key + META_SUFFIX

    def _head_size(self, remote_key: str) -> int:
        """Fragt ContentLength ab. Fallback 0 bei Fehler (best effort)."""
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=remote_key)
            return int(head.get("ContentLength", 0))
        except (ClientError, BotoCoreError):
            return 0

    # ── public API ────────────────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Prueft ob Bucket erreichbar + existent. head_bucket reicht dafuer."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except (ClientError, BotoCoreError):
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
        try:
            if progress_cb:
                # Multipart-Upload fuer grosse Files. Threshold 5 MB,
                # Chunks 5 MB. use_threads=False weil wir ohnehin in
                # asyncio.to_thread laufen — extra Thread-Pool waere Overhead.
                from boto3.s3.transfer import TransferConfig
                tcfg = TransferConfig(
                    multipart_threshold=5 * 1024 * 1024,
                    multipart_chunksize=5 * 1024 * 1024,
                    use_threads=False,
                )
                self._client.upload_file(
                    str(local_path),
                    self.bucket,
                    remote_key,
                    Config=tcfg,
                    Callback=progress_cb,
                )
            else:
                self._client.upload_file(
                    str(local_path),
                    self.bucket,
                    remote_key,
                )
        except (ClientError, BotoCoreError) as e:
            # Generischer Text — kein Bucket-Name, kein Key im Log
            logger.warning("S3-Upload fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Upload fehlgeschlagen") from e

        size_bytes = self._head_size(remote_key)
        size_mb = int(size_bytes // (1024 * 1024)) if size_bytes else None
        return BackupLocation(remote_key=remote_key, size_mb=size_mb)

    def download(
        self,
        remote_key: str,
        local_path: Path,
        *,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if progress_cb:
                from boto3.s3.transfer import TransferConfig
                tcfg = TransferConfig(
                    multipart_threshold=5 * 1024 * 1024,
                    multipart_chunksize=5 * 1024 * 1024,
                    use_threads=False,
                )
                self._client.download_file(
                    self.bucket,
                    remote_key,
                    str(local_path),
                    Config=tcfg,
                    Callback=progress_cb,
                )
            else:
                self._client.download_file(
                    self.bucket,
                    remote_key,
                    str(local_path),
                )
        except (ClientError, BotoCoreError) as e:
            logger.warning("S3-Download fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Download fehlgeschlagen") from e

    def delete(self, remote_key: str) -> None:
        """Loescht Daten- und Meta-Key. Idempotent (S3 akzeptiert fehlende Keys)."""
        try:
            self._client.delete_object(Bucket=self.bucket, Key=remote_key)
            self._client.delete_object(
                Bucket=self.bucket, Key=self._meta_key(remote_key)
            )
        except (ClientError, BotoCoreError) as e:
            logger.warning("S3-Delete fehlgeschlagen: %s", type(e).__name__)
            raise ProviderError("Loeschen fehlgeschlagen") from e

    def list_metadata(self) -> list[BackupMetadata]:
        """Listet alle *.meta.json im Bucket und parsed jedes.

        Pagination wird via Paginator gehandhabt (S3-Default 1000/Page).
        Kaputte/parse-failende Meta-Files werden uebersprungen.
        """
        try:
            paginator = self._client.get_paginator("list_objects_v2")
        except (ClientError, BotoCoreError) as e:
            raise ProviderError("Liste fehlgeschlagen") from e

        results: list[BackupMetadata] = []
        try:
            for page in paginator.paginate(Bucket=self.bucket):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if not key.endswith(META_SUFFIX):
                        continue
                    try:
                        resp = self._client.get_object(Bucket=self.bucket, Key=key)
                        body = resp["Body"].read().decode("utf-8")
                        results.append(BackupMetadata.from_json(body))
                    except (ClientError, BotoCoreError, ValueError, TypeError, OSError) as e:
                        # Generischer Skip-Log ohne Pfad/Key
                        logger.warning(
                            "Ueberspringe kaputte Backup-Metadaten: %s",
                            type(e).__name__,
                        )
                        continue
        except (ClientError, BotoCoreError) as e:
            raise ProviderError("Liste fehlgeschlagen") from e
        return results
