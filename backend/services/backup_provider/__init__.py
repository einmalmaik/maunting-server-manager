"""Backup-Provider Factory.

Liest ``MSM_BACKUP_PROVIDER`` aus der zentralen Config und instanziiert
den passenden Adapter. Aktuell: local (Schritt 1), s3 (Schritt 2),
sftp (Schritt 3), dropbox (Schritt 4), gcs (Schritt 5), azure (Schritt 6).
"""
import logging

from config import settings

from .base import BackupLocation, BackupMetadata, BackupProvider, ProviderError
from .azure import AzureProvider
from .dropbox import DropboxProvider
from .gcs import GCSProvider
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

    if name == "gcs":
        # Import hier (nicht oben) → google-cloud-storage wird nur geladen
        # wenn gcs genutzt wird (vermeidet 50+ MB grpcio-Import-Overhead
        # fuer User, die GCS nicht nutzen).
        from .gcs import GCSProvider
        if not settings.backup_gcs_bucket:
            raise ProviderError("GCS-Bucket nicht konfiguriert")
        if not settings.backup_gcs_sa_file:
            raise ProviderError("GCS Service-Account-Datei nicht konfiguriert")
        return GCSProvider(
            bucket=settings.backup_gcs_bucket,
            sa_file_path=settings.backup_gcs_sa_file,
            path_prefix=settings.backup_gcs_path_prefix or "msm-backups",
        )

    if name == "azure":
        # Import hier (nicht oben) → azure-storage-blob wird nur geladen
        # wenn azure genutzt wird (vermeidet azure-core + isodate-Import-
        # Overhead fuer User, die Azure nicht nutzen).
        from .azure import AzureProvider
        if not settings.backup_azure_connection_string:
            raise ProviderError("Azure Connection-String nicht konfiguriert")
        return AzureProvider(
            connection_string=settings.backup_azure_connection_string,
            container=settings.backup_azure_container or "msm-backups",
            path_prefix=settings.backup_azure_path_prefix or "",
            account_name=settings.backup_azure_account or "",
        )

    # Alle 6 Provider abgedeckt. Ein hier ankommender Name ist ein Bug.
    raise ProviderError(
        f"Unbekannter Backup-Provider: {name!r}. "
        "Erwartet: local | s3 | sftp | dropbox | gcs | azure."
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
    "GCSProvider",
    "AzureProvider",
    "get_provider",
    "probe_cloud_backups",
]


def probe_cloud_backups() -> list[BackupMetadata]:
    """Listet Metadaten aus dem aktuell konfigurierten Cloud-Provider.

    Wird vom install.sh nach dem .env-Write aufgerufen, um zu erkennen ob
    im Cloud-Storage bereits Backups existieren (Fresh-Install-Restore-Flow,
    siehe Plan 3.7). Liefert eine leere Liste bei lokalem Provider oder
    wenn der Provider-Call fehlschlaegt. Fehler werden geloggt aber NICHT
    propagiert - der Probe soll den Installer nie abbrechen.

    Security: Kein Download, nur list_metadata(). Provider-Fehlertexte
    werden sanitisiert (kein Pfad-Leak, kein Stack-Trace, keine Tokens).
    """
    if (settings.backup_provider or "local").lower() == "local":
        return []
    try:
        provider = get_provider()
        return provider.list_metadata()
    except ProviderError as e:
        logger.warning("Cloud-Probe: Provider nicht verfuegbar (%s)", e)
        return []
    except Exception as e:  # noqa: BLE001 — bewusst breit, da Probe-Phase
        # Sanitisiert: nur Exception-Typ-Name, keine Details (kein Pfad,
        # kein Token, keine Stack-Trace). User sieht in show_current_config
        # oder im Installer-Log 'Cloud-Probe fehlgeschlagen, fahre fort'.
        logger.warning("Cloud-Probe: unerwarteter Fehler (%s)", type(e).__name__)
        return []
