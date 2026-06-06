"""Tests fuer den LocalProvider (Filesystem-Adapter).

Deckt:
- upload → download Roundtrip (Datei byte-genau erhalten)
- delete ist idempotent (fehlende Datei = OK)
- delete entfernt auch die Meta-Datei
- list_metadata parst *.meta.json
- list_metadata ueberspringt kaputte Meta-Files ohne Raise
- Path-Traversal-Schutz: relative "..", absolute Pfade werden abgelehnt
- test_connection: True bei existierendem, schreibbarem Root; False sonst
- root_dir wird automatisch angelegt, falls nicht existent
- upload erstellt Zwischenverzeichnisse automatisch
- Meta-File-Konvention: <key>.enc + <key>.meta.json

Security-relevant: keine Assertion enthaelt Pfade aus produktiven Servern.
"""
import json
from pathlib import Path
from typing import Optional

import pytest

from services.backup_provider import (
    BackupMetadata,
    BackupProvider,
    LocalProvider,
    ProviderError,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def provider(root: Path) -> LocalProvider:
    return LocalProvider(root_dir=str(root))


# ── Helpers ──────────────────────────────────────────────────────────────


def _meta(server_id: int = 42, name: str | None = None) -> BackupMetadata:
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


def _write_meta(provider: LocalProvider, remote_key: str, meta: BackupMetadata) -> None:
    """Schreibt eine .meta.json parallel zur remote_key (folgt Provider-Konvention)."""
    data_path = provider._full_path(remote_key)  # noqa: SLF001 — Test-Helper
    meta_path = provider._meta_path(data_path)  # noqa: SLF001
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(meta.to_json(), encoding="utf-8")


# ── Tests ────────────────────────────────────────────────────────────────


class TestContract:
    def test_implements_backup_provider_interface(self, provider: LocalProvider):
        assert isinstance(provider, BackupProvider)
        assert provider.name == "local"

    def test_root_dir_creation_on_init(self, tmp_path: Path):
        # Root existiert noch nicht — Provider soll ihn beim ersten Zugriff anlegen
        root = tmp_path / "fresh-root"
        assert not root.exists()
        p = LocalProvider(root_dir=str(root))
        # Beim test_connection (oder upload) wird angelegt
        assert p.test_connection() is True
        assert root.is_dir()


class TestUploadDownload:
    def test_upload_creates_file(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"backup payload")
        loc = provider.upload(src, "42/server.tar.gz.enc")
        assert loc.remote_key == "42/server.tar.gz.enc"
        assert loc.size_mb == 0  # sehr klein, < 1 MB → 0
        # Datei liegt physisch unter root/42/server.tar.gz.enc
        assert (provider.root_dir / "42" / "server.tar.gz.enc").is_file()

    def test_upload_creates_intermediate_dirs(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/4/deep.tar.gz.enc")
        assert (provider.root_dir / "1" / "2" / "3" / "4" / "deep.tar.gz.enc").is_file()

    def test_upload_missing_source_raises(self, provider: LocalProvider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")

    def test_download_writes_file_byte_exact(
        self, provider: LocalProvider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        payload = b"x" * 5000
        src.write_bytes(payload)
        provider.upload(src, "42/server.tar.gz.enc")
        dst = tmp_path / "downloaded.bin"
        provider.download("42/server.tar.gz.enc", dst)
        assert dst.read_bytes() == payload

    def test_download_creates_intermediate_dirs(
        self, provider: LocalProvider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/server.enc")
        dst = tmp_path / "out" / "deep" / "downloaded.bin"
        provider.download("1/2/3/server.enc", dst)
        assert dst.is_file()

    def test_download_missing_file_raises(self, provider: LocalProvider, tmp_path: Path):
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")

    def test_progress_callback_called(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x" * 100)
        calls: list[tuple[int, int]] = []

        def cb(done: int, total: int) -> None:
            calls.append((done, total))

        provider.upload(src, "42/x.enc", progress_cb=cb)
        # Local hat keinen In-File-Progress; einmaliger Callback am Ende erwartet
        assert len(calls) == 1
        assert calls[0] == (100, 100)


class TestDelete:
    def test_delete_removes_data_and_meta(
        self, provider: LocalProvider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/server.tar.gz.enc")
        _write_meta(provider, "42/server.tar.gz.enc", _meta(42))
        data_path = provider.root_dir / "42" / "server.tar.gz.enc"
        meta_path = data_path.with_name(data_path.name + ".meta.json")
        assert data_path.is_file()
        assert meta_path.is_file()
        provider.delete("42/server.tar.gz.enc")
        assert not data_path.exists()
        assert not meta_path.exists()

    def test_delete_missing_file_is_noop(self, provider: LocalProvider):
        # Kein vorheriger Upload — delete soll nicht raisen
        provider.delete("42/never-existed.enc")

    def test_delete_malformed_key_is_noop(self, provider: LocalProvider):
        # Key mit ".." → wuerde eigentlich raisen, aber delete ist idempotent
        provider.delete("../../../etc/passwd")


class TestListMetadata:
    def test_list_metadata_returns_parsed(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/server.tar.gz.enc")
        provider.upload(src, "43/other.tar.gz.enc")
        _write_meta(provider, "42/server.tar.gz.enc", _meta(42, "Vor Update"))
        _write_meta(provider, "43/other.tar.gz.enc", _meta(43))

        results = provider.list_metadata()
        assert len(results) == 2
        server_ids = {m.server_id for m in results}
        assert server_ids == {42, 43}
        m42 = next(m for m in results if m.server_id == 42)
        assert m42.name == "Vor Update"
        assert m42.game_type == "minecraft"

    def test_list_metadata_skips_broken_files(
        self, provider: LocalProvider, tmp_path: Path
    ):
        # Eine kaputte meta.json neben einer funktionierenden
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/good.enc")
        provider.upload(src, "43/bad.enc")
        _write_meta(provider, "42/good.enc", _meta(42))
        # Manuell kaputte Meta-Datei reinschreiben
        bad_meta = provider._full_path("43/bad.enc").with_name(  # noqa: SLF001
            "bad.enc.meta.json"
        )
        bad_meta.parent.mkdir(parents=True, exist_ok=True)
        bad_meta.write_text("{ not valid json", encoding="utf-8")

        results = provider.list_metadata()
        # Nur die funktionierende Meta kommt zurueck
        assert len(results) == 1
        assert results[0].server_id == 42

    def test_list_metadata_empty_on_empty_root(self, provider: LocalProvider):
        assert provider.list_metadata() == []


class TestSecurityPathTraversal:
    def test_upload_absolute_path_rejected(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "/etc/passwd")

    def test_upload_dotdot_rejected(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "../../../etc/passwd")

    def test_upload_mixed_traversal_rejected(
        self, provider: LocalProvider, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "42/../../../etc/passwd")

    def test_download_absolute_path_rejected(
        self, provider: LocalProvider, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("/etc/passwd", tmp_path / "out.bin")

    def test_download_dotdot_rejected(
        self, provider: LocalProvider, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("../../../etc/passwd", tmp_path / "out.bin")

    def test_empty_key_rejected(self, provider: LocalProvider, tmp_path: Path):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "")


class TestConnection:
    def test_connection_succeeds_with_writable_root(
        self, provider: LocalProvider
    ):
        assert provider.test_connection() is True

    def test_connection_fails_on_readonly_root(self, tmp_path: Path):
        # Wenn wir auf einer realen Maschine sind: chmod + readonly, dann test_connection
        # Auf Windows-Tests kann das verhalten unterschiedlich sein — skippen wenn nicht
        # unter POSIX testbar.
        import os
        import sys
        if sys.platform == "win32":
            pytest.skip("chmod-readonly-Test nur auf POSIX relevant")
        root = tmp_path / "ro-root"
        root.mkdir()
        os.chmod(root, 0o555)
        try:
            p = LocalProvider(root_dir=str(root))
            assert p.test_connection() is False
        finally:
            os.chmod(root, 0o755)
