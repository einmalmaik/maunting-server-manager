"""Tests fuer Panel Backup Ops (list, delete, retention, RBAC, no-secrets).

Abgedeckte Assertions:
- VAL-PANEL-BACKUP-012: Admin kann Panel-Backups auflisten (sorted desc, keine sensitiven Pfade)
- VAL-PANEL-BACKUP-013: Admin kann Panel-Backup loeschen (lokal + S3 + DB, best-effort S3, idempotent)
- VAL-PANEL-BACKUP-014: Retention (aelteste jenseits keep, lokal + S3 + DB, best-effort S3, laeuft nach create)
- VAL-PANEL-BACKUP-015: Alle panel-backup Endpunkte erfordern admin (panel.settings.write)
- VAL-PANEL-BACKUP-016: Keine Secrets in API-Responses oder Logs
"""
from __future__ import annotations

import json
import os
import tarfile
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from config import settings
from models import PanelBackup
from services import panel_backup_service as pbs
from services.backup_config_service import BackupConfigService
from services.panel_settings_service import PanelSettingsService

TEST_BUCKET = "msm-panel-ops-bucket"
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "****************************************"
TEST_PASSWORD = "********************!"

_FAKE_SQLITE_DUMP = b"BEGIN TRANSACTION;\nCREATE TABLE users (id INTEGER PRIMARY KEY);\nCOMMIT;\n"


# ── Helper (analog test_panel_backup_service.py) ────────────────────────


@pytest.fixture(autouse=True)
def _postgres_panel_runtime(monkeypatch):
    monkeypatch.setattr(
        settings,
        "database_url",
        "postgresql+psycopg2://msm:test@localhost/msm",
    )


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


def _setup_s3_config() -> None:
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


def _create_backups(db, n: int, *, names: list[str] | None = None) -> list[PanelBackup]:
    """Erstellt n Panel-Backups (mit gemocktem DB-Dump). Gibt die Records zurueck."""
    created: list[PanelBackup] = []
    with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
        for i in range(n):
            name = names[i] if names else f"backup-{i}"
            b = pbs.create_panel_backup(db, name=name)
            created.append(b)
    return created


def _get(client, cookies):
    return client.get("/api/panel-backups", cookies=cookies)


def _delete(client, cookies, backup_id: int):
    csrf = cookies.get("__Secure-csrf_token")
    return client.delete(
        f"/api/panel-backups/{backup_id}",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def _post(client, cookies, *, name: str | None = None):
    csrf = cookies.get("__Secure-csrf_token")
    body = {"name": name} if name else {}
    return client.post(
        "/api/panel-backups",
        json=body,
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


# ── VAL-PANEL-BACKUP-012: List ──────────────────────────────────────────


class TestPanelBackupList:
    """VAL-PANEL-BACKUP-012: Admin kann Panel-Backups auflisten."""

    def test_list_sorted_desc(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Liste ist nach created_at desc sortiert."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backups = _create_backups(db, 3)

        resp = _get(client, owner_cookies)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 3
        # Sortiert desc: neueste zuerst
        created_times = [item["created_at"] for item in data]
        assert created_times == sorted(created_times, reverse=True)
        # Neueste ID zuerst (letzte erstellte hat hoechste created_at)
        assert data[0]["id"] == backups[-1].id

    def test_list_no_sensitive_paths(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """List-Items enthalten keine local_path, s3_key, s3_bucket."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _create_backups(db, 2)

        resp = _get(client, owner_cookies)
        assert resp.status_code == 200
        for item in resp.json():
            assert "local_path" not in item
            assert "s3_key" not in item
            assert "s3_bucket" not in item

    def test_list_required_fields(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Jedes Item hat id, name, size_mb, created_at, s3_status, db_type."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _create_backups(db, 1)

        resp = _get(client, owner_cookies)
        assert resp.status_code == 200
        item = resp.json()[0]
        assert {"id", "name", "size_mb", "created_at", "s3_status", "db_type", "encrypted"} <= set(item.keys())

    def test_list_s3_status_cloud(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """S3-backed Backup zeigt s3_status='cloud'."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                pbs.create_panel_backup(db, name="cloud-bkp")

            resp = _get(client, owner_cookies)
            assert resp.status_code == 200
            assert resp.json()[0]["s3_status"] == "cloud"
            assert resp.json()[0]["encrypted"] is True

    def test_list_s3_status_local(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Local-only Backup zeigt s3_status='local'."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _create_backups(db, 1)

        resp = _get(client, owner_cookies)
        assert resp.status_code == 200
        assert resp.json()[0]["s3_status"] == "local"
        assert resp.json()[0]["encrypted"] is False

    def test_list_empty(self, db, client, owner_cookies):
        """Leere Liste gibt [] zurueck."""
        resp = _get(client, owner_cookies)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_non_admin_403(self, db, client, user_cookies):
        """Non-admin bekommt 403 auf GET /api/panel-backups."""
        resp = _get(client, user_cookies)
        assert resp.status_code == 403

    def test_list_unauthenticated_401(self, db, client):
        """Unauth bekommt 401 auf GET /api/panel-backups."""
        resp = client.get("/api/panel-backups")
        assert resp.status_code == 401


# ── VAL-PANEL-BACKUP-013: Delete ────────────────────────────────────────


class TestPanelBackupDelete:
    """VAL-PANEL-BACKUP-013: Admin kann Panel-Backup loeschen (lokal+S3+DB, best-effort, idempotent)."""

    def test_delete_removes_local_s3_db(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Delete entfernt lokale Datei, S3-Objekt und DB-Row."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db, name="del-me")

            bid = backup.id
            local_path = backup.local_path
            s3_key = backup.s3_key
            assert os.path.exists(local_path)
            assert s3_key is not None

            resp = _delete(client, owner_cookies, bid)
            assert resp.status_code == 200, resp.text
            assert resp.json()["deleted"] is True

            # Lokale Datei entfernt
            assert not os.path.exists(local_path)
            # S3-Objekt entfernt
            s3 = boto3.client("s3", region_name=TEST_REGION)
            objs = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix=s3_key)
            assert objs.get("KeyCount", 0) == 0
            # DB-Row entfernt
            assert db.query(PanelBackup).filter(PanelBackup.id == bid).first() is None

    def test_delete_local_only(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Delete eines local-only Backups: nur lokal + DB (kein S3-Delete-Versuch)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]
        bid = backup.id
        local_path = backup.local_path
        assert backup.s3_key is None

        resp = _delete(client, owner_cookies, bid)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not os.path.exists(local_path)
        assert db.query(PanelBackup).filter(PanelBackup.id == bid).first() is None

    def test_delete_idempotent_missing_local(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Idempotent: lokale Datei fehlt -> DB-Row wird trotzdem entfernt."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]
        bid = backup.id
        # Lokale Datei manuell entfernen (simuliert fehlende Datei)
        os.remove(backup.local_path)
        assert not os.path.exists(backup.local_path)

        resp = _delete(client, owner_cookies, bid)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        # DB-Row wurde entfernt trotz fehlender lokaler Datei
        assert db.query(PanelBackup).filter(PanelBackup.id == bid).first() is None

    def test_delete_idempotent_nonexistent_id(self, db, client, owner_cookies):
        """Idempotent: nicht-existente ID -> 200, deleted=False."""
        resp = _delete(client, owner_cookies, 999999)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False

    def test_delete_s3_failure_does_not_block_local(self, db, client, owner_cookies, tmp_path, monkeypatch, caplog):
        """S3-Fehler blockiert nicht lokales Loeschen + DB-Row."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db, name="s3-fail")

            bid = backup.id
            local_path = backup.local_path

            # S3 delete_object schlaegt fehl
            with patch("services.s3_service.S3Service.delete_object", side_effect=RuntimeError("S3 down")):
                with caplog.at_level("WARNING"):
                    resp = _delete(client, owner_cookies, bid)

            assert resp.status_code == 200
            assert resp.json()["deleted"] is True
            # Lokale Datei dennoch entfernt
            assert not os.path.exists(local_path)
            # DB-Row dennoch entfernt
            assert db.query(PanelBackup).filter(PanelBackup.id == bid).first() is None
            # Warning-Log vorhanden (kein Secret-Leak)
            warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
            assert "S3-Delete" in warning_text or "Panel-Backup" in warning_text

    def test_delete_non_admin_403(self, db, client, user_cookies, tmp_path, monkeypatch):
        """Non-admin bekommt 403 auf DELETE."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]

        resp = _delete(client, user_cookies, backup.id)
        assert resp.status_code == 403
        # Backup noch vorhanden
        assert db.query(PanelBackup).filter(PanelBackup.id == backup.id).first() is not None

    def test_delete_unauthenticated_401(self, db, client, tmp_path, monkeypatch):
        """Unauth bekommt 401 auf DELETE."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]

        resp = client.delete(f"/api/panel-backups/{backup.id}")
        assert resp.status_code == 401

    def test_delete_csrf_required(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """DELETE ohne CSRF wird abgewiesen."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]

        resp = client.delete(f"/api/panel-backups/{backup.id}", cookies=owner_cookies)
        assert resp.status_code in (403, 400)


# ── VAL-PANEL-BACKUP-014: Retention ─────────────────────────────────────


class TestPanelBackupRetentionOps:
    """VAL-PANEL-BACKUP-014: Retention (aelteste jenseits keep, lokal+S3+DB, best-effort S3, laeuft nach create)."""

    def test_retention_runs_after_manual_create(self, db, tmp_path, monkeypatch):
        """Retention laeuft nach manuellem create (create_panel_backup ruft cleanup auf)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        # Retention auf 2 setzen
        PanelSettingsService.set("backup.panel_retention_count", "2")

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            ids = []
            for i in range(4):
                b = pbs.create_panel_backup(db, name=f"b-{i}")
                ids.append(b.id)

        # Nach 4 Creates mit keep=2 duerfen nur die neuesten 2 bleiben
        remaining = db.query(PanelBackup).order_by(PanelBackup.created_at.desc()).all()
        assert len(remaining) == 2
        assert {r.id for r in remaining} == {ids[-1], ids[-2]}

    def test_retention_deletes_oldest_beyond_count(self, db, tmp_path, monkeypatch):
        """Retention loescht aelteste Backups jenseits keep (lokal + DB)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
            backups = _create_backups(db, 5)

        # Alle 5 vorhanden
        assert db.query(PanelBackup).count() == 5
        # Alle lokalen Dateien vorhanden
        for b in backups:
            assert os.path.exists(b.local_path)

        # Retention auf 3
        PanelSettingsService.set("backup.panel_retention_count", "3")
        pbs.cleanup_old_panel_backups(db)

        # Nur neueste 3 bleiben
        remaining = db.query(PanelBackup).order_by(PanelBackup.created_at.desc()).all()
        assert len(remaining) == 3
        remaining_ids = {r.id for r in remaining}
        assert remaining_ids == {b.id for b in backups[-3:]}

        # Geloeschte lokale Dateien entfernt
        for b in backups:
            if b.id not in remaining_ids:
                assert not os.path.exists(b.local_path)
            else:
                assert os.path.exists(b.local_path)

    def test_retention_deletes_s3_best_effort(self, db, tmp_path, monkeypatch):
        """Retention loescht S3-Objekte (best-effort)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backups = _create_backups(db, 4)

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

    def test_retention_s3_failure_continues(self, db, tmp_path, monkeypatch, caplog):
        """S3-Fehler bei Retention bricht nicht ab (lokal + DB werden trotzdem bereinigt)."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backups = _create_backups(db, 4)

            # S3 delete fuer die ersten beiden Loeschungen fehlschlagen lassen
            call_count = {"n": 0}
            original_delete = pbs.os.remove

            def flaky_s3_delete(key):
                call_count["n"] += 1
                if call_count["n"] <= 2:
                    raise RuntimeError("S3 transient error")
                # Ab dem 3. Aufruf: echter Delete (hier no-op, da gemockt)
                return None

            PanelSettingsService.set("backup.panel_retention_count", "1")
            with patch("services.s3_service.S3Service.delete_object", side_effect=flaky_s3_delete):
                with caplog.at_level("WARNING"):
                    pbs.cleanup_old_panel_backups(db)

            # Trotz S3-Fehlern: lokal + DB bereinigt (3 geloescht, 1 bleibt)
            assert db.query(PanelBackup).count() == 1
            # Warning-Log vorhanden
            warning_text = " ".join(r.message for r in caplog.records if r.levelname == "WARNING")
            assert "S3-Delete" in warning_text

    def test_retention_keep_all_when_under_limit(self, db, tmp_path, monkeypatch):
        """Wenn Anzahl < keep, wird nichts geloescht."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])

        _create_backups(db, 2)
        PanelSettingsService.set("backup.panel_retention_count", "5")
        pbs.cleanup_old_panel_backups(db)
        assert db.query(PanelBackup).count() == 2


# ── VAL-PANEL-BACKUP-015: Alle Endpunkte erfordern Admin ────────────────


class TestPanelBackupRBAC:
    """VAL-PANEL-BACKUP-015: Alle panel-backup Endpunkte erfordern panel.settings.write."""

    @pytest.mark.parametrize("method,path,use_body", [
        ("GET", "/api/panel-backups", False),
        ("POST", "/api/panel-backups", True),
    ])
    def test_non_admin_403_on_each_endpoint(self, client, user_cookies, tmp_path, monkeypatch, method, path, use_body):
        """Non-admin bekommt 403 auf jedem panel-backup Endpunkt."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        csrf = user_cookies.get("__Secure-csrf_token")
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        if use_body:
            resp = client.request(method, path, json={}, cookies=user_cookies, headers=headers)
        else:
            resp = client.request(method, path, cookies=user_cookies, headers=headers)
        assert resp.status_code == 403

    def test_non_admin_403_on_delete(self, db, client, user_cookies, tmp_path, monkeypatch):
        """Non-admin bekommt 403 auf DELETE."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]
        csrf = user_cookies.get("__Secure-csrf_token")
        headers = {"X-CSRF-Token": csrf} if csrf else {}
        resp = client.delete(f"/api/panel-backups/{backup.id}", cookies=user_cookies, headers=headers)
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,use_body", [
        ("GET", "/api/panel-backups", False),
        ("POST", "/api/panel-backups", True),
    ])
    def test_admin_not_403_on_each_endpoint(self, db, client, owner_cookies, tmp_path, monkeypatch, method, path, use_body):
        """Admin bekommt NICHT 403 auf jedem Endpunkt."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        csrf = owner_cookies.get("__Secure-csrf_token")
        headers = {"X-CSRF-Token": csrf}
        if method == "POST":
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                resp = client.post(path, json={}, cookies=owner_cookies, headers=headers)
        else:
            resp = client.get(path, cookies=owner_cookies, headers=headers)
        assert resp.status_code != 403

    def test_admin_not_403_on_delete(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Admin bekommt NICHT 403 auf DELETE."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        backup = _create_backups(db, 1)[0]
        resp = _delete(client, owner_cookies, backup.id)
        assert resp.status_code != 403
        assert resp.status_code == 200


# ── VAL-PANEL-BACKUP-016: No Secrets in Responses or Logs ───────────────


class TestPanelBackupNoSecretsOps:
    """VAL-PANEL-BACKUP-016: Keine Secrets in API-Responses oder Logs."""

    def test_list_response_no_secrets(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """List-Response enthaelt keine Credentials/Passwort/Pfade."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                pbs.create_panel_backup(db, name="secret-test")

        resp = _get(client, owner_cookies)
        body = resp.text
        assert TEST_ACCESS_KEY not in body
        assert TEST_SECRET_KEY not in body
        assert TEST_PASSWORD not in body
        # Keine sensitiven Pfade
        for item in resp.json():
            assert "local_path" not in item
            assert "s3_key" not in item
            assert "s3_bucket" not in item

    def test_delete_response_no_secrets(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Delete-Response enthaelt keine Credentials/Passwort/Pfade."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db, name="del-secret")

            resp = _delete(client, owner_cookies, backup.id)
            body = resp.text
            assert TEST_ACCESS_KEY not in body
            assert TEST_SECRET_KEY not in body
            assert TEST_PASSWORD not in body
            # Keine sensitiven Pfade in Response
            assert backup.local_path not in body
            if backup.s3_key:
                assert backup.s3_key not in body

    def test_delete_logs_no_secrets_on_s3_failure(self, db, client, owner_cookies, tmp_path, monkeypatch, caplog):
        """Logs enthalten keine Secrets bei S3-Fehler waehrend Delete."""
        _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(tmp_path / "config", [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            with patch.object(pbs, "_dump_database", return_value=_FAKE_SQLITE_DUMP):
                backup = pbs.create_panel_backup(db, name="log-test")

            with patch("services.s3_service.S3Service.delete_object", side_effect=RuntimeError("S3 boom")):
                with caplog.at_level("WARNING"):
                    _delete(client, owner_cookies, backup.id)

            log_text = " ".join(r.message for r in caplog.records)
            assert TEST_ACCESS_KEY not in log_text
            assert TEST_SECRET_KEY not in log_text
            assert TEST_PASSWORD not in log_text
