"""Tests fuer den SFTP-Provider (Hetzner Storage Box + generische SFTP-Server).

Deckt:
- upload → download Roundtrip (Datei byte-genau erhalten)
- delete entfernt Daten- und Meta-Datei
- delete ist idempotent (fehlende Dateien = OK)
- list_metadata parst *.meta.json aus base_path
- list_metadata ueberspringt kaputte Meta-Files ohne Raise
- test_connection: True bei erreichbarem Host + existentem base_path
- test_connection: False bei Auth-Fehler
- test_connection: False bei SSH-Connect-Fehler
- test_connection: legt base_path an, falls fehlend (mkdir-p Side-Effect)
- Konstruktor: leere Felder → ProviderError
- Konstruktor: relative base_path → ProviderError
- Konstruktor: ungueltiger Port → ProviderError
- Progress-Callback wird aufgerufen
- Path-Traversal-Schutz: relative "..", absolute Pfade werden abgelehnt
- Host-Key-Policy: RejectPolicy wird gesetzt
- Agent/SSH-Key-Lookup wird explizit deaktiviert
- Factory: sftp-Branch instanziiert SFTPProvider korrekt
- Factory: fehlende Credentials → ProviderError

Mocking: ein in-memory ``FakeSFTP`` ersetzt ``paramiko.SFTPClient``.
``paramiko.SSHClient`` wird per ``monkeypatch`` auf einen Factory-Mock
gesetzt, der eine ``MagicMock``-Client-Instanz liefert, deren
``open_sftp()`` die Fake-SFTP-Instanz zurueckgibt. Es ist KEIN echter
SFTP-Server noetig; die Tests laufen offline.
"""
import io
import posixpath
import stat as stat_mod
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import paramiko
import pytest

from services.backup_provider import (
    BackupMetadata,
    ProviderError,
    SFTPProvider,
)


# ── Fake-SFTP (in-memory) ────────────────────────────────────────────────


class FakeSFTP:
    """In-memory-Fake fuer ``paramiko.sftp_client.SFTPClient``.

    Realistisches genug, um Pfad-Traversal- und Existenz-Logik zu
    validieren, ohne einen echten SFTP-Server hochfahren zu muessen.
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}  # path -> content
        self._dirs: set[str] = {"/"}  # root existiert
        # Telemetrie (fuer Test-Asserts)
        self.stat_log: list[str] = []
        self.put_log: list[tuple[str, str, object]] = []
        self.get_log: list[tuple[str, str, object]] = []
        self.remove_log: list[str] = []
        self.mkdir_log: list[str] = []
        self.listdir_log: list[str] = []
        self.open_log: list[tuple[str, str]] = []
        # Verhalten-Toggles (fuer Fehler-Pfade)
        self.raise_on_open: BaseException | None = None
        self.raise_on_stat: BaseException | None = None

    # ── public SFTP-Interface (genutzte Methoden) ──────────────────────

    def stat(self, path: str):
        self.stat_log.append(path)
        if self.raise_on_stat is not None:
            raise self.raise_on_stat
        if path in self._files or path in self._dirs:
            return self._attr(path)
        raise OSError(2, "No such file or directory", path)

    def put(self, localpath: str, remotepath: str, callback=None):
        self.put_log.append((localpath, remotepath, callback))
        data = Path(localpath).read_bytes()
        self._files[remotepath] = data
        if callback:
            callback(len(data), len(data))

    def get(self, remotepath: str, localpath: str, callback=None):
        self.get_log.append((remotepath, localpath, callback))
        if remotepath not in self._files:
            raise OSError(2, "No such file or directory", remotepath)
        data = self._files[remotepath]
        Path(localpath).parent.mkdir(parents=True, exist_ok=True)
        Path(localpath).write_bytes(data)
        if callback:
            callback(len(data), len(data))

    def remove(self, path: str):
        self.remove_log.append(path)
        if path in self._files:
            del self._files[path]
        elif path in self._dirs:
            raise OSError(21, "Is a directory", path)
        else:
            raise OSError(2, "No such file or directory", path)

    def mkdir(self, path: str):
        self.mkdir_log.append(path)
        if path in self._dirs or path in self._files:
            raise OSError(17, "File exists", path)
        self._dirs.add(path)

    def listdir_attr(self, path: str):
        self.listdir_log.append(path)
        if path not in self._dirs:
            raise OSError(2, "No such directory", path)
        prefix = path.rstrip("/") + "/"
        children: dict[str, bool] = {}  # name -> is_dir
        for p in self._files:
            if p.startswith(prefix):
                rest = p[len(prefix):]
                if "/" not in rest:
                    children[rest] = False
                else:
                    name = rest.split("/", 1)[0]
                    if name not in children:
                        children[name] = True
        for p in self._dirs:
            if p != path and p.startswith(prefix):
                rest = p[len(prefix):]
                if "/" not in rest:
                    children[rest] = True
                else:
                    name = rest.split("/", 1)[0]
                    children[name] = True
        result = []
        for name, is_dir in children.items():
            mode = (
                stat_mod.S_IFDIR | 0o755 if is_dir else stat_mod.S_IFREG | 0o644
            )
            result.append(SimpleNamespace(filename=name, st_mode=mode))
        return result

    def open(self, path: str, mode: str = "r"):
        self.open_log.append((path, mode))
        if mode == "r":
            if path not in self._files:
                raise OSError(2, "No such file or directory", path)
            return _FakeSFTPFile(self._files[path])
        raise NotImplementedError(f"open mode {mode!r} not in fake")

    def close(self) -> None:
        pass

    # ── Test-Helfer ───────────────────────────────────────────────────

    def add_dir(self, path: str) -> None:
        """Forge eine existierende Verzeichnisstruktur (fuer listdir-Tests)."""
        # Erstes Segment mit "/" joinen, damit der Pfad absolut wird
        # (posixpath.join("", p) gibt nur p zurueck, was die echte Wurzel verfaelscht).
        parts = path.strip("/").split("/")
        cur = ""
        for p in parts:
            cur = posixpath.join("/", p) if cur == "" else posixpath.join(cur, p)
            self._dirs.add(cur)

    def _attr(self, path: str):
        if path in self._dirs:
            return SimpleNamespace(
                filename=path.rsplit("/", 1)[-1] or path,
                st_mode=stat_mod.S_IFDIR | 0o755,
            )
        return SimpleNamespace(
            filename=path.rsplit("/", 1)[-1] or path,
            st_mode=stat_mod.S_IFREG | 0o644,
        )


class _FakeSFTPFile:
    """BytesIO-Wrapper, der als Context-Manager funktioniert (with)."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self._buf.close()

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_sftp() -> FakeSFTP:
    return FakeSFTP()


@pytest.fixture
def sftp_client(fake_sftp: FakeSFTP, monkeypatch) -> MagicMock:
    """Patcht ``paramiko.SSHClient`` so dass ``open_sftp()`` die Fake-SFTP liefert."""
    fake_client = MagicMock()
    fake_client.open_sftp.return_value = fake_sftp
    monkeypatch.setattr(paramiko, "SSHClient", lambda: fake_client)
    return fake_client


@pytest.fixture
def provider(sftp_client) -> SFTPProvider:
    """Frischer SFTPProvider mit gueltigen Test-Credentials."""
    return SFTPProvider(
        host="sftp.test.example",
        user="u123",
        password="secret-pw",
        base_path="/msm-backups",
        port=22,
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
    def test_implements_backup_provider_interface(self, provider: SFTPProvider):
        from services.backup_provider.base import BackupProvider
        assert isinstance(provider, BackupProvider)
        assert provider.name == "sftp"

    def test_constructor_rejects_empty_host(self):
        with pytest.raises(ProviderError):
            SFTPProvider(host="", user="u", password="p", base_path="/x")

    def test_constructor_rejects_empty_user(self):
        with pytest.raises(ProviderError):
            SFTPProvider(host="h", user="", password="p", base_path="/x")

    def test_constructor_rejects_empty_password(self):
        with pytest.raises(ProviderError):
            SFTPProvider(host="h", user="u", password="", base_path="/x")

    def test_constructor_rejects_empty_base_path(self):
        with pytest.raises(ProviderError):
            SFTPProvider(host="h", user="u", password="p", base_path="")

    def test_constructor_rejects_relative_base_path(self):
        with pytest.raises(ProviderError):
            SFTPProvider(
                host="h", user="u", password="p", base_path="relative/path"
            )

    def test_constructor_rejects_invalid_port_low(self):
        with pytest.raises(ProviderError):
            SFTPProvider(
                host="h", user="u", password="p", base_path="/x", port=0
            )

    def test_constructor_rejects_invalid_port_high(self):
        with pytest.raises(ProviderError):
            SFTPProvider(
                host="h", user="u", password="p", base_path="/x", port=70000
            )

    def test_default_port_is_22(self):
        p = SFTPProvider(
            host="h", user="u", password="p", base_path="/x"
        )
        assert p.port == 22

    def test_base_path_normalized(self):
        # trailing slash und ".." werden normalisiert
        p = SFTPProvider(
            host="h", user="u", password="p", base_path="/msm-backups/"
        )
        assert p.base_path == "/msm-backups"


class TestConnectionSetup:
    def test_connection_calls_load_system_host_keys(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        provider.test_connection()
        assert sftp_client.load_system_host_keys.called

    def test_connection_sets_reject_policy(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        provider.test_connection()
        # RejectPolicy wird als Argument uebergeben — MagicMock zeichnet es auf
        sftp_client.set_missing_host_key_policy.assert_called_with(
            paramiko.RejectPolicy
        )

    def test_connection_passes_no_agent_no_keyfile(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        provider.test_connection()
        kwargs = sftp_client.connect.call_args.kwargs
        assert kwargs["allow_agent"] is False
        assert kwargs["look_for_keys"] is False

    def test_connection_uses_configured_port(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        provider.test_connection()
        kwargs = sftp_client.connect.call_args.kwargs
        assert kwargs["port"] == 22
        assert kwargs["hostname"] == "sftp.test.example"
        assert kwargs["username"] == "u123"
        # Passwort wird im Klartext uebergeben (paramiko erlaubt es nicht anders);
        # wir verifizieren hier NUR dass es gesetzt ist, loggen/asserten es nicht.
        assert kwargs["password"] == "secret-pw"

    def test_test_connection_succeeds_when_base_path_exists(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        fake_sftp.add_dir("/msm-backups")
        assert provider.test_connection() is True

    def test_test_connection_creates_base_path(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        # base_path existiert noch nicht → test_connection soll es anlegen
        assert "/msm-backups" not in fake_sftp._dirs  # pre-condition
        assert provider.test_connection() is True
        assert "/msm-backups" in fake_sftp._dirs
        # mkdir wurde aufgerufen
        assert "/msm-backups" in fake_sftp.mkdir_log

    def test_test_connection_fails_on_auth_error(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        sftp_client.connect.side_effect = paramiko.AuthenticationException(
            "auth failed"
        )
        assert provider.test_connection() is False

    def test_test_connection_fails_on_ssh_error(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        sftp_client.connect.side_effect = paramiko.SSHException(
            "connection refused"
        )
        assert provider.test_connection() is False

    def test_test_connection_fails_on_sftp_error(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        sftp_client.open_sftp.side_effect = paramiko.sftp.SFTPError(
            "sftp subsystem unavailable"
        )
        assert provider.test_connection() is False

    def test_test_connection_closes_client_on_failure(
        self, sftp_client: MagicMock, provider: SFTPProvider
    ):
        sftp_client.connect.side_effect = paramiko.SSHException("nope")
        provider.test_connection()
        # Auch im Fehlerfall muss close() aufgerufen werden (kein Socket-Leak)
        assert sftp_client.close.called


class TestUploadDownload:
    def test_upload_creates_remote_file(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"backup payload")
        loc = provider.upload(src, "42/server.tar.gz.enc")
        assert loc.remote_key == "42/server.tar.gz.enc"
        assert "/msm-backups/42/server.tar.gz.enc" in fake_sftp._files
        assert fake_sftp._files["/msm-backups/42/server.tar.gz.enc"] == b"backup payload"

    def test_upload_creates_intermediate_dirs(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/deep.tar.gz.enc")
        # Zwischenverzeichnisse wurden angelegt
        for d in ("/msm-backups/1", "/msm-backups/1/2", "/msm-backups/1/2/3"):
            assert d in fake_sftp._dirs
        assert "/msm-backups/1/2/3/deep.tar.gz.enc" in fake_sftp._files

    def test_upload_is_idempotent_when_dir_exists(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        # base_path und server-dir existieren bereits (z. B. von vorherigem Upload)
        fake_sftp.add_dir("/msm-backups")
        fake_sftp.add_dir("/msm-backups/42")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        # Sollte nicht raisen trotz existierender dirs
        provider.upload(src, "42/x.enc")

    def test_download_writes_file_byte_exact(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        payload = b"x" * 12345
        src.write_bytes(payload)
        provider.upload(src, "42/server.tar.gz.enc")
        dst = tmp_path / "downloaded.bin"
        provider.download("42/server.tar.gz.enc", dst)
        assert dst.read_bytes() == payload

    def test_download_creates_intermediate_dirs(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        # Vorher uploaden, dann downloaden
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "1/2/3/server.enc")
        dst = tmp_path / "out" / "nested" / "downloaded.bin"
        provider.download("1/2/3/server.enc", dst)
        assert dst.is_file()
        assert dst.read_bytes() == b"x"

    def test_download_missing_key_raises(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        with pytest.raises(ProviderError):
            provider.download("42/missing.enc", tmp_path / "out.bin")

    def test_upload_missing_source_raises(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        with pytest.raises(ProviderError):
            provider.upload(tmp_path / "nope.bin", "42/x.enc")

    def test_progress_callback_called_for_upload(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x" * 5000)
        calls: list[int] = []

        def cb(transferred: int) -> None:
            calls.append(transferred)

        provider.upload(src, "42/x.enc", progress_cb=cb)
        # Fake ruft callback einmalig mit final size auf
        assert len(calls) == 1
        assert calls[0] == 5000

    def test_progress_callback_called_for_download(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
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
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        provider.upload(src, "42/server.tar.gz.enc")
        # Meta-File manuell anlegen
        meta_path = "/msm-backups/42/server.tar.gz.enc.meta.json"
        fake_sftp._files[meta_path] = _make_meta(42).to_json().encode("utf-8")
        assert "/msm-backups/42/server.tar.gz.enc" in fake_sftp._files
        assert meta_path in fake_sftp._files

        provider.delete("42/server.tar.gz.enc")
        assert "/msm-backups/42/server.tar.gz.enc" not in fake_sftp._files
        assert meta_path not in fake_sftp._files

    def test_delete_missing_data_still_removes_meta(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        meta_path = "/msm-backups/42/server.tar.gz.enc.meta.json"
        fake_sftp._files[meta_path] = b"{}"
        # Daten-File fehlt — delete soll trotzdem laufen (idempotent)
        provider.delete("42/server.tar.gz.enc")
        assert meta_path not in fake_sftp._files

    def test_delete_missing_both_is_noop(self, provider: SFTPProvider):
        # Kein vorheriger Upload — delete soll nicht raisen
        provider.delete("42/never-existed.enc")

    def test_delete_malformed_key_is_noop(self, provider: SFTPProvider):
        # Key mit ".." → wuerde eigentlich raisen, aber delete ist idempotent
        provider.delete("../../../etc/passwd")


class TestListMetadata:
    def test_list_metadata_returns_parsed(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        fake_sftp.add_dir("/msm-backups")
        fake_sftp.add_dir("/msm-backups/42")
        fake_sftp.add_dir("/msm-backups/43")
        fake_sftp.add_dir("/msm-backups/44")
        # Meta-Files in verschiedenen Server-Ordnern
        meta1 = _make_meta(42, "Vor Update")
        meta2 = _make_meta(43)
        fake_sftp._files["/msm-backups/42/server.tar.gz.enc.meta.json"] = (
            meta1.to_json().encode("utf-8")
        )
        fake_sftp._files["/msm-backups/43/other.tar.gz.enc.meta.json"] = (
            meta2.to_json().encode("utf-8")
        )
        # Eine Daten-Datei ohne Meta (partial write) soll ignoriert werden
        fake_sftp._files["/msm-backups/44/orphan.tar.gz.enc"] = b"x"

        results = provider.list_metadata()
        assert len(results) == 2
        ids = {m.server_id for m in results}
        assert ids == {42, 43}
        m42 = next(m for m in results if m.server_id == 42)
        assert m42.name == "Vor Update"

    def test_list_metadata_skips_broken_files(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        fake_sftp.add_dir("/msm-backups")
        fake_sftp.add_dir("/msm-backups/42")
        fake_sftp.add_dir("/msm-backups/43")
        meta = _make_meta(42)
        fake_sftp._files["/msm-backups/42/good.enc.meta.json"] = (
            meta.to_json().encode("utf-8")
        )
        # Kaputtes JSON
        fake_sftp._files["/msm-backups/43/bad.enc.meta.json"] = b"{ not valid json"

        results = provider.list_metadata()
        assert len(results) == 1
        assert results[0].server_id == 42

    def test_list_metadata_empty_base_path(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        fake_sftp.add_dir("/msm-backups")
        assert provider.list_metadata() == []

    def test_list_metadata_finds_files_in_nested_dirs(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP
    ):
        fake_sftp.add_dir("/msm-backups")
        fake_sftp.add_dir("/msm-backups/42")
        fake_sftp.add_dir("/msm-backups/42/sub")
        meta = _make_meta(42)
        fake_sftp._files["/msm-backups/42/sub/nested.tar.gz.enc.meta.json"] = (
            meta.to_json().encode("utf-8")
        )
        results = provider.list_metadata()
        assert len(results) == 1
        assert results[0].server_id == 42


class TestSecurityPathTraversal:
    def test_upload_absolute_path_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "/etc/passwd")

    def test_upload_dotdot_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "../../../etc/passwd")

    def test_upload_mixed_traversal_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        fake_sftp.add_dir("/msm-backups")
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "42/../../../etc/passwd")

    def test_download_absolute_path_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("/etc/passwd", tmp_path / "out.bin")

    def test_download_dotdot_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        with pytest.raises(ProviderError):
            provider.download("../../../etc/passwd", tmp_path / "out.bin")

    def test_empty_key_rejected(
        self, provider: SFTPProvider, fake_sftp: FakeSFTP, tmp_path: Path
    ):
        src = tmp_path / "src.bin"
        src.write_bytes(b"x")
        with pytest.raises(ProviderError):
            provider.upload(src, "")


class TestFactory:
    def test_factory_returns_sftp_provider(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "sftp")
        monkeypatch.setattr(settings, "backup_sftp_host", "sftp.test.example")
        monkeypatch.setattr(settings, "backup_sftp_port", 22)
        monkeypatch.setattr(settings, "backup_sftp_user", "u123")
        monkeypatch.setattr(settings, "backup_sftp_password", "secret")
        monkeypatch.setattr(settings, "backup_sftp_path", "/msm-backups")
        p = get_provider()
        assert p.name == "sftp"
        assert p.host == "sftp.test.example"
        assert p.user == "u123"
        assert p.base_path == "/msm-backups"

    def test_factory_rejects_sftp_without_host(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "sftp")
        monkeypatch.setattr(settings, "backup_sftp_host", "")
        monkeypatch.setattr(settings, "backup_sftp_user", "u")
        monkeypatch.setattr(settings, "backup_sftp_password", "p")
        with pytest.raises(ProviderError):
            get_provider()

    def test_factory_rejects_sftp_without_credentials(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "sftp")
        monkeypatch.setattr(settings, "backup_sftp_host", "sftp.test.example")
        monkeypatch.setattr(settings, "backup_sftp_user", "")
        monkeypatch.setattr(settings, "backup_sftp_password", "")
        with pytest.raises(ProviderError):
            get_provider()

    def test_factory_uses_default_port_22(self, monkeypatch):
        from services.backup_provider import get_provider
        from config import settings
        monkeypatch.setattr(settings, "backup_provider", "sftp")
        monkeypatch.setattr(settings, "backup_sftp_host", "sftp.test.example")
        monkeypatch.setattr(settings, "backup_sftp_port", 0)  # wird zu 22
        monkeypatch.setattr(settings, "backup_sftp_user", "u")
        monkeypatch.setattr(settings, "backup_sftp_password", "p")
        monkeypatch.setattr(settings, "backup_sftp_path", "/x")
        p = get_provider()
        assert p.port == 22
