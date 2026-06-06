"""Tests fuer den GCS-Provider.

Deckt:
- upload → download Roundtrip (Datei byte-genau erhalten)
- delete entfernt Daten- und Meta-Datei
- delete ist idempotent (fehlende Dateien = OK)
- list_metadata parst *.meta.json aus path_prefix
- list_metadata ueberspringt kaputte Meta-Files ohne Raise
- list_metadata ignoriert Folder-Marker
- test_connection: True bei gueltigen Credentials + existentem Bucket
- test_connection: False bei Auth-Fehler / nicht-existentem Bucket
- Konstruktor: leere Felder → ProviderError
- Konstruktor: SA-Datei nicht lesbar / JSON kaputt → ProviderError
- Progress-Callback wird aufgerufen
- Path-Traversal-Schutz: relative "..", absolute Pfade werden abgelehnt
- Factory: gcs-Branch instanziiert korrekt
- Factory: fehlende Credentials → ProviderError

Mocking: ``google.cloud.storage.Client.from_service_account_json`` wird
per ``monkeypatch`` ersetzt, sodass der GCS-Client eine
``FakeGcsClient``-Instanz zurueckliefert. Diese emuliert
``bucket()``, ``blob()``, ``upload_from_filename``,
``download_to_filename``, ``delete``, ``exists``, ``list_blobs`` in
einem In-Memory-Dict. Es ist KEIN echter GCS-Account noetig; die
Tests laufen offline.
"""
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as gcs_exceptions

from services.backup_provider import (
    BackupMetadata,
    GCSProvider,
    ProviderError,
)


# ── Mock-Helpers ──────────────────────────────────────────────────────────


def _make_fake_sa_file(tmp_path: Path) -> Path:
    """Erzeugt eine minimale, aber gueltige Service-Account-JSON.

    google-cloud-storage validiert die JSON-Struktur NICHT direkt beim
    ``from_service_account_json``-Aufruf (es liest sie nur als
    Credentials-Quelle). Daher reicht ein Minimal-Skeleton mit den
    Feldern, die das SDK spaeter konsumiert.
    """
    sa_path = tmp_path / "fake-sa.json"
    sa_path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "msm-test-project",
                "private_key_id": "fake-key-id",
                "private_key": "-----BEGIN PRIVATE KEY-----\n"
                "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
                "-----END PRIVATE KEY-----\n",
                "client_email": "msm-test@msm-test-project.iam.gserviceaccount.com",
                "client_id": "1234567890",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    return sa_path


# ── Fake-GCS (in-memory) ─────────────────────────────────────────────────


class FakeBlob:
    """In-Memory-Blob, der die google-cloud-storage Blob-API emuliert."""

    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        self.chunk_size: int | None = None
        self._delete_raises: BaseException | None = None
        self._download_raises: BaseException | None = None
        self._upload_raises: BaseException | None = None

    def upload_from_filename(self, path: str, **kwargs) -> None:
        if self._upload_raises:
            raise self._upload_raises
        with open(path, "rb") as f:
            self._store[self.name] = f.read()

    def download_to_filename(self, path: str, **kwargs) -> None:
        if self._download_raises:
            raise self._download_raises
        if self.name not in self._store:
            raise gcs_exceptions.NotFound(f"Blob {self.name} not found")
        with open(path, "wb") as f:
            f.write(self._store[self.name])

    def delete(self, **kwargs) -> None:
        if self._delete_raises:
            raise self._delete_raises
        # 404 = ok (idempotent), wie echtes GCS
        self._store.pop(self.name, None)

    def download_as_text(self, **kwargs) -> str:
        if self.name not in self._store:
            raise gcs_exceptions.NotFound(f"Blob {self.name} not found")
        return self._store[self.name].decode("utf-8")


class FakeBucket:
    """In-Memory-Bucket, der die google-cloud-storage Bucket-API emuliert."""

    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        self._exists_result: bool = True
        self._exists_raises: BaseException | None = None

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self._store)

    def exists(self, **kwargs) -> bool:
        if self._exists_raises:
            raise self._exists_raises
        return self._exists_result


class FakeGcsClient:
    """In-Memory-Fake fuer ``google.cloud.storage.Client``."""

    def __init__(self, *args, **kwargs) -> None:
        self._buckets: dict[str, FakeBucket] = {}
        self._list_blobs_result: list[FakeBlob] = []
        self._list_blobs_raises: BaseException | None = None
        # Track calls for asserts
        self.list_blobs_calls: list[tuple[str, dict]] = []

    def bucket(self, name: str) -> FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeBucket(name, self._buckets_data(name))
        return self._buckets[name]

    def _buckets_data(self, name: str) -> dict[str, bytes]:
        # Gemeinsamer Store pro Bucket (mehrere Bucket-Handles sehen
        # die gleichen Daten — wie echtes GCS)
        return self._shared_store.setdefault(name, {})

    # We use a class-level dict so multiple clients share state per bucket
    _shared_store: dict[str, dict[str, bytes]] = {}

    def list_blobs(self, bucket_name: str, **kwargs) -> list[FakeBlob]:
        self.list_blobs_calls.append((bucket_name, kwargs))
        if self._list_blobs_raises:
            raise self._list_blobs_raises
        # Liefere Sicht auf den Store, gefiltert auf Prefix
        prefix = kwargs.get("prefix", "")
        store = self._buckets_data(bucket_name)
        return [FakeBlob(name, store) for name in store if name.startswith(prefix)]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_gcs_store() -> dict[str, dict[str, bytes]]:
    # Reset shared store vor jedem Test (FakeGcsClient._shared_store ist class-level)
    FakeGcsClient._shared_store = {}
    return FakeGcsClient._shared_store


@pytest.fixture
def fake_gcs_client(fake_gcs_store) -> FakeGcsClient:
    return FakeGcsClient()


@pytest.fixture
def gcs_client(fake_gcs_client: FakeGcsClient, monkeypatch) -> FakeGcsClient:
    """Patcht ``google.cloud.storage.Client.from_service_account_json`` so dass
    jeder Aufruf eine Fake-Instanz liefert. Das Original wird komplett
    umgangen — keine echte GCS-Verbindung, keine JSON-Validierung gegen
    den Google-Endpoint."""
    monkeypatch.setattr(
        "services.backup_provider.gcs.gcs.Client.from_service_account_json",
        classmethod(lambda cls, *args, **kwargs: fake_gcs_client),
    )
    return fake_gcs_client


@pytest.fixture
def sa_file(tmp_path: Path) -> Path:
    return _make_fake_sa_file(tmp_path)


@pytest.fixture
def provider(gcs_client, sa_file) -> GCSProvider:
    return GCSProvider(
        bucket="msm-test-bucket",
        sa_file_path=str(sa_file),
        path_prefix="msm-backups",
    )


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_meta(server_id: int = 42, name: str | None = None) -> BackupMetadata:
    return BackupMetadata(
        backup_version=1,
        server_id=server_id,
        server_name=f"Test Server {server_id}",
        game_type="minecraft",
        created_at="2026-06-06T15:30:00Z",
        panel_version="v1.6.0",
        cpu_limit_percent=200,
        ram_limit_mb=4096,
        disk_limit_gb=50,
        public_bind_ip=None,
        ports=[{"role": "game", "port": 25565, "protocol": "tcp"}],
        name=name,
        size_mb=10,
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestContract:
    def test_implements_backup_provider_interface(self, provider: GCSProvider):
        from services.backup_provider.base import BackupProvider
        assert isinstance(provider, BackupProvider)
        assert provider.name == "gcs"

    def test_constructor_rejects_empty_bucket(self, gcs_client, sa_file):
        with pytest.raises(ProviderError):
            GCSProvider(bucket="", sa_file_path=str(sa_file))

    def test_constructor_rejects_empty_sa_file(self, gcs_client):
        with pytest.raises(ProviderError):
            GCSProvider(bucket="b", sa_file_path="")

    def test_constructor_rejects_empty_path_prefix(self, gcs_client, sa_file):
        with pytest.raises(ProviderError):
            GCSProvider(bucket="b", sa_file_path=str(sa_file), path_prefix="")

    def test_constructor_rejects_path_prefix_only_slashes(
        self, gcs_client, sa_file
    ):
        with pytest.raises(ProviderError):
            GCSProvider(
                bucket="b", sa_file_path=str(sa_file), path_prefix="///"
            )

    def test_constructor_normalizes_path_prefix(self, gcs_client, sa_file):
        p = GCSProvider(
            bucket="b",
            sa_file_path=str(sa_file),
            path_prefix="/msm-backups/",
        )
        assert p.path_prefix == "msm-backups"

    def test_constructor_rejects_missing_sa_file(
        self, tmp_path: Path
    ):
        # File existiert nicht — OSError beim Oeffnen → ProviderError
        # (kein gcs_client-Mock hier: wir testen die ECHTE
        # from_service_account_json-Initialisierung)
        with pytest.raises(ProviderError):
            GCSProvider(
                bucket="b",
                sa_file_path=str(tmp_path / "does-not-exist.json"),
            )

    def test_constructor_rejects_invalid_sa_file_json(
        self, tmp_path: Path
    ):
        # Datei existiert, aber Inhalt ist kein JSON → ValueError → ProviderError
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(ProviderError):
            GCSProvider(bucket="b", sa_file_path=str(bad))


class TestConnection:
    def test_connection_succeeds_when_bucket_exists(self, provider: GCSProvider):
        # Default: FakeBucket._exists_result = True
        assert provider.test_connection() is True

    def test_connection_fails_when_bucket_missing(self, provider: GCSProvider):
        # Existiert nicht → 404
        bucket = provider._bucket
        bucket._exists_result = False
        assert provider.test_connection() is False

    def test_connection_fails_on_auth_error(self, provider: GCSProvider):
        provider._bucket._exists_raises = gcs_exceptions.Unauthorized(
            "invalid credentials"
        )
        assert provider.test_connection() is False

    def test_connection_fails_on_forbidden(self, provider: GCSProvider):
        provider._bucket._exists_raises = gcs_exceptions.Forbidden(
            "no permission"
        )
        assert provider.test_connection() is False

    def test_connection_fails_on_service_unavailable(
        self, provider: GCSProvider
    ):
        provider._bucket._exists_raises = gcs_exceptions.ServiceUnavailable(
            "GCS down"
        )
        assert provider.test_connection() is False


class TestUploadDownload:
    def test_upload_stores_file(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"backup payload")
        loc = provider.upload(src, "42/server.tar.gz.enc")
        assert loc.remote_key == "42/server.tar.gz.enc"
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        assert "msm-backups/42/server.tar.gz.enc" in store
        assert store["msm-backups/42/server.tar.gz.enc"] == b"backup payload"

    def test_upload_creates_parent_dirs_implicitly(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        # GCS hat keine echten Ordner — Keys sind flach mit "/"-Separator.
        # Listing und Restore arbeiten direkt mit dem vollen Key.
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/deep.tar.gz.enc")
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        assert "msm-backups/1/2/3/deep.tar.gz.enc" in store

    def test_download_writes_file_byte_exact(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        payload = b"x" * 12345
        src.write_bytes(payload)
        provider.upload(src, "42/server.tar.gz.enc")
        dst = tmp_path / "downloaded.bin"
        provider.download("42/server.tar.gz.enc", dst)
        assert dst.read_bytes() == payload

    def test_download_creates_intermediate_dirs(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/server.enc")
        dst = tmp_path / "out" / "nested" / "downloaded.bin"
        provider.download("1/2/3/server.enc", dst)
        assert dst.is_file()

    def test_download_missing_key_raises(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")

    def test_upload_missing_source_raises(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")

    def test_upload_error_propagates_as_provider_error(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        # Injiziere einen Upload-Fehler in den FakeBlob
        bucket = provider._bucket
        # FakeBlob wird bei jedem .blob(name) neu erzeugt — also direkt am
        # naechsten blob() einen Fehler setzen ist umstaendlich. Stattdessen
        # monkeypatchen wir den Store, sodass open() fehlschlaegt.
        from unittest.mock import patch
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with patch("builtins.open", side_effect=OSError("disk full")):
            with pytest.raises(ProviderError):
                provider.upload(src, "42/x.enc")

    def test_progress_callback_called_for_upload(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x" * 5000)
        calls: list[int] = []

        def cb(transferred: int) -> None:
            calls.append(transferred)

        provider.upload(src, "42/x.enc", progress_cb=cb)
        # GCS-SDK hat keinen in-call Progress — einmaliger Final-Callback
        assert len(calls) == 1
        assert calls[0] == 5000

    def test_progress_callback_called_for_download(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x" * 5000)
        provider.upload(src, "42/x.enc")
        calls: list[int] = []

        def cb(transferred: int) -> None:
            calls.append(transferred)

        dst = tmp_path / "out.bin"
        provider.download("42/x.enc", dst, progress_cb=cb)
        assert len(calls) == 1
        assert calls[0] == 5000


class TestDelete:
    def test_delete_removes_data_and_meta(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/server.tar.gz.enc")
        # Meta-File manuell anlegen
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/server.tar.gz.enc.meta.json"] = b"{}"
        assert "msm-backups/42/server.tar.gz.enc" in store
        assert "msm-backups/42/server.tar.gz.enc.meta.json" in store

        provider.delete("42/server.tar.gz.enc")
        assert "msm-backups/42/server.tar.gz.enc" not in store
        assert "msm-backups/42/server.tar.gz.enc.meta.json" not in store

    def test_delete_missing_data_still_removes_meta(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/server.tar.gz.enc.meta.json"] = b"{}"
        # Daten-File fehlt — delete soll trotzdem laufen (idempotent)
        provider.delete("42/server.tar.gz.enc")
        assert "msm-backups/42/server.tar.gz.enc.meta.json" not in store

    def test_delete_missing_both_is_noop(self, provider: GCSProvider):
        # Kein vorheriger Upload — delete soll nicht raisen
        provider.delete("42/never-existed.enc")

    def test_delete_malformed_key_is_noop(self, provider: GCSProvider):
        # Key mit ".." → wuerde eigentlich raisen, aber delete ist idempotent
        provider.delete("../../../etc/passwd")

    def test_delete_non_not_found_error_raises(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        # Authorization-Fehler beim Loeschen → ProviderError
        # Wir patchen das naechste erzeugte FakeBlob via list_blobs-Spy:
        # einfacher ist es, den Bucket.blob() monkeyzupatchen, sodass
        # er einen Blob liefert, der beim delete() raiset.
        class RaisingBlob:
            name = "msm-backups/42/x.enc"

            def delete(self, **kwargs):
                raise gcs_exceptions.Forbidden("no delete permission")

        original_blob = provider._bucket.blob
        provider._bucket.blob = lambda name: RaisingBlob()
        try:
            with pytest.raises(ProviderError):
                provider.delete("42/x.enc")
        finally:
            provider._bucket.blob = original_blob


class TestListMetadata:
    def test_list_metadata_returns_parsed(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        meta1 = _make_meta(42, "Vor Update")
        meta2 = _make_meta(43)
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/server.tar.gz.enc.meta.json"] = meta1.to_json().encode(
            "utf-8"
        )
        store["msm-backups/43/other.tar.gz.enc.meta.json"] = meta2.to_json().encode(
            "utf-8"
        )
        # Orphan-Daten-File (sollte ignoriert werden)
        store["msm-backups/44/orphan.tar.gz.enc"] = b"x"

        results = provider.list_metadata()
        assert len(results) == 2
        ids = {m.server_id for m in results}
        assert ids == {42, 43}
        m42 = next(m for m in results if m.server_id == 42)
        assert m42.name == "Vor Update"

    def test_list_metadata_skips_broken_files(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        meta = _make_meta(42)
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/good.enc.meta.json"] = meta.to_json().encode("utf-8")
        store["msm-backups/43/bad.enc.meta.json"] = b"{ not valid json"

        results = provider.list_metadata()
        assert len(results) == 1
        assert results[0].server_id == 42

    def test_list_metadata_empty_bucket(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        # Nichts im Bucket → leere Liste
        assert provider.list_metadata() == []

    def test_list_metadata_uses_correct_prefix(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        # Sicherstellen, dass list_blobs mit dem richtigen Prefix aufgerufen wird
        provider.list_metadata()
        assert len(fake_gcs_client.list_blobs_calls) == 1
        bucket_name, kwargs = fake_gcs_client.list_blobs_calls[0]
        assert bucket_name == "msm-test-bucket"
        assert kwargs.get("prefix") == "msm-backups/"

    def test_list_metadata_fails_on_gcs_error(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        fake_gcs_client._list_blobs_raises = gcs_exceptions.ServiceUnavailable(
            "GCS down"
        )
        with pytest.raises(ProviderError):
            provider.list_metadata()

    def test_list_metadata_ignores_folder_markers(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
        # GCS kann Folder-Marker-Objekte (name endet mit /, size 0) liefern
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/"] = b""  # Folder-Marker, sollte uebersprungen werden
        assert provider.list_metadata() == []


class TestSecurityPathTraversal:
    def test_upload_absolute_path_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "/etc/passwd")

    def test_upload_dotdot_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "../../../etc/passwd")

    def test_upload_mixed_traversal_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "42/../../../etc/passwd")

    def test_download_absolute_path_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("/etc/passwd", tmp_path / "out.bin")

    def test_download_dotdot_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("../../../etc/passwd", tmp_path / "out.bin")

    def test_empty_key_rejected(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "")


class TestFactory:
    def test_factory_returns_gcs_provider(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "gcs")
        monkeypatch.setattr(settings, "backup_gcs_bucket", "test-bucket")
        monkeypatch.setattr(settings, "backup_gcs_sa_file", "/tmp/sa.json")
        monkeypatch.setattr(settings, "backup_gcs_path_prefix", "msm-backups")
        # SA-Init monkeypatchen, damit der Factory-Call nicht scheitert
        monkeypatch.setattr(
            "services.backup_provider.gcs.gcs.Client.from_service_account_json",
            classmethod(lambda cls, *args, **kwargs: MagicMock()),
        )
        p = get_provider()
        assert p.name == "gcs"
        assert p.bucket_name == "test-bucket"
        assert p.path_prefix == "msm-backups"

    def test_factory_rejects_gcs_without_bucket(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "gcs")
        monkeypatch.setattr(settings, "backup_gcs_bucket", "")
        monkeypatch.setattr(settings, "backup_gcs_sa_file", "/tmp/sa.json")
        with pytest.raises(ProviderError):
            get_provider()

    def test_factory_rejects_gcs_without_sa_file(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "gcs")
        monkeypatch.setattr(settings, "backup_gcs_bucket", "b")
        monkeypatch.setattr(settings, "backup_gcs_sa_file", "")
        with pytest.raises(ProviderError):
            get_provider()
