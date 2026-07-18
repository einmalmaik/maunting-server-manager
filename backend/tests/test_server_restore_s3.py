"""Tests fuer Server-Restore von S3 (server-restore-s3 Feature).

Abgedeckte Assertions:
- VAL-SERVER-RESTORE-001: Restore von lokal (bestehendes Verhalten erhalten)
- VAL-SERVER-RESTORE-002: Restore von S3 wenn lokal fehlt (download, decrypt, restore)
- VAL-SERVER-RESTORE-003: Lifecycle-Lock waehrend Restore (concurrent → 409)
- VAL-SERVER-RESTORE-004: Lokal und S3 fehlen → 404
- VAL-SERVER-RESTORE-005: Rollback bei Restore-Fehler
- VAL-SERVER-RESTORE-006: Decrypt-Fehler (falsches Passwort) → klare Fehlermeldung
- VAL-SERVER-RESTORE-007: Permissions und Key-Invalidierung
- VAL-SERVER-RESTORE-008: S3-Restore-Inhalt entspricht Original
- VAL-CROSS-001: Full Backup Cycle (create, delete local, restore from S3)
- VAL-CROSS-005: S3 unreachable waehrend Restore → klare Fehlermeldung
- VAL-CROSS-006: Falsches Passwort waehrend Restore → DecryptionFailed klar
- VAL-CROSS-007: Key-Lifecycle (init before, invalidate after)
- VAL-CROSS-011: Upload-to-cloud enables S3 restore for legacy backups
"""
from __future__ import annotations

import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from models import Backup, Server, ServerPermission
from services.backup_config_service import BackupConfigService

TEST_BUCKET = "msm-restore-s3-bucket"
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TEST_PASSWORD = "TestBackupPassword123!"


# ── Helper ───────────────────────────────────────────────────────────────


def _grant_permission(db, user_id: int, server_id: int, key: str) -> None:
    """Grant a single server-scoped permission."""
    perm = ServerPermission(user_id=user_id, server_id=server_id, permission_key=key)
    db.add(perm)
    db.commit()


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
    """Backup-Passwort setzen (verschluesselt via DIS-Mock)."""
    BackupConfigService.set_backup_password(TEST_PASSWORD)


def _create_moto_bucket() -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=TEST_BUCKET)


def _make_real_tar(
    db,
    server: Server,
    tmp_path: Path,
    *,
    s3_key: str | None = None,
    encrypted: bool = False,
    s3_bucket: str | None = None,
) -> Backup:
    """Erstellt ein echtes tar.gz in tmp_path und einen Backup-DB-Record."""
    from services.backup_paths import create_full_backup_tar

    backup_dir = tmp_path / "backups" / str(server.id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"server_{server.id}_{timestamp}.tar.gz"
    filepath = str(backup_dir / filename)
    create_full_backup_tar(filepath, server.install_dir, server_id=server.id)
    size_mb = os.path.getsize(filepath) // (1024 * 1024)
    backup = Backup(
        server_id=server.id,
        filename=filepath,
        size_mb=size_mb,
        s3_key=s3_key,
        s3_bucket=s3_bucket,
        encrypted=encrypted,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _encrypt_and_upload_to_s3(local_path: str, s3_key: str) -> None:
    """Verschluesselt eine lokale Datei via DIS-Mock und laedt sie zu S3 hoch."""
    from services.backup_crypto_service import BackupCryptoService
    from services.s3_service import S3Service

    password = BackupConfigService.get_backup_password()
    salt = BackupConfigService.get_backup_salt()
    key_id = BackupCryptoService.init_key(password, salt)
    try:
        encrypted_stream = BackupCryptoService.encrypt_file_stream(local_path, key_id)
        S3Service.upload_stream(encrypted_stream, s3_key)
    finally:
        BackupCryptoService.invalidate_key(key_id)


def _upload_raw_s3_object(s3_key: str, data: bytes) -> None:
    """Laedt rohe Bytes in den moto-Bucket hoch (fuer korrupte/nicht-encrypted Objekte)."""
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=TEST_BUCKET, Key=s3_key, Body=data,
    )


def _make_install_dir(tmp_path: Path, server: Server, db, *, content: str = "world data") -> Path:
    """Erstellt ein install_dir mit Inhalt und updated den Server."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "world.dat").write_text(content)
    server.install_dir = str(install)
    db.commit()
    return install


def _client_override(db):
    """Override get_db und gibt (client_context, cleanup) zurueck."""
    from fastapi.testclient import TestClient
    from main import app
    from database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _teardown_override():
    from main import app
    app.dependency_overrides.clear()


# ── VAL-SERVER-RESTORE-001: Restore von lokal (bestehendes Verhalten) ──


class TestRestoreFromLocal:
    """Restore von lokaler Datei — bestehendes Verhalten bleibt unveraendert."""

    def test_restore_from_local_existing_behavior(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-001: Lokale Datei existiert → bestehende Restore-Logik."""
        _make_install_dir(tmp_path, test_server, db, content="original world")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
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
        finally:
            _teardown_override()


# ── VAL-SERVER-RESTORE-002: Restore von S3 wenn lokal fehlt ────────────


class TestRestoreFromS3:
    """Restore von S3: download, decrypt, save locally, dann Restore-Logik."""

    @mock_aws
    def test_restore_from_s3_when_local_missing(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-002: Lokal fehlt, s3_key vorhanden → S3-Restore."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="s3 restore world")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/server_{test_server.id}_test.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        # Verschluesseltes Objekt zu S3 hochladen
        _encrypt_and_upload_to_s3(backup.filename, s3_key)

        # Lokale Datei loeschen (simuliert fehlendes lokales Backup)
        os.remove(backup.filename)
        assert not os.path.exists(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
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
                    # Lokale Datei wurde durch S3-Download wiederhergestellt
                    assert os.path.exists(backup.filename)
        finally:
            _teardown_override()

    @mock_aws
    def test_s3_restore_downloads_and_decrypts(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-002: S3Service.download_stream und decrypt werden aufgerufen."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="spy test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/spy.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _encrypt_and_upload_to_s3(backup.filename, s3_key)
        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.s3_service.S3Service.download_stream", wraps=__import__("services.s3_service", fromlist=["S3Service"]).S3Service.download_stream) as mock_dl, \
                     patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file", wraps=__import__("services.backup_crypto_service", fromlist=["BackupCryptoService"]).BackupCryptoService.decrypt_to_file) as mock_dec:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    mock_dl.assert_called_once_with(s3_key, bucket=TEST_BUCKET)
                    mock_dec.assert_called_once()
        finally:
            _teardown_override()

    @mock_aws
    def test_s3_restore_content_matches_original(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-008: S3-restored Archiv ist byte-identisch mit Original."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="content match test")
        (install / "subdir").mkdir()
        (install / "subdir" / "nested.dat").write_text("nested content")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        original_bytes = Path(backup.filename).read_bytes()

        s3_key = f"msm-backups/servers/{test_server.id}/match.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _encrypt_and_upload_to_s3(backup.filename, s3_key)
        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # Die wiederhergestellte lokale Datei ist byte-identisch
                    restored_bytes = Path(backup.filename).read_bytes()
                    assert restored_bytes == original_bytes
                    # install_dir wurde korrekt extrahiert
                    assert (Path(test_server.install_dir) / "world.dat").exists()
                    assert (Path(test_server.install_dir) / "subdir" / "nested.dat").exists()
        finally:
            _teardown_override()

    @mock_aws
    def test_container_stopped_before_extraction(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-002: Container wird VOR Extraktion gestoppt."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="stop order test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/stop.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _encrypt_and_upload_to_s3(backup.filename, s3_key)
        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                call_order = []
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop", side_effect=lambda *a, **k: call_order.append("stop")) as mock_stop, \
                     patch("services.docker_service.remove", side_effect=lambda *a, **k: (call_order.append("remove") or {"ok": True})):
                    mock_stop.return_value = {"ok": True}
                    mock_run.return_value = True
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # Stop wurde vor Extract aufgerufen (Extract passiert im _safe_extract_backup_tar)
                    assert "stop" in call_order
                    assert call_order.index("stop") < call_order.index("remove") if "remove" in call_order else True
        finally:
            _teardown_override()


# ── VAL-SERVER-RESTORE-003: Lifecycle-Lock (concurrent → 409) ──────────


class TestLifecycleLock:
    """Lifecycle-Lock wird waehrend Restore gehalten, concurrent → 409."""

    def test_concurrent_restore_returns_409(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-003: Concurrent Restore (Lock gehalten) → 409."""
        _make_install_dir(tmp_path, test_server, db, content="lock test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        from services.server_lifecycle_service import get_server_lifecycle_lock
        lock = get_server_lifecycle_lock(test_server.id)
        # Lock manuell halten (simuliert laufende Operation)
        acquired = lock.acquire(blocking=False)
        assert acquired

        try:
            client_ctx = _client_override(db)
            try:
                with client_ctx as client:
                    csrf = owner_cookies.get("__Secure-csrf_token")
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 409
            finally:
                _teardown_override()
        finally:
            lock.release()

    def test_lock_released_after_successful_restore(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-003: Lock wird nach erfolgreichem Restore freigegeben."""
        _make_install_dir(tmp_path, test_server, db, content="release test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
        finally:
            _teardown_override()

        # Lock ist freigegeben
        from services.server_lifecycle_service import get_server_lifecycle_lock
        lock = get_server_lifecycle_lock(test_server.id)
        assert lock.acquire(blocking=False), "Lock wurde nicht freigegeben"
        lock.release()

    def test_lock_released_on_failure(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-003/005: Lock wird auch bei Fehler freigegeben."""
        _make_install_dir(tmp_path, test_server, db, content="fail release test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("routers.backups._safe_extract_backup_tar", side_effect=Exception("extract fail")):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 500
        finally:
            _teardown_override()

        from services.server_lifecycle_service import get_server_lifecycle_lock
        lock = get_server_lifecycle_lock(test_server.id)
        assert lock.acquire(blocking=False), "Lock wurde nach Fehler nicht freigegeben"
        lock.release()


# ── VAL-SERVER-RESTORE-004: Lokal und S3 fehlen → 404 ──────────────────


class TestBothMissing:
    """Lokal und S3 fehlen → 404, kein State-Change."""

    def test_no_s3_key_local_missing_404(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-004: Lokal fehlt, kein s3_key → 404."""
        _make_install_dir(tmp_path, test_server, db, content="both missing test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        os.remove(backup.filename)
        assert backup.s3_key is None

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop") as mock_stop:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 404
                    # Kein State-Change: Container nicht gestoppt
                    mock_stop.assert_not_called()
                    db.refresh(test_server)
                    assert test_server.status == "running"
        finally:
            _teardown_override()

    @mock_aws
    def test_s3_key_set_but_object_missing(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-004: s3_key gesetzt aber S3-Objekt fehlt → klarer Fehler."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="s3 obj missing test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/nonexistent.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        os.remove(backup.filename)
        # S3-Objekt NICHT hochladen (simuliert fehlendes Objekt)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop") as mock_stop:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    # S3-Objekt fehlt → klarer Fehler (nicht 200)
                    assert resp.status_code != 200
                    assert resp.status_code != 404  # s3_key ist gesetzt, also nicht "beide fehlen"
                    # Container nicht gestoppt (S3-Fehler vor Stop)
                    mock_stop.assert_not_called()
                    db.refresh(test_server)
                    assert test_server.status == "running"
        finally:
            _teardown_override()


# ── VAL-SERVER-RESTORE-005: Rollback bei Restore-Fehler ────────────────


class TestRollback:
    """Rollback bei Restore-Fehler."""

    def test_extract_failure_rollback(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-005: Extract-Fehler → install_dir wird zurueckgesichert."""
        install = _make_install_dir(tmp_path, test_server, db, content="rollback original")
        (install / "keep_me.txt").write_text("should survive")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("routers.backups._safe_extract_backup_tar", side_effect=Exception("extract boom")):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 500
                    # Rollback: install_dir wiederhergestellt
                    db.refresh(test_server)
                    assert test_server.status == "error"
                    # Original content survived (rollback)
                    assert (install / "keep_me.txt").exists()
        finally:
            _teardown_override()

    @mock_aws
    def test_download_failure_install_dir_untouched(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-005: S3-Download-Fehler → install_dir unveraendert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="untouched")
        (install / "original.txt").write_text("original content")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/download_fail.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop") as mock_stop, \
                     patch("services.s3_service.S3Service.download_stream", side_effect=Exception("S3 unreachable")):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code != 200
                    # Container nicht gestoppt (Download-Fehler vor Stop)
                    mock_stop.assert_not_called()
                    # install_dir unveraendert
                    assert (install / "original.txt").exists()
                    assert (install / "original.txt").read_text() == "original content"
                    db.refresh(test_server)
                    assert test_server.status == "running"
        finally:
            _teardown_override()


# ── VAL-SERVER-RESTORE-006: Decrypt-Fehler → klare Fehlermeldung ───────


class TestDecryptFailure:
    """Decrypt-Fehler (falsches Passwort / manipuliert) → klare Fehlermeldung."""

    @mock_aws
    def test_wrong_password_clear_error(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-006: Falsches Passwort → klarer Fehler, kein Restore."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="decrypt fail test")
        (install / "keep.txt").write_text("keep me")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/decrypt_fail.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        # Korrupte Daten zu S3 hochladen (ungueltige Frames → DecryptionFailed)
        _upload_raw_s3_object(s3_key, b"this-is-not-valid-encrypted-frame-data")
        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop") as mock_stop:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    # Klarer Fehler (nicht 200)
                    assert resp.status_code != 200
                    detail = resp.json().get("detail", "")
                    # Fehlermeldung erwaehnt Entschluesselung (Deutsch)
                    detail_lower = str(detail).lower()
                    assert "entschl" in detail_lower or "decrypt" in detail_lower or "fehl" in detail_lower
                    # Keine internen Pfade geleakt
                    assert "/tmp/" not in str(detail)
                    assert "install_dir" not in str(detail)
                    # Container nicht gestoppt
                    mock_stop.assert_not_called()
                    # install_dir unveraendert
                    assert (install / "keep.txt").exists()
                    db.refresh(test_server)
                    assert test_server.status == "running"
        finally:
            _teardown_override()

    @mock_aws
    def test_key_invalidated_on_decrypt_failure(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-006: Key wird auch bei Decrypt-Fehler invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="key invalidation decrypt fail")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/key_inv.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _upload_raw_s3_object(s3_key, b"corrupt-data")
        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto:
                    mock_crypto.init_key.return_value = "decrypt-fail-key-id"
                    mock_crypto.decrypt_to_file.side_effect = __import__(
                        "services.backup_crypto_service", fromlist=["BackupDecryptionError"]
                    ).BackupDecryptionError("decrypt failed")
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code != 200
                    # Key wurde invalidiert (try/finally)
                    mock_crypto.invalidate_key.assert_called_once_with("decrypt-fail-key-id")
        finally:
            _teardown_override()


# ── VAL-SERVER-RESTORE-007: Permissions und Key-Invalidierung ──────────


class TestPermissionsAndValidation:
    """Restore-Permissions und Key-Invalidierung nach S3-Restore."""

    def test_restore_permission_403(
        self, db, test_server, user_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-007: Keine server.backups.restore-Permission → 403."""
        _make_install_dir(tmp_path, test_server, db, content="perm test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = user_cookies.get("__Secure-csrf_token")
                resp = client.post(
                    f"/api/backups/{test_server.id}/restore/{backup.id}",
                    cookies=user_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 403
        finally:
            _teardown_override()

    def test_restore_unauthenticated_401(self, db, test_server, tmp_path):
        """VAL-SERVER-RESTORE-007: Unauth → 401."""
        _make_install_dir(tmp_path, test_server, db, content="unauth test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                resp = client.post(
                    f"/api/backups/{test_server.id}/restore/{backup.id}",
                )
                assert resp.status_code == 401
        finally:
            _teardown_override()

    def test_restore_nonexistent_backup_404(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-007: Nichtexistente backup_id → 404."""
        _make_install_dir(tmp_path, test_server, db, content="nonexistent test")
        db.commit()

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                resp = client.post(
                    f"/api/backups/{test_server.id}/restore/99999",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 404
        finally:
            _teardown_override()

    def test_restore_cross_server_backup_404(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-007: Cross-Server backup_id → 404."""
        _make_install_dir(tmp_path, test_server, db, content="cross server test")
        db.commit()

        # Backup fuer einen anderen Server erstellen
        other_server = Server(
            name="Other Server",
            game_type="dayz",
            install_dir="/tmp/other_server",
            container_name="msm-srv-other",
            status="stopped",
        )
        db.add(other_server)
        db.commit()
        db.refresh(other_server)

        other_install = tmp_path / "other_install"
        other_install.mkdir()
        (other_install / "data.dat").write_text("other")
        other_server.install_dir = str(other_install)
        db.commit()

        other_backup = _make_real_tar(db, other_server, tmp_path)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                # Restore other_server's backup on test_server → 404
                resp = client.post(
                    f"/api/backups/{test_server.id}/restore/{other_backup.id}",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 404
        finally:
            _teardown_override()

    @mock_aws
    def test_key_invalidated_after_s3_restore_success(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-SERVER-RESTORE-007: Key nach erfolgreichem S3-Restore invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="key inv success")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/key_inv_ok.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _encrypt_and_upload_to_s3(backup.filename, s3_key)
        os.remove(backup.filename)

        # Real BackupCryptoService nutzen, aber invalidate_key spyen.
        from services.backup_crypto_service import BackupCryptoService
        invalidate_calls: list[str] = []
        original_invalidate = BackupCryptoService.invalidate_key

        def _spy_invalidate(key_id: str):
            invalidate_calls.append(key_id)
            return original_invalidate(key_id)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch.object(BackupCryptoService, "invalidate_key", staticmethod(_spy_invalidate)):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # Key wurde invalidiert
                    assert len(invalidate_calls) == 1
        finally:
            _teardown_override()


# ── VAL-CROSS-005: S3 unreachable waehrend Restore ─────────────────────


class TestS3Unreachable:
    """S3 unreachable waehrend Restore → klare Fehlermeldung."""

    @mock_aws
    def test_s3_unreachable_clear_error(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-CROSS-005: S3 nicht erreichbar → klarer Fehler, kein Partial-File."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="s3 unreachable test")
        test_server.status = "running"
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/unreachable.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        os.remove(backup.filename)

        from services.s3_service import S3OperationError

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=True) as mock_run, \
                     patch("services.docker_service.stop") as mock_stop, \
                     patch("services.s3_service.S3Service.download_stream", side_effect=S3OperationError("S3 error")):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code != 200
                    # Kein Partial-File (lokal wurde nichts geschrieben)
                    assert not os.path.exists(backup.filename)
                    # Container nicht gestoppt
                    mock_stop.assert_not_called()
                    # Kein corrupt State
                    db.refresh(test_server)
                    assert test_server.status == "running"
                    assert (install / "world.dat").exists()
        finally:
            _teardown_override()


# ── VAL-CROSS-007: Key-Lifecycle (init before, invalidate after) ────────


class TestKeyLifecycle:
    """Key-Lifecycle: init vor Operation, invalidate nachher (try/finally)."""

    @mock_aws
    def test_key_init_before_invalidate_after_s3_restore(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-CROSS-007: init_key vor decrypt, invalidate_key nachher."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="lifecycle test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/lifecycle.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        _encrypt_and_upload_to_s3(backup.filename, s3_key)
        os.remove(backup.filename)

        # Real BackupCryptoService nutzen, init_key und invalidate_key spyen.
        from services.backup_crypto_service import BackupCryptoService
        call_order: list[str] = []
        original_init = BackupCryptoService.init_key
        original_invalidate = BackupCryptoService.invalidate_key

        def _spy_init(password, salt):
            call_order.append("init")
            return original_init(password, salt)

        def _spy_invalidate(key_id):
            call_order.append("invalidate")
            return original_invalidate(key_id)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch.object(BackupCryptoService, "init_key", staticmethod(_spy_init)), \
                     patch.object(BackupCryptoService, "invalidate_key", staticmethod(_spy_invalidate)):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # init vor invalidate
                    assert "init" in call_order
                    assert "invalidate" in call_order
                    assert call_order.index("init") < call_order.index("invalidate")
        finally:
            _teardown_override()

    @mock_aws
    def test_key_invalidated_on_s3_download_failure(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-CROSS-007: Key wird auch bei S3-Download-Fehler invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        _make_install_dir(tmp_path, test_server, db, content="dl fail key test")
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        s3_key = f"msm-backups/servers/{test_server.id}/dl_fail_key.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()

        os.remove(backup.filename)

        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"), \
                     patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
                     patch("services.s3_service.S3Service.download_stream", side_effect=Exception("S3 error")):
                    mock_crypto.init_key.return_value = "dl-fail-key-id"
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code != 200
                    # Key trotzdem invalidiert
                    mock_crypto.invalidate_key.assert_called_once_with("dl-fail-key-id")
        finally:
            _teardown_override()


# ── VAL-CROSS-001: Full Backup Cycle ────────────────────────────────────


class TestFullBackupCycle:
    """Full Backup Cycle: create, delete local, restore from S3."""

    @mock_aws
    def test_full_cycle_create_delete_local_restore_from_s3(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-CROSS-001: Backup erstellen → S3 → lokal loeschen → von S3 restoren."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="full cycle world")
        (install / "important.dat").write_text("important data")
        test_server.status = "running"
        db.commit()

        # 1. Backup erstellen (lokal)
        backup = _make_real_tar(db, test_server, tmp_path)

        # 2. Zu S3 hochladen (verschluesselt)
        s3_key = f"msm-backups/servers/{test_server.id}/cycle.enc"
        backup.s3_key = s3_key
        backup.encrypted = True
        backup.s3_bucket = TEST_BUCKET
        db.commit()
        _encrypt_and_upload_to_s3(backup.filename, s3_key)

        # Verifiziere: S3-Objekt existiert
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.head_object(Bucket=TEST_BUCKET, Key=s3_key)

        # 3. Lokale Datei loeschen (simuliert Disaster)
        os.remove(backup.filename)
        assert not os.path.exists(backup.filename)

        # 4. Inhalt von install_dir aendern (simuliert Vernderung nach Backup)
        (install / "important.dat").write_text("modified data")
        (install / "new_file.txt").write_text("new file")

        # 5. Von S3 restoren
        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200

                    # 6. Server-State stimmt mit Backup-Ursprung ueberein
                    db.refresh(test_server)
                    assert test_server.status == "stopped"
                    # install_dir wurde aus Backup extrahiert
                    assert (install / "world.dat").exists()
                    assert (install / "important.dat").read_text() == "important data"
                    # Neue Datei wurde durch Restore entfernt (full backup overwrite)
                    assert not (install / "new_file.txt").exists()
                    # Lokale Backup-Datei wiederhergestellt
                    assert os.path.exists(backup.filename)
        finally:
            _teardown_override()


# ── VAL-CROSS-011: Upload-to-cloud enables S3 restore ──────────────────


class TestUploadToCloudEnablesRestore:
    """Upload-to-cloud fuer Legacy-Backups → S3-Restore moeglich."""

    @mock_aws
    def test_upload_to_cloud_then_delete_local_then_restore_from_s3(
        self, db, test_server, owner_cookies, tmp_path,
    ):
        """VAL-CROSS-011: Legacy local-only → upload-to-cloud → delete local → S3 restore."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = _make_install_dir(tmp_path, test_server, db, content="legacy restore world")
        test_server.status = "running"
        db.commit()

        # 1. Legacy local-only Backup (s3_key=null, encrypted=False)
        backup = _make_real_tar(db, test_server, tmp_path)
        assert backup.s3_key is None
        assert backup.encrypted is False

        # 2. Upload-to-cloud (setzt s3_key, encrypted=True)
        client_ctx = _client_override(db)
        try:
            with client_ctx as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                resp_upload = client.post(
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp_upload.status_code == 200
                db.refresh(backup)
                assert backup.s3_key is not None
                assert backup.encrypted is True

                # 3. Lokale Datei loeschen (Disaster-Recovery-Szenario)
                os.remove(backup.filename)
                assert not os.path.exists(backup.filename)

                # 4. Von S3 restoren
                with patch("services.docker_service.is_running", return_value=False), \
                     patch("services.docker_service.remove"):
                    resp_restore = client.post(
                        f"/api/backups/{test_server.id}/restore/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp_restore.status_code == 200
                    db.refresh(test_server)
                    assert test_server.status == "stopped"
                    assert (install / "world.dat").exists()
                    assert os.path.exists(backup.filename)
        finally:
            _teardown_override()
