"""Tests fuer Panel-Backup-Scheduler und Settings (M3 panel-backup-scheduler).

Abgedeckte Assertions:
- VAL-PANEL-SCHED-001: Scheduler erstellt Panel-Backup im konfigurierten Intervall
- VAL-PANEL-SCHED-002: Scheduler respektiert enabled-Flag (disabled = kein Job)
- VAL-PANEL-SCHED-003: Geplantes Backup laedt zu S3 hoch + fuehrt Retention aus
- VAL-PANEL-SCHED-004: Scheduler-Job wird bei Settings-Aenderung rescheduled/removed
- VAL-PANEL-SCHED-005: Geplantes Backup-Fehler crashed den Scheduler nicht
- VAL-PANEL-SETTINGS-001: Admin kann GET/PATCH settings (Defaults bei fehlenden Werten)
- VAL-PANEL-SETTINGS-002: Settings-Validierung (interval>0, retention>=1, partial PATCH)
- VAL-PANEL-SETTINGS-003: GET settings leakt keine Secrets
- VAL-CROSS-008: Panel-Neustart -> Scheduler nimmt Jobs aus panel_settings wieder auf
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from config import settings
from models import PanelBackup
from services import panel_backup_service as pbs
from services import scheduler_service
from services.backup_config_service import BackupConfigService
from services.panel_settings_service import PanelSettingsService
from services.scheduler_service import (
    PANEL_BACKUP_JOB_ID,
    _panel_backup_task,
    get_scheduler,
    sync_panel_backup_schedule,
)

TEST_BUCKET = "msm-panel-sched-bucket"
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "****************************************"
TEST_PASSWORD = "*********************!"
TEST_S3_ENDPOINT = ""
TEST_NEW_BUCKET = "msm-panel-sched-bucket-2"

_FAKE_SQLITE_DUMP = b"BEGIN TRANSACTION;\nCREATE TABLE users (id INTEGER PRIMARY KEY);\nCOMMIT;\n"


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_scheduler():
    """Scheduler-Jobs vor/nach jedem Test bereinigen."""
    scheduler = get_scheduler()
    for job in list(scheduler.get_jobs()):
        try:
            scheduler.remove_job(job.id)
        except Exception:
            pass
    yield
    scheduler = get_scheduler()
    for job in list(scheduler.get_jobs()):
        try:
            scheduler.remove_job(job.id)
        except Exception:
            pass


def _prepare_dirs(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    config_dir = tmp_path / "config"
    backup_dir = tmp_path / "backups" / "panel"
    config_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "panel_config_dir", str(config_dir))
    monkeypatch.setattr(settings, "panel_backup_dir", str(backup_dir))
    return config_dir, backup_dir


def _write_config_files(config_dir: Path, names: list[str]) -> None:
    for name in names:
        (config_dir / name).write_text(f"# content of {name}\n", encoding="utf-8")


def _setup_s3_config(bucket: str = TEST_BUCKET) -> None:
    BackupConfigService.set_s3_config(
        endpoint=TEST_S3_ENDPOINT,
        access_key=TEST_ACCESS_KEY,
        secret_key=TEST_SECRET_KEY,
        bucket=bucket,
        region=TEST_REGION,
    )


def _setup_backup_password() -> None:
    BackupConfigService.set_backup_password(TEST_PASSWORD)


def _create_moto_bucket(bucket: str = TEST_BUCKET) -> None:
    boto3.client("s3", region_name=TEST_REGION).create_bucket(Bucket=bucket)


def _get_settings(client, cookies):
    return client.get("/api/panel-backups/settings", cookies=cookies)


def _patch_settings(client, cookies, body: dict):
    csrf = cookies.get("__Secure-csrf_token")
    return client.patch(
        "/api/panel-backups/settings",
        json=body,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def _job_exists(job_id: str = PANEL_BACKUP_JOB_ID) -> bool:
    scheduler = get_scheduler()
    return any(j.id == job_id for j in scheduler.get_jobs())


def _get_job(job_id: str = PANEL_BACKUP_JOB_ID):
    scheduler = get_scheduler()
    for j in scheduler.get_jobs():
        if j.id == job_id:
            return j
    return None


# ── VAL-PANEL-SETTINGS-001: GET/PATCH settings with defaults ───────────


class TestPanelBackupSettingsGet:
    """VAL-PANEL-SETTINGS-001: Admin kann GET settings (Defaults bei fehlenden Werten)."""

    def test_get_defaults_when_no_settings(self, client, owner_cookies):
        """GET ohne gesetzte Settings liefert Defaults (enabled=False, 24h, 7)."""
        resp = _get_settings(client, owner_cookies)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["enabled"] is False
        assert data["interval_hours"] == 24
        assert data["retention_count"] == 7

    def test_get_returns_stored_values(self, client, owner_cookies):
        """GET gibt gesetzte Werte zurueck."""
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "12")
        PanelSettingsService.set("backup.panel_retention_count", "3")
        PanelSettingsService.invalidate_cache()

        resp = _get_settings(client, owner_cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["interval_hours"] == 12
        assert data["retention_count"] == 3

    def test_get_non_admin_403(self, client, user_cookies):
        """Non-admin bekommt 403 auf GET settings."""
        resp = _get_settings(client, user_cookies)
        assert resp.status_code == 403

    def test_get_unauthenticated_401(self, client):
        """Unauth bekommt 401 auf GET settings."""
        resp = client.get("/api/panel-backups/settings")
        assert resp.status_code == 401


# ── VAL-PANEL-SETTINGS-001: PATCH settings ─────────────────────────────


class TestPanelBackupSettingsPatch:
    """VAL-PANEL-SETTINGS-001: Admin kann PATCH settings (partial)."""

    def test_patch_all_fields(self, client, owner_cookies):
        """PATCH setzt alle Felder."""
        resp = _patch_settings(client, owner_cookies, {
            "enabled": True,
            "interval_hours": 6,
            "retention_count": 5,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["enabled"] is True
        assert data["interval_hours"] == 6
        assert data["retention_count"] == 5

    def test_patch_partial_enabled_only(self, client, owner_cookies):
        """Partial PATCH: nur enabled aendern, andere bleiben (Default)."""
        resp = _patch_settings(client, owner_cookies, {"enabled": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["interval_hours"] == 24
        assert data["retention_count"] == 7

    def test_patch_partial_interval_only(self, client, owner_cookies):
        """Partial PATCH: nur interval aendern."""
        # Erst alle setzen
        _patch_settings(client, owner_cookies, {
            "enabled": True, "interval_hours": 12, "retention_count": 3,
        })
        # Dann nur interval aendern
        resp = _patch_settings(client, owner_cookies, {"interval_hours": 6})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["interval_hours"] == 6
        assert data["retention_count"] == 3

    def test_patch_partial_retention_only(self, client, owner_cookies):
        """Partial PATCH: nur retention aendern."""
        _patch_settings(client, owner_cookies, {
            "enabled": False, "interval_hours": 8, "retention_count": 5,
        })
        resp = _patch_settings(client, owner_cookies, {"retention_count": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["interval_hours"] == 8
        assert data["retention_count"] == 2

    def test_patch_non_admin_403(self, client, user_cookies):
        """Non-admin bekommt 403 auf PATCH settings."""
        csrf = user_cookies.get("__Secure-csrf_token")
        resp = client.patch(
            "/api/panel-backups/settings",
            json={"enabled": True},
            cookies=user_cookies,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 403

    def test_patch_unauthenticated_401(self, client):
        """Unauth bekommt 401 auf PATCH settings."""
        resp = client.patch("/api/panel-backups/settings", json={"enabled": True})
        assert resp.status_code == 401

    def test_patch_csrf_required(self, client, owner_cookies):
        """PATCH ohne CSRF wird abgewiesen."""
        resp = client.patch(
            "/api/panel-backups/settings",
            json={"enabled": True},
            cookies=owner_cookies,
        )
        assert resp.status_code in (403, 400)


# ── VAL-PANEL-SETTINGS-002: Settings validation ────────────────────────


class TestPanelBackupSettingsValidation:
    """VAL-PANEL-SETTINGS-002: Validierung (interval>0, retention>=1, boundary)."""

    def test_patch_interval_zero_rejected(self, client, owner_cookies):
        """interval_hours=0 -> 400."""
        resp = _patch_settings(client, owner_cookies, {"interval_hours": 0})
        assert resp.status_code == 400

    def test_patch_interval_negative_rejected(self, client, owner_cookies):
        """interval_hours=-1 -> 400."""
        resp = _patch_settings(client, owner_cookies, {"interval_hours": -1})
        assert resp.status_code == 400

    def test_patch_retention_zero_rejected(self, client, owner_cookies):
        """retention_count=0 -> 400."""
        resp = _patch_settings(client, owner_cookies, {"retention_count": 0})
        assert resp.status_code == 400

    def test_patch_retention_negative_rejected(self, client, owner_cookies):
        """retention_count=-1 -> 400."""
        resp = _patch_settings(client, owner_cookies, {"retention_count": -1})
        assert resp.status_code == 400

    def test_patch_boundary_interval_1_accepted(self, client, owner_cookies):
        """Boundary: interval_hours=1 wird akzeptiert."""
        resp = _patch_settings(client, owner_cookies, {"interval_hours": 1})
        assert resp.status_code == 200
        assert resp.json()["interval_hours"] == 1

    def test_patch_boundary_retention_1_accepted(self, client, owner_cookies):
        """Boundary: retention_count=1 wird akzeptiert."""
        resp = _patch_settings(client, owner_cookies, {"retention_count": 1})
        assert resp.status_code == 200
        assert resp.json()["retention_count"] == 1

    def test_patch_invalid_does_not_persist(self, client, owner_cookies):
        """Bei Validierungsfehler werden keine Werte persistiert."""
        _patch_settings(client, owner_cookies, {"interval_hours": 10})
        # Invalid value
        _patch_settings(client, owner_cookies, {"interval_hours": -5})
        # Originalwert bleibt
        PanelSettingsService.invalidate_cache()
        data = _get_settings(client, owner_cookies).json()
        assert data["interval_hours"] == 10


# ── VAL-PANEL-SETTINGS-003: GET settings no secrets ────────────────────


class TestPanelBackupSettingsNoSecrets:
    """VAL-PANEL-SETTINGS-003: GET settings leakt keine Secrets."""

    def test_get_only_three_keys(self, client, owner_cookies):
        """GET-Response hat nur enabled, interval_hours, retention_count."""
        # S3-Config + Passwort setzen (sollten NICHT in settings-Response erscheinen)
        _setup_s3_config()
        _setup_backup_password()

        resp = _get_settings(client, owner_cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"enabled", "interval_hours", "retention_count"}

    def test_get_no_secrets_in_body(self, client, owner_cookies):
        """Response-Body enthaelt keine S3-Credentials/Passwort/Salt."""
        _setup_s3_config()
        _setup_backup_password()

        body = _get_settings(client, owner_cookies).text
        assert TEST_ACCESS_KEY not in body
        assert TEST_SECRET_KEY not in body
        assert TEST_PASSWORD not in body
        assert "s3_" not in body
        assert "password" not in body.lower()
        assert "salt" not in body.lower()
        assert "encrypted" not in body.lower()


# ── VAL-PANEL-SCHED-001/002: Scheduler job from settings ───────────────


class TestPanelBackupSchedulerJob:
    """VAL-PANEL-SCHED-001/002: Scheduler erstellt/entfernt Job basierend auf settings."""

    def test_sync_disabled_no_job(self):
        """VAL-PANEL-SCHED-002: enabled=False -> kein Job."""
        PanelSettingsService.set("backup.panel_enabled", "false")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        assert not _job_exists()

    def test_sync_enabled_adds_job(self):
        """VAL-PANEL-SCHED-001: enabled=True -> Job mit IntervalTrigger."""
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "24")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        job = _get_job()
        assert job is not None
        assert job.id == PANEL_BACKUP_JOB_ID
        # IntervalTrigger.interval ist timedelta(hours=24)
        assert job.trigger.interval.total_seconds() == 24 * 3600

    def test_sync_respects_interval_value(self):
        """VAL-PANEL-SCHED-001: Job nutzt konfiguriertes Intervall."""
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "6")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        job = _get_job()
        assert job is not None
        assert job.trigger.interval.total_seconds() == 6 * 3600

    def test_sync_removes_job_when_disabled(self):
        """VAL-PANEL-SCHED-002: disabled entfernt bestehenden Job."""
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "12")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        assert _job_exists()

        # Jetzt deaktivieren
        PanelSettingsService.set("backup.panel_enabled", "false")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        assert not _job_exists()

    def test_sync_no_job_when_defaults(self):
        """Defaults (enabled=False) -> kein Job."""
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        assert not _job_exists()


# ── VAL-PANEL-SCHED-004: Scheduler job updates on settings change ──────


class TestPanelBackupSchedulerReschedule:
    """VAL-PANEL-SCHED-004: PATCH settings rescheduled/removed den Job live."""

    def test_patch_enabled_true_adds_job(self, client, owner_cookies):
        """PATCH enabled=True -> Job wird hinzugefuegt."""
        assert not _job_exists()
        resp = _patch_settings(client, owner_cookies, {
            "enabled": True, "interval_hours": 24,
        })
        assert resp.status_code == 200
        assert _job_exists()

    def test_patch_enabled_false_removes_job(self, client, owner_cookies):
        """PATCH enabled=False -> Job wird entfernt."""
        _patch_settings(client, owner_cookies, {
            "enabled": True, "interval_hours": 24,
        })
        assert _job_exists()

        resp = _patch_settings(client, owner_cookies, {"enabled": False})
        assert resp.status_code == 200
        assert not _job_exists()

    def test_patch_interval_reschedules_job(self, client, owner_cookies):
        """PATCH interval_hours aendert Job-Intervall (reschedule)."""
        _patch_settings(client, owner_cookies, {
            "enabled": True, "interval_hours": 12,
        })
        job = _get_job()
        assert job is not None
        assert job.trigger.interval.total_seconds() == 12 * 3600

        resp = _patch_settings(client, owner_cookies, {"interval_hours": 6})
        assert resp.status_code == 200
        job = _get_job()
        assert job is not None
        assert job.trigger.interval.total_seconds() == 6 * 3600


# ── VAL-PANEL-SCHED-001: Scheduled task creates backup ─────────────────


class TestPanelBackupScheduledTask:
    """VAL-PANEL-SCHED-001: _panel_backup_task erstellt ein Panel-Backup."""

    def test_task_calls_create_panel_backup(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-SCHED-001: Task ruft create_panel_backup auf."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            with patch("services.scheduler_service.SessionLocal") as sl:
                sl.return_value = db
                original_close = db.close
                db.close = lambda: None  # type: ignore
                try:
                    asyncio.run(_panel_backup_task())
                finally:
                    db.close = original_close  # type: ignore

        # PanelBackup wurde erstellt
        assert db.query(PanelBackup).count() == 1

    def test_task_swallows_exception_no_crash(self, db):
        """VAL-PANEL-SCHED-005: Exception in create_panel_backup crashed Task nicht."""
        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.panel_backup_service.create_panel_backup",
                   side_effect=RuntimeError("pg_dump failed")):
            fake_db = MagicMock()
            sl.return_value = fake_db
            # Keine Exception propagiert
            asyncio.run(_panel_backup_task())
            fake_db.close.assert_called_once()

    def test_task_failure_scheduler_still_has_job(self, db):
        """VAL-PANEL-SCHED-005: Nach Fehler bleibt der Job im Scheduler."""
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "24")
        PanelSettingsService.invalidate_cache()
        sync_panel_backup_schedule()
        assert _job_exists()

        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.panel_backup_service.create_panel_backup",
                   side_effect=RuntimeError("boom")):
            fake_db = MagicMock()
            sl.return_value = fake_db
            asyncio.run(_panel_backup_task())

        # Job noch da
        assert _job_exists()

    def test_task_next_trigger_succeeds_after_failure(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-SCHED-005: Nach Fehler feuert der naechste Tick erfolgreich."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        original_close = db.close
        with patch("services.scheduler_service.SessionLocal") as sl:
            sl.return_value = db
            db.close = lambda: None  # type: ignore
            try:
                # Erster Tick: Fehler (wird abgefangen, kein Crash)
                with patch("services.panel_backup_service.create_panel_backup",
                           side_effect=RuntimeError("first tick fails")):
                    asyncio.run(_panel_backup_task())
                # Kein Backup erstellt beim ersten Tick
                assert db.query(PanelBackup).count() == 0

                # Zweiter Tick: erfolgreich (echter create_panel_backup, DB-Dump gemockt)
                with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                    asyncio.run(_panel_backup_task())
                assert db.query(PanelBackup).count() == 1
            finally:
                db.close = original_close  # type: ignore


# ── VAL-PANEL-SCHED-003: Scheduled backup uploads to S3 + retention ────


class TestPanelBackupScheduledS3:
    """VAL-PANEL-SCHED-003: Geplantes Backup laedt zu S3 hoch + Retention."""

    @mock_aws
    def test_scheduled_backup_uploads_to_s3(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-SCHED-003: Task laedt verschluesselt zu S3 hoch."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            with patch("services.scheduler_service.SessionLocal") as sl:
                sl.return_value = db
                original_close = db.close
                db.close = lambda: None  # type: ignore
                try:
                    asyncio.run(_panel_backup_task())
                finally:
                    db.close = original_close  # type: ignore

        backup = db.query(PanelBackup).first()
        assert backup is not None
        assert backup.s3_key is not None
        assert backup.encrypted is True
        assert backup.s3_bucket == TEST_BUCKET

        # S3-Objekt existiert
        s3 = boto3.client("s3", region_name=TEST_REGION)
        s3.head_object(Bucket=TEST_BUCKET, Key=backup.s3_key)

    @mock_aws
    def test_scheduled_backup_runs_retention(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-SCHED-003: Nach Backup laeuft Retention-Cleanup."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()
        # Retention auf 2
        PanelSettingsService.set("backup.panel_retention_count", "2")

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            with patch("services.scheduler_service.SessionLocal") as sl:
                sl.return_value = db
                original_close = db.close
                db.close = lambda: None  # type: ignore
                try:
                    # 4 Backups via Scheduler-Task erstellen
                    for _ in range(4):
                        asyncio.run(_panel_backup_task())
                finally:
                    db.close = original_close  # type: ignore

        # Retention hat auf 2 reduziert
        assert db.query(PanelBackup).count() == 2

    @mock_aws
    def test_scheduled_backup_local_only_without_s3(self, db, tmp_path, monkeypatch):
        """VAL-PANEL-SCHED-003: Ohne S3-Config bleibt Backup rein lokal."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        # Kein S3 konfiguriert

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            with patch("services.scheduler_service.SessionLocal") as sl:
                sl.return_value = db
                original_close = db.close
                db.close = lambda: None  # type: ignore
                try:
                    asyncio.run(_panel_backup_task())
                finally:
                    db.close = original_close  # type: ignore

        backup = db.query(PanelBackup).first()
        assert backup is not None
        assert backup.s3_key is None
        assert backup.encrypted is False
        assert os.path.exists(backup.local_path)


# ── VAL-CROSS-008: Panel restart - scheduler resumes ───────────────────


class TestPanelBackupSchedulerRestart:
    """VAL-CROSS-008: Panel-Neustart -> Scheduler nimmt Jobs wieder auf."""

    def test_init_server_schedules_resumes_panel_backup(self, db, tmp_path, monkeypatch):
        """VAL-CROSS-008: init_server_schedules stellt Panel-Backup-Job wieder her."""
        _prepare_dirs(tmp_path, monkeypatch)
        # Settings in panel_settings speichern (simuliert persistierte Config)
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "48")
        PanelSettingsService.invalidate_cache()

        # Scheduler leeren (simuliert frischer Start)
        scheduler = get_scheduler()
        for job in list(scheduler.get_jobs()):
            scheduler.remove_job(job.id)
        assert not _job_exists()

        # init_server_schedules (wie beim Panel-Start)
        scheduler_service.init_server_schedules(db)

        # Job wurde wiederhergestellt
        assert _job_exists()
        job = _get_job()
        assert job is not None
        assert job.trigger.interval.total_seconds() == 48 * 3600

    def test_init_server_schedules_no_job_when_disabled(self, db):
        """VAL-CROSS-008: Bei disabled wird kein Job beim Start registriert."""
        PanelSettingsService.set("backup.panel_enabled", "false")
        PanelSettingsService.invalidate_cache()

        scheduler = get_scheduler()
        for job in list(scheduler.get_jobs()):
            scheduler.remove_job(job.id)

        scheduler_service.init_server_schedules(db)
        assert not _job_exists()

    def test_init_resumes_with_config_after_s3_change(self, db, tmp_path, monkeypatch):
        """VAL-CROSS-008: Config + Backups bleiben erhalten, S3-Referenz konsistent."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config(TEST_BUCKET)
        _setup_backup_password()

        # Backup mit Bucket A erstellen
        with mock_aws():
            _create_moto_bucket(TEST_BUCKET)
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db, name="pre-restart")
            assert backup.s3_bucket == TEST_BUCKET
            old_s3_key = backup.s3_key
            old_s3_bucket = backup.s3_bucket

        # S3-Config aendern (neuer Bucket)
        _setup_s3_config(TEST_NEW_BUCKET)
        # Scheduler-Job leeren + wiederherstellen (simuliert Neustart)
        PanelSettingsService.set("backup.panel_enabled", "true")
        PanelSettingsService.set("backup.panel_interval_hours", "24")
        PanelSettingsService.invalidate_cache()

        scheduler = get_scheduler()
        for job in list(scheduler.get_jobs()):
            scheduler.remove_job(job.id)

        scheduler_service.init_server_schedules(db)
        assert _job_exists()

        # Altes Backup referenziert noch alten Bucket
        db.refresh(backup)
        assert backup.s3_bucket == old_s3_bucket
        assert backup.s3_key == old_s3_key

        # Neues Backup geht in neuen Bucket
        with mock_aws():
            _create_moto_bucket(TEST_NEW_BUCKET)
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                new_backup = pbs.create_panel_backup(db, name="post-restart")
            assert new_backup.s3_bucket == TEST_NEW_BUCKET
            assert new_backup.s3_bucket != old_s3_bucket
