"""Tests fuer den Azure-Provider.

Deckt:
- upload → download Roundtrip (Datei byte-genau erhalten)
- delete entfernt Daten- und Meta-Blob
- delete ist idempotent (fehlende Blobs = OK)
- list_metadata parst *.meta.json aus path_prefix
- list_metadata ueberspringt kaputte Meta-Files ohne Raise
- list_metadata ignoriert Folder-Marker
- test_connection: True bei gueltigen Credentials + existentem Container
- test_connection: legt fehlenden Container an
- test_connection: False bei Auth-Fehler
- Konstruktor: leere Felder / ungueltige Conn-String → ProviderError
- Progress-Callback wird mehrfach aufgerufen bei Multi-Block-Upload
  (Azure progress_hook feuert pro Block, ~4 MB)
- Progress-Callback ist kumulativ (bytes_transferred vom Hook mappen)
- Progress-Callback wird mehrfach aufgerufen bei Multi-Chunk-Download
- Upload ohne progress_cb nutzt Single-Shot-Pfad (kein Hook)
- Download ohne progress_cb nutzt Single-Shot-Pfad (kein Wrapper)
- Path-Traversal-Schutz: relative "..", absolute Pfade werden abgelehnt
- Factory: azure-Branch instanziiert korrekt
- Factory: fehlende Credentials → ProviderError

Mocking: ``azure.storage.blob.BlobServiceClient.from_connection_string``
wird per ``monkeypatch`` ersetzt, sodass der Azure-Client eine
``FakeBlobServiceClient``-Instanz zurueckliefert. Diese emuliert
``get_container_client()``, ``upload_blob()``, ``download_blob()``,
``delete_blob()``, ``list_blobs()``, ``exists()``, ``create_container()``
in einem In-Memory-Dict. Es ist KEIN echter Azure-Account noetig; die
Tests laufen offline.
"""
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from azure.core import exceptions as azure_exceptions

from services.backup_provider import (
    AzureProvider,
    BackupMetadata,
    ProviderError,
)


# ── Fake-Stream-Downloader ────────────────────────────────────────────────


class FakeStorageStreamDownloader:
    """Emuliert ``StorageStreamDownloader`` mit readall() + readinto().

    readinto() ruft wrapper.write(data) fuer jeden Chunk auf — das
    ist exakt das Verhalten, das ``_ProgressFileWrapper`` trackt.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._chunk_size = 4 * 1024 * 1024  # ~4 MB wie Azure-Default

    def readall(self) -> bytes:
        d = self._data[self._pos:]
        self._pos = len(self._data)
        return d

    def readinto(self, stream) -> int:
        if self._pos >= len(self._data):
            return 0
        chunk = self._data[self._pos:self._pos + self._chunk_size]
        n = stream.write(chunk)
        self._pos += n
        return n


# ── Fake-Container-Client ────────────────────────────────────────────────


class FakeBlobClient:
    """Emuliert ``ContainerClient.get_blob_client().*``."""

    def __init__(self, container_name: str, store: dict[str, bytes]) -> None:
        self.container_name = container_name
        self._store = store
        # Error-Injection
        self._upload_raises: BaseException | None = None
        self._download_raises: BaseException | None = None
        self._delete_raises: BaseException | None = None
        # Track calls
        self.upload_calls: list[dict[str, Any]] = []
        self.download_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        # Verhalten: simulate 404 wenn Key nicht existiert
        self.raise_not_found_on_missing: bool = True

    def upload_blob(self, data, **kwargs) -> dict:
        self.upload_calls.append({
            "kwargs": dict(kwargs),
            "has_hook": "progress_hook" in kwargs,
        })

        if self._upload_raises:
            raise self._upload_raises

        # data ist ein file-like obj (vom Provider) — lies alle Bytes
        if hasattr(data, "read"):
            content = data.read()
        else:
            content = data

        # Azure's progress_hook simulieren: hook(done, total) wird
        # blockweise aufgerufen, ~4 MB pro Block.
        if "progress_hook" in kwargs:
            hook = kwargs["progress_hook"]
            block_size = 4 * 1024 * 1024  # 4 MB
            sent = 0
            total = len(content)
            while sent < total:
                end = min(sent + block_size, total)
                hook(end, total)
                sent = end

        # Tatsaechlich in den Store schreiben (das war der Bug)
        self._store[self._name] = bytes(content)
        return {"name": self._name}

    def download_blob(self, **kwargs) -> FakeStorageStreamDownloader:
        self.download_calls.append({"kwargs": dict(kwargs)})
        if self._download_raises:
            raise self._download_raises
        if self._name not in self._store:
            # Echtes Azure wirft ResourceNotFoundError bei 404
            raise azure_exceptions.ResourceNotFoundError(
                f"Blob {self._name} not found"
            )
        data = self._store[self._name]
        return FakeStorageStreamDownloader(data)

    def delete_blob(self, **kwargs) -> None:
        self.delete_calls.append({"kwargs": dict(kwargs)})
        if self._delete_raises:
            raise self._delete_raises
        # Wie oben: Name kommt via _name. Missing = noop (echtes Azure
        # wirft 404 fuer delete, wir tolerieren das in unserem delete()
        # ohnehin — siehe Provider-Code).
        self._store.pop(self._name, None)

    # Wird vom FakeContainerClient.get_blob_client gesetzt
    _name: str = ""


class FakeContainerClient:
    """Emuliert ``ContainerClient`` mit in-memory Blob-Store."""

    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.container_name = name
        self._store = store
        self._exists_result: bool = True
        self._exists_raises: BaseException | None = None
        self._create_container_raises: BaseException | None = None
        self.create_container_calls: int = 0
        # Fake-Blob-Client-Cache (pro Name)
        self._blob_clients: dict[str, FakeBlobClient] = {}
        # list_blobs Konfiguration
        self._list_blobs_raises: BaseException | None = None

    def get_blob_client(self, name: str) -> FakeBlobClient:
        if name not in self._blob_clients:
            c = FakeBlobClient(self.container_name, self._store)
            c._name = name
            self._blob_clients[name] = c
        return self._blob_clients[name]

    def exists(self, **kwargs) -> bool:
        if self._exists_raises:
            raise self._exists_raises
        return self._exists_result

    def create_container(self, **kwargs) -> dict:
        self.create_container_calls += 1
        if self._create_container_raises:
            raise self._create_container_raises
        self._exists_result = True
        return {"created": self.container_name}

    def list_blobs(self, name_starts_with=None, **kwargs) -> list:
        if self._list_blobs_raises:
            raise self._list_blobs_raises

        class BlobProps:
            def __init__(self, name: str) -> None:
                self.name = name

        prefix = name_starts_with or ""
        return [BlobProps(n) for n in self._store if n.startswith(prefix)]


# ── Fake-BlobServiceClient ────────────────────────────────────────────────


class FakeBlobServiceClient:
    """Emuliert ``BlobServiceClient`` mit Container-Management."""

    def __init__(self, *args, **kwargs) -> None:
        self._containers: dict[str, FakeContainerClient] = {}
        self._init_raises: BaseException | None = None

    @property
    def url(self) -> str:
        return "https://fake-account.blob.core.windows.net/"

    def get_container_client(self, name: str) -> FakeContainerClient:
        if name not in self._containers:
            self._containers[name] = FakeContainerClient(
                name, self._shared_store.setdefault(name, {})
            )
        return self._containers[name]

    # Class-level: gemeinsamer Store pro Container
    _shared_store: dict[str, dict[str, bytes]] = {}


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_azure_store() -> dict[str, dict[str, bytes]]:
    FakeBlobServiceClient._shared_store = {}
    return FakeBlobServiceClient._shared_store


@pytest.fixture
def fake_service(fake_azure_store) -> FakeBlobServiceClient:
    return FakeBlobServiceClient()


@pytest.fixture
def azure_client(fake_service: FakeBlobServiceClient, monkeypatch) -> FakeBlobServiceClient:
    """Patcht ``azure.storage.blob.BlobServiceClient.from_connection_string``."""
    monkeypatch.setattr(
        "services.backup_provider.azure.BlobServiceClient.from_connection_string",
        classmethod(lambda cls, *args, **kwargs: fake_service),
    )
    return fake_service


CONN_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=KEY"
    ";EndpointSuffix=core.windows.net"
)


@pytest.fixture
def provider(azure_client) -> AzureProvider:
    return AzureProvider(
        connection_string=CONN_STRING,
        container="msm-backups",
        account_name="test",
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
    def test_implements_backup_provider_interface(self, provider: AzureProvider):
        from services.backup_provider.base import BackupProvider
        assert isinstance(provider, BackupProvider)
        assert provider.name == "azure"

    def test_constructor_rejects_empty_connection_string(
        self, azure_client
    ):
        with pytest.raises(ProviderError):
            AzureProvider(connection_string="")

    def test_constructor_rejects_empty_container(
        self, azure_client
    ):
        with pytest.raises(ProviderError):
            AzureProvider(connection_string=CONN_STRING, container="")

    def test_constructor_rejects_connection_string_without_account_name(
        self, azure_client
    ):
        with pytest.raises(ProviderError):
            AzureProvider(
                connection_string="DefaultEndpointsProtocol=https;EndpointSuffix=core.windows.net"
            )

    def test_constructor_rejects_connection_string_without_account_key(
        self, azure_client
    ):
        with pytest.raises(ProviderError):
            AzureProvider(
                connection_string="DefaultEndpointsProtocol=https;AccountName=test;EndpointSuffix=core.windows.net"
            )


class TestConnection:
    def test_connection_succeeds_when_container_exists(self, provider: AzureProvider):
        # Default: container exists
        assert provider.test_connection() is True

    def test_connection_creates_missing_container(
        self, provider: AzureProvider
    ):
        # Container existiert nicht → wird angelegt
        provider._container._exists_result = False
        assert provider.test_connection() is True
        assert provider._container.create_container_calls == 1

    def test_connection_fails_on_auth_error(self, provider: AzureProvider):
        provider._container._exists_raises = (
            azure_exceptions.ClientAuthenticationError("bad creds")
        )
        assert provider.test_connection() is False

    def test_connection_fails_on_service_error(self, provider: AzureProvider):
        provider._container._exists_raises = azure_exceptions.ServiceRequestError(
            "network down"
        )
        assert provider.test_connection() is False


class TestUploadWithoutProgress:
    def test_upload_uses_single_shot_without_progress(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"backup payload")
        loc = provider.upload(src, "42/server.tar.gz.enc")
        assert loc.remote_key == "42/server.tar.gz.enc"
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        assert "42/server.tar.gz.enc" in store
        assert store["42/server.tar.gz.enc"] == b"backup payload"

    def test_upload_creates_container_if_missing(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        # Container fehlt → wird vor upload angelegt
        provider._container._exists_result = False
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/x.enc")
        assert provider._container.create_container_calls == 1

    def test_upload_missing_source_raises(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")

    def test_upload_error_propagates(
        self, provider, tmp_path: Path
    ):
        provider._container.get_blob_client("42/x.enc")._upload_raises = (
            azure_exceptions.ServiceRequestError("network")
        )
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "42/x.enc")


class TestUploadWithProgress:
    def test_small_file_single_block_progress(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        # 5 KB < 4 MB Block → ein einziger Hook-Call
        src = tmp_path / "small.bin"
        src.write_bytes(b"x" * 5000)
        calls: list[int] = []

        provider.upload(src, "42/small.enc", progress_cb=calls.append)

        store = FakeBlobServiceClient._shared_store["msm-backups"]
        assert store["42/small.enc"] == b"x" * 5000
        # Azure-Hook: 1 Call mit 5000 Bytes
        assert calls == [5000]

    def test_multi_block_progress_is_cumulative(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        # 10 MB → 3 Blöcke (4+4+2) bei Azure's 4-MB-Block-Size
        src = tmp_path / "big.bin"
        src.write_bytes(b"x" * (10 * 1024 * 1024))
        calls: list[int] = []

        provider.upload(src, "42/big.enc", progress_cb=calls.append)

        # 3 Hook-Calls (4 MB, 8 MB, 10 MB)
        assert calls == [
            4 * 1024 * 1024,
            8 * 1024 * 1024,
            10 * 1024 * 1024,
        ]
        # Store hat die volle Datei
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        assert len(store["42/big.enc"]) == 10 * 1024 * 1024


class TestDownloadWithoutProgress:
    def test_download_uses_single_shot_without_progress(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        payload = b"y" * 12345
        store["42/x.enc"] = payload

        dst = tmp_path / "out.bin"
        provider.download("42/x.enc", dst)
        assert dst.read_bytes() == payload

    def test_download_missing_key_raises(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")

    def test_download_creates_intermediate_dirs(
        self, provider, fake_service: FakeBlobServiceClient, tmp_path: Path
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["1/2/3/server.enc"] = b"x"
        dst = tmp_path / "out" / "nested" / "downloaded.bin"
        provider.download("1/2/3/server.enc", dst)
        assert dst.is_file()


class TestDownloadWithProgress:
    def test_stream_download_writes_file_byte_exact(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        # 10 MB File → 3 Chunks im FakeStreamDownloader (4+4+2 MB)
        payload = b"x" * (10 * 1024 * 1024)
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/big.enc"] = payload

        calls: list[int] = []
        dst = tmp_path / "out.bin"
        provider.download("42/big.enc", dst, progress_cb=calls.append)

        assert dst.read_bytes() == payload
        # 3 kumulative Progress-Calls
        assert calls == [
            4 * 1024 * 1024,
            8 * 1024 * 1024,
            10 * 1024 * 1024,
        ]

    def test_stream_download_single_chunk(
        self,
        provider: AzureProvider,
        fake_service: FakeBlobServiceClient,
        tmp_path: Path,
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        payload = b"x" * 100
        store["42/small.enc"] = payload

        calls: list[int] = []
        dst = tmp_path / "out.bin"
        provider.download("42/small.enc", dst, progress_cb=calls.append)

        assert dst.read_bytes() == payload
        # 1 Progress-Call (alles in einem Stueck)
        assert calls == [100]

    def test_download_missing_key_raises_with_progress(
        self, provider, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download(
                "42/missing.enc",
                tmp_path / "out.bin",
                progress_cb=lambda n: None,
            )


class TestDelete:
    def test_delete_removes_data_and_meta(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient, tmp_path: Path
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/server.tar.gz.enc"] = b"x"
        store["42/server.tar.gz.enc.meta.json"] = b"{}"
        assert "42/server.tar.gz.enc" in store
        assert "42/server.tar.gz.enc.meta.json" in store

        provider.delete("42/server.tar.gz.enc")
        assert "42/server.tar.gz.enc" not in store
        assert "42/server.tar.gz.enc.meta.json" not in store

    def test_delete_missing_data_still_removes_meta(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/server.tar.gz.enc.meta.json"] = b"{}"
        provider.delete("42/server.tar.gz.enc")
        assert "42/server.tar.gz.enc.meta.json" not in store

    def test_delete_missing_both_is_noop(self, provider: AzureProvider):
        provider.delete("42/never-existed.enc")

    def test_delete_malformed_key_is_noop(self, provider: AzureProvider):
        provider.delete("../../../etc/passwd")

    def test_delete_non_not_found_error_raises(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        provider._container.get_blob_client(
            "42/x.enc"
        )._delete_raises = azure_exceptions.ServiceRequestError("network")
        with pytest.raises(ProviderError):
            provider.delete("42/x.enc")


class TestListMetadata:
    def test_list_metadata_returns_parsed(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        meta1 = _make_meta(42, "Vor Update")
        meta2 = _make_meta(43)
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/server.tar.gz.enc.meta.json"] = meta1.to_json().encode("utf-8")
        store["43/other.tar.gz.enc.meta.json"] = meta2.to_json().encode("utf-8")
        store["44/orphan.tar.gz.enc"] = b"x"

        results = provider.list_metadata()
        assert len(results) == 2
        ids = {m.server_id for m in results}
        assert ids == {42, 43}
        m42 = next(m for m in results if m.server_id == 42)
        assert m42.name == "Vor Update"

    def test_list_metadata_skips_broken_files(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        meta = _make_meta(42)
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/good.enc.meta.json"] = meta.to_json().encode("utf-8")
        store["43/bad.enc.meta.json"] = b"{ not valid json"

        results = provider.list_metadata()
        assert len(results) == 1
        assert results[0].server_id == 42

    def test_list_metadata_empty_container(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        assert provider.list_metadata() == []

    def test_list_metadata_fails_on_azure_error(
        self, provider: AzureProvider
    ):
        provider._container._list_blobs_raises = azure_exceptions.ServiceRequestError(
            "down"
        )
        with pytest.raises(ProviderError):
            provider.list_metadata()

    def test_list_metadata_ignores_folder_markers(
        self, provider: AzureProvider, fake_service: FakeBlobServiceClient
    ):
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        store["42/"] = b""
        assert provider.list_metadata() == []


class TestSecurityPathTraversal:
    def test_upload_absolute_path_rejected(self, provider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "/etc/passwd")

    def test_upload_dotdot_rejected(self, provider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "../../../etc/passwd")

    def test_upload_mixed_traversal_rejected(self, provider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "42/../../../etc/passwd")

    def test_download_absolute_path_rejected(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.download("/etc/passwd", tmp_path / "out.bin")

    def test_download_dotdot_rejected(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.download("../../../etc/passwd", tmp_path / "out.bin")

    def test_empty_key_rejected(self, provider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "")


class TestPathPrefix:
    def test_path_prefix_prepended_to_blob_name(
        self, provider_with_prefix, fake_service, tmp_path: Path
    ):
        from tests.test_backup_provider_azure import AzureProvider
        # Eigener Provider mit path_prefix
        store = FakeBlobServiceClient._shared_store["msm-backups"]
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider_with_prefix.upload(src, "42/x.enc")
        assert "my-prefix/42/x.enc" in store
        assert "42/x.enc" not in store  # kein doppeltes Speichern ohne prefix

    def test_path_prefix_with_traversal_rejected(
        self, provider_with_prefix, tmp_path: Path
    ):
        # Auch mit Prefix darf der Key nicht ausbrechen
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider_with_prefix.upload(src, "../../../etc/passwd")


@pytest.fixture
def provider_with_prefix(azure_client) -> AzureProvider:
    return AzureProvider(
        connection_string=CONN_STRING,
        container="msm-backups",
        path_prefix="my-prefix",
        account_name="test",
    )


class TestFactory:
    def test_factory_returns_azure_provider(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "azure")
        monkeypatch.setattr(settings, "backup_azure_connection_string", CONN_STRING)
        monkeypatch.setattr(settings, "backup_azure_container", "msm-backups")
        monkeypatch.setattr(settings, "backup_azure_path_prefix", "")
        monkeypatch.setattr(settings, "backup_azure_account", "test")
        # SA-Init monkeypatchen, damit der Factory-Call nicht scheitert
        monkeypatch.setattr(
            "services.backup_provider.azure.BlobServiceClient.from_connection_string",
            classmethod(lambda cls, *args, **kwargs: MagicMock()),
        )
        p = get_provider()
        assert p.name == "azure"
        assert p.container_name == "msm-backups"

    def test_factory_rejects_azure_without_connection_string(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "azure")
        monkeypatch.setattr(settings, "backup_azure_connection_string", "")
        monkeypatch.setattr(settings, "backup_azure_container", "msm-backups")
        with pytest.raises(ProviderError):
            get_provider()
