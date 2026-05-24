"""Tests for servers router: CRUD, permissions, CSRF."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, Server, Permission


class TestListServers:
    def test_owner_sees_all_servers(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.get("/api/servers", cookies=owner_cookies)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(s["id"] == test_server.id for s in data)

    def test_regular_user_sees_only_allowed(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: Permission):
        response = client.get("/api/servers", cookies=user_cookies)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == test_server.id

    def test_unauthorized_cannot_list(self, client: TestClient):
        response = client.get("/api/servers")
        assert response.status_code == 401


class TestGetServer:
    def test_owner_can_view_any(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert response.status_code == 200
        assert response.json()["id"] == test_server.id

    def test_user_with_permission_can_view(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: Permission):
        response = client.get(f"/api/servers/{test_server.id}", cookies=user_cookies)
        assert response.status_code == 200

    def test_user_without_permission_blocked(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server):
        # No permission granted
        response = client.get(f"/api/servers/{test_server.id}", cookies=user_cookies)
        assert response.status_code == 403


class TestCreateServer:
    def test_owner_can_create(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
        # docker_service.is_available() darf False sein — der Endpunkt lebt
        # auch ohne lokales Docker (install() schl\u00e4gt nur fehl). Wir mocken
        # nichts; install_dir landet unter /tmp/msm-test/.
        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.get_plugin", return_value=None):
            response = client.post(
                "/api/servers",
                json={"name": "New Server", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code in (200, 201)

    def test_regular_user_cannot_create(self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str):
        response = client.post(
            "/api/servers",
            json={"name": "New Server", "game_type": "dayz"},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403

    def test_create_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict):
        response = client.post(
            "/api/servers",
            json={"name": "New Server", "game_type": "dayz"},
            cookies=owner_cookies,
        )
        assert response.status_code == 403


class TestDeleteServer:
    def test_owner_can_delete(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str):
        response = client.delete(
            f"/api/servers/{test_server.id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200

    def test_regular_user_cannot_delete(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_csrf_token: str):
        response = client.delete(
            f"/api/servers/{test_server.id}",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403


class TestStartServer:
    def test_owner_can_start(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str):
        response = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        # May fail because plugin isn't available in test env, but must not be 401/403
        assert response.status_code not in (401, 403)

    def test_user_with_permission_can_start(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: Permission, user_csrf_token: str):
        response = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code not in (401, 403)

    def test_user_without_permission_blocked(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_csrf_token: str):
        response = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403

    def test_start_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.post(
            f"/api/servers/{test_server.id}/start",
            cookies=owner_cookies,
        )
        assert response.status_code == 403


class TestServerStatusDiskFields:
    """Status-Endpoint liefert Disk-Used/Free auch ohne disk_limit."""

    def test_status_includes_disk_used_and_free(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        test_server: Server,
        db: Session,
    ):
        # Simuliere, dass der Disk-Scheduler bereits einen Wert geschrieben hat
        test_server.disk_usage_mb = 456
        db.commit()

        # Plugin-Status mocken (kein echter Docker im Test)
        with patch("routers.servers.get_plugin") as mock_get_plugin:
            from games.base import ServerStatus
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_status.return_value = ServerStatus(
                status="stopped", cpu_percent=None, ram_mb=None, disk_mb=None,
            )
            response = client.get(
                f"/api/servers/{test_server.id}/status",
                cookies=owner_cookies,
            )

        assert response.status_code == 200
        data = response.json()
        # Limits (immer mitgesendet)
        assert "cpu_limit_percent" in data
        assert "ram_limit_mb" in data
        assert "disk_limit_gb" in data
        # Disk-Used kommt aus server.disk_usage_mb
        assert data["disk_used_mb"] == 456
        # Disk-Free ist ein Integer oder None (je nach Host-Filesystem)
        assert data["disk_free_mb"] is None or isinstance(data["disk_free_mb"], int)
        # Disk-MB fällt auf den DB-Wert zurück, wenn das Plugin None liefert
        assert data["disk_mb"] == 456
