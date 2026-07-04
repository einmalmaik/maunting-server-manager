"""Tests fuer Scheduler S3-Integration (services/scheduler_service.py).

Abgedeckte Assertions:
- VAL-SCHED-001: Geplante Backups laden automatisch zu S3 hoch (via Orchestrator)
- VAL-SCHED-002: Auto-Backup-on-Start laedt zu S3 hoch (via Orchestrator)
- VAL-SCHED-003: S3-Fehler crashen den Scheduler nicht, lokales Backup bleibt
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from models import Backup, Server
from services.backup_config_service import BackupConfigService

TEST_BUCKET = "msm-scheduler-s3-bucket"
TEST_ENDPOINT = ""
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "****************************************"
TEST_PASSWORD = "*********************!"


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


def _make_real_tar(db, server: Server, tmp_path: Path) -> Backup:
    """Erstellt ein echtes tar.gz in tmp_path und einen Backup-DB-Record."""
    from services.backup_paths import create_full_backup_tar

    backup_dir = tmp_path / "backups" / str(server.id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"server_{server.id}_{timestamp}.tar.gz"
    filepath = str(backup_dir / filename)
    create_full_backup_tar(filepath, server.install_dir, server_id=server.id)
    size_mb = os.path.getsize(filepath) // (1024 * 1024)
    backup = Backup(server_id=server.id, filename=filepath, size_mb=size_mb)
    db.add(backup)
    db.commit()
    db.refresh(backup)
    return backup


def _patch_orchestrator(backup: Backup):
    """Patch backup_orchestrator.create_server_backup, um ein vordefiniertes Backup
    zurueckzugeben (umgeht /opt/msm-Pfadabhaengigkeit von run_backup im Orchestrator).

    Der Orchestrator wird weiterhin *aufgerufen* (VAL-SCHED-001), aber sein
    internes run_backup + S3-Upload werden durch dieses Patch ersetzt. Fuer
    echte S3-Upload-Tests siehe TestSchedulerS3Upload unten (echter Orchestrator).
    """
    def _fake(server_id, db, *, name=None, timeout_seconds=600):
        return backup
    return patch(
        "services.backup_orchestrator.create_server_backup",
        side_effect=_fake,
    )


# ── VAL-SCHED-001: Scheduler ruft Orchestrator auf ─────────────────────


class TestSchedulerUsesOrchestrator:
    """VAL-SCHED-001: Scheduler verwendet backup_orchestrator (nicht legacy run_backup)."""

    def test_scheduler_task_calls_orchestrator(self):
        """Scheduler-Job ruft backup_orchestrator.create_server_backup auf."""
        from services.scheduler_service import _backup_server_task

        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.backup_orchestrator.create_server_backup") as orch_mock:

            fake_db = MagicMock()
            fake_srv = MagicMock(id=42)
            fake_db.query.return_value.filter.return_value.first.return_value = fake_srv
            sl.return_value = fake_db

            asyncio.run(_backup_server_task(42))

            orch_mock.assert_called_once_with(42, fake_db, timeout_seconds=300)

    def test_scheduler_does_not_call_legacy_run_backup(self):
        """VAL-SCHED-001: backup_service.run_backup wird NICHT direkt vom Scheduler gerufen."""
        from services.scheduler_service import _backup_server_task

        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.backup_orchestrator.create_server_backup") as orch_mock, \
             patch("services.backup_service.run_backup") as legacy_mock:

            fake_db = MagicMock()
            fake_srv = MagicMock(id=42)
            fake_db.query.return_value.filter.return_value.first.return_value = fake_srv
            sl.return_value = fake_db

            asyncio.run(_backup_server_task(42))

            orch_mock.assert_called_once()
            # Legacy-Service wird nicht direkt vom Scheduler aufgerufen
            legacy_mock.assert_not_called()


# ── VAL-SCHED-001/003: Scheduled Backup mit S3 (echter Orchestrator) ──


class TestSchedulerS3Upload:
    """Scheduled Backup laedt via Orchestrator zu S3 hoch (VAL-SCHED-001)."""

    @mock_aws
    def test_scheduled_backup_uploads_to_s3(self, db, test_server, tmp_path):
        """VAL-SCHED-001: Scheduler-Backup laedt verschluesselt zu S3 hoch."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("scheduled s3 test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)
        # Simuliere den Scheduler-Aufruf: Patche run_backup *im backup_service-Modul*
        # (der Orchestrator ruft run_backup intern auf), damit der echte Orchestrator
        # den S3-Upload ausfuehrt, ohne /opt/msm-Pfade zu benoetigen.
        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from services.scheduler_service import _backup_server_task

        # Verhindere, dass der Scheduler-Task die Test-Session schliesst
        # (sonst wird backup_pre detached und kann nicht mehr refresh'd werden).
        original_close = db.close
        db.close = lambda: None  # type: ignore
        try:
            with patch("services.scheduler_service.SessionLocal") as sl, \
                 patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                sl.return_value = db
                asyncio.run(_backup_server_task(test_server.id))
        finally:
            db.close = original_close  # type: ignore

        db.refresh(backup_pre)
        assert backup_pre.s3_key is not None
        assert backup_pre.s3_bucket == TEST_BUCKET
        assert backup_pre.encrypted is True

        # S3-Objekt existiert
        client = boto3.client("s3", region_name="us-east-1")
        client.head_object(Bucket=TEST_BUCKET, Key=backup_pre.s3_key)

    @mock_aws
    def test_scheduled_backup_local_only_without_s3(self, db, test_server, tmp_path):
        """VAL-SCHED-001: Ohne S3-Config bleibt das geplante Backup rein lokal."""
        # Kein S3 konfiguriert
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("local only scheduled")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from services.scheduler_service import _backup_server_task

        original_close = db.close
        db.close = lambda: None  # type: ignore
        try:
            with patch("services.scheduler_service.SessionLocal") as sl, \
                 patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                sl.return_value = db
                asyncio.run(_backup_server_task(test_server.id))
        finally:
            db.close = original_close  # type: ignore

        db.refresh(backup_pre)
        assert backup_pre.s3_key is None
        assert backup_pre.encrypted is False
        # Lokale Datei bleibt erhalten
        assert os.path.exists(backup_pre.filename)


# ── VAL-SCHED-003: S3-Fehler crashen Scheduler nicht ───────────────────


class TestSchedulerS3FailureResilience:
    """VAL-SCHED-003: S3-Fehler blockieren Scheduler nicht, lokales Backup bleibt."""

    @mock_aws
    def test_s3_failure_scheduler_continues_local_preserved(self, db, test_server, tmp_path):
        """VAL-SCHED-003: S3-Fehler → lokales Backup bleibt, s3_key=null, kein Crash."""
        _setup_s3_config()
        _setup_backup_password()
        # KEIN moto Bucket → S3-Upload schlaegt fehl

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("s3 failure scheduler test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from services.scheduler_service import _backup_server_task

        original_close = db.close
        db.close = lambda: None  # type: ignore
        try:
            with patch("services.scheduler_service.SessionLocal") as sl, \
                 patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                sl.return_value = db
                # Sollte KEINE Exception werfen (Scheduler bleibt am Leben)
                asyncio.run(_backup_server_task(test_server.id))
        finally:
            db.close = original_close  # type: ignore

        db.refresh(backup_pre)
        # Lokales Backup existiert
        assert os.path.exists(backup_pre.filename)
        # S3-Felder null/False (Best-Effort)
        assert backup_pre.s3_key is None
        assert backup_pre.encrypted is False

    @mock_aws
    def test_s3_failure_scheduler_next_job_still_runs(self, db, test_server, tmp_path):
        """VAL-SCHED-003: Nach S3-Fehler kann der Scheduler erneut feuern."""
        _setup_s3_config()
        _setup_backup_password()
        # Kein Bucket → S3-Fehler

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("next job test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from services.scheduler_service import _backup_server_task

        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
            sl.return_value = db
            # Erster Tick: S3-Fehler wird intern abgefangen, kein Crash
            asyncio.run(_backup_server_task(test_server.id))
            # Zweiter Tick: Scheduler feuert erneut (Beweis: Scheduler laeuft weiter)
            asyncio.run(_backup_server_task(test_server.id))

        # Beide Ticks liefen durch, lokales Backup existiert weiterhin
        assert os.path.exists(backup_pre.filename)

    @mock_aws
    def test_s3_failure_warning_logged_no_secrets(self, db, test_server, tmp_path, caplog):
        """VAL-SCHED-003: Warning-Log bei S3-Fehler enthaelt keine Secrets."""
        import logging
        _setup_s3_config()
        _setup_backup_password()
        # Kein Bucket → Fehler

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("log secrets test")
        test_server.install_dir = str(install)
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from services.scheduler_service import _backup_server_task

        caplog.set_level(logging.WARNING)
        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
            sl.return_value = db
            asyncio.run(_backup_server_task(test_server.id))

        log_text = caplog.text
        # Keine Secrets im Log
        assert TEST_PASSWORD not in log_text
        assert TEST_ACCESS_KEY not in log_text
        assert TEST_SECRET_KEY not in log_text

    def test_orchestrator_exception_does_not_crash_scheduler(self):
        """VAL-SCHED-003: Orchestrator-Exception wird vom Scheduler abgefangen."""
        from services.scheduler_service import _backup_server_task

        with patch("services.scheduler_service.SessionLocal") as sl, \
             patch("services.backup_orchestrator.create_server_backup",
                   side_effect=RuntimeError("orchestrator crash")) as orch_mock:

            fake_db = MagicMock()
            fake_srv = MagicMock(id=42)
            fake_db.query.return_value.filter.return_value.first.return_value = fake_srv
            sl.return_value = fake_db

            # Keine Exception propagiert
            asyncio.run(_backup_server_task(42))

            orch_mock.assert_called_once()
            fake_db.close.assert_called_once()


# ── VAL-SCHED-002: Auto-Backup-on-Start via Orchestrator (Router) ──────


class TestAutoBackupOnStart:
    """VAL-SCHED-002: Auto-Backup-on-Start laedt via Orchestrator zu S3 hoch."""

    @mock_aws
    def test_auto_backup_on_start_uploads_to_s3(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SCHED-002: /auto Endpoint mit S3 konfiguriert → S3-Upload."""
        _setup_s3_config()
        _setup_backup_password()
        _create_moto_bucket()

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("auto start s3 test")
        test_server.install_dir = str(install)
        test_server.backup_on_start = True
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                with patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/auto",
                        headers={"X-MSM-Internal-Auto": "1"},
                    )
                assert resp.status_code == 200
                assert "erstellt" in resp.json()["message"].lower()

                db.refresh(backup_pre)
                # S3-Upload via Orchestrator passiert
                assert backup_pre.s3_key is not None
                assert backup_pre.encrypted is True
                # S3-Objekt existiert
                s3_client = boto3.client("s3", region_name="us-east-1")
                s3_client.head_object(Bucket=TEST_BUCKET, Key=backup_pre.s3_key)
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_auto_backup_on_start_local_only_without_s3(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SCHED-002: Auto-Backup ohne S3-Config bleibt rein lokal."""
        # Kein S3 konfiguriert
        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("auto start local only")
        test_server.install_dir = str(install)
        test_server.backup_on_start = True
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                with patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/auto",
                        headers={"X-MSM-Internal-Auto": "1"},
                    )
                assert resp.status_code == 200

                db.refresh(backup_pre)
                assert backup_pre.s3_key is None
                assert backup_pre.encrypted is False
        finally:
            app.dependency_overrides.clear()

    @mock_aws
    def test_auto_backup_on_start_s3_failure_local_preserved(self, db, test_server, owner_cookies, tmp_path):
        """VAL-SCHED-003: S3-Fehler bei Auto-Backup → lokal bleibt, kein Crash."""
        _setup_s3_config()
        _setup_backup_password()
        # Kein Bucket → S3-Fehler

        install = tmp_path / "install"
        install.mkdir()
        (install / "world.dat").write_text("auto start s3 failure")
        test_server.install_dir = str(install)
        test_server.backup_on_start = True
        db.commit()

        backup_pre = _make_real_tar(db, test_server, tmp_path)

        def _fake_run_backup(server_id, db, *, name=None, timeout_seconds=600,
                             encrypted=False, encryption_algorithm=None):
            return backup_pre

        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        def override_get_db():
            yield db

        app.dependency_overrides[get_db] = override_get_db
        try:
            with TestClient(app) as client:
                with patch("services.backup_service.run_backup", side_effect=_fake_run_backup):
                    resp = client.post(
                        f"/api/backups/{test_server.id}/auto",
                        headers={"X-MSM-Internal-Auto": "1"},
                    )
                # Auto-Backup-Endpoint gibt 200 (fehlgeschlagen) oder 200 (erstellt) -
                # beides ist OK, wichtig ist: kein 5xx-Crash, lokales Backup bleibt.
                assert resp.status_code == 200

                db.refresh(backup_pre)
                assert os.path.exists(backup_pre.filename)
                # S3-Fehler abgefangen → s3_key null
                assert backup_pre.s3_key is None
                assert backup_pre.encrypted is False
        finally:
            app.dependency_overrides.clear()


# ── VAL-SCHED-002: Pre-Start Backup via Orchestrator (Lifecycle) ───────


class TestPreStartBackupOrchestrator:
    """VAL-SCHED-002: _run_pre_start_backup_if_enabled verwendet den Orchestrator."""

    def test_pre_start_backup_calls_orchestrator_not_legacy(self):
        """VAL-SCHED-002: Pre-Start-Backup ruft Orchestrator (nicht legacy run_backup)."""
        from services.server_lifecycle_service import _run_pre_start_backup_if_enabled

        server = MagicMock(id=7, backup_on_start=True)
        fake_db = MagicMock()
        # Kein letztes Backup → kein Skip
        fake_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        with patch("services.backup_orchestrator.create_server_backup") as orch_mock, \
             patch("services.backup_service.run_backup") as legacy_mock, \
             patch("services.server_lifecycle_service._append_console_log"):
            _run_pre_start_backup_if_enabled(fake_db, server, context="Start")

            orch_mock.assert_called_once()
            args, kwargs = orch_mock.call_args
            assert args[0] == 7
            assert kwargs.get("timeout_seconds") == 300
            # Legacy-Service wird NICHT direkt vom Pre-Start-Backup aufgerufen
            legacy_mock.assert_not_called()

    def test_pre_start_backup_swallows_orchestrator_error(self):
        """VAL-SCHED-003: Orchestrator-Fehler blockiert Start nicht (Best-Effort)."""
        from services.server_lifecycle_service import _run_pre_start_backup_if_enabled

        server = MagicMock(id=7, backup_on_start=True)
        fake_db = MagicMock()
        fake_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        with patch("services.backup_orchestrator.create_server_backup",
                   side_effect=RuntimeError("orch fail")) as orch_mock, \
             patch("services.server_lifecycle_service._append_console_log"):
            # Keine Exception propagiert (Start wird fortgesetzt)
            _run_pre_start_backup_if_enabled(fake_db, server, context="Start")

            orch_mock.assert_called_once()
