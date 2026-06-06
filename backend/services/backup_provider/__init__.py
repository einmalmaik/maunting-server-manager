"""Backup-Provider Factory.

Liest ``MSM_BACKUP_PROVIDER`` aus der zentralen Config und instanziiert
den passenden Adapter. In Schritt 1 nur der lokale Provider; S3, SFTP,
Dropbox, GCS und Azure kommen in eigenen Commits (Plan-Reihenfolge).
"""
import logging

from config import settings

from .base import BackupLocation, BackupMetadata, BackupProvider, ProviderError
from .local import LocalProvider

logger = logging.getLogger(__name__)


def get_provider(provider_name: str | None = None) -> BackupProvider:
    """Gibt den konfigurierten Backup-Provider zurueck.

    Args:
        provider_name: Optional Override (fuer Tests + Cross-Cloud-Migration).
                        Default liest ``settings.backup_provider``.

    Raises:
        ProviderError: bei unbekanntem Provider-Typ.
    """
    name = (provider_name or settings.backup_provider or "local").lower()

    if name == "local":
        # Default-Root ist /opt/msm/backups; in Dev-Tests ueberschreibbar.
        root = settings.backup_local_dir or "/opt/msm/backups"
        return LocalProvider(root_dir=root)

    # Weitere Provider kommen in eigenen Commits (siehe Plan):
    #   s3, sftp, dropbox, gcs, azure
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
    "get_provider",
]
