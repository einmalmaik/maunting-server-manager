"""Tests fuer die Cloud-Backup-Probe-Funktion.

probe_cloud_backups() wird vom install.sh nach .env-Write aufgerufen, um
zu erkennen ob im Cloud-Storage bereits Backups existieren (Fresh-Install-
Restore-Flow, siehe Plan 3.7).

Wichtige Invarianten (jeder Test faengt eine Regression ab):
- Lokaler Provider: leere Liste, kein Provider-Aufruf.
- Cloud-Provider mit Treffern: Liste wird durchgereicht.
- Cloud-Provider mit ProviderError: leere Liste, KEIN Raise (Installer
  soll weiterlaufen).
- Cloud-Provider mit unerwartetem Fehler: leere Liste, KEIN Raise
  (sanitized auf type(e).__name__).
- KEIN Download wird je aufgerufen - nur list_metadata().
"""
from unittest.mock import MagicMock, patch

import pytest
from services.backup_provider import (
    BackupMetadata,
    probe_cloud_backups,
)


# ── Fixtures ─────────────────────────────────────────────────────────


class _FakeSettings:
    """Minimal-Settings-Stub, nur backup_provider wird gelesen."""

    def __init__(self, provider: str):
        self.backup_provider = provider


@pytest.fixture
def restore_settings():
    """Stellt settings.backup_provider pro Test wieder her."""
    from config import settings

    original = settings.backup_provider
    yield
    settings.backup_provider = original


# ── Test-Cases ───────────────────────────────────────────────────────


def test_local_provider_returns_empty_without_call(restore_settings):
    """Lokaler Provider darf NIE einen Remote-Call machen."""
    from config import settings

    settings.backup_provider = "local"

    with patch("services.backup_provider.get_provider") as mock_get:
        result = probe_cloud_backups()

    assert result == []
    mock_get.assert_not_called()


def test_cloud_provider_passes_through_metadata(restore_settings):
    """Cloud-Provider mit Treffern -> Liste wird 1:1 zurueckgegeben."""
    from config import settings

    settings.backup_provider = "s3"

    fake_metadata = [
        BackupMetadata(
            backup_version=1,
            server_id=123,
            server_name="Mein Server",
            game_type="minecraft",
            created_at="2026-06-06T10:00:00Z",
            panel_version="v1.6.0",
            cpu_limit_percent=200,
            ram_limit_mb=4096,
            disk_limit_gb=50,
            public_bind_ip=None,
            ports=[],
            remote_key="123/foo.tar.gz.enc",
            size_mb=512,
        ),
        BackupMetadata(
            backup_version=1,
            server_id=456,
            server_name="Anderer Server",
            game_type="valheim",
            created_at="2026-06-06T11:00:00Z",
            panel_version="v1.6.0",
            cpu_limit_percent=150,
            ram_limit_mb=8192,
            disk_limit_gb=100,
            public_bind_ip=None,
            ports=[],
            remote_key="456/bar.tar.gz.enc",
            size_mb=1024,
        ),
    ]
    fake_provider = MagicMock()
    fake_provider.list_metadata.return_value = fake_metadata

    with patch("services.backup_provider.get_provider", return_value=fake_provider):
        result = probe_cloud_backups()

    assert result == fake_metadata
    fake_provider.list_metadata.assert_called_once()
    # KEIN Download wurde versucht
    fake_provider.download.assert_not_called()
    fake_provider.upload.assert_not_called()


def test_provider_error_returns_empty_no_raise(restore_settings, caplog):
    """ProviderError (z.B. Credentials falsch) -> leere Liste, kein Raise.

    Der Installer darf nicht abbrechen, nur weil die Cloud-Credentials
    nicht stimmen. Der Probe-Output erscheint im Installer-Log als Warnung.
    """
    from services.backup_provider import ProviderError
    from config import settings

    settings.backup_provider = "s3"

    with patch(
        "services.backup_provider.get_provider",
        side_effect=ProviderError("S3-Bucket nicht konfiguriert"),
    ):
        with caplog.at_level("WARNING"):
            result = probe_cloud_backups()

    assert result == []
    # Log-Eintrag enthaelt den ProviderError-Text (fuer Installer-Log)
    assert any("S3-Bucket nicht konfiguriert" in r.message for r in caplog.records)


def test_unexpected_exception_returns_empty_no_leak(restore_settings, caplog):
    """Unerwarteter Fehler (z.B. Netzwerk) -> leere Liste, sanitized Log.

    Sanitization: nur type(e).__name__ im Log, NICHT die Message.
    Verhindert Path-Leak, Token-Leak, Stack-Trace im Installer-Output.
    """
    from config import settings

    settings.backup_provider = "azure"

    class WeirdError(Exception):
        def __init__(self):
            super().__init__("/etc/passwd leaked, secret=AKIA123")

    with patch(
        "services.backup_provider.get_provider",
        side_effect=WeirdError(),
    ):
        with caplog.at_level("WARNING"):
            result = probe_cloud_backups()

    assert result == []
    log_text = "\n".join(r.message for r in caplog.records)
    # Sanitization: type name appears, message does NOT
    assert "WeirdError" in log_text
    assert "/etc/passwd" not in log_text
    assert "AKIA123" not in log_text


def test_empty_cloud_storage_returns_empty(restore_settings):
    """Cloud-Provider mit leerem Storage -> leere Liste (kein Banner)."""
    from config import settings

    settings.backup_provider = "gcs"

    fake_provider = MagicMock()
    fake_provider.list_metadata.return_value = []

    with patch("services.backup_provider.get_provider", return_value=fake_provider):
        result = probe_cloud_backups()

    assert result == []


@pytest.mark.parametrize("provider", ["s3", "sftp", "dropbox", "gcs", "azure"])
def test_all_cloud_providers_call_get_provider(restore_settings, provider):
    """Alle 5 Cloud-Provider rufen get_provider() auf, keiner tut 'local'."""
    from config import settings

    settings.backup_provider = provider

    fake_provider = MagicMock()
    fake_provider.list_metadata.return_value = []

    with patch("services.backup_provider.get_provider", return_value=fake_provider) as mock_get:
        result = probe_cloud_backups()

    assert result == []
    mock_get.assert_called_once()
    fake_provider.list_metadata.assert_called_once()
