"""Backup-Provider Factory.

Liest ``MSM_BACKUP_PROVIDER`` aus der zentralen Config und instanziiert
den passenden Adapter. Aktuell: local (Schritt 1) und s3 (Schritt 2).
SFTP, Dropbox, GCS und Azure kommen in eigenen Commits (Plan-Reihenfolge).
"""
import logging

from config import settings

from .base import BackupLocation, BackupMetadata, BackupProvider, ProviderError
from .local import LocalProvider
from .s3 import S3Provider

logger = logging.getLogger(__name__)


def get_provider(provider_name: str | None = None) -> BackupProvider:
    """Gibt den konfigurierten Backup-Provider zurueck.

    Args:
        provider_name: Optional Override (fuer Tests + Cross-Cloud-Migration).
                        Default liest ``settings.backup_provider``.

    Raises:
        ProviderError: bei unbekanntem Provider-Typ oder fehlender Config.
    """
    name = (provider_name or settings.backup_provider or "local").lower()

    if name == "local":
        root = settings.backup_local_dir or "/opt/msm/backups"
        return LocalProvider(root_dir=root)

    if name == "s3":
        # Import hier (nicht oben) → boto3 wird nur geladen wenn s3 genutzt wird
        from .s3 import S3Provider
        if not settings.backup_s3_bucket:
            raise ProviderError("S3-Bucket nicht konfiguriert")
        if not settings.backup_s3_access_key or not settings.backup_s3_secret_key:
            raise ProviderError("S3-Credentials fehlen")
        return S3Provider(
            bucket=settings.backup_s3_bucket,
            region=settings.backup_s3_region or "us-east-1",
            endpoint=settings.backup_s3_endpoint or "",
            access_key=settings.backup_s3_access_key,
            secret_key=settings.backup_s3_secret_key,
        )

    # Weitere Provider kommen in eigenen Commits (siehe Plan):
    #   sftp, dropbox, gcs, azure
    raise ProviderError(
        f"Backup-Provider {name!r} ist in dieser Version noch nicht verfuegbar. "
        "Er wird in einem spaeteren Commit nachgereicht."
    )


__all__ = [
    "BackupLocation",
    "BackupMetadata",
    "BackupProvider",
    "ProviderError",
    "LocalProvider",
    "S3Provider",
    "get_provider",
]
