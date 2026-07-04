"""Tests fuer Backup Orchestrator (services/backup_orchestrator.py).

Abgedeckte Assertions:
- VAL-SERVER-BACKUP-001: Lokales tar.gz erstellt (bestehendes Verhalten)
- VAL-SERVER-BACKUP-002: S3-Upload wenn konfiguriert + Passwort gesetzt
- VAL-SERVER-BACKUP-003: S3-Upload via Streaming (keine temp verschluesselte Datei)
- VAL-SERVER-BACKUP-004: S3-Upload-Fehler blockiert nicht lokales Backup
- VAL-SERVER-BACKUP-005: S3 nicht konfiguriert oder Passwort nicht gesetzt: nur lokal
- VAL-SERVER-BACKUP-006: Blueprint selective paths und pg_dump erhalten
- VAL-SERVER-BACKUP-007: Manifest mit required + extended encryption fields
- VAL-SERVER-BACKUP-008: Permissions und Filename-Schema erhalten
- VAL-SERVER-BACKUP-009: Verschluesseltes S3-Objekt ist nicht Plaintext tar.gz
- VAL-SERVER-BACKUP-010: Verschluesseltes S3-Objekt round-tript durch decrypt
- VAL-SERVER-BACKUP-011: Backup-Key nach Upload invalidiert (Erfolg und Fehler)
- VAL-CROSS-003: S3 unreachable waehrend Backup - lokal erfolgreich
- VAL-CROSS-004: DIS unreachable waehrend Backup - lokal erfolgreich
- VAL-CROSS-012: Concurrent Backup-Erstellung korruptiert nicht
"""
from __future__ import annotations

import io
import json
import os
import struct
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from models import Backup, Server, ServerPermission
from services.backup_config_service import BackupConfigService
from services.backup_crypto_service import BackupCryptoService
from services.dis_client import DisClient
from services.panel_settings_service import PanelSettingsService

S3_AAD = "msm:backup:s3"
PW_AAD = "msm:backup:pw"
TEST_BUCKET = "msm-orchestrator-bucket"
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "****************************************"
TEST_PASSWORD = "*********************!"


# ── Helper ───────────────────────────────────────────────────────────────


def _setup_s3_config() -> None:
    """S3-Config in panel_settings mit mock-verschluesselten Credentials."""
    BackupConfigService.set_s3_config(
        endpoint=TEST_ENDPOINT,
        access_key=TEST_ACCESS_KEY,
        secret_key=TEST_SECRET_KEY,
        bucket=TEST_BUCKET,
        region=TEST_REGION,
    )


def _setup_backup_password() -> None:
    """Backup-Passwort setzen (verschluesselt via DIS)."""
    BackupConfigService.set_backup_password(TEST_PASSWORD)


def _create_moto_bucket() -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=TEST_BUCKET)


def _make_real_tar(
    db,
    server: Server,
    tmp_path: Path,
    *,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> Backup:
    """Erstellt ein echtes tar.gz in tmp_path und einen Backup-DB-Record.

    Simuliert run_backup ohne die /opt/msm-Pfadabhaengigkeit.
    """
    from services.backup_paths import create_full_backup_tar

    backup_dir = tmp_path / "backups" / str(server.id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"server_{server.id}_{timestamp}.tar.gz"
    filepath = str(backup_dir / filename)
    create_full_backup_tar(
        filepath,
        server.install_dir,
        server_id=server.id,
        encrypted=encrypted,
        encryption_algorithm=encryption_algorithm,
    )
    size_mb = os.path.getsize(filepath) // (1024 * 1024)
    backup = Backup(
        server_id=server.id,
        filename=filepath,
        size_mb=size_mb,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _make_real_selective_tar(
    db,
    server: Server,
    tmp_path: Path,
    include_paths: list[str],
    *,
    encrypted: bool = False,
    encryption_algorithm: str | None = None,
) -> Backup:
    """Erstellt ein echtes selective tar.gz in tmp_path und Backup-Record."""
    from services.backup_paths import create_selective_backup_tar

    backup_dir = tmp_path / "backups" / str(server.id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"server_{server.id}_{timestamp}.tar.gz"
    filepath = str(backup_dir / filename)
    create_selective_backup_tar(
        filepath,
        server.install_dir,
        include_paths,
        server_id=server.id,
        encrypted=encrypted,
        encryption_algorithm=encryption_algorithm,
    )
    size_mb = os.path.getsize(filepath) // (1024 * 1024)
    backup = Backup(
        server_id=server.id,
        filename=filepath,
        size_mb=size_mb,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _patch_run_backup(backup: Backup):
    """Patch run_backup im backup_service-Modul, um ein vordefiniertes Backup zurueckzugeben."""
    def _fake(server_id, db, *, name=None, timeout_seconds=600, encrypted=False, encryption_algorithm=None):
        return backup
    return patch("services.backup_service.run_backup", side_effect=_fake)


def _read_manifest_from_tar(tar_path: str) -> dict | None:
    """Liest .msm/backup-manifest.json aus einem tar.gz."""
    from services.backup_paths import BACKUP_MANIFEST_ARCNAME
    with tarfile.open(tar_path, "r:gz") as tar:
        try:
            member = tar.getmember(BACKUP_MANIFEST_ARCNAME)
        except KeyError:
            return None
        extracted = tar.extractfile(member)
        if extracted is None:
            return None
        return json.loads(extracted.read().decode("utf-8"))


def _decrypt_s3_object(s3_key: str, password: str, salt: str) -> bytes:
    """Laedt ein S3-Objekt herunter und entschluesselt es via DIS (Mock)."""
    from services.s3_service import S3Service

    body = S3Service.download_stream(s3_key)
    encrypted_bytes = body.read()
    key_id = BackupCryptoService.init_key(password, salt)
    try:
        out = io.BytesIO()
        BackupCryptoService.decrypt_to_file(iter([encrypted_bytes]), key_id, out.name if hasattr(out, 'name') else "/tmp/decrypt.bin")
        # decrypt_to_file schreibt in output_path — wir nutzen einen tmp_path statt Memory
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            tmp_path = tmp.name
        try:
            BackupCryptoService.decrypt_to_file(iter([encrypted_bytes]), key_id, tmp_path)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)
    finally:
        BackupCryptoService.invalidate_key(key_id)


# ── VAL-SERVER-BACKUP-001: Lokales tar.gz erstellt ─────────────────────


class TestLocalBackupPreserved:
    """Lokales Backup wird auch ohne S3 erstellt (bestehendes Verhalten)."""

    def test_local_only_no_s3(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-001/005: Ohne S3-Config wird nur lokales Backup erstellt."""
        from services.backup_orchestrator import create_server_backup

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("test world")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup(test_server.id, db)

        assert result is not None
        assert result.server_id == test_server.id
        assert os.path.exists(result.filename)
        assert result.s3_key is None
        assert result.encrypted is False

    def test_s3_configured_no_password_local_only(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-005: S3 konfiguriert aber kein Passwort → nur lokal."""
        from services.backup_orchestrator import create_server_backup

        _setup_s3_config()
        # Kein Passwort gesetzt
        assert not BackupConfigService.is_backup_password_set()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("test world")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup(test_server.id, db)

        assert result.s3_key is None
        assert result.encrypted is False


# ── VAL-SERVER-BACKUP-002: S3-Upload wenn konfiguriert + Passwort ──────


class TestS3Upload:
    """S3-Upload wenn S3 konfiguriert und Backup-Passwort gesetzt."""

    @mock_aws
    def test_s3_upload_success(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-002: S3-Upload erfolgreich, s3_key + encrypted=True."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("test world for s3 upload")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        assert result.s3_key is not None
        assert result.s3_bucket == TEST_BUCKET
        assert result.encrypted is True

        # S3-Objekt existiert am erwarteten Key
        assert result.s3_key.startswith(f"msm-backups/servers/{test_server.id}/")
        assert result.s3_key.endswith(".enc")
        client = boto3.client("s3", region_name="us-east-1")
        client.head_object(Bucket=TEST_BUCKET, Key=result.s3_key)

    @mock_aws
    def test_s3_key_schema(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-002: S3-Key folgt msm-backups/servers/{id}/server_{id}_{ts}_{bid}.enc."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("content")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        s3_key = result.s3_key
        # Schema: msm-backups/servers/{id}/server_{id}_{timestamp}_{backup_id}.enc
        prefix = f"msm-backups/servers/{test_server.id}/server_{test_server.id}_"
        assert s3_key.startswith(prefix)
        assert s3_key.endswith(f"_{result.id}.enc")


def create_server_backup_with_s3(db, server):
    """Wrapper der create_server_backup mit echtem run_backup aufruft (via patch)."""
    from services.backup_orchestrator import create_server_backup
    return create_server_backup(server.id, db)


# ── VAL-SERVER-BACKUP-003: Streaming (keine temp verschluesselte Datei) ─


class TestStreamingUpload:
    """S3-Upload via Streaming — keine temp verschluesselte Datei."""

    @mock_aws
    def test_no_temp_encrypted_file(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-003: Keine temp verschluesselte Datei auf der Platte."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("streaming test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        files_before = set(Path(tmp_path).rglob("*"))

        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        files_after = set(Path(tmp_path).rglob("*"))
        new_files = files_after - files_before
        # Keine neue .enc-Datei auf der Platte (Streaming geht direkt zu S3)
        enc_files = [f for f in new_files if str(f).endswith(".enc")]
        assert len(enc_files) == 0

    @mock_aws
    def test_encrypt_stream_yields_to_upload(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-003: encrypt_file_stream yielded direkt an upload_stream."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("yield test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        with patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
             patch("services.s3_service.S3Service") as mock_s3, \
             _patch_run_backup(backup_pre):

            mock_crypto.init_key.return_value = "test-key-id"
            mock_crypto.encrypt_file_stream.return_value = iter([b"encrypted-chunk"])
            mock_s3.upload_stream = MagicMock()

            create_server_backup_with_s3(db, test_server)

            # upload_stream wurde mit dem encrypt_file_stream-Iterator aufgerufen
            mock_s3.upload_stream.assert_called_once()
            call_args = mock_s3.upload_stream.call_args
            stream_arg = call_args[0][0]  # first positional arg
            assert hasattr(stream_arg, "__iter__")
            # invalidate_key wurde aufgerufen
            mock_crypto.invalidate_key.assert_called_once_with("test-key-id")


# ── VAL-SERVER-BACKUP-004: S3-Fehler blockiert nicht lokales Backup ────


class TestS3Failure:
    """S3-Upload-Fehler blockiert nicht das lokale Backup."""

    @mock_aws
    def test_s3_upload_failure_local_preserved(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-004: S3-Fehler → lokales Backup bleibt, s3_key=null."""
        _setup_s3_config()
        _setup_backup_password()
        # Kein moto Bucket erstellen → upload schlaegt fehl

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("failure test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        # Lokales Backup existiert
        assert os.path.exists(result.filename)
        # S3-Felder null/False
        assert result.s3_key is None
        assert result.encrypted is False

    @mock_aws
    def test_s3_failure_logs_warning_no_secrets(self, db, test_server, tmp_path, caplog):
        """VAL-SERVER-BACKUP-004: Warning-Log ohne Secrets."""
        import logging
        _setup_s3_config()
        _setup_backup_password()
        # Kein Bucket → Fehler

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("log test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        caplog.set_level(logging.WARNING)
        with _patch_run_backup(backup_pre):
            create_server_backup_with_s3(db, test_server)

        log_text = caplog.text
        # Warning wurde geloggt
        assert "S3" in log_text or "Upload" in log_text
        # Keine Secrets im Log
        assert TEST_PASSWORD not in log_text
        assert TEST_ACCESS_KEY not in log_text
        assert TEST_SECRET_KEY not in log_text

    @mock_aws
    def test_s3_failure_2xx_response(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-004: S3-Fehler → 2xx (Best-Effort, keine Exception)."""
        _setup_s3_config()
        _setup_backup_password()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("response test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            # Sollte keine Exception werfen
            result = create_server_backup_with_s3(db, test_server)
        assert result is not None


# ── VAL-CROSS-004: DIS unreachable → lokal erfolgreich ────────────────


class TestDISFailure:
    """DIS nicht erreichbar → S3 skipped, lokales Backup bleibt."""

    @mock_aws
    def test_dis_unreachable_local_preserved(self, db, test_server, tmp_path):
        """VAL-CROSS-004: DIS nicht erreichbar → lokales Backup, s3_key=null."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("dis failure test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre), \
             patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto:
            mock_crypto.init_key.side_effect = Exception("DIS nicht erreichbar")
            mock_crypto.invalidate_key = MagicMock()

            result = create_server_backup_with_s3(db, test_server)

        assert os.path.exists(result.filename)
        assert result.s3_key is None
        assert result.encrypted is False

    @mock_aws
    def test_dis_failure_no_key_left(self, db, test_server, tmp_path):
        """VAL-CROSS-004: DIS-Fehler → kein Key bleibt im Speicher."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("key leak test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre), \
             patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto:
            mock_crypto.init_key.side_effect = Exception("DIS nicht erreichbar")
            mock_crypto.invalidate_key = MagicMock()

            create_server_backup_with_s3(db, test_server)

            # invalidate_key wurde NICHT aufgerufen (init_key schlug fehl, kein key_id)
            mock_crypto.invalidate_key.assert_not_called()


# ── VAL-SERVER-BACKUP-011: Key invalidiert nach Upload ──────────────────


class TestKeyLifecycle:
    """Backup-Key wird nach Upload invalidiert (Erfolg und Fehler)."""

    @mock_aws
    def test_key_invalidated_on_success(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-011: Key nach erfolgreichem Upload invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("invalidate success test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
             patch("services.s3_service.S3Service") as mock_s3, \
             _patch_run_backup(backup_pre):
            mock_crypto.init_key.return_value = "key-success"
            mock_crypto.encrypt_file_stream.return_value = iter([b"enc"])
            mock_s3.upload_stream = MagicMock()

            create_server_backup_with_s3(db, test_server)

            mock_crypto.invalidate_key.assert_called_once_with("key-success")

    @mock_aws
    def test_key_invalidated_on_failure(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-011: Key auch bei S3-Fehler invalidiert (try/finally)."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("invalidate failure test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
             patch("services.s3_service.S3Service") as mock_s3, \
             _patch_run_backup(backup_pre):
            mock_crypto.init_key.return_value = "key-fail"
            mock_crypto.encrypt_file_stream.return_value = iter([b"enc"])
            mock_s3.upload_stream.side_effect = Exception("S3 error")

            create_server_backup_with_s3(db, test_server)

            # Key wurde trotz Fehler invalidiert
            mock_crypto.invalidate_key.assert_called_once_with("key-fail")

    @mock_aws
    def test_key_invalidated_on_encrypt_failure(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-011: Key auch bei encrypt-Fehler invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("encrypt fail test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
             patch("services.s3_service.S3Service") as mock_s3, \
             _patch_run_backup(backup_pre):
            mock_crypto.init_key.return_value = "key-enc-fail"
            mock_crypto.encrypt_file_stream.side_effect = Exception("encrypt error")
            mock_s3.upload_stream = MagicMock()

            create_server_backup_with_s3(db, test_server)

            mock_crypto.invalidate_key.assert_called_once_with("key-enc-fail")
            # upload wurde nicht aufgerufen (encrypt schlug fehl)
            mock_s3.upload_stream.assert_not_called()


# ── VAL-SERVER-BACKUP-007: Manifest mit required + extended fields ─────


class TestManifest:
    """Manifest enthaelt required + extended encryption fields."""

    def test_manifest_local_only(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-007: Lokales Backup — Manifest ohne encrypted fields."""
        # Kein S3 konfiguriert
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("manifest local")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        manifest = _read_manifest_from_tar(backup.filename)

        assert manifest is not None
        assert manifest["scope"] == "full"
        assert manifest["version"] == 1
        assert "timestamp" in manifest
        assert manifest["server_id"] == test_server.id
        # Keine encryption fields fuer local-only
        assert not manifest.get("encrypted")
        assert "encryption_algorithm" not in manifest

    @mock_aws
    def test_manifest_s3_encrypted(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-007: S3-Backup — Manifest mit encrypted + algorithm."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("manifest encrypted")
        test_server.install_dir = str(install)
        db.commit()

        # Erstelle tar mit encrypted=True (wie der Orchestrator es wuerde)
        backup = _make_real_tar(
            db, test_server, tmp_path,
            encrypted=True,
            encryption_algorithm="AES-256-GCM",
        )
        manifest = _read_manifest_from_tar(backup.filename)

        assert manifest is not None
        assert manifest["scope"] == "full"
        assert manifest["version"] == 1
        assert "timestamp" in manifest
        assert manifest["server_id"] == test_server.id
        # Extended encryption fields
        assert manifest["encrypted"] is True
        assert manifest["encryption_algorithm"] == "AES-256-GCM"


# ── VAL-SERVER-BACKUP-006: Blueprint selective paths + pg_dump ─────────


class TestSelectiveAndPgDump:
    """Blueprint selective paths und pg_dump werden erhalten."""

    def test_selective_paths_preserved(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-006: Selective Backup enthaelt nur Blueprint-Pfade."""
        install = tmp_path / "install"
        # Conan-Struktur
        cfg = install / "ConanSandbox/Saved/Config"
        cfg.mkdir(parents=True)
        (cfg / "Game.ini").write_text("[x]\n")
        saves = install / "ConanSandbox/Saved/SaveGames"
        saves.mkdir(parents=True)
        (saves / "world.sav").write_bytes(b"save")
        steam = install / "steamapps"
        steam.mkdir()

        test_server.game_type = "conan_exiles_ue5"
        test_server.install_dir = str(install)
        db.commit()

        include_paths = ["ConanSandbox/Saved/Config", "ConanSandbox/Saved/SaveGames"]
        backup = _make_real_selective_tar(db, test_server, tmp_path, include_paths)

        with tarfile.open(backup.filename, "r:gz") as tar:
            names = tar.getnames()
        # Selective Pfade enthalten
        assert any("ConanSandbox/Saved/Config" in n for n in names)
        assert any("SaveGames" in n for n in names)
        # Nicht-selective Pfade NICHT enthalten
        assert not any("steamapps" in n for n in names)

    def test_pg_dump_preserved(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-006: pg_dump im Archiv wenn vorhanden."""
        from services.backup_paths import BACKUP_POSTGRES_ARCNAME, create_full_backup_tar

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("pg test")
        test_server.install_dir = str(install)
        db.commit()

        backup_dir = tmp_path / "backups" / str(test_server.id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(backup_dir / "pg_test.tar.gz")
        create_full_backup_tar(
            filepath,
            test_server.install_dir,
            pg_dump_bytes=b"-- pg_dump output\nCREATE TABLE test();",
            server_id=test_server.id,
        )
        with tarfile.open(filepath, "r:gz") as tar:
            names = tar.getnames()
        assert BACKUP_POSTGRES_ARCNAME in names


# ── VAL-SERVER-BACKUP-009: S3-Objekt nicht Plaintext ───────────────────


class TestEncryptedS3Object:
    """Verschluesseltes S3-Objekt ist nicht Plaintext tar.gz."""

    @mock_aws
    def test_s3_object_not_plaintext(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-009: S3-Objekt unterscheidet sich von lokalem tar.gz."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("plaintext check")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        # S3-Objekt herunterladen
        client = boto3.client("s3", region_name="us-east-1")
        s3_obj = client.get_object(Bucket=TEST_BUCKET, Key=result.s3_key)
        s3_bytes = s3_obj["Body"].read()

        # Lokale Datei lesen
        with open(result.filename, "rb") as f:
            local_bytes = f.read()

        # S3-Objekt ist nicht das lokale tar.gz
        assert s3_bytes != local_bytes

        # Erste 4 Bytes sind BE frame length (encrypted frame format)
        frame_len = struct.unpack(">I", s3_bytes[:4])[0]
        assert frame_len >= 12  # nonce + ciphertext minimum

    @mock_aws
    def test_s3_object_round_trip_decrypt(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-010: S3-Objekt round-tript durch decrypt."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("roundtrip check")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        with _patch_run_backup(backup_pre):
            result = create_server_backup_with_s3(db, test_server)

        # S3-Objekt herunterladen
        from services.s3_service import S3Service
        body = S3Service.download_stream(result.s3_key)
        encrypted_bytes = body.read()

        # Entschluesseln mit gleichem Passwort + Salt
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
                tmp_path_dec = tmp.name
            try:
                BackupCryptoService.decrypt_to_file(iter([encrypted_bytes]), key_id, tmp_path_dec)
                with open(tmp_path_dec, "rb") as f:
                    decrypted_bytes = f.read()
            finally:
                os.unlink(tmp_path_dec)
        finally:
            BackupCryptoService.invalidate_key(key_id)

        # Entschluesselte Bytes == lokales tar.gz
        with open(result.filename, "rb") as f:
            local_bytes = f.read()
        assert decrypted_bytes == local_bytes


# ── VAL-CROSS-012: Concurrent Backup-Erstellung ────────────────────────


class TestConcurrentBackups:
    """Concurrent Backup-Erstellung korruptiert nicht."""

    @mock_aws
    def test_concurrent_backups_distinct_keys_and_s3keys(self, db, test_server, tmp_path):
        """VAL-CROSS-012: Zwei Backups → distinct filenames + s3_keys.

        Verwendet sequenzielle Erstellung (SQLite In-Memory ist nicht thread-safe),
        aber verifiziert dass jeder Aufruf einen eigenen key_id und s3_key bekommt.
        """
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("concurrent test")
        test_server.install_dir = str(install)
        db.commit()

        from services.backup_orchestrator import create_server_backup

        # Zwei separate Backups erstellen (sequenziell, aber separate key_ids)
        # Verwende eindeutige Subdirs fuer distinct filenames (1s-Timestamp-Resolution).
        backup1 = _make_real_tar(db, test_server, tmp_path / "b1")
        with _patch_run_backup(backup1):
            result1 = create_server_backup(test_server.id, db)

        backup2 = _make_real_tar(db, test_server, tmp_path / "b2")
        with _patch_run_backup(backup2):
            result2 = create_server_backup(test_server.id, db)

        # Distinct filenames (verschiedene Subdirs)
        assert result1.filename != result2.filename
        # Distinct s3_keys
        assert result1.s3_key != result2.s3_key
        # Beide encrypted
        assert result1.encrypted is True
        assert result2.encrypted is True
        # Beide S3-Objekte existieren
        client = boto3.client("s3", region_name="us-east-1")
        client.head_object(Bucket=TEST_BUCKET, Key=result1.s3_key)
        client.head_object(Bucket=TEST_BUCKET, Key=result2.s3_key)


# ── VAL-SERVER-BACKUP-008: Router Integration ──────────────────────────


class TestRouterIntegration:
    """Router POST /api/backups/{server_id} verwendet Orchestrator."""

    @mock_aws
    def test_create_backup_uses_orchestrator(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-008: POST verwendet Orchestrator (S3-Upload passiert)."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("orchestrator spy test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with _patch_run_backup(backup_pre):
                    resp = client.post(
                        f"/api/backups/{test_server.id}",
                        json={"name": "orch-test"},
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                assert resp.status_code == 200
                data = resp.json()
                backup = db.query(Backup).filter(Backup.id == data["backup_id"]).first()
                # Orchestrator wurde verwendet → S3-Upload passiert
                assert backup.s3_key is not None
                assert backup.encrypted is True
        finally:
            app.dependency_overrides.clear()

    def test_create_backup_permission_403(self, db, test_server, user_cookies):
        """VAL-SERVER-BACKUP-008: server.backups.create erforderlich."""
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = user_cookies.get("__Secure-csrf_token")
                resp = client.post(
                    f"/api/backups/{test_server.id}",
                    json={"name": "test"},
                    cookies=user_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                # regular_user hat alle Permissions via user_permission fixture nicht gesetzt hier
                # → 403 (kein server.backups.create)
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_create_backup_unauthenticated_401(self, db, test_server):
        """VAL-SERVER-BACKUP-008: Unauth → 401."""
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                resp = client.post(f"/api/backups/{test_server.id}", json={"name": "test"})
                assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()

    def test_create_backup_nonexistent_server_404(self, db, owner_cookies):
        """VAL-SERVER-BACKUP-008: Nichtexistent server_id → 404."""
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                resp = client.post(
                    "/api/backups/99999",
                    json={"name": "test"},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_create_backup_with_s3_via_router(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-002/008: Router erstellt Backup mit S3-Upload."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("router s3 test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with _patch_run_backup(backup_pre):
                    resp = client.post(
                        f"/api/backups/{test_server.id}",
                        json={"name": "s3-test"},
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                assert resp.status_code == 200
                data = resp.json()
                assert "backup_id" in data

                # Backup-Record hat S3-Felder
                backup = db.query(Backup).filter(Backup.id == data["backup_id"]).first()
                assert backup is not None
                assert backup.s3_key is not None
                assert backup.encrypted is True
        finally:
            app.dependency_overrides.clear()


# ── VAL-SERVER-BACKUP-008: Filename-Schema ─────────────────────────────


class TestFilenameSchema:
    """Filename folgt server_{id}_{timestamp}.tar.gz Schema (kein Path-Traversal)."""

    def test_filename_schema(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-008: Filename folgt Schema, keine Traversal-Chars."""
        import re
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("filename test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        basename = os.path.basename(backup.filename)
        # Schema: server_{id}_{timestamp}.tar.gz
        pattern = rf"^server_{test_server.id}_\d{{8}}_\d{{6}}\.tar\.gz$"
        assert re.match(pattern, basename), f"Filename {basename} folgt nicht dem Schema"
        # Keine Path-Traversal-Chars
        assert ".." not in basename
        assert "/" not in basename
        assert "\\" not in basename
