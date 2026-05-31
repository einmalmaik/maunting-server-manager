"""Tests for install lifecycle:

- `finish_install(server_id, result)` transitions DB-status correctly
- Plugin.install() spawns thread that calls finish_install() after SteamCMD
- Delete-Endpoint reinigt Container, install_dir, backup_dir, console-logs
- Restore-Endpoint stoppt Container vor dem Extrahieren
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from blueprints.schema import load_blueprint_file
from games.blueprint_plugin import BlueprintPlugin
from games.base import _console_log_path, finish_install
from models import Server, User


def _native_blueprint_plugin(blueprint_id: str) -> BlueprintPlugin:
    path = Path(__file__).resolve().parents[1] / "blueprints" / "native" / f"{blueprint_id}.blueprint.json"
    return BlueprintPlugin(load_blueprint_file(path))


class TestFinishInstall:
    """Background-Thread-Callback setzt Server-Status nach SteamCMD."""

    def test_ok_transitions_installing_to_stopped(self, db: Session, test_server: Server):
        test_server.status = "installing"
        test_server.status_message = "Installation gestartet"
        db.commit()

        finish_install(test_server.id, {"ok": True})

        db.refresh(test_server)
        assert test_server.status == "stopped"
        assert test_server.status_message is None

    def test_error_transitions_installing_to_error(self, db: Session, test_server: Server):
        test_server.status = "installing"
        db.commit()

        finish_install(test_server.id, {"ok": False, "error": "Steam login failed"})

        db.refresh(test_server)
        assert test_server.status == "error"
        assert test_server.status_message == "Steam login failed"

    def test_error_without_message_uses_default(self, db: Session, test_server: Server):
        test_server.status = "installing"
        db.commit()

        finish_install(test_server.id, {"ok": False})

        db.refresh(test_server)
        assert test_server.status == "error"
        assert "fehlgeschlagen" in (test_server.status_message or "").lower()

    def test_error_message_is_truncated(self, db: Session, test_server: Server):
        test_server.status = "installing"
        db.commit()
        long_err = "x" * 1000

        finish_install(test_server.id, {"ok": False, "error": long_err})

        db.refresh(test_server)
        assert test_server.status == "error"
        assert len(test_server.status_message) <= 500

    def test_unknown_server_id_is_noop(self, db: Session):
        # Soll NICHT crashen, sondern still durchfallen
        finish_install(999_999, {"ok": True})

    def test_finish_releases_install_update_lock(self, db: Session, test_server: Server):
        from services.install_update_lock_service import (
            active_install_update_lock,
            try_acquire_install_update_lock,
        )

        assert try_acquire_install_update_lock(test_server.id, "install") is True
        finish_install(test_server.id, {"ok": True})
        assert active_install_update_lock() is None

    def test_finish_releases_install_update_lock_after_error(self, db: Session, test_server: Server):
        from services.install_update_lock_service import (
            active_install_update_lock,
            try_acquire_install_update_lock,
        )

        assert try_acquire_install_update_lock(test_server.id, "install") is True
        finish_install(test_server.id, {"ok": False, "error": "synthetic failure"})
        assert active_install_update_lock() is None


class TestInstallUpdateLockService:
    def test_second_job_is_rejected_until_release(self):
        from services.install_update_lock_service import (
            release_install_update_lock,
            try_acquire_install_update_lock,
        )

        assert try_acquire_install_update_lock(1, "install") is True
        assert try_acquire_install_update_lock(2, "install") is False
        release_install_update_lock(1)
        assert try_acquire_install_update_lock(2, "install") is True

    def test_stale_lock_can_be_replaced(self):
        from services.install_update_lock_service import (
            active_install_update_lock,
            try_acquire_install_update_lock,
        )

        assert try_acquire_install_update_lock(1, "install", ttl_seconds=-1) is True
        assert active_install_update_lock() is None
        assert try_acquire_install_update_lock(2, "install") is True


class TestInstallEndpointLock:
    def test_install_returns_structured_conflict_when_job_running(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        from services.install_update_lock_service import (
            INSTALL_UPDATE_ALREADY_RUNNING,
            try_acquire_install_update_lock,
        )

        assert try_acquire_install_update_lock(999, "install") is True

        with patch("routers.servers.get_plugin") as mock_get_plugin:
            mock_get_plugin.return_value = MagicMock()
            response = client.post(
                f"/api/servers/{test_server.id}/install",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == INSTALL_UPDATE_ALREADY_RUNNING
        assert response.json()["detail"]["message"] == "errors.install_update_already_running"

    def test_install_releases_lock_when_plugin_errors_synchronously(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        from services.install_update_lock_service import active_install_update_lock

        plugin = MagicMock()
        plugin.install.return_value = {"error": "synthetic install failure"}

        with patch("routers.servers.get_plugin", return_value=plugin):
            response = client.post(
                f"/api/servers/{test_server.id}/install",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 500
        assert active_install_update_lock() is None

    def test_install_releases_lock_when_plugin_raises_synchronously(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
    ):
        from services.install_update_lock_service import active_install_update_lock

        plugin = MagicMock()
        plugin.install.side_effect = RuntimeError("synthetic install exception")

        with patch("routers.servers.get_plugin", return_value=plugin):
            response = client.post(
                f"/api/servers/{test_server.id}/install",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 500
        assert active_install_update_lock() is None


class TestDeleteServerCleanup:
    """Delete-Endpoint räumt Container, install_dir, backup_dir, console-logs."""

    def test_owner_delete_removes_container_and_dirs(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        test_server: Server,
        csrf_token: str,
        tmp_path,
    ):
        # install_dir vorbereiten
        install_dir = str(tmp_path / "install")
        os.makedirs(install_dir)
        with open(os.path.join(install_dir, "marker.txt"), "w") as f:
            f.write("data")
        test_server.install_dir = install_dir

        # Backups + Console-Logs unter den vom Code gebauten Pfaden ablegen
        backup_dir = f"/opt/msm/backups/{test_server.id}"
        backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        console_log_dir = os.path.join(backend_root, "logs", str(test_server.id))

        from database import SessionLocal
        s = SessionLocal()
        try:
            srv = s.query(Server).filter(Server.id == test_server.id).first()
            srv.install_dir = install_dir
            s.commit()
        finally:
            s.close()

        # Mock-Verzeichnisse mit Dummy-Files anlegen
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "test.tar.gz"), "w") as f:
            f.write("dummy")
        os.makedirs(console_log_dir, exist_ok=True)
        with open(os.path.join(console_log_dir, "console.log"), "w") as f:
            f.write("[MSM] test")

        try:
            with patch("routers.servers.docker_service.remove") as mock_remove, \
                 patch("routers.servers.docker_service.repair_bind_mount_permissions") as mock_repair, \
                 patch("routers.servers.close_ports"):
                mock_remove.return_value = {"ok": True}
                mock_repair.return_value = {"ok": True, "stdout": "", "stderr": ""}
                response = client.delete(
                    f"/api/servers/{test_server.id}",
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )

            assert response.status_code == 200
            body = response.json()
            # Container-Cleanup wurde angefordert
            mock_remove.assert_called_once()
            mock_repair.assert_called_once_with(install_dir)
            assert body["cleanup"]["container_removed"].startswith("msm-srv-")
            # Install-Dir entfernt
            assert not os.path.exists(install_dir)
            # Backup-Dir entfernt
            assert not os.path.exists(backup_dir)
            # Console-Log-Dir entfernt
            assert not os.path.exists(console_log_dir)
        finally:
            # Sauberer Cleanup, falls Test scheitert
            shutil.rmtree(backup_dir, ignore_errors=True)
            shutil.rmtree(console_log_dir, ignore_errors=True)
            shutil.rmtree(install_dir, ignore_errors=True)

    def test_delete_idempotent_when_dirs_missing(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        test_server: Server,
        csrf_token: str,
    ):
        # install_dir existiert NICHT — Delete muss trotzdem 200 liefern
        from database import SessionLocal
        s = SessionLocal()
        try:
            srv = s.query(Server).filter(Server.id == test_server.id).first()
            srv.install_dir = "/nonexistent/path/abc"
            s.commit()
        finally:
            s.close()

        with patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.repair_bind_mount_permissions") as mock_repair, \
             patch("routers.servers.close_ports"):
            mock_remove.return_value = {"ok": True}
            response = client.delete(
                f"/api/servers/{test_server.id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        mock_repair.assert_not_called()
        assert response.json()["cleanup"]["dir_removed"] is None

    def test_delete_repairs_install_dir_permissions_before_rmtree(
        self,
        client: TestClient,
        owner_cookies: dict,
        test_server: Server,
        csrf_token: str,
        tmp_path,
    ):
        install_dir = str(tmp_path / "wine-install")
        os.makedirs(install_dir)
        with open(os.path.join(install_dir, "wine-extension-chm.desktop"), "w") as f:
            f.write("synthetic wine desktop file")

        test_server.install_dir = install_dir
        db = Session.object_session(test_server)
        db.commit()

        calls: list[str] = []
        real_rmtree = shutil.rmtree

        def tracked_rmtree(path, *args, **kwargs):
            if path == install_dir:
                calls.append("rmtree")
            return real_rmtree(path, *args, **kwargs)

        def repair(path):
            assert path == install_dir
            calls.append("repair")
            return {"ok": True, "stdout": "", "stderr": ""}

        with patch("routers.servers.docker_service.remove", return_value={"ok": True}), \
             patch("routers.servers.docker_service.repair_bind_mount_permissions", side_effect=repair), \
             patch("routers.servers.close_ports"), \
             patch("routers.servers.shutil.rmtree", side_effect=tracked_rmtree):
            response = client.delete(
                f"/api/servers/{test_server.id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert calls[:2] == ["repair", "rmtree"]
        assert not os.path.exists(install_dir)


class TestSteamCMDFullLogOnFailure:
    """SteamCMD-Output muss IMMER ins Console-Log — auch bei Fehler.

    Bug-Report (User, 2026-05): DayZ-Install schlug fehl, im Panel war als
    einzige Log-Zeile nur die kryptische Wrapper-Meldung
    ``steamcmd.sh[7]: Restarting steamcmd by request...`` sichtbar. Die echte
    Ursache (Login-Problem, App nicht verfuegbar, etc.) wurde verschluckt,
    weil der alte Code im Fehlerpfad nur ``result['error']`` (= letzte Zeile)
    geloggt hat und stdout/stderr verworfen wurden.
    """

    def _read_console_log(self, server_id: int) -> str:
        path = _console_log_path(server_id)
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _clear_console_log(self, server_id: int) -> None:
        path = _console_log_path(server_id)
        if os.path.exists(path):
            os.remove(path)

    def test_failure_writes_full_stdout_and_stderr_to_console(self, test_server: Server, tmp_path):
        from games.base import run_steamcmd_install

        self._clear_console_log(test_server.id)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
                mock_eph.return_value = {
                    "ok": False,
                    "error": "steamcmd.sh[7]: Restarting steamcmd by request...",
                    "stdout": "Redirecting stderr to '/data/logs/stderr.txt'\nLogging directory: '/data/logs'\nERROR! Failed to install app '223350' (No subscription)\n",
                    "stderr": "steamcmd.sh[7]: Restarting steamcmd by request...\n",
                }
                run_steamcmd_install(
                    server_id=test_server.id,
                    install_dir=str(tmp_path),
                    app_id="223350",
                )

            log = self._read_console_log(test_server.id)
            # Volle SteamCMD-Diagnose muss sichtbar sein — sonst kann der User
            # die Ursache nicht erkennen.
            assert "No subscription" in log
            assert "Failed to install app '223350'" in log
            # Die kurze Fehler-Zusammenfassung (Wrapper-Meldung) bleibt am Ende
            # erhalten — fuer den Status-Text.
            assert "[MSM] SteamCMD fehlgeschlagen" in log
        finally:
            self._clear_console_log(test_server.id)

    def test_success_still_writes_full_output(self, test_server: Server, tmp_path):
        from games.base import run_steamcmd_install

        self._clear_console_log(test_server.id)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)):
                mock_eph.return_value = {
                    "ok": True,
                    "stdout": "Success! App '223350' fully installed.\n",
                    "stderr": "",
                }
                run_steamcmd_install(
                    server_id=test_server.id,
                    install_dir=str(tmp_path),
                    app_id="223350",
                )

            log = self._read_console_log(test_server.id)
            assert "Success! App '223350' fully installed" in log
            assert "[MSM] SteamCMD abgeschlossen" in log
        finally:
            self._clear_console_log(test_server.id)

    def test_live_output_is_written_during_steamcmd_run_and_redacted(self, test_server: Server, tmp_path):
        from games.base import run_steamcmd_install

        self._clear_console_log(test_server.id)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.host_uid_gid", return_value=(1001, 1001)), \
                 patch("services.steam_account_service.SteamAccountService.is_configured", return_value=True), \
                 patch("services.steam_account_service.SteamAccountService.get_username", return_value="steam-user"), \
                 patch("services.steam_account_service.SteamAccountService.get_decrypted_password", return_value="secret-pass"):

                def fake_run_ephemeral(**kwargs):
                    kwargs["log_callback"](
                        "Update state (0x61) downloading, progress: 68.94 secret-pass\n"
                    )
                    return {"ok": True, "stdout": "", "stderr": ""}

                mock_eph.side_effect = fake_run_ephemeral
                run_steamcmd_install(
                    server_id=test_server.id,
                    install_dir=str(tmp_path),
                    app_id="223350",
                    use_authenticated_login=True,
                )

            log = self._read_console_log(test_server.id)
            assert "Update state (0x61) downloading, progress: 68.94" in log
            assert "secret-pass" not in log
            assert "***" in log
        finally:
            self._clear_console_log(test_server.id)

    def test_maps_known_steamcmd_0x202_error_without_secret_leak(self, test_server: Server, tmp_path):
        from games.base import run_steamcmd_install

        self._clear_console_log(test_server.id)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1001)), \
                 patch("services.steam_account_service.SteamAccountService.is_configured", return_value=True), \
                 patch("services.steam_account_service.SteamAccountService.get_username", return_value="steam-user"), \
                 patch("services.steam_account_service.SteamAccountService.get_decrypted_password", return_value="secret-pass"):

                def fake_run_ephemeral(**kwargs):
                    kwargs["log_callback"](
                        "Error! App '3792580' state is 0x202 after update job secret-pass\n"
                    )
                    return {"ok": False, "error": "exit 7", "stdout": "", "stderr": ""}

                mock_eph.side_effect = fake_run_ephemeral
                result = run_steamcmd_install(
                    server_id=test_server.id,
                    install_dir=str(tmp_path),
                    app_id="3792580",
                    use_authenticated_login=True,
                )

            assert result["ok"] is False
            assert result["error_code"] == "steamcmd_update_state_0x202"
            assert "nicht verifiziert" in result["error"]
            log = self._read_console_log(test_server.id)
            assert "secret-pass" not in log
            assert "0x202" in log
            assert "nicht verifiziert" in log
        finally:
            self._clear_console_log(test_server.id)

    def test_maps_missing_configuration_error(self, test_server: Server, tmp_path):
        from games.base import run_steamcmd_install

        self._clear_console_log(test_server.id)
        try:
            with patch("games.base.docker_service.run_ephemeral") as mock_eph, \
                 patch("games.base.docker_service.container_runtime_uid_gid", return_value=(1001, 1001)):
                mock_eph.return_value = {
                    "ok": False,
                    "error": "Missing Configuration",
                    "stdout": "ERROR! Failed to install app '123' (Missing Configuration)\n",
                    "stderr": "",
                }
                result = run_steamcmd_install(
                    server_id=test_server.id,
                    install_dir=str(tmp_path),
                    app_id="123",
                )

            assert result["error_code"] == "steamcmd_missing_configuration"
            assert "nicht verifiziert" in result["error"]
        finally:
            self._clear_console_log(test_server.id)


class TestPluginInstallCallback:
    """Plugin.install() spawnt Thread, der finish_install() aufruft."""

    def test_conan_install_thread_calls_finish(self, db: Session, test_server: Server):
        plugin = _native_blueprint_plugin("conan_exiles_ue5")
        test_server.status = "installing"
        test_server.install_dir = tempfile.mkdtemp()
        db.commit()

        with patch("games.blueprint_plugin.run_steamcmd_install") as mock_run, \
             patch("games.blueprint_plugin.finish_install") as mock_finish:
            mock_run.return_value = {"ok": True}
            plugin.install(test_server)

            # Thread muss durchgelaufen sein
            import time
            for _ in range(20):
                if mock_finish.called:
                    break
                time.sleep(0.05)

            assert mock_finish.called
            mock_finish.assert_called_with(test_server.id, {"ok": True})

        shutil.rmtree(test_server.install_dir, ignore_errors=True)

    def test_dayz_install_thread_calls_finish(self, db: Session, test_server: Server):
        plugin = _native_blueprint_plugin("dayz")
        test_server.status = "installing"
        test_server.install_dir = tempfile.mkdtemp()
        db.commit()

        with patch("games.blueprint_plugin.SteamAccountService.is_configured", return_value=True), \
             patch("games.blueprint_plugin.run_steamcmd_install") as mock_run, \
             patch("games.blueprint_plugin.finish_install") as mock_finish:
            mock_run.return_value = {"ok": False, "error": "test failure"}
            plugin.install(test_server)

            import time
            for _ in range(20):
                if mock_finish.called:
                    break
                time.sleep(0.05)

            assert mock_finish.called
            mock_finish.assert_called_with(test_server.id, {"ok": False, "error": "test failure"})

        shutil.rmtree(test_server.install_dir, ignore_errors=True)


class TestManualUploadLifecycle:
    def test_manual_upload_install_writes_readme_sets_status(self, db: Session, test_server: Server, tmp_path):
        from games.blueprint_plugin import BlueprintPlugin
        from blueprints.schema import Blueprint, BlueprintSourceType

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_manual", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "manualUpload",
                "manual": {
                    "requiredFiles": ["server.jar"],
                    "instructions": "Upload server.jar",
                },
            },
        })
        plugin = BlueprintPlugin(bp)
        test_server.status = "installing"
        test_server.install_dir = str(tmp_path)
        db.commit()

        with patch("games.blueprint_plugin.finish_install") as mock_finish:
            plugin.install(test_server)
            import time
            for _ in range(20):
                if mock_finish.called:
                    break
                time.sleep(0.05)
            assert mock_finish.called
            args = mock_finish.call_args
            assert args[0][1]["ok"] is True
            assert args[0][1]["next_status"] == "awaiting_files"

        readme = tmp_path / "MANUAL_INSTALL.md"
        assert readme.exists()
        assert "Upload server.jar" in readme.read_text(encoding="utf-8")

    def test_manual_upload_existing_readme_not_overwritten(self, db: Session, test_server: Server, tmp_path):
        from games.blueprint_plugin import BlueprintPlugin
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_manual2", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "manualUpload",
                "manual": {
                    "requiredFiles": ["a.jar"],
                    "instructions": "New instr",
                },
            },
        })
        plugin = BlueprintPlugin(bp)
        test_server.install_dir = str(tmp_path)
        existing = tmp_path / "MANUAL_INSTALL.md"
        existing.write_text("User notes", encoding="utf-8")
        db.commit()

        plugin.install(test_server)
        assert existing.read_text(encoding="utf-8") == "User notes"

    def test_steam_requires_login_blocks_install_without_account(self, db: Session, test_server: Server):
        from games.blueprint_plugin import BlueprintPlugin
        from blueprints.schema import Blueprint
        from services.steam_account_service import SteamAccountService

        SteamAccountService.clear()
        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_steam_login", "name": "Test", "category": "steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "steam",
                "steam": {"appId": "123", "platform": "linux", "compatibility": "native", "requiresLogin": True},
            },
        })
        plugin = BlueprintPlugin(bp)
        test_server.status = "installing"
        db.commit()

        result = plugin.install(test_server)
        assert "error" in result
        assert "Steam-Account" in result["error"]
        # Status darf nicht in "installing" haengen bleiben — sonst kann der
        # User den Server nicht mehr reparieren ohne Re-Install zu triggern.
        db.expire_all()
        refreshed = db.query(Server).filter(Server.id == test_server.id).first()
        assert refreshed is not None
        assert refreshed.status == "error"
        assert refreshed.status_message is not None
        assert "Steam-Account" in refreshed.status_message

    def test_steam_requires_login_uses_account_when_configured(self, db: Session, test_server: Server, tmp_path):
        from games.blueprint_plugin import BlueprintPlugin
        from blueprints.schema import Blueprint
        from services.steam_account_service import SteamAccountService

        SteamAccountService.set("steamuser", "steampass")
        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_steam_login_ok", "name": "Test", "category": "steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "steam",
                "steam": {"appId": "123", "platform": "linux", "compatibility": "native", "requiresLogin": True},
            },
        })
        plugin = BlueprintPlugin(bp)
        test_server.status = "installing"
        test_server.install_dir = str(tmp_path)
        db.commit()

        with patch("games.blueprint_plugin.run_steamcmd_install") as mock_run, \
             patch("games.blueprint_plugin.finish_install") as mock_finish:
            mock_run.return_value = {"ok": True}
            plugin.install(test_server)
            import time
            for _ in range(20):
                if mock_finish.called:
                    break
                time.sleep(0.05)
            assert mock_finish.called
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["use_authenticated_login"] is True

        SteamAccountService.clear()


class TestRestoreStopsContainer:
    """Restore-Endpoint stoppt Container, bevor er install_dir ersetzt."""

    def test_restore_calls_remove_before_extract(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        test_server: Server,
        csrf_token: str,
        tmp_path,
        db: Session,
    ):
        from models import Backup

        # Backup-File anlegen
        backup_file = tmp_path / "backup.tar.gz"
        # Minimal-Tar bauen (leer, nur Header)
        import tarfile
        with tarfile.open(str(backup_file), "w:gz") as tf:
            placeholder = tmp_path / "placeholder.txt"
            placeholder.write_text("ok")
            tf.add(str(placeholder), arcname="placeholder.txt")

        # install_dir vorbereiten
        install_dir = tmp_path / "install"
        install_dir.mkdir()

        # Test-Server konfigurieren
        from database import SessionLocal
        s = SessionLocal()
        try:
            srv = s.query(Server).filter(Server.id == test_server.id).first()
            srv.install_dir = str(install_dir)
            s.commit()
            backup = Backup(
                server_id=srv.id,
                filename=str(backup_file),
                size_mb=1,
            )
            s.add(backup)
            s.commit()
            s.refresh(backup)
            backup_id = backup.id
        finally:
            s.close()

        # Permission setzen für Owner (Owner kommt eh durch, aber Permission-Check braucht's nicht)
        with patch("services.docker_service.is_running", return_value=True) as mock_running, \
             patch("services.docker_service.stop") as mock_stop, \
             patch("services.docker_service.remove") as mock_remove:
            mock_stop.return_value = {"ok": True}
            mock_remove.return_value = {"ok": True}

            response = client.post(
                f"/api/backups/{test_server.id}/restore/{backup_id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        # Restore muss erfolgreich sein
        assert response.status_code == 200, response.json()

        # Container MUSS gestoppt + entfernt worden sein
        mock_stop.assert_called_once()
        mock_remove.assert_called_once()

        # Server-Status zurückgesetzt
        s = SessionLocal()
        try:
            srv = s.query(Server).filter(Server.id == test_server.id).first()
            assert srv.status == "stopped"
        finally:
            s.close()
