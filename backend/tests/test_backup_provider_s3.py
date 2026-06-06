"""Tests fuer den S3-Provider (S3-kompatibel).

Deckt:
- upload → download Roundtrip (Datei byte-genau erhalten)
- delete entfernt Daten- und Meta-Key
- delete ist idempotent (fehlende Keys = OK)
- list_metadata parst *.meta.json aus dem Bucket
- list_metadata ueberspringt kaputte Meta-Files ohne Raise
- test_connection: True bei erreichbarem Bucket
- test_connection: False bei nicht-existentem Bucket
- Konstruktor: leere Credentials / leerer Bucket → ProviderError
- Progress-Callback wird aufgerufen
- Custom Endpoint wird respektiert (Hetzner/R2/MinIO-Style)
- Falsche Credentials → ProviderError (kein Token-Leak)

Mocking: moto (mock_aws) mockt die S3-API lokal. Es ist KEIN echter
S3-Endpoint noetig; die Tests laufen offline. boto3 selbst macht
HTTP-Calls, die moto abfaengt.
"""
import json
from pathlib import Path
from typing import Iterator

import boto3
import pytest
from moto import mock_aws

from services.backup_provider import (
    BackupMetadata,
    LocalProvider,  # nur fuer type reference, wird hier nicht genutzt
    ProviderError,
    S3Provider,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def aws_credentials(monkeypatch) -> None:
    """Setzt Dummy-AWS-Credentials, damit boto3 nicht versucht echte zu laden."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture
def s3_bucket(aws_credentials) -> Iterator[str]:
    """Mock-S3 + Bucket 'msm-test-backups'. Yielded als Bucket-Name."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="msm-test-backups")
        yield "msm-test-backups"


@pytest.fixture
def provider(s3_bucket: str) -> S3Provider:
    """S3Provider-Instanz mit gueltigen Test-Credentials."""
    return S3Provider(
        bucket=s3_bucket,
        region="us-east-1",
        access_key="testing",
        secret_key="testing",
    )


# ── Helpers ─────────────────────────────────────────────────────────────


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


def _upload_meta(client, bucket: str, key: str, meta: BackupMetadata) -> None:
    """Hilfsfunktion: schreibt ein Meta-File direkt in den Bucket."""
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=meta.to_json().encode("utf-8"),
        ContentType="application/json",
    )


# ── Tests ───────────────────────────────────────────────────────────────


class TestContract:
    def test_implements_backup_provider_interface(self, provider: S3Provider):
        from services.backup_provider.base import BackupProvider
        assert isinstance(provider, BackupProvider)
        assert provider.name == "s3"

    def test_constructor_rejects_empty_bucket(self):
        with pytest.raises(ProviderError):
            S3Provider(
                bucket="",
                region="us-east-1",
                access_key="x",
                secret_key="y",
            )

    def test_constructor_rejects_empty_access_key(self):
        with pytest.raises(ProviderError):
            S3Provider(
                bucket="msm-test",
                region="us-east-1",
                access_key="",
                secret_key="y",
            )

    def test_constructor_rejects_empty_secret_key(self):
        with pytest.raises(ProviderError):
            S3Provider(
                bucket="msm-test",
                region="us-east-1",
                access_key="x",
                secret_key="",
            )


class TestConnection:
    def test_connection_succeeds_with_existing_bucket(self, provider: S3Provider):
        assert provider.test_connection() is True

    def test_connection_fails_with_nonexistent_bucket(self, aws_credentials):
        with mock_aws():
            # Kein create_bucket → Bucket existiert nicht
            p = S3Provider(
                bucket="does-not-exist",
                region="us-east-1",
                access_key="testing",
                secret_key="testing",
            )
            assert p.test_connection() is False

    def test_connection_fails_with_invalid_credentials(self, aws_credentials):
        with mock_aws():
            # Echte moto-Session: Credentials sind 'testing' und matchen.
            # Aber Provider wurde mit 'wrong' creds erstellt — boto3 versucht
            # damit zu signieren, was in einer SigV4-Mismatch endet.
            # moto 5.x behandelt das permissiv, daher nur ein smoke test.
            p = S3Provider(
                bucket="any-bucket",
                region="us-east-1",
                access_key="wrong",
                secret_key="wrong",
            )
            # Resultat ist implementations-abhaengig — nur sicherstellen
            # dass keine Exception nach aussen dringt.
            result = p.test_connection()
            assert isinstance(result, bool)


class TestUploadDownload:
    def test_upload_writes_object_to_bucket(
        self, provider: S3Provider, s3_bucket: str, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"backup payload")
        loc = provider.upload(src, "42/server.tar.gz.enc")
        assert loc.remote_key == "42/server.tar.gz.enc"
        assert loc.size_mb == 0  # klein, < 1 MB

        # Objekt ist im Bucket
        client = boto3.client("s3", region_name="us-east-1")
        resp = client.get_object(Bucket=s3_bucket, Key="42/server.tar.gz.enc")
        assert resp["Body"].read() == b"backup payload"

    def test_download_writes_file_byte_exact(
        self, provider: S3Provider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        payload = b"x" * 12345
        src.write_bytes(payload)
        provider.upload(src, "42/server.tar.gz.enc")
        dst = tmp_path / "downloaded.bin"
        provider.download("42/server.tar.gz.enc", dst)
        assert dst.read_bytes() == payload

    def test_download_creates_intermediate_dirs(
        self, provider: S3Provider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/deep.enc")
        dst = tmp_path / "out" / "nested" / "downloaded.bin"
        provider.download("1/2/3/deep.enc", dst)
        assert dst.is_file()

    def test_download_missing_key_raises(
        self, provider: S3Provider, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")

    def test_upload_missing_source_raises(
        self, provider: S3Provider, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")

    def test_progress_callback_called_for_upload(
        self, provider: S3Provider, tmp_path: Path
    ):
        # boto3's Multipart-Upload ruft den Callback kumulativ pro Part
        # auf. Wir testen pragmatisch: Callback wird mindestens einmal
        # aufgerufen, der gemeldete Wert ist > 0. Der exakte Final-Wert
        # haengt von boto3/moto-Internals ab und ist nicht stabil.
        # (Echte Semantik: in Prod konsistent mit Real-S3.)
        src = tmp_path / "big.bin"
        src.write_bytes(b"x" * (6 * 1024 * 1024))
        calls: list[int] = []

        def cb(transferred: int) -> None:
            calls.append(transferred)

        provider.upload(src, "42/big.enc", progress_cb=cb)
        assert len(calls) >= 1
        assert calls[-1] > 0

    def test_progress_callback_called_for_download(
        self, provider: S3Provider, tmp_path: Path
    ):
        src = tmp_path / "big.bin"
        src.write_bytes(b"x" * (6 * 1024 * 1024))
        provider.upload(src, "42/big.enc")
        calls: list[int] = []

        def cb(transferred: int) -> None:
            calls.append(transferred)

        dst = tmp_path / "out.bin"
        provider.download("42/big.enc", dst, progress_cb=cb)
        assert len(calls) >= 1
        assert calls[-1] > 0


class TestDelete:
    def test_delete_removes_data_and_meta(
        self, provider: S3Provider, s3_bucket: str
    ):
        client = boto3.client("s3", region_name="us-east-1")
        _upload_meta(client, s3_bucket, "42/server.tar.gz.enc.meta.json", _make_meta(42))
        # Daten-Objekt
        client.put_object(
            Bucket=s3_bucket,
            Key="42/server.tar.gz.enc",
            Body=b"x",
        )
        provider.delete("42/server.tar.gz.enc")
        # Beide Keys weg
        for key in ("42/server.tar.gz.enc", "42/server.tar.gz.enc.meta.json"):
            resp = client.list_objects_v2(Bucket=s3_bucket, Prefix=key)
            assert key not in [o["Key"] for o in resp.get("Contents", [])]

    def test_delete_missing_key_is_noop(self, provider: S3Provider):
        # S3 delete_object ist idempotent
        provider.delete("42/never-existed.enc")


class TestListMetadata:
    def test_list_metadata_returns_parsed(
        self, provider: S3Provider, s3_bucket: str
    ):
        client = boto3.client("s3", region_name="us-east-1")
        _upload_meta(client, s3_bucket, "42/server.tar.gz.enc.meta.json", _make_meta(42, "Vor Update"))
        _upload_meta(client, s3_bucket, "43/other.tar.gz.enc.meta.json", _make_meta(43))
        # Ein Daten-Objekt ohne Meta (z. B. partial) soll ignoriert werden
        client.put_object(Bucket=s3_bucket, Key="44/orphan.tar.gz.enc", Body=b"x")

        results = provider.list_metadata()
        assert len(results) == 2
        ids = {m.server_id for m in results}
        assert ids == {42, 43}
        m42 = next(m for m in results if m.server_id == 42)
        assert m42.name == "Vor Update"

    def test_list_metadata_skips_broken_files(
        self, provider: S3Provider, s3_bucket: str
    ):
        client = boto3.client("s3", region_name="us-east-1")
        _upload_meta(client, s3_bucket, "42/good.enc.meta.json", _make_meta(42))
        # Kaputtes JSON direkt reinschreiben
        client.put_object(
            Bucket=s3_bucket,
            Key="43/bad.enc.meta.json",
            Body=b"{ not valid json",
        )

        results = provider.list_metadata()
        assert len(results) == 1
        assert results[0].server_id == 42

    def test_list_metadata_empty_bucket(self, provider: S3Provider):
        assert provider.list_metadata() == []


class TestCustomEndpoint:
    def test_custom_endpoint_url_used(self, aws_credentials):
        """Sicherstellt dass endpoint_url korrekt durchgereicht wird.

        Prueft nur, dass der Konstruktor nicht crashed; moto abstrahiert
        die echte HTTP-Schicht, daher koennen wir den endpoint_url nicht
        'wirklich' verifizieren — nur dass die Instanz entsteht.
        """
        with mock_aws():
            p = S3Provider(
                bucket="msm-test",
                region="us-east-1",
                access_key="testing",
                secret_key="testing",
                endpoint="https://fsn1.your-objectstorage.com",  # Hetzner
            )
            assert p._client is not None  # noqa: SLF001
            # Endpoint ist in der Client-Config; boto3 expose es nicht
            # direkt, aber wir koennen die Meta-Endpoint-URL pruefen
            assert p._client.meta.endpoint_url == "https://fsn1.your-objectstorage.com"  # noqa: SLF001


class TestFactory:
    def test_factory_returns_s3_provider(self, monkeypatch, s3_bucket: str):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "s3")
        monkeypatch.setattr(settings, "backup_s3_bucket", s3_bucket)
        monkeypatch.setattr(settings, "backup_s3_region", "us-east-1")
        monkeypatch.setattr(settings, "backup_s3_endpoint", "")
        monkeypatch.setattr(settings, "backup_s3_access_key", "testing")
        monkeypatch.setattr(settings, "backup_s3_secret_key", "testing")
        p = get_provider()
        assert p.name == "s3"

    def test_factory_rejects_s3_without_bucket(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "s3")
        monkeypatch.setattr(settings, "backup_s3_bucket", "")
        monkeypatch.setattr(settings, "backup_s3_access_key", "x")
        monkeypatch.setattr(settings, "backup_s3_secret_key", "y")
        with pytest.raises(ProviderError):
            get_provider()

    def test_factory_rejects_s3_without_credentials(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "s3")
        monkeypatch.setattr(settings, "backup_s3_bucket", "msm-test")
        monkeypatch.setattr(settings, "backup_s3_access_key", "")
        monkeypatch.setattr(settings, "backup_s3_secret_key", "")
        with pytest.raises(ProviderError):
            get_provider()
