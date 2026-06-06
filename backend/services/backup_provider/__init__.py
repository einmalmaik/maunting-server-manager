"""Backup-Provider Factory.

Liest ``MSM_BACKUP_PROVIDER`` aus der zentralen Config und instanziiert
den passenden Adapter. Aktuell: local (Schritt 1), s3 (Schritt 2),
sftp (Schritt 3), dropbox (Schritt 4). GCS und Azure kommen in eigenen
Commits (Plan-Reihenfolge).
"""
import logging

from config import settings

from .base import BackupLocation, BackupMetadata, BackupProvider, ProviderError
from .dropbox import DropboxProvider
from .local import LocalProvider
from .s3 import S3Provider
from .sftp import SFTPProvider

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

    if name == "sftp":
        # Import hier (nicht oben) → paramiko wird nur geladen wenn sftp genutzt wird
        from .sftp import SFTPProvider
        if not settings.backup_sftp_host:
            raise ProviderError("SFTP-Host nicht konfiguriert")
        if not settings.backup_sftp_user or not settings.backup_sftp_password:
            raise ProviderError("SFTP-Credentials fehlen")
        return SFTPProvider(
            host=settings.backup_sftp_host,
            port=settings.backup_sftp_port or 22,
            user=settings.backup_sftp_user,
            password=settings.backup_sftp_password,
            base_path=settings.backup_sftp_path or "/msm-backups",
        )

    if name == "dropbox":
        # Import hier (nicht oben) → dropbox SDK wird nur geladen wenn dropbox genutzt wird
        from .dropbox import DropboxProvider
        if not settings.backup_dropbox_app_key:
            raise ProviderError("Dropbox App-Key nicht konfiguriert")
        if not settings.backup_dropbox_app_secret:
            raise ProviderError("Dropbox App-Secret nicht konfiguriert")
        if not settings.backup_dropbox_refresh_token:
            raise ProviderError("Dropbox Refresh-Token nicht konfiguriert")
        return DropboxProvider(
            app_key=settings.backup_dropbox_app_key,
            app_secret=settings.backup_dropbox_app_secret,
            refresh_token=settings.backup_dropbox_refresh_token,
            base_path=settings.backup_dropbox_path or "/msm-backups",
        )

    # Weitere Provider kommen in eigenen Commits (siehe Plan):
    #   gcs, azure
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
    "SFTPProvider",
    "DropboxProvider",
    "get_provider",
]
