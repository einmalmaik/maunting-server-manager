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
- Progress-Callback wird mehrfach aufgerufen bei Multi-Chunk-Upload
- Progress-Callback ist kumulativ (nicht delta)
- Progress-Callback wird mehrfach aufgerufen bei Multi-Chunk-Download
- Resumable-Upload: 308 + 200 OK Sequenz funktioniert
- Resumable-Upload: 4xx-Response → ProviderError
- Resumable-Upload ohne progress_cb nutzt Single-Shot-Pfad
- Download ohne progress_cb nutzt Single-Shot-Pfad
- Path-Traversal-Schutz: relative "..", absolute Pfade werden abgelehnt
- Factory: gcs-Branch instanziiert korrekt
- Factory: fehlende Credentials → ProviderError

Mocking: ``google.cloud.storage.Client.from_service_account_json`` wird
per ``monkeypatch`` ersetzt, sodass der GCS-Client eine
``FakeGcsClient``-Instanz zurueckliefert. Diese emuliert
``bucket()``, ``blob()``, ``upload_from_filename``,
``download_to_filename``, ``delete``, ``exists``, ``list_blobs``,
``create_resumable_upload_session``, ``media_link`` und stellt eine
``_credentials``-Property bereit, die eine
``FakeAuthorizedSession`` fuer den Resumable-Pfad speist.
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
    Credentials-Quelle). Daher reicht ein Minimal-Skeleton.
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


class FakeResponse:
    """HTTP-Response-Mock fuer Resumable-Upload- und Stream-Download-PATCH/GET."""

    def __init__(
        self,
        status_code: int = 200,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self._chunks = chunks or []
        self.headers: dict[str, str] = {}
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            # Wie requests: HTTPError werfen. Test faengt das ggf. ab.
            import httpx
            raise httpx.HTTPStatusError(
                "error", request=MagicMock(), response=self
            )

    def iter_content(self, chunk_size: int):
        for chunk in self._chunks:
            yield chunk


class FakeAuthorizedSession:
    """Mock fuer ``google.auth.transport.requests.AuthorizedSession``.

    Wird vom Provider fuer Resumable-Upload-PATCH und Stream-Download-
    GET genutzt. Standard-Verhalten: 200 OK fuer alle PATCH-Requests
    (single-chunk Upload), leere Body fuer GET. Tests koennen
    ``patch_responses``/``get_responses`` vorkonfigurieren, um
    Multi-Chunk-Uploads (308 + 200) und Fehler zu simulieren.
    """

    def __init__(self, credentials=None, refresh_status_codes=None) -> None:
        self._credentials = credentials
        self._refresh_status_codes = refresh_status_codes
        self.patch_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        # Vorkonfigurierte Antworten. Bei None: Default = 200/308-Sequenz.
        self.patch_responses: list[FakeResponse] | None = None
        self.get_responses: FakeResponse | None = None
        self._patch_iter = 0

    def patch(self, url, data=None, headers=None, **kwargs) -> FakeResponse:
        self.patch_calls.append(
            {"url": url, "data": data, "headers": dict(headers or {}), **kwargs}
        )
        if self.patch_responses is not None:
            # Sequenz: 308, 308, ..., 200 OK
            if self._patch_iter < len(self.patch_responses):
                resp = self.patch_responses[self._patch_iter]
                self._patch_iter += 1
                return resp
            # Mehr Chunks als vorkonfiguriert → letzten Eintrag nochmal
            return self.patch_responses[-1]
        # Default: ein einzelner 200 OK (single-chunk passt, weil
        # size_bytes < 8 MB, dann ist die Datei ein einziger Chunk)
        return FakeResponse(status_code=200)

    def get(self, url, **kwargs) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        if self.get_responses is not None:
            return self.get_responses
        return FakeResponse(status_code=200, chunks=[])


class FakeBlob:
    """In-Memory-Blob, der die google-cloud-storage Blob-API emuliert."""

    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        self.chunk_size: int | None = None
        self.media_link: str = (
            f"https://storage.googleapis.com/test/{name}?generation=1"
        )
        # Error-Injection
        self._delete_raises: BaseException | None = None
        self._download_raises: BaseException | None = None
        self._upload_raises: BaseException | None = None
        self._resumable_session_raises: BaseException | None = None
        # Konfigurierte Resumable-URL (sonst Default)
        self._resumable_session_url: str = (
            f"https://storage.googleapis.com/upload/test/{name}?upload_id=fake"
        )

    # Single-Shot-Pfade (kein progress_cb) — backward compat
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
        self._store.pop(self.name, None)

    def download_as_text(self, **kwargs) -> str:
        if self.name not in self._store:
            raise gcs_exceptions.NotFound(f"Blob {self.name} not found")
        return self._store[self.name].decode("utf-8")

    # Resumable-Upload-Pfad
    def create_resumable_upload_session(
        self,
        content_type=None,
        size=None,
        origin=None,
        client=None,
        timeout=60,
        checksum=None,
        **kwargs,
    ) -> str:
        if self._resumable_session_raises:
            raise self._resumable_session_raises
        return self._resumable_session_url

    def reload(self, client=None, **kwargs) -> None:
        # Test-Hook: nichts zu tun, media_link ist statisch
        pass


class FakeBucket:
    """In-Memory-Bucket, der die google-cloud-storage Bucket-API emuliert."""

    def __init__(self, name: str, store: dict[str, bytes]) -> None:
        self.name = name
        self._store = store
        self._exists_result: bool = True
        self._exists_raises: BaseException | None = None

    def blob(self, name: str, **kwargs) -> FakeBlob:
        return FakeBlob(name, self._store)

    def exists(self, **kwargs) -> bool:
        if self._exists_raises:
            raise self._exists_raises
        return self._exists_result


class FakeCredentials:
    """Dummy-Credentials fuer AuthorizedSession-Construction.

    Wird nur als Object-Identity gebraucht, nicht funktional genutzt
    (der Fake-AuthorizedSession ignoriert das Objekt).
    """

    def __init__(self) -> None:
        self.token = "fake-token"
        self.expired = False

    def refresh(self, request) -> None:
        self.expired = False


class FakeGcsClient:
    """In-Memory-Fake fuer ``google.cloud.storage.Client``."""

    def __init__(self, *args, **kwargs) -> None:
        self._buckets: dict[str, FakeBucket] = {}
        self._list_blobs_result: list[FakeBlob] = []
        self._list_blobs_raises: BaseException | None = None
        # Wird vom Provider ueber client._credentials abgegriffen
        self._credentials = FakeCredentials()
        # Track calls
        self.list_blobs_calls: list[tuple[str, dict]] = []
        # AuthorizedSession-Konstruktion
        self.authorized_sessions: list[FakeAuthorizedSession] = []
        self._authorized_session_factory = None

    @property
    def credentials(self):  # alias wie im echten Client
        return self._credentials

    def bucket(self, name: str) -> FakeBucket:
        if name not in self._buckets:
            self._buckets[name] = FakeBucket(name, self._buckets_data(name))
        return self._buckets[name]

    def _buckets_data(self, name: str) -> dict[str, bytes]:
        return self._shared_store.setdefault(name, {})

    # Class-level: gemeinsamer Store ueber alle Client-Instanzen hinweg
    _shared_store: dict[str, dict[str, bytes]] = {}

    def list_blobs(self, bucket_name: str, **kwargs) -> list[FakeBlob]:
        self.list_blobs_calls.append((bucket_name, kwargs))
        if self._list_blobs_raises:
            raise self._list_blobs_raises
        prefix = kwargs.get("prefix", "")
        store = self._buckets_data(bucket_name)
        return [FakeBlob(name, store) for name in store if name.startswith(prefix)]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_gcs_store() -> dict[str, dict[str, bytes]]:
    FakeGcsClient._shared_store = {}
    return FakeGcsClient._shared_store


@pytest.fixture
def fake_gcs_client(fake_gcs_store) -> FakeGcsClient:
    return FakeGcsClient()


@pytest.fixture
def gcs_client(fake_gcs_client: FakeGcsClient, monkeypatch) -> FakeGcsClient:
    """Patcht ``google.cloud.storage.Client.from_service_account_json`` so dass
    jeder Aufruf eine Fake-Instanz liefert. Patcht zusaetzlich
    ``google.auth.transport.requests.AuthorizedSession`` so dass die
    Resumable-Upload- und Stream-Download-Pfade mit FakeAuthorizedSession
    laufen."""
    monkeypatch.setattr(
        "services.backup_provider.gcs.gcs.Client.from_service_account_json",
        classmethod(lambda cls, *args, **kwargs: fake_gcs_client),
    )
    # AuthorizedSession monkeypatchen — Provider ruft
    # google.auth.transport.requests.AuthorizedSession(credentials).
    # Wir geben eine Factory, die bei jedem Aufruf ein
    # FakeAuthorizedSession zurueckgibt und am Client trackt.
    sessions: list[FakeAuthorizedSession] = []

    def factory(*args, **kwargs):
        s = FakeAuthorizedSession(*args, **kwargs)
        sessions.append(s)
        return s

    monkeypatch.setattr(
        "services.backup_provider.gcs.google_requests.AuthorizedSession",
        factory,
    )
    fake_gcs_client.authorized_sessions = sessions
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

    def test_constructor_rejects_missing_sa_file(self, tmp_path: Path):
        # File existiert nicht — OSError beim Oeffnen → ProviderError
        with pytest.raises(ProviderError):
            GCSProvider(
                bucket="b",
                sa_file_path=str(tmp_path / "does-not-exist.json"),
            )

    def test_constructor_rejects_invalid_sa_file_json(self, tmp_path: Path):
        # Datei existiert, aber Inhalt ist kein JSON → ValueError → ProviderError
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(ProviderError):
            GCSProvider(bucket="b", sa_file_path=str(bad))


class TestConnection:
    def test_connection_succeeds_when_bucket_exists(self, provider: GCSProvider):
        assert provider.test_connection() is True

    def test_connection_fails_when_bucket_missing(self, provider: GCSProvider):
        provider._bucket._exists_result = False
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


class TestUploadWithoutProgress:
    """Tests fuer den Single-Shot-Upload-Pfad (ohne progress_cb).

    Ohne progress_cb nutzt der Provider ``upload_from_filename`` —
    kein Resumable-Protokoll, keine AuthorizedSession. Das ist der
    Fallback fuer Aufrufer, die keinen Live-Progress brauchen
    (z. B. Auto-Migration wo der Restore-Frontend-Progress egal ist).
    """

    def test_upload_uses_single_shot_without_progress(
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
        # Kein Resumable-Pfad → keine AuthorizedSession-Calls
        assert len(fake_gcs_client.authorized_sessions) == 0

    def test_upload_missing_source_raises(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")


class TestUploadWithProgress:
    """Tests fuer den Resumable-Upload-Pfad (mit progress_cb).

    Bei progress_cb nutzt der Provider das GCS-Resumable-Protokoll
    ueber AuthorizedSession.patch in 8-MB-Chunks. Tests hier
    validieren: mehrere Progress-Calls, kumulative Bytes, korrekte
    Content-Range-Header, Fehler-Handling.
    """

    def test_small_file_single_chunk_progress(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # 5 KB < 8 MB Chunk → ein einzelner PATCH
        src = tmp_path / "small.bin"
        src.write_bytes(b"x" * 5000)
        calls: list[int] = []

        provider.upload(src, "42/small.enc", progress_cb=calls.append)

        # AuthorizedSession wurde einmal instanziiert
        assert len(fake_gcs_client.authorized_sessions) == 1
        session = fake_gcs_client.authorized_sessions[0]
        # 1 PATCH-Call
        assert len(session.patch_calls) == 1
        # Progress: 1 Call mit 5000 Bytes (kumulativ)
        assert calls == [5000]

    def test_multi_chunk_progress_is_cumulative(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # 25 MB → 4 Chunks (8+8+8+1), jeder feuert Progress
        src = tmp_path / "big.bin"
        src.write_bytes(b"x" * (25 * 1024 * 1024))
        calls: list[int] = []

        # Sequenz konfigurieren: 308, 308, 308, 200
        session = fake_gcs_client.authorized_sessions
        # authorized_sessions wird erst beim ersten authorized_session() erzeugt.
        # Aber die Liste ist getrackt. Wir koennen sie jetzt befuellen,
        # weil der Provider sie zur Laufzeit baut.
        # ABER: zu dem Zeitpunkt wo wir hier sind, ist die Liste leer.
        # Also: provider erst NACH dem Setup aufrufen.
        # Trick: wir ueberschreiben die Liste mit einem Pre-Config.
        from tests.test_backup_provider_gcs import FakeAuthorizedSession
        pre = FakeAuthorizedSession()
        pre.patch_responses = [
            FakeResponse(status_code=308),
            FakeResponse(status_code=308),
            FakeResponse(status_code=308),
            FakeResponse(status_code=200),
        ]
        fake_gcs_client.authorized_sessions.append(pre)
        # Die monkeypatch-Factory checkt die Liste NICHT, sie legt
        # einfach neue Sessions an. Wir muessen die monkeypatch-Factory
        # andersrum konfigurieren: bei naechstem Aufruf soll sie unsere
        # pre-config Session liefern.
        # Loesung: wir replacen den Provider-Call nach dem ersten
        # authorized_sessions.append mit einer zweiten session.

        # Stattdessen: wir setzen authorized_sessions via Provider
        # Override. Einfacher: monkeypatch direkt das Klassenattribut.
        from unittest.mock import patch as mock_patch

        # Cleaner: wir ueberschreiben die google_requests.AuthorizedSession
        # so, dass sie bei JEDEM Aufruf dieselbe pre-Config-Session
        # zurueckgibt. Dafuer patchen wir neu.
        pre2 = FakeAuthorizedSession()
        pre2.patch_responses = [
            FakeResponse(status_code=308),
            FakeResponse(status_code=308),
            FakeResponse(status_code=308),
            FakeResponse(status_code=200),
        ]

        import services.backup_provider.gcs as gcs_module

        original_factory = gcs_module.google_requests.AuthorizedSession

        def factory(*args, **kwargs):
            # Immer dieselbe pre-config Session zurueckgeben
            return pre2

        gcs_module.google_requests.AuthorizedSession = factory
        try:
            calls.clear()
            provider.upload(src, "42/big.enc", progress_cb=calls.append)
        finally:
            gcs_module.google_requests.AuthorizedSession = original_factory

        # 4 PATCH-Calls
        assert len(pre2.patch_calls) == 4
        # Progress-Calls: 4, jeder mit kumulativen Bytes
        assert len(calls) == 4
        assert calls == [
            8 * 1024 * 1024,
            16 * 1024 * 1024,
            24 * 1024 * 1024,
            25 * 1024 * 1024,
        ]
        # Content-Range korrekt fuer jeden Chunk
        for i, call in enumerate(pre2.patch_calls):
            expected_start = i * 8 * 1024 * 1024
            expected_end = min(
                expected_start + 8 * 1024 * 1024 - 1, 25 * 1024 * 1024 - 1
            )
            assert call["headers"]["Content-Range"] == (
                f"bytes {expected_start}-{expected_end}/26214400"
            )

    def test_progress_callback_receives_partial_even_on_error(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # 16 MB → 2 Chunks. Erster 200 OK, zweiter 500 → ProviderError.
        src = tmp_path / "broken.bin"
        src.write_bytes(b"x" * (16 * 1024 * 1024))
        calls: list[int] = []

        pre = FakeAuthorizedSession()
        pre.patch_responses = [
            FakeResponse(status_code=200),
            FakeResponse(status_code=500),
        ]

        import services.backup_provider.gcs as gcs_module

        original = gcs_module.google_requests.AuthorizedSession

        def factory(*args, **kwargs):
            return pre

        gcs_module.google_requests.AuthorizedSession = factory
        try:
            with pytest.raises(ProviderError):
                provider.upload(src, "42/broken.enc", progress_cb=calls.append)
        finally:
            gcs_module.google_requests.AuthorizedSession = original

        # 1 Progress-Call vor dem Fehler
        assert calls == [8 * 1024 * 1024]

    def test_resumable_session_creation_failure_raises(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # Wenn die Resumable-Session-Erstellung scheitert → ProviderError
        # ohne PATCH-Calls
        src = tmp_path / "x.bin"
        src.write_bytes(b"x")
        calls: list[int] = []

        def raising_blob_factory(*args, **kwargs):
            raise gcs_exceptions.ServiceUnavailable("GCS down")

        original_blob = provider._bucket.blob
        provider._bucket.blob = lambda name: type(
            "B", (), {"create_resumable_upload_session": staticmethod(raising_blob_factory)}
        )()
        try:
            with pytest.raises(ProviderError):
                provider.upload(src, "42/x.enc", progress_cb=calls.append)
        finally:
            provider._bucket.blob = original_blob
        assert calls == []


class TestDownloadWithoutProgress:
    """Tests fuer den Single-Shot-Download-Pfad (ohne progress_cb)."""

    def test_download_uses_single_shot_without_progress(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        src = tmp_path / "src.bin"
        payload = b"y" * 100
        src.write_bytes(payload)
        provider.upload(src, "42/x.enc")
        # Upload ohne progress_cb → kein AuthorizedSession-Construction
        sessions_before = len(fake_gcs_client.authorized_sessions)

        dst = tmp_path / "out.bin"
        provider.download("42/x.enc", dst)
        assert dst.read_bytes() == payload
        # Download ohne progress_cb → KEINE neue AuthorizedSession
        assert len(fake_gcs_client.authorized_sessions) == sessions_before

    def test_download_missing_key_raises(self, provider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")


class TestDownloadWithProgress:
    """Tests fuer den Stream-Download-Pfad (mit progress_cb).

    Bei progress_cb nutzt der Provider Stream-Download ueber
    AuthorizedSession.get(media_link, stream=True) und iter_content
    in 8-MB-Chunks.
    """

    def test_stream_download_writes_file_byte_exact(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # File existiert bereits im Fake-Store
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        payload = b"x" * 12345
        store["msm-backups/42/x.enc"] = payload

        # AuthorizedSession.get liefert die Bytes in mehreren Chunks
        chunk1, chunk2, chunk3 = payload[:5000], payload[5000:10000], payload[10000:]
        session = FakeAuthorizedSession()
        session.get_responses = FakeResponse(
            status_code=200, chunks=[chunk1, chunk2, chunk3]
        )

        import services.backup_provider.gcs as gcs_module

        original = gcs_module.google_requests.AuthorizedSession
        gcs_module.google_requests.AuthorizedSession = lambda *a, **kw: session
        try:
            calls: list[int] = []
            dst = tmp_path / "out.bin"
            provider.download("42/x.enc", dst, progress_cb=calls.append)
        finally:
            gcs_module.google_requests.AuthorizedSession = original

        assert dst.read_bytes() == payload
        # 3 Progress-Calls, kumulativ
        assert calls == [5000, 10000, 12345]

    def test_stream_download_single_chunk(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        payload = b"x" * 100
        store["msm-backups/42/small.enc"] = payload

        session = FakeAuthorizedSession()
        session.get_responses = FakeResponse(status_code=200, chunks=[payload])

        import services.backup_provider.gcs as gcs_module

        original = gcs_module.google_requests.AuthorizedSession
        gcs_module.google_requests.AuthorizedSession = lambda *a, **kw: session
        try:
            calls: list[int] = []
            dst = tmp_path / "out.bin"
            provider.download("42/small.enc", dst, progress_cb=calls.append)
        finally:
            gcs_module.google_requests.AuthorizedSession = original

        assert dst.read_bytes() == payload
        assert calls == [100]

    def test_stream_download_404_raises(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        # 404 → ProviderError (kein File in store)
        session = FakeAuthorizedSession()
        session.get_responses = FakeResponse(status_code=404, chunks=[])

        import services.backup_provider.gcs as gcs_module

        original = gcs_module.google_requests.AuthorizedSession
        gcs_module.google_requests.AuthorizedSession = lambda *a, **kw: session
        try:
            with pytest.raises(ProviderError):
                provider.download(
                    "42/missing.enc",
                    tmp_path / "out.bin",
                    progress_cb=lambda n: None,
                )
        finally:
            gcs_module.google_requests.AuthorizedSession = original

    def test_download_creates_intermediate_dirs(
        self,
        provider: GCSProvider,
        fake_gcs_client: FakeGcsClient,
        tmp_path: Path,
    ):
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/1/2/3/server.enc"] = b"x"

        session = FakeAuthorizedSession()
        session.get_responses = FakeResponse(status_code=200, chunks=[b"x"])

        import services.backup_provider.gcs as gcs_module

        original = gcs_module.google_requests.AuthorizedSession
        gcs_module.google_requests.AuthorizedSession = lambda *a, **kw: session
        try:
            dst = tmp_path / "out" / "nested" / "downloaded.bin"
            provider.download(
                "1/2/3/server.enc", dst, progress_cb=lambda n: None
            )
            assert dst.is_file()
        finally:
            gcs_module.google_requests.AuthorizedSession = original


class TestDelete:
    def test_delete_removes_data_and_meta(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/server.tar.gz.enc")
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
        provider.delete("42/server.tar.gz.enc")
        assert "msm-backups/42/server.tar.gz.enc.meta.json" not in store

    def test_delete_missing_both_is_noop(self, provider: GCSProvider):
        provider.delete("42/never-existed.enc")

    def test_delete_malformed_key_is_noop(self, provider: GCSProvider):
        provider.delete("../../../etc/passwd")

    def test_delete_non_not_found_error_raises(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient, tmp_path: Path
    ):
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
        assert provider.list_metadata() == []

    def test_list_metadata_uses_correct_prefix(
        self, provider: GCSProvider, fake_gcs_client: FakeGcsClient
    ):
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
        store = FakeGcsClient._shared_store["msm-test-bucket"]
        store["msm-backups/42/"] = b""
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


class TestFactory:
    def test_factory_returns_gcs_provider(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "gcs")
        monkeypatch.setattr(settings, "backup_gcs_bucket", "test-bucket")
        monkeypatch.setattr(settings, "backup_gcs_sa_file", "/tmp/sa.json")
        monkeypatch.setattr(settings, "backup_gcs_path_prefix", "msm-backups")
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
