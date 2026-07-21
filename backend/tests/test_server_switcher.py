from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models.server import Server
from models.user import User


class TestServerSwitcher:
    def test_switch_blueprint_fails_when_running(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        test_server.status = "running"
        db.commit()

        res = client.post(
            f"/api/servers/{test_server.id}/switch-blueprint",
            json={"new_blueprint_id": "valheim"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 400
        assert "gestoppt" in res.json()["detail"]["message"]

    def test_switch_blueprint_fails_when_blueprint_not_found(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        test_server.status = "stopped"
        db.commit()

        res = client.post(
            f"/api/servers/{test_server.id}/switch-blueprint",
            json={"new_blueprint_id": "invalid_nonexistent_game_999"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 404

    def test_switch_blueprint_aborts_when_backup_fails(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        test_server.status = "stopped"
        test_server.game_type = "palworld"
        db.commit()

        with patch("services.backup_orchestrator.create_server_backup", side_effect=RuntimeError("S3 quota exceeded")):
            res = client.post(
                f"/api/servers/{test_server.id}/switch-blueprint",
                json={"new_blueprint_id": "valheim"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert res.status_code == 500
            assert "Pre-Switch-Backup" in res.json()["detail"]["message"]

        # Invariante: Server game_type unveraendert
        db.refresh(test_server)
        assert test_server.game_type == "palworld"

    def test_switch_blueprint_success(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        test_server.status = "stopped"
        test_server.game_type = "palworld"
        db.commit()

        mock_backup = MagicMock()
        mock_backup.id = 123
        mock_backup.status = "completed"

        with patch("services.backup_orchestrator.create_server_backup", return_value=mock_backup) as mock_backup_fn, \
             patch("games.get_plugin") as mock_get_plugin:
            
            mock_plugin = MagicMock()
            mock_get_plugin.return_value = mock_plugin

            res = client.post(
                f"/api/servers/{test_server.id}/switch-blueprint",
                json={"new_blueprint_id": "valheim"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["new_blueprint"] == "valheim"
            assert data["backup_id"] == 123

            mock_backup_fn.assert_called_once()
            mock_plugin.install.assert_called_once()

        db.refresh(test_server)
        assert test_server.game_type == "valheim"
