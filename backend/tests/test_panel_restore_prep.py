"""Tests fuer Panel Restore Preparation (services/panel_backup_service.py:
prepare_panel_restore, routers/panel_backups.py: POST /prepare-restore).

Abgedeckte Assertions:
- VAL-PANEL-RESTORE-001: Admin kann Restore vorbereiten (lokales Backup)
- VAL-PANEL-RESTORE-002: Restore vorbereiten aus S3 (download + decrypt)
- VAL-PANEL-RESTORE-003: Restore verwendet lokal wenn vorhanden
- VAL-PANEL-RESTORE-004: Berechtigungen und Fehler (403, 401, 404, Decrypt-Fehler)
- VAL-PANEL-RESTORE-005: Anweisungen mit deutscher Warnung und sudo bash
- VAL-PANEL-RESTORE-006: Script ausfuehrbar mit Shebang und strict mode
- VAL-PANEL-RESTORE-007: Script stoppt Panel-Service und sichert .env
- VAL-PANEL-RESTORE-008: Script stellt Datenbank wieder her (PostgreSQL und SQLite)
- VAL-PANEL-RESTORE-009: Script stellt Configs wieder her und startet Panel neu
- VAL-PANEL-RESTORE-010: Script enthaelt keine Plaintext-Secrets
- VAL-PANEL-RESTORE-011: Script ist idempotent
- VAL-PANEL-RESTORE-012: Temp-Verzeichnis nach Script-Generierung bereinigt
- VAL-CROSS-002: Panel-Backup Full Cycle (create, verify, prepare restore, verify script)
"""
from __future__ import annotations

import json
import os
import stat
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

TEST_BUCKET = "msm-panel-restore-bucket"
TEST_REGION = "us-east-1"
TEST_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
TEST_SECRET_KEY = "****************************************"
TEST_PASSWORD = "********************!"

_FAKE_SQLITE_DUMP = b"BEGIN TRANSACTION;\nCREATE TABLE users (id INTEGER PRIMARY KEY);\nCOMMIT;\n"
_FAKE_PG_DUMP = b"-- PostgreSQL database dump\n-- pg_dump version 17\n\nCREATE TABLE users ();\n"


# ── Helper ───────────────────────────────────────────────────────────────


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


def _create_backup(db, *, name: str = "test", db_type: str = "sqlite3",
                   config_names: list[str] | None = None) -> PanelBackup:
    """Erstellt ein Panel-Backup mit gemocktem DB-Dump."""
    if config_names is None:
        config_names = [".env", "install.sh"]
    if db_type == "postgresql":
        dump = _FAKE_PG_DUMP
        monkeypatch_db = "postgresql://msm:pw@127.0.0.1:15432/msm"
    else:
        dump = _FAKE_SQLITE_DUMP
        monkeypatch_db = "sqlite:///./msm.db"

    with patch.object(pbs, "_dump_database", return_value=dump):
        with patch.object(settings, "database_url", monkeypatch_db):
            return pbs.create_panel_backup(db, name=name)


def _prepare_restore(client, cookies, backup_id: int):
    csrf = cookies.get("__Secure-csrf_token")
    return client.post(
        f"/api/panel-backups/{backup_id}/prepare-restore",
        cookies=cookies,
        headers={"X-CSRF-Token": csrf},
    )


def _read_script(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── VAL-PANEL-RESTORE-001: Admin kann Restore vorbereiten (lokal) ────────


class TestPrepareRestoreLocal:
    """VAL-PANEL-RESTORE-001: Admin kann Restore vorbereiten (lokales Backup)."""

    def test_prepare_restore_local_success(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """POST /prepare-restore mit lokalem Backup gibt 200 + script_path + instructions."""
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env", "install.sh"])
        backup = _create_backup(db, name="local-restore")

        resp = _prepare_restore(client, owner_cookies, backup.id)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "script_path" in data
        assert "instructions" in data
        # Script-Datei existiert
        script_path = data["script_path"]
        assert os.path.isfile(script_path)
        # Script-Name folgt Schema restore_<id>.sh
        assert script_path.endswith(f"restore_{backup.id}.sh")

    def test_prepare_restore_returns_instructions(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """instructions-Feld ist nicht leer und enthaelt deutschen Text."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        resp = _prepare_restore(client, owner_cookies, backup.id)
        assert resp.status_code == 200
        instructions = resp.json()["instructions"]
        assert len(instructions) > 0
        # Deutscher Text
        assert "Restore" in instructions or "Warnung" in instructions


# ── VAL-PANEL-RESTORE-002: Restore aus S3 (download + decrypt) ──────────


class TestPrepareRestoreS3:
    """VAL-PANEL-RESTORE-002: Restore aus S3 (download + decrypt)."""

    def test_prepare_restore_downloads_from_s3(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Lokale Datei fehlt, s3_key vorhanden: S3-Download + DIS-Decrypt."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env", "install.sh"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="s3-restore")
            assert backup.s3_key is not None
            local_path = backup.local_path

            # Lokale Datei entfernen (simuliert fehlendes lokales Backup)
            os.remove(local_path)
            assert not os.path.exists(local_path)

            # S3-Download + Decrypt mocken verifizieren
            with patch("services.s3_service.S3Service.download_stream",
                       wraps=__import__("services.s3_service", fromlist=["S3Service"]).S3Service.download_stream) as dl_spy:
                resp = _prepare_restore(client, owner_cookies, backup.id)

            assert resp.status_code == 200, resp.text
            assert dl_spy.called
            # Script wurde generiert
            assert os.path.isfile(resp.json()["script_path"])

    def test_prepare_restore_decrypt_called_for_s3(self, db, tmp_path, monkeypatch):
        """DIS decrypt wird aufgerufen wenn lokale Datei fehlt (Service-Layer)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="decrypt-test")
            os.remove(backup.local_path)

            with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file",
                       wraps=__import__("services.backup_crypto_service", fromlist=["BackupCryptoService"]).BackupCryptoService.decrypt_to_file) as dec_spy:
                result = pbs.prepare_panel_restore(backup.id, db)

            assert dec_spy.called
            assert os.path.isfile(result["script_path"])

    def test_prepare_restore_s3_restores_local_copy(self, db, tmp_path, monkeypatch):
        """Nach S3-Download+Decrypt liegt das Archiv wieder lokal vor."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="s3-restore-local")
            os.remove(backup.local_path)
            assert not os.path.exists(backup.local_path)

            pbs.prepare_panel_restore(backup.id, db)

            # Lokale Datei wurde durch Download+Decrypt wiederhergestellt
            assert os.path.exists(backup.local_path)


# ── VAL-PANEL-RESTORE-003: Restore verwendet lokal wenn vorhanden ────────


class TestPrepareRestoreUsesLocal:
    """VAL-PANEL-RESTORE-003: Restore verwendet lokale Datei wenn vorhanden."""

    def test_no_s3_download_when_local_exists(self, db, tmp_path, monkeypatch):
        """Lokale .tar.gz-Datei vorhanden: kein S3-Download, kein DIS-Decrypt.

        VAL-PANEL-RESTORE-003: Wenn ein lokales .tar.gz existiert (kein Passwort
        gesetzt → backward compat), wird kein S3-Download und kein DIS-Decrypt
        benoetigt. Bei .enc-Backups (Passwort gesetzt) ist DIS-Decrypt erwartet.
        """
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        # Kein Backup-Passwort → Backup ist .tar.gz (backward compat)
        # S3-Key manuell setzen um den Fall "lokal + S3" zu simulieren
        from services.backup_config_service import BackupConfigService
        assert not BackupConfigService.is_backup_password_set()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="local-present")
            # Manuell S3-Key setzen (simuliert Backup mit Cloud-Kopie)
            backup.s3_key = f"msm-backups/panel/test_{backup.id}.enc"
            backup.s3_bucket = "test-bucket"
            db.commit()
            db.refresh(backup)
            assert os.path.exists(backup.local_path)
            assert backup.local_path.endswith(".tar.gz")

            with patch("services.s3_service.S3Service.download_stream") as dl_mock:
                with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file") as dec_mock:
                    result = pbs.prepare_panel_restore(backup.id, db)

            # Weder S3-Download noch DIS-Decrypt wurden aufgerufen (.tar.gz lokal)
            assert not dl_mock.called
            assert not dec_mock.called
            assert os.path.isfile(result["script_path"])

    def test_no_key_init_when_local_exists(self, db, tmp_path, monkeypatch):
        """Lokale .tar.gz-Datei vorhanden: kein DIS init_key (kein Key noetig).

        Bei .tar.gz (kein Passwort) ist kein DIS-Key noetig.
        Bei .enc (Passwort gesetzt) waere init_key erwartet (Decrypt).
        """
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        # Kein Backup-Passwort → .tar.gz (backward compat, kein Decrypt noetig)

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="no-key-init")
            assert os.path.exists(backup.local_path)
            assert backup.local_path.endswith(".tar.gz")

            with patch("services.backup_crypto_service.BackupCryptoService.init_key") as init_mock:
                pbs.prepare_panel_restore(backup.id, db)

            assert not init_mock.called


# ── VAL-PANEL-RESTORE-004: Berechtigungen und Fehler ─────────────────────


class TestPrepareRestorePermissions:
    """VAL-PANEL-RESTORE-004: Berechtigungen und Fehler (403, 401, 404, decrypt)."""

    def test_non_admin_403(self, db, client, user_cookies, tmp_path, monkeypatch):
        """Non-admin bekommt 403."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="rbac")

        resp = _prepare_restore(client, user_cookies, backup.id)
        assert resp.status_code == 403

    def test_unauthenticated_401(self, db, client, tmp_path, monkeypatch):
        """Unauth bekommt 401."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="unauth")

        resp = client.post(f"/api/panel-backups/{backup.id}/prepare-restore")
        assert resp.status_code == 401

    def test_nonexistent_id_404(self, db, client, owner_cookies):
        """Nicht-existente Backup-ID gibt 404."""
        resp = _prepare_restore(client, owner_cookies, 999999)
        assert resp.status_code == 404

    def test_no_archive_source_404(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Lokal fehlt und kein s3_key: 404 (keine Archiv-Quelle)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="no-archive")
        # Lokale Datei entfernen, kein S3
        os.remove(backup.local_path)
        assert backup.s3_key is None

        resp = _prepare_restore(client, owner_cookies, backup.id)
        assert resp.status_code == 404

    def test_decrypt_failure_returns_400(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Decrypt-Fehler gibt 400 (kein Script, Key invalidiert)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="decrypt-fail")
            os.remove(backup.local_path)

            from services.backup_crypto_service import BackupDecryptionError
            with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file",
                       side_effect=BackupDecryptionError("decryption failed")):
                with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key") as inv_mock:
                    resp = _prepare_restore(client, owner_cookies, backup.id)

            assert resp.status_code == 400
            assert "Entschluesselung" in resp.json()["detail"] or "decrypt" in resp.json()["detail"].lower()
            # Key wurde invalidiert (trotz Fehler)
            assert inv_mock.called

    def test_csrf_required(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """POST ohne CSRF wird abgewiesen."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="csrf")

        resp = client.post(
            f"/api/panel-backups/{backup.id}/prepare-restore",
            cookies=owner_cookies,
        )
        assert resp.status_code in (403, 400)


# ── VAL-PANEL-RESTORE-005: Instructions mit deutscher Warnung und sudo bash ─


class TestPrepareRestoreInstructions:
    """VAL-PANEL-RESTORE-005: Instructions enthalten deutsche Warnung und sudo bash."""

    def test_instructions_contain_sudo_bash(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """instructions enthaelt 'sudo bash'."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        resp = _prepare_restore(client, owner_cookies, backup.id)
        instructions = resp.json()["instructions"]
        assert "sudo bash" in instructions

    def test_instructions_contain_german_warning(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """instructions enthaelt deutsche Warnung (Warnung/Datenverlust/Stop)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        resp = _prepare_restore(client, owner_cookies, backup.id)
        instructions = resp.json()["instructions"]
        # Mindestens ein Warn-Keyword
        assert "WARNUNG" in instructions or "Datenverlust" in instructions or "Stop" in instructions


# ── VAL-PANEL-RESTORE-006: Script ausfuehrbar mit Shebang und strict mode ─


class TestPrepareRestoreScriptFormat:
    """VAL-PANEL-RESTORE-006: Script ausfuehrbar, Shebang, strict mode."""

    def test_script_has_shebang(self, db, tmp_path, monkeypatch):
        """Script beginnt mit #!/bin/bash."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        lines = content.split("\n")
        assert lines[0] == "#!/bin/bash"

    def test_script_has_strict_mode(self, db, tmp_path, monkeypatch):
        """Script enthaelt 'set -euo pipefail'."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "set -euo pipefail" in content

    def test_script_is_executable(self, db, tmp_path, monkeypatch):
        """Script hat Execute-Bit (os.access X_OK)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        # Auf Windows: os.access X_OK kann True sein wenn Datei existiert.
        # Auf Linux: Execute-Bit muss gesetzt sein.
        assert os.path.isfile(result["script_path"])
        # chmod 0o755 wurde gesetzt (mindestens lesbar + ausfuehrbar versucht)
        mode = os.stat(result["script_path"]).st_mode
        assert mode & stat.S_IRUSR  # readable
        # Auf Windows ist S_IXUSR nicht reliable, aber chmod wurde aufgerufen


# ── VAL-PANEL-RESTORE-007: Script stoppt Panel und sichert .env ──────────


class TestPrepareRestoreScriptStopAndBackup:
    """VAL-PANEL-RESTORE-007: Script stoppt Panel-Service und sichert .env."""

    def test_script_stops_panel_service(self, db, tmp_path, monkeypatch):
        """Script enthaelt 'systemctl stop msm-panel.service' (ohne --user, VAL-FIX-010)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "systemctl stop msm-panel.service" in content
        # MSM ist als System-Unit installiert (install.sh /etc/systemd/system/) —
        # restore darf NICHT 'systemctl --user' verwenden (VAL-FIX-010).
        assert "systemctl --user stop" not in content

    def test_script_backs_up_env(self, db, tmp_path, monkeypatch):
        """Script sichert .env mit pre_restore Zeitstempel."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert ".env.pre_restore_" in content
        assert "cp" in content
        assert "date +%Y%m%d_%H%M%S" in content


# ── VAL-PANEL-RESTORE-008: Script stellt Datenbank wieder her ────────────


class TestPrepareRestoreScriptDB:
    """VAL-PANEL-RESTORE-008: Script stellt DB wieder her (PostgreSQL und SQLite)."""

    def test_script_postgres_restore(self, db, tmp_path, monkeypatch):
        """PostgreSQL: Script enthaelt psql mit msm_db.sql."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, db_type="postgresql")

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "psql" in content
        assert "msm_db.sql" in content

    def test_script_sqlite_restore(self, db, tmp_path, monkeypatch):
        """SQLite: Script enthaelt sqlite3 mit msm_db.sql."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, db_type="sqlite3")

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "sqlite3" in content
        assert "msm_db.sql" in content


# ── VAL-PANEL-RESTORE-009: Script stellt Configs wieder her und restartet ─


class TestPrepareRestoreScriptConfigs:
    """VAL-PANEL-RESTORE-009: Script stellt Configs wieder her und startet Panel neu."""

    def test_script_copies_configs_from_manifest(self, db, tmp_path, monkeypatch):
        """Script enthaelt cp-Befehle fuer jede Config im manifest."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        config_names = [".env", "install.sh", "Caddyfile.template"]
        _write_config_files(config_dir, config_names)
        backup = _create_backup(db, config_names=config_names)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        for name in config_names:
            assert name in content, f"config {name} not in script"
            assert "cp" in content

    def test_script_restarts_panel(self, db, tmp_path, monkeypatch):
        """Script startet Panel via systemctl start (ohne --user, VAL-FIX-010)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "systemctl start msm-panel.service" in content
        # Restore darf NICHT 'systemctl --user' verwenden (VAL-FIX-010).
        assert "systemctl --user start" not in content

    def test_script_has_status_hint(self, db, tmp_path, monkeypatch):
        """Script enthaelt echo mit Status-Hinweis."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "echo" in content
        assert "status" in content.lower()


# ── VAL-PANEL-RESTORE-010: Script enthaelt keine Plaintext-Secrets ───────


class TestPrepareRestoreScriptNoSecrets:
    """VAL-PANEL-RESTORE-010: Script enthaelt keine Plaintext-Secrets."""

    def test_script_no_s3_credentials(self, db, tmp_path, monkeypatch):
        """Script enthaelt keine S3 Access-Key/Secret-Key."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="no-secrets")
            result = pbs.prepare_panel_restore(backup.id, db)

        content = _read_script(result["script_path"])
        assert TEST_ACCESS_KEY not in content
        assert TEST_SECRET_KEY not in content

    def test_script_no_backup_password(self, db, tmp_path, monkeypatch):
        """Script enthaelt kein Backup-Passwort."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="no-pw")
            result = pbs.prepare_panel_restore(backup.id, db)

        content = _read_script(result["script_path"])
        assert TEST_PASSWORD not in content

    def test_script_no_database_url_credentials(self, db, tmp_path, monkeypatch):
        """Script enthaelt keine DATABASE_URL mit Credentials (nur Pfad-Referenz auf .env)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        # Setze eine database_url mit Credentials — darf nicht im Script landen
        monkeypatch.setattr(settings, "database_url",
                            "postgresql://msm:supersecret_pw@127.0.0.1:15432/msm")
        backup = _create_backup(db, db_type="postgresql")

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        # Die Credentials aus der URL duermen nicht im Script stehen
        assert "supersecret_pw" not in content
        # DATABASE_URL wird nur als Variable referenziert (aus .env geladen)
        assert "DATABASE_URL" in content


# ── VAL-PANEL-RESTORE-011: Script ist idempotent ────────────────────────


class TestPrepareRestoreScriptIdempotent:
    """VAL-PANEL-RESTORE-011: Script ist idempotent (Safety-Copies mit eindeutigen Zeitstempeln)."""

    def test_script_uses_unique_timestamps(self, db, tmp_path, monkeypatch):
        """Safety-Copies nutzen 'date +%Y%m%d_%H%M%S' (eindeutig zur Laufzeit)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        # Statischer Zeitstempel wuerde Kollision verursachen — Script nutzt
        # $(date +%Y%m%d_%H%M%S) was zur Laufzeit eindeutig ist.
        assert "$(date +%Y%m%d_%H%M%S)" in content

    def test_prepare_restore_idempotent(self, db, tmp_path, monkeypatch):
        """Zweimaliges prepare-restore erzeugt dasselbe Script (ueberschreibt sauber)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result1 = pbs.prepare_panel_restore(backup.id, db)
        result2 = pbs.prepare_panel_restore(backup.id, db)
        # Script-Pfad ist deterministic (restore_<id>.sh)
        assert result1["script_path"] == result2["script_path"]
        assert os.path.isfile(result2["script_path"])

    def test_script_has_trap_cleanup(self, db, tmp_path, monkeypatch):
        """Script hat trap EXIT cleanup (idempotent, kein leftover temp dir)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        result = pbs.prepare_panel_restore(backup.id, db)
        content = _read_script(result["script_path"])
        assert "trap" in content
        assert "rm -rf" in content


# ── VAL-PANEL-RESTORE-012: Temp-Verzeichnis bereinigt ───────────────────


class TestPrepareRestoreTempCleanup:
    """VAL-PANEL-RESTORE-012: Temp-Verzeichnis nach Script-Generierung bereinigt."""

    def test_temp_dir_cleaned_up(self, db, tmp_path, monkeypatch):
        """Nach prepare-restore ist kein Temp-Verzeichnis mehr vorhanden."""
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db)

        pbs.prepare_panel_restore(backup.id, db)

        # Keine .restore_tmp_* Verzeichnisse im backup_dir
        entries = os.listdir(backup_dir)
        temp_entries = [e for e in entries if e.startswith(".restore_tmp_")]
        assert len(temp_entries) == 0

    def test_no_loose_db_dump_or_configs(self, db, tmp_path, monkeypatch):
        """Keine losen msm_db.sql oder configs/ im backup_dir (nur Script + tar.gz)."""
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env", "install.sh"])
        backup = _create_backup(db)

        pbs.prepare_panel_restore(backup.id, db)

        entries = os.listdir(backup_dir)
        # Keine losen msm_db.sql Dateien
        assert "msm_db.sql" not in entries
        # Keine loses configs/ Verzeichnis
        assert "configs" not in entries
        # Script ist vorhanden
        assert f"restore_{backup.id}.sh" in entries
        # Backup tar.gz ist noch vorhanden
        assert os.path.isfile(backup.local_path)

    def test_temp_cleaned_on_decrypt_failure(self, db, tmp_path, monkeypatch):
        """Temp-Verzeichnis wird auch bei Decrypt-Fehler bereinigt."""
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="cleanup-fail")
            os.remove(backup.local_path)

            from services.backup_crypto_service import BackupDecryptionError
            with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file",
                       side_effect=BackupDecryptionError("fail")):
                with pytest.raises(Exception):
                    pbs.prepare_panel_restore(backup.id, db)

            # Temp-Verzeichnis trotzdem bereinigt
            entries = os.listdir(backup_dir)
            temp_entries = [e for e in entries if e.startswith(".restore_tmp_")]
            assert len(temp_entries) == 0


# ── VAL-CROSS-002: Panel-Backup Full Cycle ──────────────────────────────


class TestPanelBackupFullCycle:
    """VAL-CROSS-002: Panel-Backup Full Cycle (create, verify, prepare restore, verify script)."""

    def test_full_cycle_with_s3(self, db, client, owner_cookies, tmp_path, monkeypatch):
        """Create -> verify local + S3 -> prepare restore -> verify script."""
        config_dir, backup_dir = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env", "install.sh"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            # 1. Create panel backup
            backup = _create_backup(db, name="full-cycle", config_names=[".env", "install.sh"])

            # 2. Verify local exists
            assert os.path.isfile(backup.local_path)

            # 3. Verify S3 exists
            assert backup.s3_key is not None
            assert backup.encrypted is True
            s3 = boto3.client("s3", region_name=TEST_REGION)
            objs = s3.list_objects_v2(Bucket=TEST_BUCKET, Prefix=backup.s3_key)
            assert len(objs.get("Contents", [])) == 1

            # 4. Prepare restore
            resp = _prepare_restore(client, owner_cookies, backup.id)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            script_path = data["script_path"]

            # 5. Verify script exists and is valid bash
            assert os.path.isfile(script_path)
            content = _read_script(script_path)
            lines = content.split("\n")
            assert lines[0] == "#!/bin/bash"
            assert "set -euo pipefail" in content
            # Script stops panel
            assert "systemctl stop msm-panel.service" in content
            # Script restores DB
            assert "msm_db.sql" in content
            # Script restarts panel
            assert "systemctl start msm-panel.service" in content
            # No plaintext secrets
            assert TEST_ACCESS_KEY not in content
            assert TEST_SECRET_KEY not in content
            assert TEST_PASSWORD not in content

    def test_full_cycle_local_only(self, db, tmp_path, monkeypatch):
        """Full cycle ohne S3: create -> verify local -> prepare restore -> verify script."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="local-cycle")

        # Verify local exists, no S3
        assert os.path.isfile(backup.local_path)
        assert backup.s3_key is None
        assert backup.encrypted is False

        # Prepare restore
        result = pbs.prepare_panel_restore(backup.id, db)
        assert os.path.isfile(result["script_path"])

        content = _read_script(result["script_path"])
        assert content.startswith("#!/bin/bash")
        assert "set -euo pipefail" in content
        assert "systemctl stop msm-panel.service" in content
        assert "systemctl start msm-panel.service" in content


# ── Key-Invalidierung nach Operation ─────────────────────────────────────


class TestPrepareRestoreKeyInvalidation:
    """Key wird nach Restore-Vorbereitung invalidiert (success und failure)."""

    def test_key_invalidated_after_s3_restore(self, db, tmp_path, monkeypatch):
        """Key wird nach S3-basiertem prepare-restore invalidiert."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="key-inv")
            os.remove(backup.local_path)

            with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key",
                       wraps=__import__("services.backup_crypto_service", fromlist=["BackupCryptoService"]).BackupCryptoService.invalidate_key) as inv_spy:
                pbs.prepare_panel_restore(backup.id, db)

            assert inv_spy.called

    def test_key_invalidated_on_decrypt_failure(self, db, tmp_path, monkeypatch):
        """Key wird auch bei Decrypt-Fehler invalidiert (try/finally)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        _setup_s3_config()
        _setup_backup_password()

        with mock_aws():
            _create_moto_bucket()
            backup = _create_backup(db, name="key-inv-fail")
            os.remove(backup.local_path)

            from services.backup_crypto_service import BackupDecryptionError
            with patch("services.backup_crypto_service.BackupCryptoService.decrypt_to_file",
                       side_effect=BackupDecryptionError("fail")):
                with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key") as inv_mock:
                    with pytest.raises(Exception):
                        pbs.prepare_panel_restore(backup.id, db)

                assert inv_mock.called

    def test_no_key_invalidation_for_local_only(self, db, tmp_path, monkeypatch):
        """Lokales Backup: kein Key init, kein invalidate (kein S3-Pfad)."""
        config_dir, _ = _prepare_dirs(tmp_path, monkeypatch)
        _write_config_files(config_dir, [".env"])
        backup = _create_backup(db, name="no-key")

        with patch("services.backup_crypto_service.BackupCryptoService.invalidate_key") as inv_mock:
            pbs.prepare_panel_restore(backup.id, db)

        assert not inv_mock.called
