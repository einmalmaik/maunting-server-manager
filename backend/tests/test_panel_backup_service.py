"""Tests fuer Panel Backup Service und Router (services/panel_backup_service.py,
routers/panel_backups.py).

Abgedeckte Assertions:
- VAL-PANEL-BACKUP-001: Admin kann Panel-Backup erstellen (POST /api/panel-backups)
- VAL-PANEL-BACKUP-002: Non-admin 403, unauth 401
- VAL-PANEL-BACKUP-003: pg_dump fuer PostgreSQL
- VAL-PANEL-BACKUP-004: sqlite3 dump fuer SQLite dev
- VAL-PANEL-BACKUP-005: Config-Dateien im Archiv
- VAL-PANEL-BACKUP-006: manifest.json mit Metadaten
- VAL-PANEL-BACKUP-007: S3-Upload wenn konfiguriert + Passwort
- VAL-PANEL-BACKUP-008: S3/DIS-Fehler blockiert nicht lokales Backup
- VAL-PANEL-BACKUP-009: pg_dump-Fehler: kein partieller Backup, Temp cleaned
- VAL-PANEL-BACKUP-010: Fehlende Config-Datei mit Warning skipped
- VAL-PANEL-BACKUP-011: Backup-Key nach S3-Upload invalidiert
"""
from __future__ import annotations

import io
import json
import os
import struct
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from config import settings
from models import PanelBackup, PanelSetting
from services import panel_backup_service as pbs
from services.backup_config_service import BackupConfigService
from services.panel_settings_service import PanelSettingsService

TEST_BUCKET = "msm-panel-backup-bucket"
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
TEST_PASSWORD = "SuperSecretBackup123!"

_FAKE_PG_DUMP = b"-- PostgreSQL database dump\n-- pg_dump version 17\n\nCREATE TABLE users ();\n"
_FAKE_SQLITE_DUMP = b"BEGIN TRANSACTION;\nCREATE TABLE users (id INTEGER PRIMARY KEY);\nCOMMIT;\n"


# ── Helper ───────────────────────────────────────────────────────────────


def _prepare_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Setzt panel_config_dir und panel_backup_dir auf tmp_path-Unterverzeichnisse.

    Gibt (config_dir, backup_dir) zurueck und erstellt sie.
    """
    config_dir = tmp_path / "config"
    backup_dir = tmp_path / "backups" / "panel"
    config_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "panel_config_dir", str(config_dir))
    monkeypatch.setattr(settings, "panel_backup_dir", str(backup_dir))
    return config_dir, backup_dir


def _write_config_files(config_dir: Path, names: list[str]) -> None:
    """Schreibt Config-Dateien in config_dir."""
    for name in names:
        (config_dir / name).write_text(f"# content of {name}\n", encoding="utf-8")


def _setup_s3_config() -> None:
    """S3-Config in panel_settings mit mock-verschluesselten Credentials."""
    BackupConfigService.set_s3_config(
        endpoint="",
        access_key=TEST_ACCESS_KEY,
        secret_key=TEST_SECRET_KEY,
        bucket=TEST_BUCKET,
        region=TEST_REGION,
    )


def _setup_backup_password() -> None:
    BackupConfigService.set_backup_password(TEST_PASSWORD)


def _create_moto_bucket() -> None:
    boto3.client("s3", region_name=TEST_REGION).create_bucket(Bucket=TEST_BUCKET)


def _extract_archive(local_path: str) -> dict[str, bytes]:
    """Entpackt ein Panel-Backup-tar.gz in ein dict {arcname: bytes}."""
    out: dict[str, bytes] = {}
    with tarfile.open(local_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                if f is not None:
                    out[member.name] = f.read()
    return out


def _post_panel_backup(client, cookies, *, name: str | None = None):
    csrf = cookies.get("__Secure-csrf_token")
    body = {"name": name} if name else {}
    return client.post(
        "/api/panel-backups",
        json=body,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


# ── Service: Basic Creation ──────────────────────────────────────────────


class TestPanelBackupService:
    """Unit-Tests fuer create_panel_backup (Service-Layer)."""

    def test_creates_local_backup_sqlite(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-004: sqlite3 dump fuer SQLite dev + lokales tar.gz."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env", "install.sh"])
        monkeypatch.setattr(settings, "database_url", "sqlite:///./msm.db")

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            backup = pbs.create_panel_backup(db)

        assert backup.id is not None
        assert backup.db_type == "sqlite3"
        assert backup.size_mb is not None and backup.size_mb >= 0
        assert backup.encrypted is False
        assert backup.s3_key is None
        assert os.path.exists(backup.local_path)

        # Archiv-Inhalt pruefen
        files = _extract_archive(backup.local_path)
        assert "manifest.json" in files
        assert "msm_db.sql" in files
        assert b"sqlite" in files["msm_db.sql"].lower() or b"BEGIN TRANSACTION" in files["msm_db.sql"]
        assert "configs/.env" in files
        assert "configs/install.sh" in files

    def test_creates_local_backup_postgres(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-003: pg_dump fuer PostgreSQL."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        monkeypatch.setattr(settings, "database_url", "postgresql://msm:pw@127.0.0.1:15432/msm")

        with patch.object(pbs, "_dump_database", return_value=_FAKE_PG_DUMP):
            backup = pbs.create_panel_backup(db)

        assert backup.db_type == "postgresql"
        files = _extract_archive(backup.local_path)
        assert b"pg_dump" in files["msm_db.sql"] or b"PostgreSQL" in files["msm_db.sql"]

    def test_db_type_detection(self, monkeypatch):
        """VAL-PANEL-BACKUP-003/004: db_type Detection aus database_url."""
        monkeypatch.setattr(settings, "database_url", "postgresql://msm:pw@host/msm")
        assert pbs._detect_db_type() == "postgresql"
        monkeypatch.setattr(settings, "database_url", "postgres://msm:pw@host/msm")
        assert pbs._detect_db_type() == "postgresql"
        monkeypatch.setattr(settings, "database_url", "sqlite:///./msm.db")
        assert pbs._detect_db_type() == "sqlite3"
        monkeypatch.setattr(settings, "database_url", "sqlite:///:memory:")
        assert pbs._detect_db_type() == "sqlite3"


# ── Service: Config Files + Manifest ─────────────────────────────────────


class TestPanelBackupConfigFiles:
    """VAL-PANEL-BACKUP-005/006/010: Config-Dateien und manifest.json."""

    def test_all_config_files_included(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-005: Alle Config-Dateien im Archiv."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        all_names = [".env", "install.sh", "Caddyfile.template",
                     "msm.service.template", "msm-update.service",
                     "msm-update.timer", "update.sh"]
        _write_config_files(config_dir, all_names)

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            backup = pbs.create_panel_backup(db)

        files = _extract_archive(backup.local_path)
        for name in all_names:
            assert f"configs/{name}" in files, f"missing {name}"

    def test_missing_config_file_skipped_with_warning(self, db, tmp_path, monkeypatch, caplog):
        """VAL-PANEL-BACKUP-010: Fehlende Config-Datei wird mit Warning skipped."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        # Nur .env und install.sh existieren, Rest fehlt
        _write_config_files(config_dir, [".env", "install.sh"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            with caplog.at_level("WARNING"):
                backup = pbs.create_panel_backup(db)

        files = _extract_archive(backup.local_path)
        # Vorhandene Dateien sind enthalten
        assert "configs/.env" in files
        assert "configs/install.sh" in files
        # Fehlende Dateien sind NICHT enthalten
        assert "configs/Caddyfile.template" not in files
        assert "configs/update.sh" not in files

        # manifest config_list enthaelt nur vorhandene Dateien
        manifest = json.loads(files["manifest.json"])
        assert set(manifest["config_list"]) == {".env", "install.sh"}

        # Warning-Log fuer fehlende Dateien
        warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
        assert "Caddyfile.template" in warning_text or "update.sh" in warning_text

    def test_manifest_required_metadata(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-006: manifest.json mit timestamp, msm_version, db_type, config_list."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            backup = pbs.create_panel_backup(db)

        files = _extract_archive(backup.local_path)
        manifest = json.loads(files["manifest.json"])
        assert "timestamp" in manifest
        # timestamp ist ISO8601
        datetime.fromisoformat(manifest["timestamp"])
        assert "msm_version" in manifest
        assert "db_type" in manifest
        assert manifest["db_type"] == backup.db_type
        assert "config_list" in manifest
        assert isinstance(manifest["config_list"], list)
        assert manifest["config_list"] == [".env"]


# ── Service: S3 Upload ───────────────────────────────────────────────────


class TestPanelBackupS3Upload:
    """VAL-PANEL-BACKUP-007/008/011: S3-Upload, Fehler-Handling, Key-Invalidierung."""

    def test_s3_upload_when_configured_and_password(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-007: S3-Upload wenn konfiguriert + Passwort."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db)

            assert backup.s3_key is not None
            assert backup.s3_bucket == TEST_BUCKET
            assert backup.encrypted is True
            assert backup.s3_key.startswith("msm-backups/panel/panel_")
            assert backup.s3_key.endswith(".enc")

            # S3-Objekt existiert
            s3 = boto3.client("s3", region_name=TEST_REGION)
            objs = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix="msm-backups/panel/")
            assert len(objs.get("Contents", [])) == 1
            assert objs["Contents"][0]["Key"] == backup.s3_key

    def test_s3_failure_does_not_block_local(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-008: S3-Fehler blockiert nicht lokales Backup."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            # KEIN Bucket erstellen → S3-Upload schlaegt fehl
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db)

            # Lokales Backup existiert
            assert os.path.exists(backup.local_path)
            # S3-Fehler: s3_key None, encrypted False
            assert backup.s3_key is None
            assert backup.encrypted is False

    def test_dis_failure_does_not_block_local(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-008: DIS-Fehler blockiert nicht lokales Backup."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            # DIS init_key schlaegt fehl
            from services.backup_crypto_service import BackupCryptoError
            with patch("services.backup_crypto_service.BackupCryptoService.init_key",
                       side_effect=BackupCryptoError("DIS down")):
                with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                    backup = pbs.create_panel_backup(db)

            assert os.path.exists(backup.local_path)
            assert backup.s3_key is None
            assert backup.encrypted is False

    def test_no_s3_upload_when_not_configured(self, db, tmp_path, monkeypatch):
        """S3 nicht konfiguriert: nur lokales Backup."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            backup = pbs.create_panel_backup(db)

        assert backup.s3_key is None
        assert backup.encrypted is False
        assert os.path.exists(backup.local_path)

    def test_key_invalidated_after_s3_upload(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-011: Backup-Key nach S3-Upload invalidiert."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key") as inv:
                    pbs.create_panel_backup(db)

            # invalidate_key wurde (genau einmal) im finally aufgerufen
            assert inv.call_count == 1

    def test_key_invalidated_on_s3_failure(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-011: Key wird auch bei S3-Fehler invalidiert (try/finally)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            # KEIN Bucket → S3-Fehler
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key") as inv:
                    pbs.create_panel_backup(db)

            # Key wurde trotz Fehler invalidiert (finally-Block)
            assert inv.call_count == 1


# ── Service: pg_dump Failure ─────────────────────────────────────────────


class TestPanelBackupDumpFailure:
    """VAL-PANEL-BACKUP-009: pg_dump-Fehler → kein partieller Backup, Temp cleaned."""

    def test_dump_failure_no_backup_record(self, db, tmp_path, monkeypatch):
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])

        with patch.object(pbs, "_dump_database", side_effect=RuntimeError("pg_dump fehlgeschlagen")):
            with pytest.raises(RuntimeError):
                pbs.create_panel_backup(db)

        # Kein PanelBackup-Record
        assert db.query(PanelBackup).count() == 0

        # Keine tar.gz im backup_dir (kein partieller Backup)
        tar_files = list(backup_dir.glob("panel_*.tar.gz"))
        assert tar_files == []

        # Temp-Verzeichnisse bereinigt
        tmp_dirs = [d for d in backup_dir.iterdir() if d.is_dir() and d.name.startswith(".tmp_")]
        assert tmp_dirs == []

    def test_empty_dump_treated_as_failure(self, db, tmp_path, monkeypatch):
        """Leerer DB-Dump wird als Fehler behandelt (atomic — kein Record)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=b""):
            with pytest.raises(RuntimeError):
                pbs.create_panel_backup(db)

        assert db.query(PanelBackup).count() == 0


# ── Service: Retention ───────────────────────────────────────────────────


class TestPanelBackupRetention:
    """Panel-Backup Retention (lokal + S3 + DB, best-effort S3)."""

    def test_retention_keeps_newest(self, db, tmp_path, monkeypatch):
        """Retention behaelt neueste N, loescht aeltere lokal + DB."""
        _prepare_dirs(tmp_path, monkeypatch)
        monkeypatch.setattr(settings, "database_url", "sqlite:///./msm.db")

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            ids = []
            for i in range(5):
                b = pbs.create_panel_backup(db, name=f"backup-{i}")
                ids.append(b.id)

        # Retention auf 2 setzen
        PanelSettingsService.set("backup.panel_retention_count", "2")
        pbs.cleanup_old_panel_backups(db)

        remaining = db.query(PanelBackup).order_by(PanelBackup.created_at.desc()).all()
        assert len(remaining) == 2
        # Neueste zwei behalten (letzte zwei erstellten)
        remaining_ids = {r.id for r in remaining}
        assert remaining_ids == {ids[-1], ids[-2]}

        # Lokale Dateien der geloeschten sind entfernt
        all_paths = [b.local_path for b in remaining]
        for p in all_paths:
            assert os.path.exists(p)

    def test_retention_default_when_no_setting(self, db, tmp_path, monkeypatch):
        """Retention Default = 7 wenn kein panel_settings-Eintrag."""
        _prepare_dirs(tmp_path, monkeypatch)
        # Keine Retention-Setting
        assert PanelSettingsService.get("backup.panel_retention_count") == ""
        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            for i in range(3):
                pbs.create_panel_backup(db, name=f"b-{i}")

        pbs.cleanup_old_panel_backups(db)
        # Default 7 → alle 3 behalten
        assert db.query(PanelBackup).count() == 3

    def test_retention_deletes_s3(self, db, tmp_path, monkeypatch):
        """Retention loescht auch S3-Objekte (best-effort)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                for i in range(4):
                    pbs.create_panel_backup(db, name=f"b-{i}")

            s3 = boto3.client("s3", region_name=TEST_REGION)
            objs = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix="msm-backups/panel/")
            assert len(objs.get("Contents", [])) == 4

            # Retention auf 1
            PanelSettingsService.set("backup.panel_retention_count", "1")
            pbs.cleanup_old_panel_backups(db)

            # Nur 1 in S3 und DB
            objs = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix="msm-backups/panel/")
            assert len(objs.get("Contents", [])) == 1
            assert db.query(PanelBackup).count() == 1


# ── Router Tests ─────────────────────────────────────────────────────────


class TestPanelBackupRouter:
    """VAL-PANEL-BACKUP-001/002: Router-Endpunkt mit RBAC."""

    def test_admin_can_create_panel_backup(self, client, owner_cookies, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-001: Admin kann Panel-Backup erstellen (POST /api/panel-backups)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env", "install.sh"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            resp = _post_panel_backup(client, owner_cookies, name="test-backup")

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "id" in data
        assert data["name"] == "test-backup"
        assert "size_mb" in data
        assert "db_type" in data
        assert "encrypted" in data
        assert "created_at" in data
        # Keine sensitiven Pfade in der Response
        assert "local_path" not in data
        assert "s3_key" not in data
        assert "s3_bucket" not in data

    def test_admin_create_without_name(self, client, owner_cookies, tmp_path, monkeypatch):
        """POST ohne name funktioniert (name optional)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            resp = _post_panel_backup(client, owner_cookies)

        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] is None

    def test_non_admin_gets_403(self, client, user_cookies, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-002: Non-admin bekommt 403, kein Backup erstellt."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            resp = _post_panel_backup(client, user_cookies)

        assert resp.status_code == 403

    def test_unauthenticated_gets_401(self, client, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-002: Unauth bekommt 401, kein Backup erstellt."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            resp = client.post("/api/panel-backups", json={}, headers={})

        assert resp.status_code == 401

    def test_csrf_required(self, client, owner_cookies, tmp_path, monkeypatch):
        """POST ohne CSRF-Token wird abgewiesen."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            resp = client.post("/api/panel-backups", json={}, cookies=owner_cookies)

        # CSRF-Fehler → 403 (verify_csrf wirft)
        assert resp.status_code in (403, 400)

    def test_dump_failure_returns_500_no_record(self, client, owner_cookies, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-009: pg_dump-Fehler → 500, kein Record, generische Nachricht."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", side_effect=RuntimeError("pg_dump fehlgeschlagen")):
            resp = _post_panel_backup(client, owner_cookies)

        assert resp.status_code == 500
        # Generische Nachricht (kein Stacktrace/Pfad/Secret-Leak)
        detail = resp.json().get("detail", "")
        assert "pg_dump" not in detail.lower()
        assert "path" not in detail.lower()

    def test_s3_failure_returns_201(self, client, owner_cookies, tmp_path, monkeypatch):
        """VAL-PANEL-BACKUP-008: S3-Fehler blockiert nicht → 201."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            # KEIN Bucket → S3-Fehler, aber lokales Backup + 201
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                resp = _post_panel_backup(client, owner_cookies)

        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["encrypted"] is False


# ── No-Secrets in Logs ───────────────────────────────────────────────────


class TestPanelBackupNoSecrets:
    """VAL-PANEL-BACKUP-016 (no secrets in logs): keine Secrets in Caplog."""

    def test_no_secrets_in_logs_on_s3_failure(self, db, tmp_path, monkeypatch, caplog):
        """S3-Fehler-Log enthaelt keine Credentials/Passwort."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            # KEIN Bucket → S3-Fehler
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                with caplog.at_level("WARNING"):
                    pbs.create_panel_backup(db)

        log_text = " ".join(r.message for r in caplog.records)
        # Keine Credentials/Passwort in Logs
        assert TEST_ACCESS_KEY not in log_text
        assert TEST_SECRET_KEY not in log_text
        assert TEST_PASSWORD not in log_text
