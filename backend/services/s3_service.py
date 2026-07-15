"""S3 Service — Fassade fuer alle S3-kompatiblen Object-Storage-Operationen.

Single Source of Truth fuer S3-Uploads, -Downloads, -Listing und -Deletes.
Nutzt boto3 mit endpoint_url-Override, sodass jeder S3-kompatible Provider
funktioniert (Backblaze B2, Wasabi, Hetzner, MinIO, AWS, ...).

Sicherheits-Invarianten:
- S3-Credentials werden verschluesselt in panel_settings gespeichert und
  vor jeder Operation via DIS entschluesselt (AAD="msm:backup:s3").
- Keine Credentials in Fehlermeldungen oder Logs (generische Messages).
- Wenn S3 nicht konfiguriert ist, schlagen alle Operationen mit
  S3NotConfiguredError fehl (kein Verbindungsaufbau mit leeren Credentials).
- boto3-Fehler werden ohne Credential-Leak weitergereicht.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

import boto3
from botocore.exceptions import ClientError

from services.dis_client import DisClient
from services.panel_settings_service import PanelSettingsService

logger = logging.getLogger(__name__)

# AAD fuer S3-Credential-Verschluesselung (Domain Separation).
_S3_AAD = "msm:backup:s3"


class S3NotConfiguredError(Exception):
    """S3 ist nicht konfiguriert (fehlende panel_settings)."""


class S3OperationError(Exception):
    """S3-Operation fehlgeschlagen (generische Nachricht, keine Credentials)."""


class _IteratorStream:
    """Adapter: wraps einen Iterator[bytes] als file-like objekt mit read().

    boto3 upload_fileobj erwartet ein file-like Objekt. Encrypt-Streams
    (BackupCryptoService.encrypt_file_stream) liefern Iterator[bytes].
    """

    def __init__(self, iterator: Iterator[bytes]):
        self._it = iter(iterator)
        self._buf = b""
        self._done = False

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            chunks = [self._buf]
            self._buf = b""
            for chunk in self._it:
                chunks.append(chunk)
            self._done = True
            return b"".join(chunks)
        while len(self._buf) < size and not self._done:
            try:
                self._buf += next(self._it)
            except StopIteration:
                self._done = True
        result = self._buf[:size]
        self._buf = self._buf[size:]
        return result


class S3Service:
    """Statische Fassade fuer S3-Operationen via boto3."""

    @staticmethod
    def _get_config() -> dict[str, str]:
        """Liest S3-Config aus panel_settings und entschluesselt Credentials via DIS.

        Raises S3NotConfiguredError wenn erforderliche Werte fehlen.
        endpoint ist optional (fuer AWS S3 nicht noetig).
        """
        endpoint = PanelSettingsService.get("backup.s3_endpoint")
        access_enc = PanelSettingsService.get("backup.s3_access_key_encrypted")
        secret_enc = PanelSettingsService.get("backup.s3_secret_key_encrypted")
        bucket = PanelSettingsService.get("backup.s3_bucket")
        region = PanelSettingsService.get("backup.s3_region")

        if not all([access_enc, secret_enc, bucket]):
            raise S3NotConfiguredError(
                "S3 ist nicht konfiguriert. Bitte S3-Einstellungen setzen."
            )

        access_key = DisClient.decrypt(access_enc, aad=_S3_AAD)
        secret_key = DisClient.decrypt(secret_enc, aad=_S3_AAD)

        return {
            "endpoint": endpoint,
            "access_key": access_key,
            "secret_key": secret_key,
            "bucket": bucket,
            "region": region,
        }

    @staticmethod
    def get_ephemeral_agent_s3_config() -> dict[str, str]:
        """Plaintext S3 config for one-shot handoff to MSM Agent (Phase 6).

        Caller must only keep this in memory for the HTTP request to the agent.
        Never log or persist the returned credentials.
        """
        cfg = S3Service._get_config()
        return {
            "endpoint": cfg.get("endpoint") or "",
            "access_key": cfg["access_key"],
            "secret_key": cfg["secret_key"],
            "bucket": cfg["bucket"],
            "region": cfg.get("region") or "",
        }

    @staticmethod
    def _get_client():
        """Erstellt einen boto3-S3-Client aus entschluesselten Credentials."""
        cfg = S3Service._get_config()
        kwargs: dict[str, Any] = {
            "aws_access_key_id": cfg["access_key"],
            "aws_secret_access_key": cfg["secret_key"],
        }
        if cfg["endpoint"]:
            kwargs["endpoint_url"] = cfg["endpoint"]
        if cfg["region"]:
            kwargs["region_name"] = cfg["region"]
        return boto3.client("s3", **kwargs)

    @staticmethod
    def _get_bucket() -> str:
        """Gibt den konfigurierten Bucket-Namen zurueck."""
        return PanelSettingsService.get("backup.s3_bucket")

    @staticmethod
    def upload_stream(stream, key: str) -> None:
        """Laedt einen Stream (file-like oder Iterator[bytes]) via boto3 hoch.

        boto3 verwendet automatisch Multipart-Upload fuer grosse Dateien.
        """
        cfg = S3Service._get_config()
        client = S3Service._get_client()
        if hasattr(stream, "read"):
            fileobj = stream
        else:
            fileobj = _IteratorStream(stream)
        try:
            client.upload_fileobj(fileobj, Bucket=cfg["bucket"], Key=key)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "UnknownError")
            logger.warning("S3 upload fehlgeschlagen (Key=%s): %s", key, err_code)
            raise S3OperationError(f"S3-Upload fehlgeschlagen: {err_code}") from e

    @staticmethod
    def download_stream(key: str, bucket: str | None = None):
        """Laedt ein Objekt als Stream (boto3 StreamBody, lazy read).

        Optionaler `bucket` nutzt den Bucket, der beim Upload im Backup-Record
        gespeichert wurde (falls die Config zwischenzeitlich geaendert wurde).
        """
        cfg = S3Service._get_config()
        client = S3Service._get_client()
        target_bucket = bucket or cfg["bucket"]
        try:
            response = client.get_object(Bucket=target_bucket, Key=key)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "UnknownError")
            logger.warning("S3 download fehlgeschlagen (Key=%s): %s", key, err_code)
            raise S3OperationError(f"S3-Download fehlgeschlagen: {err_code}") from e
        return response["Body"]

    @staticmethod
    def list_objects(prefix: str, bucket: str | None = None) -> list[dict]:
        """Listet Objekte mit Praefix (key, size, last_modified).

        Optionaler `bucket` nutzt den Bucket, der beim Upload im Backup-Record
        gespeichert wurde (falls die Config zwischenzeitlich geaendert wurde).
        """
        cfg = S3Service._get_config()
        client = S3Service._get_client()
        target_bucket = bucket or cfg["bucket"]
        try:
            response = client.list_objects_v2(Bucket=target_bucket, Prefix=prefix)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "UnknownError")
            logger.warning("S3 list fehlgeschlagen (Prefix=%s): %s", prefix, err_code)
            raise S3OperationError(f"S3-List fehlgeschlagen: {err_code}") from e
        return [
            {
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"],
            }
            for obj in response.get("Contents", [])
        ]

    @staticmethod
    def delete_object(key: str, bucket: str | None = None) -> None:
        """Loescht ein Objekt (idempotent bei nicht-existentem Key).

        Optionaler `bucket` nutzt den Bucket, der beim Upload im Backup-Record
        gespeichert wurde. Ohne Angabe wird der aktuell konfigurierte Bucket
        verwendet (Fallback fuer alte Records mit s3_bucket=None).
        """
        cfg = S3Service._get_config()
        client = S3Service._get_client()
        target_bucket = bucket or cfg["bucket"]
        try:
            client.delete_object(Bucket=target_bucket, Key=key)
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "UnknownError")
            logger.warning("S3 delete fehlgeschlagen (Key=%s): %s", key, err_code)
            raise S3OperationError(f"S3-Delete fehlgeschlagen: {err_code}") from e

    @staticmethod
    def test_connection() -> dict:
        """Testet die S3-Verbindung (head_bucket, read-only)."""
        cfg = S3Service._get_config()
        client = S3Service._get_client()
        try:
            client.head_bucket(Bucket=cfg["bucket"])
        except ClientError as e:
            err_code = e.response.get("Error", {}).get("Code", "UnknownError")
            logger.warning("S3 Verbindungstest fehlgeschlagen: %s", err_code)
            raise S3OperationError(f"S3-Verbindungstest fehlgeschlagen: {err_code}") from e
        return {"ok": True, "bucket": cfg["bucket"]}
