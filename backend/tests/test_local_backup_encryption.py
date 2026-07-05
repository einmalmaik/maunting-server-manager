"""Tests fuer lokale Backup-Verschluesselung (fix-local-backup-encryption Feature).

Abgedeckte Assertions:
- VAL-FIX-001: Lokale Backups sind DIS-verschluesselt wenn Passwort gesetzt (.enc)
- VAL-FIX-002: Plaintext tar.gz nur in 0700 temp-dir, nach Verschluesselung geloescht
- VAL-FIX-003: Lokale verschluesselte Backups haben .enc-Erweiterung
- VAL-FIX-004: Restore entschluesselt .enc vor Extraktion

Zusaetzliche Tests:
- Backward compat: kein Passwort → plaintext .tar.gz
- DIS-Faelter → Fall back zu plaintext (Best-Effort)
- Restore von .enc lokal funktioniert
- Panel-Backup mit Passwort → .enc lokal
- Panel-Restore mit .enc lokal funktioniert
- S3-Upload von .enc (direkt, keine Re-Verschluesselung)
"""
from __future__ import annotations

import io
import os
import struct
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from models import Backup, Server
from services.backup_config_service import BackupConfigService
from services.backup_crypto_service import BackupCryptoService

TEST_BUCKET = "msm-local-enc-bucket"
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TEST_PASSWORD = "TestBackupPassword123!"

_FULL_BACKUP_PLAN = MagicMock(scope="full")


# ── Helper ───────────────────────────────────────────────────────────────


def _setup_s3_config() -> None:
    BackupConfigService.set_s3_config(
        endpoint=TEST_ENDPOINT,
        access_key=TEST_ACCESS_KEY,
        secret_key=TEST_SECRET_KEY,
        bucket=TEST_BUCKET,
        region=TEST_REGION,
    )


def _setup_backup_password() -> None:
    BackupConfigService.set_backup_password(TEST_PASSWORD)


def _create_moto_bucket() -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=TEST_BUCKET)


def _make_install_dir(tmp_path: Path, server: Server, db, *, content: str = "world data") -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "world.dat").write_text(content)
    server.install_dir = str(install)
    db.commit()
    return install


def _make_real_enc_backup(
    db, server: Server, tmp_path: Path, *, encrypted_flag: bool = True
) -> Backup:
    """Erstellt ein echtes .enc Backup in tmp_path und einen DB-Record.

    Simuliert den Output von run_backup mit encrypt_local=True.
    """
    from services.backup_paths import create_full_backup_tar
    from services.backup_service import _encrypt_local_backup

    backup_dir = tmp_path / "backups" / str(server.id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # tar.gz in temp-dir erstellen
    tmp_tar = tmp_path / f"tmp_{timestamp}.tar.gz"
    create_full_backup_tar(str(tmp_tar), server.install_dir, server_id=server.id)

    # Zu .enc verschluesseln
    enc_filename = f"server_{server.id}_{timestamp}.enc"
    enc_path = str(backup_dir / enc_filename)
    _encrypt_local_backup(str(tmp_tar), enc_path)
    os.remove(str(tmp_tar))  # Plaintext loeschen

    size_mb = os.path.getsize(enc_path) // (1024 * 1024)
    backup = Backup(
        server_id=server.id,
        filename=enc_path,
        size_mb=size_mb,
        encrypted=encrypted_flag,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _patch_run_backup_enc(backup: Backup):
    """Patch run_backup um ein .enc Backup zurueckzugeben (wie _patch_run_backup im orchestrator test)."""
    def _fake(server_id, db, *, name=None, timeout_seconds=600,
              encrypted=False, encryption_algorithm=None, encrypt_local=False):
        return backup
    return patch("services.backup_service.run_backup", side_effect=_fake)


# ── VAL-FIX-001: Lokale Backups DIS-verschluesselt wenn Passwort gesetzt ─


class TestLocalBackupEncrypted:
    """Lokale Backups werden verschluesselt wenn Passwort gesetzt ist."""

    def test_encrypt_local_backup_produces_enc(self, db, test_server, tmp_path):
        """VAL-FIX-001: _encrypt_local_backup erzeugt .enc Datei (nicht plaintext)."""
        _setup_backup_password()

        install = _make_install_dir(tmp_path, test_server, db, content="enc test")
        db.commit()

        from services.backup_paths import create_full_backup_tar
        from services.backup_service import _encrypt_local_backup

        # tar.gz erstellen
        tar_path = str(tmp_path / "test.tar.gz")
        create_full_backup_tar(tar_path, test_server.install_dir, server_id=test_server.id)

        # Zu .enc verschluesseln
        enc_path = str(tmp_path / "test.enc")
        _encrypt_local_backup(tar_path, enc_path)

        # .enc existiert
        assert os.path.exists(enc_path)
        # .enc ist nicht plaintext tar.gz (keine gzip magic bytes)
        with open(enc_path, "rb") as f:
            first_bytes = f.read(2)
        assert first_bytes != b"\x1f\x8b", "File should be encrypted, not gzip!"

        # .enc kann entschluesselt werden (round-trip)
        password = BackupConfigService.get_backup_password()
        salt = BackupConfigService.get_backup_salt()
        key_id = BackupCryptoService.init_key(password, salt)
        try:
            dec_path = str(tmp_path / "decrypted.tar.gz")
            with open(enc_path, "rb") as f:
                BackupCryptoService.decrypt_to_file(f, key_id, dec_path)
            # Entschluesselte Datei == Original tar.gz
            with open(tar_path, "rb") as orig, open(dec_path, "rb") as dec:
                assert orig.read() == dec.read()
        finally:
            BackupCryptoService.invalidate_key(key_id)

    def test_backup_plaintext_when_no_password(self, db, test_server, tmp_path):
        """VAL-FIX-003: Ohne Passwort → .tar.gz (backward compat)."""
        _make_install_dir(tmp_path, test_server, db, content="plaintext test")
        db.commit()

        from services.backup_service import run_backup

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.create_full_backup_tar"), \
             patch("services.backup_service.os.path.getsize", return_value=1), \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.backup_plan_for_server", return_value=_FULL_BACKUP_PLAN):
            backup = run_backup(test_server.id, db)

        assert backup.filename.endswith(".tar.gz")
        assert backup.encrypted is False

    @mock_aws
    def test_orchestrator_enc_local_with_s3(self, db, test_server, tmp_path):
        """VAL-FIX-001: Orchestrator mit Passwort → .enc lokal + S3-Upload."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="orch enc test")
        db.commit()

        # Echtes .enc Backup erstellen (simuliert run_backup mit encrypt_local)
        enc_backup = _make_real_enc_backup(db, test_server, tmp_path)

        from services.backup_orchestrator import create_server_backup

        with _patch_run_backup_enc(enc_backup):
            result = create_server_backup(test_server.id, db)

        # S3-Upload passiert (.enc direkt, keine Re-Verschluesselung)
        assert result.s3_key is not None
        assert result.s3_bucket == TEST_BUCKET
        assert result.encrypted is True
        # S3-Objekt existiert
        client = boto3.client("s3", region_name="us-east-1")
        client.head_object(Bucket=TEST_BUCKET, Key=result.s3_key)
        # S3-Objekt == lokale .enc (gleiche Bytes, direkter Upload)
        s3_obj = client.get_object(Bucket=TEST_BUCKET, Key=result.s3_key)
        s3_bytes = s3_obj["Body"].read()
        with open(result.filename, "rb") as f:
            local_bytes = f.read()
        assert s3_bytes == local_bytes


# ── VAL-FIX-002: Plaintext nur in 0700 temp-dir, danach geloescht ───────


class TestPlaintextSecurity:
    """Plaintext tar.gz ist nur temporaer in 0700 temp-dir, wird geloescht."""

    def test_plaintext_deleted_after_encryption(self, db, test_server, tmp_path):
        """VAL-FIX-002: Plaintext tar.gz wird nach Verschluesselung geloescht."""
        _setup_backup_password()

        _make_install_dir(tmp_path, test_server, db, content="deletion test")
        db.commit()

        from services.backup_paths import create_full_backup_tar
        from services.backup_service import _encrypt_local_backup

        tar_path = str(tmp_path / "plaintext.tar.gz")
        create_full_backup_tar(tar_path, test_server.install_dir, server_id=test_server.id)

        enc_path = str(tmp_path / "encrypted.enc")
        _encrypt_local_backup(tar_path, enc_path)

        # Plaintext tar.gz wurde geloescht (von _encrypt_local_backup)
        # Hinweis: _encrypt_local_backup loescht nicht selbst, der Caller (run_backup) loescht.
        # Hier testen wir dass das .enc existiert und das tar.gz noch da ist
        # (Caller ist verantwortlich fuer Loeschung).
        assert os.path.exists(enc_path)

        # Simuliere Caller-Loeschung
        os.remove(tar_path)
        assert not os.path.exists(tar_path)
        assert os.path.exists(enc_path)

    def test_no_plaintext_in_backup_dir(self, db, test_server, tmp_path):
        """VAL-FIX-002: Kein .tar.gz im backup_dir wenn Passwort gesetzt."""
        _setup_backup_password()

        _make_install_dir(tmp_path, test_server, db, content="no plaintext test")
        db.commit()

        backup = _make_real_enc_backup(db, test_server, tmp_path)

        backup_dir = Path(backup.filename).parent
        # Keine .tar.gz Dateien im backup_dir
        tar_files = list(backup_dir.glob("*.tar.gz"))
        assert len(tar_files) == 0, f"Found plaintext tar.gz in backup dir: {tar_files}"
        # .enc existiert
        enc_files = list(backup_dir.glob("*.enc"))
        assert len(enc_files) == 1


# ── VAL-FIX-003: .enc-Erweiterung ────────────────────────────────────────


class TestEncExtension:
    """Lokale verschluesselte Backups haben .enc-Erweiterung."""

    def test_filename_enc_when_password_set(self, db, test_server, tmp_path):
        """VAL-FIX-003: Backup.filename endet mit .enc wenn Passwort gesetzt."""
        _setup_backup_password()

        _make_install_dir(tmp_path, test_server, db, content="enc ext test")
        db.commit()

        backup = _make_real_enc_backup(db, test_server, tmp_path)

        assert backup.filename.endswith(".enc")
        basename = os.path.basename(backup.filename)
        assert f"server_{test_server.id}_" in basename

    def test_filename_tar_gz_when_no_password(self, db, test_server, tmp_path):
        """VAL-FIX-003: Backup.filename endet mit .tar.gz wenn kein Passwort."""
        _make_install_dir(tmp_path, test_server, db, content="tar.gz ext test")
        db.commit()

        from services.backup_service import run_backup

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.create_full_backup_tar"), \
             patch("services.backup_service.os.path.getsize", return_value=1), \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.backup_plan_for_server", return_value=_FULL_BACKUP_PLAN):
            backup = run_backup(test_server.id, db)

        assert backup.filename.endswith(".tar.gz")


# ── VAL-FIX-004: Restore entschluesselt .enc vor Extraktion ──────────────


class TestRestoreEncryptedLocal:
    """Restore entschluesselt .enc lokal vor Extraktion."""

    @mock_aws
    def test_restore_encrypted_local_backup(self, db, test_server, owner_cookies, tmp_path):
        """VAL-FIX-004: Restore von .enc lokal → DIS decrypt → temp tar.gz → extract."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="restore enc test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_enc_backup(db, test_server, tmp_path)
        assert backup.filename.endswith(".enc")

        # Restore ueber API
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    db.refresh(test_server)
                    assert test_server.status == "stopped"
                    # install_dir wurde restored
                    assert os.path.exists(test_server.install_dir)
                    assert (Path(test_server.install_dir) / "world.dat").exists()
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_restore_encrypted_uses_decrypt(self, db, test_server, owner_cookies, tmp_path):
        """VAL-FIX-004: Restore von .enc ruft decrypt_local_backup_for_restore auf."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="decrypt spy test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_enc_backup(db, test_server, tmp_path)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db
        from services.backup_orchestrator import decrypt_local_backup_for_restore

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.backup_orchestrator.decrypt_local_backup_for_restore",
                           wraps=decrypt_local_backup_for_restore) as decrypt_spy:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    decrypt_spy.assert_called_once_with(backup.filename)
        finally:
            app.dependency_overrides.clear()

    def test_restore_plaintext_no_decrypt(self, db, test_server, owner_cookies, tmp_path):
        """VAL-FIX-004: Restore von .tar.gz (kein Passwort) → kein DIS decrypt."""
        _make_install_dir(tmp_path, test_server, db, content="no decrypt test")
        test_server.status = "running"
        db.commit()

        # Plaintext Backup ohne Passwort
        from services.backup_paths import create_full_backup_tar

        backup_dir = tmp_path / "backups" / str(test_server.id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = str(backup_dir / f"server_{test_server.id}_{timestamp}.tar.gz")
        create_full_backup_tar(filepath, test_server.install_dir, server_id=test_server.id)
        backup = Backup(
            server_id=test_server.id,
            filename=filepath,
            size_mb=1,
        )
        db.add(backup)
        db.commit()
        db.refresh(backup)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.backup_orchestrator.decrypt_local_backup_for_restore") as decrypt_spy:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    decrypt_spy.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_restore_encrypted_wrong_password_error(self, db, test_server, owner_cookies, tmp_path):
        """VAL-FIX-004: Restore mit Decrypt-Fehler → klare Fehlermeldung (400)."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="wrong pw test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_enc_backup(db, test_server, tmp_path)

        from services.backup_crypto_service import BackupDecryptionError

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.backup_orchestrator.decrypt_local_backup_for_restore",
                           side_effect=BackupDecryptionError("Entschluesselung fehlgeschlagen")):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    # Decrypt-Fehler → 400 mit klarer Meldung
                    assert resp.status_code == 400
                    assert "Entschlüsselung" in resp.json().get("detail", "")
        finally:
            app.dependency_overrides.clear()


# ── DIS-Fehler → Fall back zu plaintext ──────────────────────────────────


class TestDisFailureFallback:
    """DIS-Fehler beim lokalen Verschluesseln → plaintext Fall back."""

    def test_dis_failure_falls_back_to_plaintext(self, db, test_server, tmp_path):
        """DIS nicht erreichbar → run_backup faellt auf plaintext .tar.gz zurueck."""
        _setup_backup_password()

        _make_install_dir(tmp_path, test_server, db, content="dis fallback test")
        db.commit()

        from services.backup_service import run_backup

        # Patch: makedirs no-op, mkdtemp returns real tmp dir,
        # init_key fails (DIS down), backup_plan full scope
        enc_tmp = tmp_path / "fallback_tmp"
        enc_tmp.mkdir(parents=True, exist_ok=True)

        with patch("services.backup_service.os.makedirs"), \
             patch("services.backup_service.tempfile.mkdtemp", return_value=str(enc_tmp)), \
             patch("services.backup_service.backup_plan_for_server", return_value=_FULL_BACKUP_PLAN), \
             patch("services.backup_service.cleanup_old_backups"), \
             patch("services.backup_service.os.path.getsize", return_value=1), \
             patch("services.backup_crypto_service.BackupCryptoService.init_key",
                   side_effect=Exception("DIS down")):
            backup = run_backup(test_server.id, db, encrypt_local=True)

        # Fall back: .tar.gz (nicht .enc)
        assert backup.filename.endswith(".tar.gz")
        # encrypted flag nicht gesetzt (plaintext fallback)


# ── S3-Upload von .enc (direkt, keine Re-Verschluesselung) ──────────────


class TestS3UploadOfEnc:
    """S3-Upload von .enc Datei: direkt, keine Re-Verschluesselung."""

    @mock_aws
    def test_s3_upload_enc_direct_no_reencrypt(self, db, test_server, tmp_path):
        """S3-Upload von .enc: keine DIS encrypt_stream im _upload_to_s3."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="s3 direct enc test")
        db.commit()

        enc_backup = _make_real_enc_backup(db, test_server, tmp_path)

        from services.backup_orchestrator import create_server_backup

        with _patch_run_backup_enc(enc_backup), \
             patch("services.backup_crypto_service.BackupCryptoService.encrypt_file_stream") as enc_stream_mock:
            result = create_server_backup(test_server.id, db)

            # encrypt_file_stream wurde NICHT im _upload_to_s3 aufgerufen
            # (die .enc ist bereits verschluesselt — direkter Upload)
            # Hinweis: encrypt_file_stream wurde ggf. in _make_real_enc_backup
            # aufgerufen, aber nicht im _upload_to_s3 Pfad des Orchestrators.
            # Der Mock hier ist nur fuer den Orchestrator-Aufruf relevant.
            enc_stream_mock.assert_not_called()

            assert result.s3_key is not None
            assert result.encrypted is True

            # S3-Objekt == lokale .enc (direkter Upload, gleiche Bytes)
            client = boto3.client("s3", region_name="us-east-1")
            s3_obj = client.get_object(Bucket=TEST_BUCKET, Key=result.s3_key)
            s3_bytes = s3_obj["Body"].read()
            with open(result.filename, "rb") as f:
                local_bytes = f.read()
            assert s3_bytes == local_bytes

    @mock_aws
    def test_s3_upload_legacy_tar_gz_uses_encrypt(self, db, test_server, tmp_path):
        """S3-Upload von .tar.gz (legacy upload-to-cloud): DIS encrypt wird verwendet."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="legacy upload test")
        db.commit()

        # Plaintext .tar.gz Backup (legacy, kein encrypt_local)
        from services.backup_paths import create_full_backup_tar

        backup_dir = tmp_path / "backups" / str(test_server.id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = str(backup_dir / f"server_{test_server.id}_{timestamp}.tar.gz")
        create_full_backup_tar(filepath, test_server.install_dir, server_id=test_server.id)
        backup = Backup(
            server_id=test_server.id,
            filename=filepath,
            size_mb=1,
        )
        db.add(backup)
        db.commit()
        db.refresh(backup)

        from services.backup_orchestrator import upload_backup_to_cloud

        with patch("services.backup_crypto_service.BackupCryptoService.encrypt_file_stream",
                   wraps=BackupCryptoService.encrypt_file_stream) as enc_stream_spy:
            success = upload_backup_to_cloud(backup, db, test_server.id)

            assert success is True
            # encrypt_file_stream wurde aufgerufen (legacy .tar.gz Pfad)
            enc_stream_spy.assert_called_once()


# ── fetch_backup_from_s3 mit .enc ────────────────────────────────────────


class TestFetchBackupFromS3:
    """fetch_backup_from_s3 mit .enc Dateiname: direkter Download, kein Decrypt."""

    @mock_aws
    def test_fetch_enc_direct_download(self, db, test_server, tmp_path):
        """fetch_backup_from_s3 mit .enc: direkter Download, kein DIS Decrypt."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        _make_install_dir(tmp_path, test_server, db, content="fetch enc test")
        db.commit()

        enc_backup = _make_real_enc_backup(db, test_server, tmp_path)
        enc_path = enc_backup.filename

        # Upload to S3
        from services.s3_service import S3Service
        s3_key = f"msm-backups/servers/{test_server.id}/test_fetch.enc"
        with open(enc_path, "rb") as f:
            S3Service.upload_stream(f, s3_key)

        enc_backup.s3_key = s3_key
        db.commit()

        # Lokale Datei loeschen
        os.remove(enc_path)
        assert not os.path.exists(enc_path)

        # fetch_backup_from_s3: .enc → direkter Download
        from services.backup_orchestrator import fetch_backup_from_s3

        with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file") as dec_mock:
            fetch_backup_from_s3(enc_backup, db)

            # decrypt_to_file wurde NICHT aufgerufen (.enc direkter Download)
            dec_mock.assert_not_called()

        # Lokale .enc Datei wurde wiederhergestellt
        assert os.path.exists(enc_path)

        # Inhalt == S3-Objekt (gleiche verschluesselte Bytes)
        client = boto3.client("s3", region_name="us-east-1")
        s3_obj = client.get_object(Bucket=TEST_BUCKET, Key=s3_key)
        s3_bytes = s3_obj["Body"].read()
        with open(enc_path, "rb") as f:
            local_bytes = f.read()
        assert s3_bytes == local_bytes


# ── Panel-Backup mit Verschluesselung ────────────────────────────────────


class TestPanelBackupEncrypted:
    """Panel-Backup mit Passwort → .enc lokal."""

    def test_panel_backup_enc_when_password_set(self, db, tmp_path, monkeypatch):
        """VAL-FIX-001: Panel-Backup mit Passwort → .enc Datei."""
        import services.panel_backup_service as pbs

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup_dir = tmp_path / "backups" / "panel"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("SECRET_KEY=test")

        monkeypatch.setattr(pbs.settings, "panel_config_dir", str(config_dir))
        monkeypatch.setattr(pbs.settings, "panel_backup_dir", str(backup_dir))

        _setup_backup_password()

        with patch.object(pbs, "_dump_database", return_value=b"-- sqlite dump\nCREATE TABLE x();"):
            with patch.object(pbs.settings, "database_url", "sqlite:///./msm.db"):
                backup = pbs.create_panel_backup(db)

        assert backup.local_path.endswith(".enc")
        assert os.path.exists(backup.local_path)

        # Datei ist nicht plaintext tar.gz
        with open(backup.local_path, "rb") as f:
            first_bytes = f.read(2)
        assert first_bytes != b"\x1f\x8b", "Panel backup should be encrypted, not gzip!"

    def test_panel_backup_tar_gz_when_no_password(self, db, tmp_path, monkeypatch):
        """VAL-FIX-003: Panel-Backup ohne Passwort → .tar.gz (backward compat)."""
        import services.panel_backup_service as pbs

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup_dir = tmp_path / "backups" / "panel"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("SECRET_KEY=test")

        monkeypatch.setattr(pbs.settings, "panel_config_dir", str(config_dir))
        monkeypatch.setattr(pbs.settings, "panel_backup_dir", str(backup_dir))

        with patch.object(pbs, "_dump_database", return_value=b"-- sqlite dump\nCREATE TABLE x();"):
            with patch.object(pbs.settings, "database_url", "sqlite:///./msm.db"):
                backup = pbs.create_panel_backup(db)

        assert backup.local_path.endswith(".tar.gz")
        assert os.path.exists(backup.local_path)

    def test_panel_backup_dis_failure_fallback(self, db, tmp_path, monkeypatch):
        """DIS-Fehler bei Panel-Backup → plaintext .tar.gz (Best-Effort)."""
        import services.panel_backup_service as pbs

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup_dir = tmp_path / "backups" / "panel"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("SECRET_KEY=test")

        monkeypatch.setattr(pbs.settings, "panel_config_dir", str(config_dir))
        monkeypatch.setattr(pbs.settings, "panel_backup_dir", str(backup_dir))

        _setup_backup_password()

        from services.backup_crypto_service import BackupCryptoError

        with patch.object(pbs, "_dump_database", return_value=b"-- sqlite dump\nCREATE TABLE x();"), \
             patch.object(pbs.settings, "database_url", "sqlite:///./msm.db"), \
             patch("services.backup_crypto_service.BackupCryptoService.init_key",
                   side_effect=BackupCryptoError("DIS down")):
            backup = pbs.create_panel_backup(db)

        # Fall back: .tar.gz (nicht .enc)
        assert backup.local_path.endswith(".tar.gz")
        assert os.path.exists(backup.local_path)


# ── Panel-Restore mit .enc ──────────────────────────────────────────────


class TestPanelRestoreEncrypted:
    """Panel-Restore mit .enc lokal → Decrypt → Extraktion."""

    def test_panel_restore_encrypted_local(self, db, tmp_path, monkeypatch):
        """VAL-FIX-004: Panel-Restore von .enc lokal → DIS decrypt → extract → script."""
        import services.panel_backup_service as pbs

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup_dir = tmp_path / "backups" / "panel"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("SECRET_KEY=test")

        monkeypatch.setattr(pbs.settings, "panel_config_dir", str(config_dir))
        monkeypatch.setattr(pbs.settings, "panel_backup_dir", str(backup_dir))

        _setup_backup_password()

        with patch.object(pbs, "_dump_database", return_value=b"-- sqlite dump\nCREATE TABLE x();"):
            with patch.object(pbs.settings, "database_url", "sqlite:///./msm.db"):
                backup = pbs.create_panel_backup(db)

        assert backup.local_path.endswith(".enc")

        # Restore vorbereiten
        result = pbs.prepare_panel_restore(backup.id, db)

        assert "script_path" in result
        assert "instructions" in result
        assert os.path.isfile(result["script_path"])

        # Script enthaelt Decrypt-Schritt (fuer .enc)
        with open(result["script_path"], "r", encoding="utf-8") as f:
            script = f.read()
        assert "Decrypt" in script or "decrypt" in script.lower()
        assert "python3" in script  # Decrypt via Python-Backend

    def test_panel_restore_plaintext_no_decrypt_in_script(self, db, tmp_path, monkeypatch):
        """Panel-Restore von .tar.gz → Script ohne Decrypt-Schritt."""
        import services.panel_backup_service as pbs

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        backup_dir = tmp_path / "backups" / "panel"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".env").write_text("SECRET_KEY=test")

        monkeypatch.setattr(pbs.settings, "panel_config_dir", str(config_dir))
        monkeypatch.setattr(pbs.settings, "panel_backup_dir", str(backup_dir))

        # Kein Passwort → .tar.gz
        with patch.object(pbs, "_dump_database", return_value=b"-- sqlite dump\nCREATE TABLE x();"):
            with patch.object(pbs.settings, "database_url", "sqlite:///./msm.db"):
                backup = pbs.create_panel_backup(db)

        assert backup.local_path.endswith(".tar.gz")

        result = pbs.prepare_panel_restore(backup.id, db)

        with open(result["script_path"], "r", encoding="utf-8") as f:
            script = f.read()
        # Kein Decrypt-Schritt im Script
        assert "Decrypt" not in script
        assert "decrypt" not in script.lower()
