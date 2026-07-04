"""Tests fuer Server-Backup Cloud-Operations (upload-to-cloud, delete+S3, retention+S3, list S3-Status, Migration).

Abgedeckte Assertions:
- VAL-SERVER-BACKUP-012: Upload-to-cloud fuer bestehende lokale Backups
- VAL-SERVER-BACKUP-013: Delete entfernt lokal UND S3 (best-effort S3)
- VAL-SERVER-BACKUP-014: Retention loescht lokal UND S3 (best-effort S3)
- VAL-SERVER-BACKUP-015: List zeigt S3-Status pro Backup
- VAL-SERVER-BACKUP-016: Bestehende lokale Backups bleiben nach Migration gueltig
"""
from __future__ import annotations

import os
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from models import Backup, Server, ServerPermission
from services.backup_config_service import BackupConfigService

S3_AAD = "msm:backup:s3"
TEST_BUCKET = "msm-cloud-ops-bucket"
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TEST_PASSWORD = "TestBackupPassword123!"


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
    s3_key: str | None = None,
    encrypted: bool = False,
    s3_bucket: str | None = None,
    age_minutes: int = 0,
) -> Backup:
    """Erstellt ein echtes tar.gz in tmp_path und einen Backup-DB-Record.

    Optional mit S3-Feldern (s3_key, encrypted, s3_bucket) fuer Migration-Tests.
    age_minutes steuert created_at fuer Retention-Ordering-Tests.
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
    )
    size_mb = os.path.getsize(filepath) // (1024 * 1024)
    created = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    backup = Backup(
        server_id=server.id,
        filename=filepath,
        size_mb=size_mb,
        s3_key=s3_key,
        s3_bucket=s3_bucket,
        encrypted=encrypted,
        created_at=created,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _upload_s3_object(s3_key: str, data: bytes = b"encrypted-backup-data") -> None:
    """Laedt ein Test-Objekt in den moto-Bucket hoch."""
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=TEST_BUCKET, Key=s3_key, Body=data,
    )


# ── VAL-SERVER-BACKUP-012: Upload-to-cloud ─────────────────────────────


class TestUploadToCloud:
    """Upload-to-cloud fuer bestehende lokale Backups."""

    @mock_aws
    def test_upload_to_cloud_success(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: Lokales Backup → S3, s3_key + encrypted=True."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("upload test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        assert backup.s3_key is None
        assert backup.encrypted is False

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
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 200
                db.refresh(backup)
                assert backup.s3_key is not None
                assert backup.encrypted is True
                assert backup.s3_bucket == TEST_BUCKET

                # S3-Objekt existiert
                s3_client = boto3.client("s3", region_name="us-east-1")
                s3_client.head_object(Bucket=TEST_BUCKET, Key=backup.s3_key)
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_upload_to_cloud_idempotent(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: Bereits hochgeladen → 2xx, kein Re-Upload."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("idempotent test")
        test_server.install_dir = str(install)
        db.commit()

        # Backup bereits mit s3_key + encrypted=True
        existing_key = f"msm-backups/servers/{test_server.id}/existing.enc"
        backup = _make_real_tar(
            db, test_server, tmp_path,
            s3_key=existing_key, encrypted=True, s3_bucket=TEST_BUCKET,
        )
        _upload_s3_object(existing_key)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.backup_orchestrator._upload_to_s3") as mock_upload:
                    resp = client.post(
                        f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # Kein Re-Upload (idempotent)
                    mock_upload.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    def test_upload_to_cloud_s3_not_configured(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: S3 nicht konfiguriert → 4xx."""
        # Kein S3-Config, kein Passwort
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("no s3 test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

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
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_upload_to_cloud_no_password(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: S3 konfiguriert aber kein Passwort → 4xx."""
        _setup_s3_config()
        # Kein Passwort
        assert not BackupConfigService.is_backup_password_set()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("no pw test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

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
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_upload_to_cloud_local_missing_404(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: Lokale Datei fehlt → 404."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("missing local test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        # Lokale Datei loeschen
        os.remove(backup.filename)

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
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_upload_to_cloud_backup_not_found_404(self, db, test_server, owner_cookies):
        """VAL-SERVER-BACKUP-012: Backup nicht gefunden → 404."""
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
                    f"/api/backups/{test_server.id}/99999/upload-to-cloud",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_upload_to_cloud_permission_403(self, db, test_server, user_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: Keine Permission → 403 (regular_user)."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("perm test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

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
                    f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                    cookies=user_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_upload_to_cloud_unauthenticated_401(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-012: Unauth → 401."""
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/backups/{test_server.id}/1/upload-to-cloud",
                )
                assert resp.status_code == 401
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_upload_to_cloud_key_invalidated(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-012: Key wird nach Upload invalidiert."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("key invalidation test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.backup_crypto_service.BackupCryptoService") as mock_crypto, \
                     patch("services.s3_service.S3Service") as mock_s3:
                    mock_crypto.init_key.return_value = "upload-key-id"
                    mock_crypto.encrypt_file_stream.return_value = iter([b"enc"])
                    mock_s3.upload_stream = MagicMock()

                    resp = client.post(
                        f"/api/backups/{test_server.id}/{backup.id}/upload-to-cloud",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # Key wurde invalidiert
                    mock_crypto.invalidate_key.assert_called_once_with("upload-key-id")
        finally:
            app.dependency_overrides.clear()


# ── VAL-SERVER-BACKUP-013: Delete entfernt lokal UND S3 ────────────────


class TestDeleteWithS3:
    """Delete loescht lokale Datei UND S3-Objekt (best-effort S3)."""

    @mock_aws
    def test_delete_removes_local_and_s3(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-013: Delete entfernt lokale Datei + S3-Objekt."""
        _setup_s3_config()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("delete s3 test")
        test_server.install_dir = str(install)
        db.commit()

        s3_key = f"msm-backups/servers/{test_server.id}/to_delete.enc"
        backup = _make_real_tar(
            db, test_server, tmp_path,
            s3_key=s3_key, encrypted=True, s3_bucket=TEST_BUCKET,
        )
        _upload_s3_object(s3_key)

        # Verify S3 object exists before delete
        s3_client = boto3.client("s3", region_name="us-east-1")
        s3_client.head_object(Bucket=TEST_BUCKET, Key=s3_key)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                resp = client.delete(
                    f"/api/backups/{test_server.id}/{backup.id}",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf},
                )
                assert resp.status_code == 200
                # Lokale Datei geloescht
                assert not os.path.exists(backup.filename)
                # S3-Objekt geloescht
                with pytest.raises(Exception):
                    s3_client.head_object(Bucket=TEST_BUCKET, Key=s3_key)
                # DB-Record geloescht
                assert db.query(Backup).filter(Backup.id == backup.id).first() is None
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_delete_s3_failure_local_still_deleted(self, db, test_server, owner_cookies, tmp_path, caplog):
        """VAL-SERVER-BACKUP-013: S3-Delete-Fehler blockiert nicht lokales Delete."""
        import logging
        _setup_s3_config()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("s3 fail test")
        test_server.install_dir = str(install)
        db.commit()

        s3_key = f"msm-backups/servers/{test_server.id}/s3_fail.enc"
        backup = _make_real_tar(
            db, test_server, tmp_path,
            s3_key=s3_key, encrypted=True, s3_bucket=TEST_BUCKET,
        )

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        caplog.set_level(logging.WARNING)
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.s3_service.S3Service.delete_object", side_effect=Exception("S3 error")):
                    resp = client.delete(
                        f"/api/backups/{test_server.id}/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    # Local delete succeeds despite S3 failure
                    assert resp.status_code == 200
                    assert not os.path.exists(backup.filename)
                    assert db.query(Backup).filter(Backup.id == backup.id).first() is None
                    # Warning logged (no secrets)
                    log_text = caplog.text
                    assert "S3" in log_text or "Delete" in log_text
        finally:
            app.dependency_overrides.clear()

    def test_delete_null_s3_key_no_s3_delete(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-013: null s3_key → kein S3-Delete-Versuch."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("null s3 test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)
        assert backup.s3_key is None

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                csrf = owner_cookies.get("__Secure-csrf_token")
                with patch("services.s3_service.S3Service.delete_object") as mock_s3_delete:
                    resp = client.delete(
                        f"/api/backups/{test_server.id}/{backup.id}",
                        cookies=owner_cookies,
                        headers={"X-CSRF-Token": csrf},
                    )
                    assert resp.status_code == 200
                    # S3-Delete wurde NICHT aufgerufen (null s3_key)
                    mock_s3_delete.assert_not_called()
        finally:
            app.dependency_overrides.clear()


# ── VAL-SERVER-BACKUP-014: Retention loescht lokal UND S3 ──────────────


class TestRetentionWithS3:
    """Retention loescht alte Backups aus lokal UND S3 (best-effort S3)."""

    @mock_aws
    def test_retention_deletes_local_and_s3(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-014: Retention loescht aelteste lokal + S3, behalt newest N."""
        from services.backup_service import cleanup_old_backups

        _setup_s3_config()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("retention test")
        test_server.install_dir = str(install)
        db.commit()

        s3_client = boto3.client("s3", region_name="us-east-1")

        # 3 Backups mit unterschiedlichen created_at (aelteste zuerst)
        backups = []
        for i in range(3):
            s3_key = f"msm-backups/servers/{test_server.id}/backup_{i}.enc"
            b = _make_real_tar(
                db, test_server, tmp_path,
                s3_key=s3_key, encrypted=True, s3_bucket=TEST_BUCKET,
                age_minutes=30 - i * 10,  # 30min, 20min, 10min alt
            )
            _upload_s3_object(s3_key)
            backups.append(b)

        # Retention: behalte 2 (newest), loesche 1 (oldest = backups[0])
        cleanup_old_backups(test_server.id, db, keep=2)

        # 2 verbleiben (newest)
        remaining = db.query(Backup).filter(Backup.server_id == test_server.id).all()
        assert len(remaining) == 2
        remaining_ids = {b.id for b in remaining}
        assert backups[0].id not in remaining_ids  # aelteste geloescht
        assert backups[1].id in remaining_ids
        assert backups[2].id in remaining_ids

        # Lokale Datei des aeltesten geloescht
        assert not os.path.exists(backups[0].filename)
        # S3-Objekt des aeltesten geloescht
        with pytest.raises(Exception):
            s3_client.head_object(
                Bucket=TEST_BUCKET,
                Key=f"msm-backups/servers/{test_server.id}/backup_0.enc",
            )
        # S3-Objekte der verbleibenden existieren noch
        s3_client.head_object(
            Bucket=TEST_BUCKET,
            Key=f"msm-backups/servers/{test_server.id}/backup_1.enc",
        )
        s3_client.head_object(
            Bucket=TEST_BUCKET,
            Key=f"msm-backups/servers/{test_server.id}/backup_2.enc",
        )

    @mock_aws
    def test_retention_s3_failure_continues(self, db, test_server, tmp_path, caplog):
        """VAL-SERVER-BACKUP-014: S3-Fehler bricht Retention nicht ab."""
        import logging
        from services.backup_service import cleanup_old_backups

        _setup_s3_config()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("retention s3 fail test")
        test_server.install_dir = str(install)
        db.commit()

        # 3 Backups, alle mit s3_key
        backups = []
        for i in range(3):
            s3_key = f"msm-backups/servers/{test_server.id}/r_fail_{i}.enc"
            b = _make_real_tar(
                db, test_server, tmp_path,
                s3_key=s3_key, encrypted=True, s3_bucket=TEST_BUCKET,
                age_minutes=30 - i * 10,
            )
            _upload_s3_object(s3_key)
            backups.append(b)

        caplog.set_level(logging.WARNING)
        # S3-Delete schlaegt fehl fuer alle
        with patch("services.s3_service.S3Service.delete_object", side_effect=Exception("S3 error")):
            cleanup_old_backups(test_server.id, db, keep=2)

        # Trotz S3-Fehler: lokale Dateien + DB-Records geloescht
        remaining = db.query(Backup).filter(Backup.server_id == test_server.id).all()
        assert len(remaining) == 2
        assert not os.path.exists(backups[0].filename)
        # Warning geloggt
        assert "S3" in caplog.text or "Delete" in caplog.text

    @mock_aws
    def test_retention_preserves_ordering(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-014: Retention behalt newest N, preserves ordering."""
        from services.backup_service import cleanup_old_backups

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("ordering test")
        test_server.install_dir = str(install)
        db.commit()

        # 5 Backups mit absteigendem Alter (aelteste = index 0)
        backups = []
        for i in range(5):
            b = _make_real_tar(
                db, test_server, tmp_path,
                age_minutes=50 - i * 10,  # 50, 40, 30, 20, 10 min alt
            )
            backups.append(b)

        # Retention: behalte 3 (newest = indices 2,3,4)
        cleanup_old_backups(test_server.id, db, keep=3)

        remaining = db.query(Backup).filter(Backup.server_id == test_server.id).all()
        assert len(remaining) == 3
        remaining_ids = {b.id for b in remaining}
        # Aelteste (0, 1) geloescht
        assert backups[0].id not in remaining_ids
        assert backups[1].id not in remaining_ids
        # Newest (2, 3, 4) behalten
        assert backups[2].id in remaining_ids
        assert backups[3].id in remaining_ids
        assert backups[4].id in remaining_ids

    @mock_aws
    def test_retention_null_s3_key_no_s3_delete(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-014: null s3_key Backups → kein S3-Delete-Versuch."""
        from services.backup_service import cleanup_old_backups

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("null s3 retention test")
        test_server.install_dir = str(install)
        db.commit()

        # 3 Backups ohne s3_key (local-only)
        for i in range(3):
            _make_real_tar(db, test_server, tmp_path, age_minutes=30 - i * 10)

        with patch("services.s3_service.S3Service.delete_object") as mock_s3_delete:
            cleanup_old_backups(test_server.id, db, keep=2)

            # S3-Delete wurde nie aufgerufen (alle s3_key=null)
            mock_s3_delete.assert_not_called()

        remaining = db.query(Backup).filter(Backup.server_id == test_server.id).all()
        assert len(remaining) == 2


# ── VAL-SERVER-BACKUP-015: List zeigt S3-Status pro Backup ────────────


class TestListS3Status:
    """List-Endpoint zeigt S3-Status (encrypted flag) pro Backup."""

    def test_list_shows_s3_status(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-015: List-Items haben encrypted flag passend zur DB."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("list s3 test")
        test_server.install_dir = str(install)
        db.commit()

        # Local-only backup
        local_backup = _make_real_tar(db, test_server, tmp_path, age_minutes=20)
        # S3-backed backup
        s3_backup = _make_real_tar(
            db, test_server, tmp_path,
            s3_key=f"msm-backups/servers/{test_server.id}/list.enc",
            encrypted=True, s3_bucket=TEST_BUCKET,
            age_minutes=10,
        )

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                resp = client.get(
                    f"/api/backups/{test_server.id}",
                    cookies=owner_cookies,
                )
                assert resp.status_code == 200
                items = resp.json()
                assert len(items) == 2

                # Sortiert nach created_at desc → s3_backup (newer) zuerst
                by_id = {item["id"]: item for item in items}

                # Local-only: encrypted=False, s3_key=None
                local_item = by_id[local_backup.id]
                assert local_item["encrypted"] is False
                assert local_item["s3_key"] is None

                # S3-backed: encrypted=True, s3_key gesetzt
                s3_item = by_id[s3_backup.id]
                assert s3_item["encrypted"] is True
                assert s3_item["s3_key"] is not None
                assert s3_item["s3_bucket"] == TEST_BUCKET
        finally:
            app.dependency_overrides.clear()

    def test_list_distinguishes_local_and_s3(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-015: Local-only und S3-backed unterscheidbar."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("distinguish test")
        test_server.install_dir = str(install)
        db.commit()

        _make_real_tar(db, test_server, tmp_path, age_minutes=20)
        _make_real_tar(
            db, test_server, tmp_path,
            s3_key=f"msm-backups/servers/{test_server.id}/dist.enc",
            encrypted=True, s3_bucket=TEST_BUCKET,
            age_minutes=10,
        )

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                resp = client.get(
                    f"/api/backups/{test_server.id}",
                    cookies=owner_cookies,
                )
                assert resp.status_code == 200
                items = resp.json()
                # Genau ein local-only und ein S3-backed
                local_only = [i for i in items if i["encrypted"] is False and i["s3_key"] is None]
                s3_backed = [i for i in items if i["encrypted"] is True and i["s3_key"] is not None]
                assert len(local_only) == 1
                assert len(s3_backed) == 1
        finally:
            app.dependency_overrides.clear()


# ── VAL-SERVER-BACKUP-016: Bestehende lokale Backups nach Migration ────


class TestMigrationValidity:
    """Bestehende lokale Backups (s3_key null, encrypted False) bleiben gueltig."""

    def test_migration_null_s3_key_encrypted_false_restore(
        self, db, test_server, owner_cookies, tmp_path
    ):
        """VAL-SERVER-BACKUP-016: Backup mit null s3_key, encrypted=False restoren."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("migration restore test")
        test_server.install_dir = str(install)
        test_server.status = "running"
        db.commit()

        # Pre-migration Backup (s3_key=null, encrypted=False)
        backup = _make_real_tar(db, test_server, tmp_path)
        assert backup.s3_key is None
        assert backup.encrypted is False

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
        finally:
            app.dependency_overrides.clear()

    def test_migration_default_values(self, db, test_server, tmp_path):
        """VAL-SERVER-BACKUP-016: Neue nullable Spalten default korrekt."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("default test")
        test_server.install_dir = str(install)
        db.commit()

        # Backup erstellen ohne S3-Felder (wie pre-migration)
        backup = _make_real_tar(db, test_server, tmp_path)

        # Default-Werte korrekt
        assert backup.s3_key is None
        assert backup.s3_bucket is None
        assert backup.encrypted is False

        # Backup-Record aus DB neu laden
        db.expire_all()
        reloaded = db.query(Backup).filter(Backup.id == backup.id).first()
        assert reloaded.s3_key is None
        assert reloaded.s3_bucket is None
        assert reloaded.encrypted is False

    def test_migration_local_only_in_list(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SERVER-BACKUP-016: Pre-migration Backup in List mit korrektem Status."""
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("list migration test")
        test_server.install_dir = str(install)
        db.commit()

        backup = _make_real_tar(db, test_server, tmp_path)

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                resp = client.get(
                    f"/api/backups/{test_server.id}",
                    cookies=owner_cookies,
                )
                assert resp.status_code == 200
                items = resp.json()
                assert len(items) == 1
                item = items[0]
                assert item["id"] == backup.id
                assert item["encrypted"] is False
                assert item["s3_key"] is None
        finally:
            app.dependency_overrides.clear()
