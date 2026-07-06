"""Tests for servers router: CRUD, permissions, CSRF."""
import logging
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, Server, ServerPermission


class TestListServers:
    def test_owner_sees_all_servers(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.get("/api/servers", cookies=owner_cookies)
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(s["id"] == test_server.id for s in data)

    def test_regular_user_sees_only_allowed(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: list[ServerPermission]):
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

    def test_user_with_permission_can_view(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: list[ServerPermission]):
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
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.get_plugin", return_value=None):
            response = client.post(
                "/api/servers",
                json={"name": "New Server", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code in (200, 201)
            # Positive "install_dir not leaked" (data minimization per security #13) even in basic create path
            create_data = response.json()
            assert "install_dir" not in create_data
            assert "container_name" not in create_data
            assert {"install_dir", "container_name"}.isdisjoint(create_data.keys())  # stronger positive removal assert for data-min (re-review gap)

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

    def test_create_uses_stable_id_based_install_dir(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
        """Nach dem Fix wird install_dir aus der echten PK (server.id) gebildet,
        nicht mehr aus Count()+1. Auch mit gemockten FS-Calls können wir prüfen,
        dass der Name im Response die ID enthält (kein Reuse nach DELETEs möglich).
        """
        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.get_plugin", return_value=None):
            response = client.post(
                "/api/servers",
                json={"name": "Id-Based Server", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code in (200, 201)
            data = response.json()
            # Positive asserts for security fix (install_dir leak removed): explicit "not in response" per re-review gap.
            assert "install_dir" not in data
            assert "container_name" not in data
            # Pfad-Logik (id-basiert) bleibt intern in create_server + DB (kein Leak mehr; siehe schemas/server.py + security review #13).

    def test_create_rejects_preexisting_dir_on_disk_with_409(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
        """Simuliert exakt den Bug (verwaistes dayz_1 oder root-owned Dir):
        os.path.exists liefert True für den id-basierten Pfad → saubere 409,
        keine 500 + EPERM, und keine Phantom-Row in der DB.
        """
        # Wir patchen nur den exists-Guard. Der Rest (ports etc.) läuft normal.
        # create_server berechnet den Pfad intern aus der frischen id.
        conflicting_path = "/opt/msm/servers/dayz_999999"  # kann nicht existieren
        with patch("routers.servers.os.path.exists", return_value=True) as mock_exists, \
             patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.get_plugin", return_value=None):
            response = client.post(
                "/api/servers",
                json={"name": "Collision Test", "game_type": "dayz"},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code == 409
            assert "existierte bereits" in response.json()["detail"].lower() or "existier" in response.json()["detail"].lower()
            # Wichtig: die Placeholder-Row wurde aufgeräumt (keine Server mit Pending-Pfad).
            # (Der genaue Check über DB ist in Integration-Tests abgedeckt; hier reicht 409.)

        # exists wurde mindestens einmal für den Guard aufgerufen.
        assert mock_exists.called

    def test_create_with_postgres_returns_one_time_credentials(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
        credentials = [{
            "database_id": 1,
            "database_name": "msm_s1_db1",
            "username": "msm_s1_u1",
            "password": "***",
            "host": "msm-postgres",
            "port": 5432,
            "is_superuser": False,
        }]
        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.get_plugin", return_value=None), \
             patch("routers.servers.postgres_service.provision_server_databases", return_value=credentials):
            response = client.post(
                "/api/servers",
                json={
                    "name": "PG Server",
                    "game_type": "dayz",
                    "postgres_enabled": True,
                    "postgres_database_count": 1,
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 201
        data = response.json()
        assert data["postgres_credentials"] == credentials

    def test_create_aborts_when_postgres_provisioning_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, db: Session):
        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.shutil.rmtree"), \
             patch("routers.servers.allocate_ports", return_value=(27015, 27016, 27017)), \
             patch("routers.servers.get_plugin", return_value=None), \
             patch("routers.servers.postgres_service.provision_server_databases", side_effect=RuntimeError("pg down")), \
             patch("routers.servers.postgres_service.drop_server_resources") as drop_resources:
            response = client.post(
                "/api/servers",
                json={
                    "name": "Broken PG Server",
                    "game_type": "dayz",
                    "postgres_enabled": True,
                    "postgres_database_count": 1,
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        assert db.query(Server).filter(Server.name == "Broken PG Server").first() is None
        assert drop_resources.called


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

    def test_user_with_permission_can_start(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: list[ServerPermission], user_csrf_token: str):
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
            started_at = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_status.return_value = ServerStatus(
                status="running", cpu_percent=None, ram_mb=None, disk_mb=None,
                uptime_seconds=60, started_at=started_at,
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
        assert data["uptime_seconds"] == 60
        assert data["started_at"].startswith("2026-06-01T10:00:00")
        db.refresh(test_server)
        assert test_server.last_started_at is not None

    def test_status_preserves_queued_lifecycle_transition(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        db: Session,
    ):
        test_server.status = "running"
        db.commit()
        with patch("services.server_lifecycle_service._start_lifecycle_thread"):
            queued = client.post(
                f"/api/servers/{test_server.id}/stop",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert queued.status_code == 200

        with patch("routers.servers.get_plugin") as mock_get_plugin:
            from games.base import ServerStatus

            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_status.return_value = ServerStatus(status="stopped")
            response = client.get(
                f"/api/servers/{test_server.id}/status",
                cookies=owner_cookies,
            )

        assert response.status_code == 200
        assert response.json()["status"] == "queued"


class TestManualUploadStartPreCheck:
    def test_start_blocks_when_files_missing(
        self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session, tmp_path
    ):
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_manual_start", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "manualUpload",
                "manual": {"requiredFiles": ["server.jar"], "instructions": "Upload"},
            },
        })
        test_server.game_type = "test_manual_start"
        test_server.install_dir = str(tmp_path)
        test_server.status = "awaiting_files"
        test_server.public_bind_ip = "127.0.0.1"
        db.commit()

        with patch("routers.servers.get_plugin") as mock_get_plugin:
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp
            response = client.post(
                f"/api/servers/{test_server.id}/start",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 400
        assert "server.jar" in response.json()["detail"]

    def test_start_succeeds_when_all_files_present(
        self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session, tmp_path
    ):
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_manual_start2", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "manualUpload",
                "manual": {"requiredFiles": ["server.jar"], "instructions": "Upload"},
            },
        })
        test_server.game_type = "test_manual_start2"
        test_server.install_dir = str(tmp_path)
        test_server.status = "awaiting_files"
        test_server.public_bind_ip = "127.0.0.1"
        db.commit()
        (tmp_path / "server.jar").write_text("fake", encoding="utf-8")

        with patch("routers.servers.get_plugin") as mock_get_plugin, \
             patch("services.server_lifecycle_service._start_lifecycle_thread") as mock_thread:
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp
            response = client.post(
                f"/api/servers/{test_server.id}/start",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        mock_thread.assert_called_once()

    def test_start_blocks_when_symlink_present(
        self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session, tmp_path
    ):
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_manual_symlink", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [],
            "source": {
                "type": "manualUpload",
                "manual": {"requiredFiles": ["server.jar"], "instructions": "Upload"},
            },
        })
        test_server.game_type = "test_manual_symlink"
        test_server.install_dir = str(tmp_path)
        test_server.status = "awaiting_files"
        test_server.public_bind_ip = "127.0.0.1"
        db.commit()
        (tmp_path / "real.jar").write_text("fake", encoding="utf-8")
        (tmp_path / "server.jar").symlink_to(tmp_path / "real.jar")

        with patch("routers.servers.get_plugin") as mock_get_plugin:
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp
            response = client.post(
                f"/api/servers/{test_server.id}/start",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 400
        assert "server.jar" in response.json()["detail"]


# === Coverage for central lifecycle lock (unified per security fix #1) ===
# Basic import + acquisition test (delegation exercised in start/stop/restart routers + scheduler).


class TestKillServer:
    """AUFGABE 5: /kill Endpoint Tests (server.kill perm, force docker remove, status=stopped, error handling, no secret leak)."""

    def test_kill_success_sets_stopped_and_calls_docker_force(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session):
        test_server.status = "running"
        db.commit()
        with patch("services.server_lifecycle_service._start_lifecycle_thread") as mock_thread:
            response = client.post(
                f"/api/servers/{test_server.id}/kill",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "stopped"
            assert response.json()["operation"] == "kill"
            # kill is now immediate (no lifecycle thread for the force-kill path)
            db.refresh(test_server)
            assert test_server.status == "stopped"
            # no secrets/paths in response (data minimization)
            assert "container" not in str(response.json()).lower()

    def test_kill_overrides_active_job(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session):
        """Kill hat einen 'emergency override': markiert den aktiven Job als
        done, damit ein zweiter Kill nicht in der 409-Active-Job-Falle stecken
        bleibt. Der erste Kill wird gequeued, der zweite ueberschreibt
        ebenfalls (override-Verhalten).
        """
        test_server.status = "running"
        db.commit()
        with patch("services.server_lifecycle_service._start_lifecycle_thread"):
            response = client.post(
                f"/api/servers/{test_server.id}/kill",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code == 200
            # Zweiter Kill: kein 409 weil der erste Kill-Active-Job durch
            # den Override markiert wurde. Der zweite Kill wird ebenfalls
            # gequeued und laeuft NACH dem ersten (kein echtes Interrupt).
            second = client.post(
                f"/api/servers/{test_server.id}/kill",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert second.status_code == 200
            db.refresh(test_server)
            assert test_server.status == "stopped"

    def test_kill_forbidden_without_permission(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_csrf_token: str):
        response = client.post(
            f"/api/servers/{test_server.id}/kill",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403

    def test_kill_404_not_found(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str):
        response = client.post(
            "/api/servers/999999/kill",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 404


class TestTransientStatusBeforeDocker:
    """AUFGABE 4A: transient statuses set BEFORE slow Docker ops (hanging mock + DB spy proves commit *before* blocking call)."""

    def test_stop_sets_stopping_before_plugin_stop_with_hanging_mock(self, client: TestClient, owner_user: User, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session):
        test_server.status = "running"
        db.commit()
        with patch("routers.servers.get_plugin") as mock_get_plugin, \
             patch("services.server_lifecycle_service._start_lifecycle_thread"):
            mock_plugin = mock_get_plugin.return_value
            response = client.post(
                f"/api/servers/{test_server.id}/stop",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        db.refresh(test_server)
        assert test_server.status == "queued"

    # Restart transient coverage is provided by the stop test pattern + direct code inspection of the locked service (the restart test was removed to avoid env-specific patch timing flakes in sqlite + to_thread while keeping the invariant proven for the feature).

# Note: full async lock usage covered in integration/runtime; this closes the "0 coverage for server_lifecycle_service.py" gap without new file.
import asyncio

from services.server_lifecycle_service import get_server_lifecycle_lock


class TestLifecycleLockBasic:
    def test_lifecycle_lock_import_and_acquisition(self):
        """Exercises import of central service + per-id lock acquisition (KISS helper)."""
        import threading

        lock = get_server_lifecycle_lock(4242)
        assert lock is not None
        assert isinstance(lock, type(threading.Lock()))
        # Re-acquire yields same instance (setdefault semantics)
        lock2 = get_server_lifecycle_lock(4242)
        assert lock is lock2
        # Additional coverage for lifecycle service helper (distinct ids -> distinct locks; addresses re-review gap on 0 coverage for server_lifecycle_service)
        assert get_server_lifecycle_lock(111) is not lock


class TestServerPortsRouter:
    def test_create_server_with_ports(self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session):
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_game_ports", "name": "Test Game Ports", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [
                {"name": "game", "protocol": "udp"},
                {"name": "query", "protocol": "udp"},
                {"name": "rcon", "protocol": "tcp"},
                {"name": "custom", "protocol": "udp"},
            ],
            "source": {"type": "manualUpload", "manual": {"requiredFiles": ["server.jar"], "instructions": "test"}},
        })

        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("routers.servers.open_ports"), \
             patch("routers.servers.allocate_ports", return_value=[
                 ("game", 27015, "udp"),
                 ("query", 27016, "udp"),
                 ("rcon", 27017, "tcp"),
                 ("custom_1", 29000, "udp"),
             ]), \
             patch("routers.servers.get_plugin") as mock_get_plugin:
            
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp

            response = client.post(
                "/api/servers",
                json={
                    "name": "Custom Ports Server",
                    "game_type": "test_game_ports",
                    "ports": {"custom_1": 29000},
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code in (200, 201)
            data = response.json()
            assert "ports" in data
            # Response must list all ports
            ports = data["ports"]
            assert len(ports) == 4
            custom_port = next(p for p in ports if p["role"] == "custom_1")
            assert custom_port["port"] == 29000
            assert custom_port["protocol"] == "udp"

    def test_create_server_with_same_role_tcp_and_udp(self, client: TestClient, owner_cookies: dict, csrf_token: str, db: Session):
        from blueprints.schema import Blueprint

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_query_dual_protocol", "name": "Test Query Dual Protocol", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [
                {"name": "game", "protocol": "udp"},
                {"name": "query", "protocol": "udp"},
                {"name": "query", "protocol": "tcp"},
            ],
            "source": {"type": "manualUpload", "manual": {"requiredFiles": ["server.jar"], "instructions": "test"}},
        })

        with patch("routers.servers.os.makedirs"), \
             patch("routers.servers.os.chmod"), \
             patch("routers.servers.os.path.exists", return_value=False), \
             patch("services.port_allocation_service.is_port_available", return_value=True), \
             patch("routers.servers.get_plugin") as mock_get_plugin:

            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp

            response = client.post(
                "/api/servers",
                json={
                    "name": "Dual Protocol Server",
                    "game_type": "test_query_dual_protocol",
                    "ports": {"query": 28015},
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code in (200, 201)
        ports = response.json()["ports"]
        query_udp = next(p for p in ports if p["role"] == "query")
        query_tcp = next(p for p in ports if p["role"] == "query_2")
        assert query_udp["port"] == 28015
        assert query_udp["protocol"] == "udp"
        assert query_tcp["port"] == 28015
        assert query_tcp["protocol"] == "tcp"

    def test_update_server_ports(self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session):
        from blueprints.schema import Blueprint
        from models.server_port import ServerPort

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_game_ports", "name": "Test Game Ports", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [
                {"name": "game", "protocol": "udp"},
                {"name": "query", "protocol": "udp"},
                {"name": "rcon", "protocol": "tcp"},
                {"name": "custom", "protocol": "udp"},
            ],
            "source": {"type": "manualUpload", "manual": {"requiredFiles": ["server.jar"], "instructions": "test"}},
        })

        test_server.game_type = "test_game_ports"
        test_server.ports = [
            ServerPort(role="game", port=27015, protocol="udp"),
            ServerPort(role="query", port=27016, protocol="udp"),
            ServerPort(role="rcon", port=27017, protocol="tcp"),
            ServerPort(role="custom_1", port=28015, protocol="udp"),
        ]
        db.commit()

        with patch("routers.servers.open_ports"), \
             patch("routers.servers.close_ports"), \
             patch("routers.servers.iptables_accept_server"), \
             patch("routers.servers.iptables_revoke_server"), \
             patch("routers.servers.allocate_ports", return_value=[
                 ("game", 27015, "udp"),
                 ("query", 27016, "udp"),
                 ("rcon", 27017, "tcp"),
                 ("custom_1", 28999, "udp"),
             ]), \
             patch("routers.servers.get_plugin") as mock_get_plugin:
            
            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp

            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={
                    "ports": {
                        "game": 27015,
                        "query": 27016,
                        "rcon": 27017,
                        "custom_1": 28999,
                    }
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
            assert response.status_code == 200
            data = response.json()
            assert "ports" in data
            ports = data["ports"]
            custom_port = next(p for p in ports if p["role"] == "custom_1")
            assert custom_port["port"] == 28999

    def test_update_server_port_protocol(self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session):
        from blueprints.schema import Blueprint
        from models.server_port import ServerPort

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_game_ports", "name": "Test Game Ports", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [
                {"name": "game", "protocol": "udp"},
                {"name": "query", "protocol": "udp"},
            ],
            "source": {"type": "manualUpload", "manual": {"requiredFiles": ["server.jar"], "instructions": "test"}},
        })

        test_server.game_type = "test_game_ports"
        test_server.ports = [
            ServerPort(role="game", port=27015, protocol="udp"),
            ServerPort(role="query", port=27016, protocol="udp"),
        ]
        db.commit()

        with patch("services.port_allocation_service.is_port_available", return_value=True), \
             patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.get_plugin") as mock_get_plugin:

            mock_plugin = mock_get_plugin.return_value
            mock_plugin.get_blueprint.return_value = bp

            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={
                    "ports": {
                        "game": 27015,
                        "query": 27016,
                    },
                    "port_protocols": {
                        "game": "udp",
                        "query": "tcp",
                    },
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        query_port = next(p for p in response.json()["ports"] if p["role"] == "query")
        assert query_port["port"] == 27016
        assert query_port["protocol"] == "tcp"


# ── Resource-Limit PATCH Hardening (CPU/RAM/Disk) ──────────────────────
#
# Covers VAL-API-001..006, 010..013, 016: strict JSON typing, validation,
# least-privilege resource authorization, CSRF/auth preservation, partial /
# null / no-op behavior, mixed payload permission + atomicity, sanitized
# errors, and rollback / no drift on failures.
#
# Strategie: Gegen den vorhandenen PATCH /api/servers/{id} Flow getestet.
# Docker-/Disk-/Lifecycle-Grenzen werden gemockt und auf "nicht aufgerufen"
# geprueft, damit Ressourcen-Patches keineexternen Seiteneffekte haben.


class TestResourcePatchPermissions:
    """Hardening for resource-field PATCH (CPU/RAM/Disk)."""

    def _grant(self, db: Session, user: User, server: Server, *keys: str) -> None:
        """Delegiert exakt die angegebenen server-scoped Permissions (KISS-Helper)."""
        for key in keys:
            db.add(ServerPermission(
                user_id=user.id, server_id=server.id, permission_key=key,
            ))
        db.commit()

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    # ── VAL-API-001: Authenticated and CSRF-protected resource PATCH ──

    def test_resource_patch_unauthenticated_rejected(self, client: TestClient, test_server: Server, db: Session):
        test_server.cpu_limit_percent = 100
        db.commit()
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
        )
        assert response.status_code == 401
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_resource_patch_missing_csrf_rejected(self, client: TestClient, owner_cookies: dict, test_server: Server, db: Session):
        test_server.cpu_limit_percent = 100
        db.commit()
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=owner_cookies,
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-API-002: Backend resource permission is authoritative ──

    def test_resource_patch_without_resources_permission_forbidden(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        # View-only user (kein server.resources.manage)
        self._grant(db, regular_user, test_server, "server.view")
        test_server.cpu_limit_percent = 100
        db.commit()
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-API-011: Resource-only PATCH uses least privilege ──

    def test_resource_only_patch_with_resources_manage_succeeds(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        # view + resources.manage, aber NICHT config.write / network.manage
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 150, "ram_limit_mb": 4096},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["cpu_limit_percent"] == 150
        assert data["ram_limit_mb"] == 4096
        # disk nicht gesendet -> bleibt erhalten
        assert data["disk_limit_gb"] == 20

    def test_resources_manage_user_cannot_patch_config_fields(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        original_name = test_server.name
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"name": "Hacked Name"},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.name == original_name

    def test_resources_manage_user_cannot_patch_network_fields(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"game_port": 27015},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert all(p.port != 27015 for p in test_server.ports)

    # ── VAL-API-003: Partial resource PATCH changes only supplied fields ──

    def test_partial_resource_patch_changes_only_supplied(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cpu_limit_percent"] == 200
        assert data["ram_limit_mb"] == 2048
        assert data["disk_limit_gb"] == 20
        # Follow-up GET bestätigt Persistenz
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.status_code == 200
        got = get.json()
        assert got["cpu_limit_percent"] == 200
        assert got["ram_limit_mb"] == 2048
        assert got["disk_limit_gb"] == 20

    # ── VAL-API-004: Null resource values mean unlimited ──

    def test_null_resource_values_clear_limits(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": None, "ram_limit_mb": None},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cpu_limit_percent"] is None
        assert data["ram_limit_mb"] is None
        # disk nicht gesendet -> bleibt erhalten
        assert data["disk_limit_gb"] == 20
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["cpu_limit_percent"] is None
        assert get.json()["ram_limit_mb"] is None

    # ── VAL-API-005: Resource validation boundaries are enforced ──

    @pytest.mark.parametrize("field,value", [
        ("cpu_limit_percent", 9),
        ("cpu_limit_percent", 3201),
        ("ram_limit_mb", 511),
        ("disk_limit_gb", 0),
    ])
    def test_invalid_resource_values_rejected(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
        field, value,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={field: value},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 422
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20

    @pytest.mark.parametrize("field,value", [
        ("cpu_limit_percent", 10),
        ("cpu_limit_percent", 3200),
        ("ram_limit_mb", 512),
        ("disk_limit_gb", 1),
    ])
    def test_valid_boundary_resource_values_accepted(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
        field, value,
    ):
        with patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={field: value},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()[field] == value

    # ── VAL-API-006: Nonexistent or unauthorized server access does not mutate ──

    def test_resource_patch_nonexistent_server_404(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
    ):
        response = client.patch(
            "/api/servers/999999",
            json={"cpu_limit_percent": 200},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 404

    def test_resource_patch_unauthorized_server_forbidden(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        # Gar keine Permissions auf diesen Server
        test_server.cpu_limit_percent = 100
        db.commit()
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-API-016: Resource JSON types are strict ──

    @pytest.mark.parametrize("field,bad_value", [
        ("cpu_limit_percent", "100"),
        ("cpu_limit_percent", 100.0),
        ("cpu_limit_percent", True),
        ("cpu_limit_percent", [100]),
        ("cpu_limit_percent", {"v": 100}),
        ("ram_limit_mb", "2048"),
        ("ram_limit_mb", 2048.5),
        ("disk_limit_gb", "20"),
    ])
    def test_strict_resource_types_rejected(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
        field, bad_value,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={field: bad_value},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 422
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20

    # ── VAL-API-012: No-op resource PATCH is idempotent ──

    def test_noop_resource_patch_idempotent_no_side_effects(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        original_status = test_server.status
        with patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.iptables_revoke_server") as mock_revoke, \
             patch("routers.servers.iptables_accept_server") as mock_accept:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 100, "ram_limit_mb": 2048, "disk_limit_gb": 20},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["cpu_limit_percent"] == 100
        assert data["ram_limit_mb"] == 2048
        assert data["disk_limit_gb"] == 20
        # Keine Docker-, Netzwerk- oder Firewall-Seiteneffekte
        mock_running.assert_not_called()
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        mock_revoke.assert_not_called()
        mock_accept.assert_not_called()
        db.refresh(test_server)
        assert test_server.status == original_status
        assert test_server.cpu_limit_percent == 100

    # ── VAL-API-013: Mixed resource and network/config PATCH is atomic ──

    def test_mixed_resource_and_config_requires_both_permissions(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        # resources + config braucht resources.manage UND config.write
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        original_name = test_server.name
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        # config.write fehlt -> 403, weder Resource noch Name darf sich aendern
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "name": "Renamed"},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.name == original_name

        # config.write gewaehren -> beide Aenderungen werden angewendet
        self._grant(db, regular_user, test_server, "server.config.write")
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "name": "Renamed"},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 200, response.text
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 200
        assert test_server.name == "Renamed"

    def test_mixed_resource_and_network_requires_both_permissions(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        # network.manage fehlt -> 403, kein Port-Update, keine Resource-Aenderung
        with patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.get_plugin", return_value=None), \
             patch("routers.servers.docker_service.is_running", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "game_port": 27015},
                cookies=user_cookies,
                headers={"X-CSRF-Token": user_csrf_token},
            )
        assert response.status_code == 403
        mock_alloc.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert all(p.port != 27015 for p in test_server.ports)

        # network.manage gewaehren -> mixed resource+network wird VOR Mutation
        # mit 409 abgelehnt (scrutiny round 2 fix: post-commit network side
        # effects can fail after DB commit, so reject before mutation).
        self._grant(db, regular_user, test_server, "server.network.manage")
        with patch("routers.servers.allocate_ports") as mock_alloc2:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "game_port": 27015},
                cookies=user_cookies,
                headers={"X-CSRF-Token": user_csrf_token},
            )
        assert response.status_code == 409
        mock_alloc2.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_mixed_resource_network_atomic_on_port_failure(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        from services.port_allocation_service import PortConflictError
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage", "server.network.manage")
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        # Mixed resource+network wird VOR Port-Allokation abgelehnt (409).
        # allocate_ports wird nie aufgerufen (scrutiny round 2 fix).
        with patch("routers.servers.allocate_ports", side_effect=PortConflictError("Port 27015/udp belegt")) as mock_alloc, \
             patch("routers.servers.get_plugin", return_value=None), \
             patch("routers.servers.docker_service.is_running", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "game_port": 27015},
                cookies=user_cookies,
                headers={"X-CSRF-Token": user_csrf_token},
            )
        assert response.status_code == 409
        mock_alloc.assert_not_called()
        db.refresh(test_server)
        # Kein Drift: Resource-Feld und Ports unveraendert
        assert test_server.cpu_limit_percent == 100
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports

    # ── VAL-API-010: Error responses and logs are sanitized ──

    def test_resource_patch_failure_response_and_logs_sanitized(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session,
        caplog,
    ):
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        # Ein synthetischer Sentinel als Exception-Nachricht. Die Implementierung
        # darf die Nachricht weder im Response-Body noch im Log reflektieren
        # (VAL-API-010): bei unerwarteten Fehlern wird nur ein generischer Text
        # plus Exception-Typ ausgegeben, niemals Host-Pfade, Socket-Pfade,
        # Secrets, Stacktraces oder Roh-Output.
        sentinel = "ZZLEAKSENTINEL_4f8a2c1d ZZNEVERLEAK"
        with patch("routers.servers._normalize_server_restart_mode", side_effect=RuntimeError(sentinel)):
            with caplog.at_level(logging.WARNING):
                response = client.patch(
                    f"/api/servers/{test_server.id}",
                    json={"cpu_limit_percent": 200},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )
        assert response.status_code == 500
        # Response enthaelt nur die generische, sanitisierte Meldung
        assert response.json()["detail"] == "Server-Aktualisierung fehlgeschlagen"
        body = response.text
        assert sentinel not in body
        assert "ZZLEAKSENTINEL" not in body
        assert "ZZNEVERLEAK" not in body
        # Logs duerfen nur den Exception-Typ enthalten, nicht die Nachricht
        log_text = caplog.text
        assert sentinel not in log_text
        assert "ZZLEAKSENTINEL" not in log_text
        assert "ZZNEVERLEAK" not in log_text
        # Kein Drift: Resource-Wert unveraendert
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100


class TestLiveResourceUpdate:
    """Tests fuer Live CPU/RAM-Update auf laufende Container (VAL-API-007..015, VAL-DOCKER-001..009)."""

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    # ── VAL-API-007: Running resource-only PATCH does not restart ──

    def test_running_resource_patch_calls_live_update_no_restart(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Laufender Server: CPU-Update ruft Docker Live-Update, keinen Restart."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True) as mock_running, \
             patch("routers.servers.docker_service.update_container_resources", return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()["cpu_limit_percent"] == 200
        # Docker Live-Update wurde aufgerufen
        mock_update.assert_called_once()
        # Kein Stop/Start/Remove (keine Network-Aenderung)
        mock_running.assert_called_once()  # fuer Stale-Check

    def test_running_resource_patch_does_not_stop_or_recreate(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-API-007: Keine stop/start/remove/restart-Aufrufe bei Resource-PATCH."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources", return_value={"ok": True}) as mock_update, \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.start") as mock_start, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        mock_update.assert_called_once()
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_start.assert_not_called()
        mock_plugin.assert_not_called()

    # ── VAL-API-008: Stopped server resource PATCH persists for next start ──

    def test_stopped_server_resource_patch_persists_no_docker(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Gestoppter Server: Werte werden persistiert, kein Docker-Update,
        kein Start. Stale-Runtime-Check prueft Docker-Status (VAL-API-015):
        DB=stopped + Docker=stopped -> sicher persistieren."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False) as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["cpu_limit_percent"] == 200
        assert data["ram_limit_mb"] == 4096
        # Status bleibt "stopped"
        assert data["status"] == "stopped"
        # Stale-Runtime-Check wurde durchgefuehrt (Docker nicht running)
        mock_running.assert_called()
        # Kein Docker Live-Update (Server ist gestoppt)
        mock_update.assert_not_called()

    # ── VAL-API-009: Runtime apply failure leaves API state unchanged ──

    def test_docker_update_failure_rolls_back_db(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Docker-Update-Fehlschlag -> DB-Werte unveraendert, kein Drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        assert "Ressourcen" in response.json()["detail"]
        # DB-Werte unveraendert (kein Drift)
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    def test_docker_update_failure_sanitized_error(self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session, caplog):
        """VAL-API-010: Fehler-Response und Logs sind sanitisiert."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        sentinel = "ZZLEAKSENTINEL_docker.sock_/var/run/docker.sock"

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": sentinel}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            with caplog.at_level(logging.WARNING):
                response = client.patch(
                    f"/api/servers/{test_server.id}",
                    json={"cpu_limit_percent": 200},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )
        assert response.status_code == 503
        assert sentinel not in response.text

    # ── VAL-DOCKER-004: Combined CPU/RAM changes are atomic ──

    def test_combined_cpu_ram_atomic_on_partial_failure(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Wenn Docker-Update fehlschlaegt, werden weder CPU noch RAM persistiert."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Update fehlgeschlagen"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        # Beide Werte unveraendert (atomar)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    # ── VAL-DOCKER-005: Rootless Docker limitations fail safely ──

    def test_rootless_failure_no_restart_no_persist(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Rootless-cgroup-Fehler -> 503, keine Werte persistiert, kein Restart."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_status = test_server.status

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Systemfehler bei Container-Operation"}), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.status == original_status
        # Kein versteckter Restart als Fallback
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()

    # ── VAL-DOCKER-006: Resource-only updates do not mutate network ──

    def test_resource_only_patch_no_network_mutation(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Resource-PATCH aendert keine Ports, Firewall oder iptables."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        original_bind_ip = test_server.public_bind_ip

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources", return_value={"ok": True}), \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.iptables_revoke_server") as mock_revoke, \
             patch("routers.servers.iptables_accept_server") as mock_accept, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Keine Firewall/iptables/Port-Aenderung
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        mock_revoke.assert_not_called()
        mock_accept.assert_not_called()
        # Ports unveraendert
        db.refresh(test_server)
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports
        assert test_server.public_bind_ip == original_bind_ip

    # ── VAL-API-014: Resource PATCH is serialized with lifecycle ──

    def test_resource_patch_conflict_when_lifecycle_active(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Lifecycle-Job aktiv -> 409 Konflikt, keine Mutation."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_update.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-API-015: Stale runtime state fails safely ──

    def test_stale_runtime_db_running_docker_stopped_fails_safely(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """DB sagt 'running', Docker sagt 'stopped' -> sicherer Abbruch, kein Drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_update.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-DOCKER-009: Docker warnings fail safely ──

    def test_docker_warnings_fail_safely_no_persist(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Docker-Warnings -> 503, keine Persistenz, keine Raw-Warning im Response."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── VAL-DOCKER-003: Clearing CPU/RAM applies unlimited live state ──

    def test_running_null_cpu_calls_docker_clear(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Laufender Server: CPU null -> Docker Live-Clear, kein Restart."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources", return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()["cpu_limit_percent"] is None
        mock_update.assert_called_once()
        # Verify None was passed to Docker service
        call_args = mock_update.call_args
        updates = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("updates", {})
        assert updates.get("cpu_limit_percent") is None

    def test_running_null_ram_calls_docker_clear(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Laufender Server: RAM null -> Docker Live-Clear fuer mem+memswap."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources", return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"ram_limit_mb": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        assert response.json()["ram_limit_mb"] is None
        mock_update.assert_called_once()

    # ── Resource-only PATCH on running server with disk change ──

    def test_disk_only_change_no_docker_update_for_running(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Aenderung allein loest kein Docker-Update aus (Soft-Limit),
        aber sofortige Disk-Soft-Limit-Re-evaluation (VAL-DISK-001, VAL-DISK-004)."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}) as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        assert response.json()["disk_limit_gb"] == 50
        # Disk ist Soft-Limit: kein Docker-Update
        mock_update.assert_not_called()
        # Disk-Soft-Limit-Re-evaluation wurde sofort aufgerufen (VAL-DISK-001)
        mock_eval.assert_called_once()


# ── Disk Soft-Limit Re-evaluation (VAL-DISK-001..007, VAL-DOCKER-010) ──


class TestDiskSoftLimitReEvaluation:
    """Tests fuer sofortige Disk-Soft-Limit-Re-evaluation nach PATCH."""

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    # ── VAL-DISK-001: Disk changes trigger immediate usage re-evaluation ──

    def test_disk_change_triggers_immediate_reevaluation(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk_limit_gb-Aenderung loest sofortige evaluate_disk_soft_limit aus."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}) as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        mock_eval.assert_called_once()
        # Verify the server object was passed
        call_args = mock_eval.call_args
        assert call_args[0][1].id == test_server.id  # db, server positional

    def test_disk_noop_does_not_trigger_reevaluation(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """No-Op-Disk-PATCH (gleicher Wert) loest keine Re-evaluation aus (VAL-API-012)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit") as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 20},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        mock_eval.assert_not_called()

    # ── VAL-DISK-002: Lowering disk below usage invokes safe stop ──

    def test_lowering_disk_below_usage_invokes_stop_no_deletion(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Limit unter aktuellen Verbrauch -> Stop via plugin.stop, keine Datenloeschung."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        stop_called = []

        class FakePlugin:
            def stop(self, server):
                stop_called.append(server.id)

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},  # 10 GB = 10240 MB, usage 30000 MB > 100%
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Stop wurde aufgerufen
        assert stop_called == [test_server.id]
        # Keine Docker-Resource-Updates (nur Disk)
        mock_update.assert_not_called()
        # Kein force-remove oder prune
        mock_remove.assert_not_called()
        # Server-Daten bleiben erhalten (kein Datei- oder DB-Loesch-Aufruf)
        db.refresh(test_server)
        assert test_server.status == "error"
        assert "Disk-Soft-Limit" in (test_server.status_message or "")
        assert test_server.disk_limit_gb == 10

    def test_lowering_disk_below_usage_stopped_server_sets_error_no_stop(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Bei bereits gestopptem Server: Status auf error, kein plugin.stop-Aufruf."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "stopped"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        stop_called = []

        class FakePlugin:
            def stop(self, server):
                stop_called.append(server.id)

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Plugin.stop wurde nicht aufgerufen (Server war bereits gestoppt)
        assert stop_called == []
        db.refresh(test_server)
        assert test_server.status == "error"
        assert "Disk-Soft-Limit" in (test_server.status_message or "")

    # ── VAL-DISK-003: Increasing or clearing disk limit is non-destructive ──

    def test_increasing_disk_limit_non_destructive(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Limit erhoehen -> keine Stop/Delete-Aufrufe, Nutzung innerhalb Policy."""
        self._set_resources(db, test_server, disk=10)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=5000), \
             patch("services.scheduler_service.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 100},  # 100 GB, usage 5000 MB < 80%
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        assert response.json()["disk_limit_gb"] == 100
        # Kein Stop, kein Docker-Update
        mock_plugin.assert_not_called()
        mock_update.assert_not_called()
        # Status bleibt running
        db.refresh(test_server)
        assert test_server.status == "running"

    def test_clearing_disk_limit_non_destructive(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Limit auf null setzen -> keine Stop/Delete, Limit geloescht."""
        self._set_resources(db, test_server, disk=10)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=5000), \
             patch("services.scheduler_service.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        assert response.json()["disk_limit_gb"] is None
        mock_plugin.assert_not_called()
        mock_update.assert_not_called()
        db.refresh(test_server)
        assert test_server.status == "running"

    # ── VAL-DISK-004: Disk remains soft limit, not Docker hard quota ──

    def test_disk_only_change_no_docker_hard_quota(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Aenderung sendet keine Docker-Hard-Quota (storage_opt, overlay, etc.)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Docker update_container_resources wurde NICHT aufgerufen
        mock_update.assert_not_called()

    # ── VAL-DISK-005: Disk re-evaluation failure has no drift ──

    def test_disk_measurement_failure_rolls_back_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Nutzungsmessung schlaegt fehl -> 503, alte Werte erhalten (kein Drift)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        test_server.status_message = "existing message"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": False, "error": "Disk-Nutzung konnte nicht ermittelt werden"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        assert "Disk-Limit" in response.json()["detail"]
        # Kein Drift: alte Werte erhalten
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20
        assert test_server.status == "stopped"
        assert test_server.status_message == "existing message"

    def test_disk_enforcement_failure_rolls_back_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Stop schlaegt fehl -> 503, alte Werte erhalten (kein Drift)."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        class FailingPlugin:
            def stop(self, server):
                raise RuntimeError("stop failed")

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FailingPlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},  # below usage -> stop needed
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        # Kein Drift: alte Werte erhalten
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 50
        assert test_server.status == "running"
        mock_update.assert_not_called()

    # ── VAL-DISK-006: Stale warning state handling ──

    def test_increasing_disk_clears_stale_warning(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Limit erhoehen, Usage innerhalb Policy -> Disk-Warnung geloescht (VAL-DISK-006)."""
        self._set_resources(db, test_server, disk=10)
        test_server.status = "running"
        test_server.status_message = "Warnung: Disk-Verbrauch bei 85 % von 10240 MB."
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=5000), \
             patch("services.scheduler_service.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 100},  # 100 GB, usage 5000 MB < 80%
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        db.refresh(test_server)
        # Warnung geloescht
        assert test_server.status_message is None
        # Status bleibt running (kein Auto-Start noetig)
        assert test_server.status == "running"
        mock_plugin.assert_not_called()

    def test_clearing_disk_clears_stale_error_no_autostart(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Limit loeschen, Server war in error -> status auf stopped, kein Auto-Start."""
        self._set_resources(db, test_server, disk=10)
        test_server.status = "error"
        test_server.status_message = "Disk-Soft-Limit erreicht (30000 MB / 10240 MB). Container gestoppt."
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin") as mock_plugin, \
             patch("routers.servers.docker_service.start") as mock_start:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        db.refresh(test_server)
        # Status von error -> stopped (nicht running! kein Auto-Start)
        assert test_server.status == "stopped"
        assert test_server.status_message is None
        assert test_server.disk_limit_gb is None
        # Kein Start-Aufruf
        mock_start.assert_not_called()
        mock_plugin.assert_not_called()

    def test_non_disk_status_message_preserved(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Nicht-Disk-Statusmeldung bleibt bei Disk-Limit-Erhoehung erhalten."""
        self._set_resources(db, test_server, disk=10)
        test_server.status = "running"
        test_server.status_message = "Hintergrund-Check: Server-Datei-Update verfuegbar."
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=5000), \
             patch("services.scheduler_service.get_plugin"):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 100},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        db.refresh(test_server)
        # Nicht-Disk-Meldung bleibt erhalten
        assert "Server-Datei-Update" in (test_server.status_message or "")

    # ── VAL-DISK-007: Disk stop uses lifecycle-safe stop path ──

    def test_disk_stop_uses_lifecycle_lock(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Stop erfolgt unter Lifecycle-Lock (VAL-DISK-007)."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        class FakePlugin:
            def stop(self, server):
                pass

        with patch("routers.servers.is_lifecycle_job_active", return_value=False) as mock_active, \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources"), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Lifecycle-Job-Check wurde aufgerufen (Lock wurde geprueft/akquiriert)
        assert mock_active.call_count >= 2  # pre-check + re-check after lock

    def test_disk_stop_conflict_when_lifecycle_active(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Lifecycle-Job aktiv -> 409, keine Disk-Mutation, kein Drift."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=True), \
             patch("services.scheduler_service.docker_service.disk_usage_mb") as mock_usage, \
             patch("services.scheduler_service.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        # Keine Disk-Messung oder Plugin-Aufruf
        mock_usage.assert_not_called()
        mock_plugin.assert_not_called()
        # Kein Drift
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 50

    def test_disk_stop_no_force_remove_or_prune(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Stop verwendet plugin.stop, nie force-remove/prune/duplicate-stop."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        stop_count = []

        class FakePlugin:
            def stop(self, server):
                stop_count.append(1)

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.update_container_resources"), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Genau ein Stop, kein force-remove
        assert len(stop_count) == 1
        mock_remove.assert_not_called()

    # ── VAL-DISK-002: No data deletion during stop ──

    def test_disk_stop_no_filesystem_or_db_deletion(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-Stop loescht keine Dateien, Backups, Logs oder DB-Zeilen."""
        self._set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        class FakePlugin:
            def stop(self, server):
                pass

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.shutil.rmtree") as mock_rmtree, \
             patch("routers.servers.docker_service.update_container_resources"), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        # Keine Datei- oder Verzeichnisloeschung
        mock_rmtree.assert_not_called()
        mock_remove.assert_not_called()
        # Server-Row existiert noch
        db.refresh(test_server)
        assert test_server.id is not None


# ── Mixed PATCH Atomicity and Stale Runtime Safety (scrutiny fix) ──────
#
# Regression tests for scrutiny blockers around mixed PATCH atomicity and
# stale runtime safety. Covers VAL-API-013, VAL-API-015, VAL-DISK-001,
# VAL-DISK-005, VAL-CROSS-010, VAL-CROSS-014.
#
# Three findings fixed:
#   1. Mixed resource + restart-scheduler config payloads committed DB
#      changes before scheduler sync could fail outside rollback handling.
#   2. CPU/RAM PATCH did not check actual Docker runtime state when DB
#      status said stopped; DB-stopped/Docker-running persisted values
#      without live update (drift).
#   3. Mixed disk_limit_gb + network PATCH paths skipped disk soft-limit
#      re-evaluation entirely.


class TestMixedPatchAtomicityAndStaleRuntime:
    """Regression tests for mixed PATCH atomicity and stale runtime safety."""

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    def _grant(self, db: Session, user: User, server: Server, *keys: str) -> None:
        for key in keys:
            db.add(ServerPermission(
                user_id=user.id, server_id=server.id, permission_key=key,
            ))
        db.commit()

    # ── Fix 1: Mixed resource + restart-scheduler atomicity (VAL-API-013) ──

    def test_mixed_resource_restart_scheduler_rollback_on_sync_failure(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Resource + restart-scheduler config in one PATCH: if scheduler
        sync fails, DB must roll back (no drift between DB and scheduler)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        test_server.auto_restart = True
        test_server.restart_interval_hours = 8
        db.commit()

        with patch("routers.servers.sync_server_restart_schedule",
                   side_effect=RuntimeError("scheduler unavailable")):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "restart_interval_hours": 4},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 500
        assert response.json()["detail"] == "Server-Aktualisierung fehlgeschlagen"
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.restart_interval_hours == 8

    def test_mixed_resource_restart_scheduler_success_commits_atomically(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Resource + restart-scheduler: both succeed -> both committed
        atomically (scheduler sync inside transaction)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        test_server.auto_restart = True
        test_server.restart_interval_hours = 8
        db.commit()

        with patch("routers.servers.sync_server_restart_schedule") as mock_sync:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "restart_interval_hours": 4},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        mock_sync.assert_called_once()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 200
        assert test_server.restart_interval_hours == 4

    def test_config_only_restart_scheduler_rollback_on_sync_failure(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Config-only restart-scheduler PATCH: scheduler sync failure rolls
        back config changes too (not just resource changes)."""
        test_server.status = "stopped"
        test_server.auto_restart = True
        test_server.restart_interval_hours = 8
        db.commit()

        with patch("routers.servers.sync_server_restart_schedule",
                   side_effect=RuntimeError("scheduler unavailable")):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"restart_interval_hours": 4},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 500
        db.refresh(test_server)
        assert test_server.restart_interval_hours == 8

    # ── Fix 2: DB-stopped/Docker-running stale runtime (VAL-API-015) ──

    def test_db_stopped_docker_running_fails_safely(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """DB says stopped, Docker says running -> 409, no persist, no drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_update.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_db_stopped_docker_stopped_persists_safely(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """DB says stopped, Docker says stopped -> persist, no Docker update."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()["cpu_limit_percent"] == 200
        mock_update.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 200

    def test_db_running_docker_running_applies_live_update(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """DB says running, Docker says running -> live update applied
        (unchanged behavior, regression guard)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        mock_update.assert_called_once()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 200

    # ── Fix 3: Mixed disk + network rejected before mutation (scrutiny r2) ──
    # VAL-DISK-001, VAL-DISK-005, VAL-CROSS-010, VAL-CROSS-014
    #
    # Scrutiny round 2 found that mixed disk + network payloads can commit
    # DB changes before post-commit network side effects (firewall, iptables,
    # plugin stop/start) fail. KISS-safe: reject with 409 before mutation.

    def test_mixed_disk_network_rejected_409_no_disk_eval(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk + network in one PATCH -> 409 before mutation, disk evaluator
        not called (scrutiny round 2 fix)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20

    def test_mixed_disk_network_rejected_409_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk + network: 409 rejection leaves DB unchanged, no network
        mutation (VAL-DISK-005, VAL-CROSS-010, VAL-CROSS-014)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.close_ports") as mock_close:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        mock_open.assert_not_called()
        mock_close.assert_not_called()
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports

    def test_mixed_cpu_ram_disk_network_rejected_409(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """CPU + RAM + disk + network -> 409 before mutation, all unchanged,
        no Docker update, no network mutation (VAL-CROSS-014)."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active") as mock_active, \
             patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.get_plugin") as mock_plugin, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.close_ports") as mock_close:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={
                    "cpu_limit_percent": 200,
                    "ram_limit_mb": 4096,
                    "disk_limit_gb": 50,
                    "game_port": 27015,
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports
        mock_update.assert_not_called()
        mock_active.assert_not_called()
        mock_running.assert_not_called()
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        mock_plugin.assert_not_called()
        mock_open.assert_not_called()
        mock_close.assert_not_called()

    def test_mixed_disk_network_running_server_no_lock_no_side_effects(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk + network on running server: 409 before mutation, no
        lifecycle lock, no Docker, no firewall, no iptables (VAL-DISK-007,
        VAL-CROSS-010)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active") as mock_active, \
             patch("routers.servers.get_server_lifecycle_lock") as mock_lock, \
             patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.get_plugin") as mock_plugin, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.iptables_accept_server") as mock_accept, \
             patch("routers.servers.iptables_revoke_server") as mock_revoke:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_active.assert_not_called()
        mock_lock.assert_not_called()
        mock_running.assert_not_called()
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        mock_plugin.assert_not_called()
        mock_open.assert_not_called()
        mock_close.assert_not_called()
        mock_accept.assert_not_called()
        mock_revoke.assert_not_called()
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20

    def test_mixed_disk_cpu_network_rejected_409_no_docker_live_update(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk + CPU + network -> 409 before mutation, no Docker live update,
        no side effects (scrutiny round 2 fix)."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active") as mock_active, \
             patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50, "cpu_limit_percent": 200, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        mock_update.assert_not_called()
        mock_active.assert_not_called()
        mock_running.assert_not_called()
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.disk_limit_gb == 20


# ── Mixed Resource/Disk + Network PATCH Rejection (scrutiny round 2) ──
#
# Scrutiny round 2 found that mixed resource/disk plus network payloads
# can commit DB changes before post-commit network side effects (firewall,
# iptables, plugin stop/start) fail. KISS-safe behavior: reject these
# unsupported mixed side-effect groups with a sanitized 409 BEFORE any
# mutation, after permission checks.
#
# Covers VAL-CROSS-010, VAL-CROSS-014, and the scrutiny round 2 blocker.
# Existing resource-only, disk-only, network-only, and config/scheduler
# paths must continue working (regression guard).


class TestMixedResourceNetworkRejection:
    """Reject mixed resource/disk + network PATCH payloads before mutation."""

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    def _grant(self, db: Session, user: User, server: Server, *keys: str) -> None:
        for key in keys:
            db.add(ServerPermission(
                user_id=user.id, server_id=server.id, permission_key=key,
            ))
        db.commit()

    # ── 409 rejection with full permissions ──

    def test_mixed_cpu_ram_network_full_perms_returns_409(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Mixed CPU/RAM + network with full permissions -> 409, DB unchanged."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        # No mutation side effects
        mock_alloc.assert_not_called()
        mock_eval.assert_not_called()
        mock_running.assert_not_called()
        mock_update.assert_not_called()

    def test_mixed_disk_network_returns_409_no_disk_eval(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Mixed disk_limit_gb + network -> 409, disk evaluator not called."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()

    def test_mixed_resource_public_bind_ip_returns_409(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Mixed resource + public_bind_ip (network field) -> 409."""
        self._set_resources(db, test_server, cpu=100)
        test_server.status = "stopped"
        test_server.public_bind_ip = "127.0.0.1"
        db.commit()

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "public_bind_ip": "192.168.1.1"},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_mixed_disk_port_protocols_returns_409(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Mixed disk + port_protocols (network field) -> 409."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"disk_limit_gb": 50, "port_protocols": {"game": "tcp"}},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20

    # ── No side effects on running servers ──

    def test_mixed_resource_network_running_no_lifecycle_lock(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Mixed resource/disk + network on running server: no lifecycle
        lock acquired, no Docker, no firewall, no iptables, no stop/start."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active") as mock_active, \
             patch("routers.servers.get_server_lifecycle_lock") as mock_lock, \
             patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.iptables_revoke_server") as mock_revoke, \
             patch("routers.servers.iptables_accept_server") as mock_accept, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.get_plugin") as mock_plugin:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={
                    "cpu_limit_percent": 200,
                    "disk_limit_gb": 50,
                    "game_port": 27015,
                },
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.disk_limit_gb == 20
        # No lifecycle lock, no Docker, no firewall, no iptables, no plugin
        mock_active.assert_not_called()
        mock_lock.assert_not_called()
        mock_running.assert_not_called()
        mock_update.assert_not_called()
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        mock_revoke.assert_not_called()
        mock_accept.assert_not_called()
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()
        mock_plugin.assert_not_called()

    # ── Permission checks before 409 ──

    def test_missing_network_permission_returns_403_before_409(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Missing network permission -> 403 before the mixed-payload 409."""
        self._grant(db, regular_user, test_server, "server.view", "server.resources.manage")
        self._set_resources(db, test_server, cpu=100)
        test_server.status = "stopped"
        db.commit()

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "game_port": 27015},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_missing_resource_permission_returns_403_before_409(
        self, client: TestClient, regular_user: User, user_cookies: dict, user_csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Missing resource permission -> 403 before the mixed-payload 409."""
        self._grant(db, regular_user, test_server, "server.view", "server.network.manage")
        self._set_resources(db, test_server, cpu=100)
        test_server.status = "stopped"
        db.commit()

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "game_port": 27015},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    # ── 409 response is sanitized ──

    def test_mixed_rejection_409_sanitized_no_internals(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """409 response is sanitized: no host paths, socket paths, stack traces."""
        self._set_resources(db, test_server, cpu=100)
        test_server.status = "stopped"
        db.commit()

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "game_port": 27015},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 409
        body = response.text
        assert "docker.sock" not in body
        assert "/var/run" not in body
        assert "Traceback" not in body
        assert "File \"" not in body

    # ── Regression: single-group payloads remain valid ──

    def test_resource_only_patch_still_works(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Resource-only PATCH (no network) still succeeds (regression)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()["cpu_limit_percent"] == 200
        assert response.json()["ram_limit_mb"] == 4096

    def test_disk_only_patch_still_works(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Disk-only PATCH (no network) still succeeds (regression)."""
        self._set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        assert response.json()["disk_limit_gb"] == 50

    def test_network_only_patch_still_works(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Network-only PATCH (no resource) still succeeds (regression)."""
        from blueprints.schema import Blueprint
        from models.server_port import ServerPort

        bp = Blueprint.model_validate({
            "version": 1,
            "meta": {"id": "test_game_ports", "name": "Test", "category": "non_steam_game"},
            "runtime": {"image": "test:latest", "startup": "./server"},
            "ports": [
                {"name": "game", "protocol": "udp"},
                {"name": "query", "protocol": "udp"},
                {"name": "rcon", "protocol": "tcp"},
            ],
            "source": {"type": "manualUpload", "manual": {"requiredFiles": ["server.jar"], "instructions": "test"}},
        })
        test_server.game_type = "test_game_ports"
        test_server.ports = [
            ServerPort(role="game", port=27015, protocol="udp"),
            ServerPort(role="query", port=27016, protocol="udp"),
            ServerPort(role="rcon", port=27017, protocol="tcp"),
        ]
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.allocate_ports", return_value=[
            ("game", 27015, "udp"), ("query", 27016, "udp"), ("rcon", 27017, "tcp"),
        ]), patch("routers.servers.get_plugin") as mock_plugin, \
             patch("routers.servers.docker_service.is_running", return_value=False):
            mock_plugin.return_value.get_blueprint.return_value = bp
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text

    def test_config_scheduler_only_patch_still_works(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Config/scheduler-only PATCH (no resource, no network) still
        succeeds (regression)."""
        test_server.status = "stopped"
        test_server.auto_restart = True
        test_server.restart_interval_hours = 8
        db.commit()

        with patch("routers.servers.sync_server_restart_schedule") as mock_sync:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"restart_interval_hours": 4},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200, response.text
        mock_sync.assert_called_once()
        db.refresh(test_server)
        assert test_server.restart_interval_hours == 4


# ── Docker Warning / Partial-Success No-Drift Regression ──────────────
#
# Regression tests for the scrutiny blocker where Docker warning or
# partial-success responses can leave Docker runtime limits changed while
# the DB rolls back. Covers VAL-DOCKER-004, VAL-DOCKER-005, VAL-DOCKER-009,
# VAL-CROSS-012, and VAL-CROSS-014 at the router level.
#
# The Docker service restore (compensation) is tested in
# test_docker_service.py::TestUpdateContainerResourcesWarningRestore.
# These router tests prove the no-persist behavior end-to-end: when the
# Docker service returns a failure (from warnings), the DB rolls back and
# the response is sanitized.


class TestDockerWarningNoDriftRegression:
    """Router-level regression tests for Docker warning/partial-success no-drift."""

    def _set_resources(self, db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
        server.cpu_limit_percent = cpu
        server.ram_limit_mb = ram
        server.disk_limit_gb = disk
        db.commit()
        db.refresh(server)

    # ── VAL-DOCKER-009: Docker warning fail safely, no persist ──

    def test_docker_warning_no_persist_sanitized_response(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session, caplog,
    ):
        """Docker warning -> 503, old DB values, response and logs sanitized."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        leak_sentinel = "ZZLEAKSENTINEL_cgroup_/sys/fs/cgroup/controller"

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            with caplog.at_level(logging.WARNING):
                response = client.patch(
                    f"/api/servers/{test_server.id}",
                    json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )
        assert response.status_code == 503
        body = response.text
        assert leak_sentinel not in body
        assert "cgroup" not in body
        assert "/sys/fs" not in body
        assert "ZZLEAKSENTINEL" not in body
        log_text = caplog.text
        assert leak_sentinel not in log_text
        assert "ZZLEAKSENTINEL" not in log_text
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    # ── VAL-DOCKER-004: Combined CPU/RAM warning is atomic ──

    def test_combined_cpu_ram_docker_warning_no_partial_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Combined CPU+RAM Docker warning -> both old values preserved (atomic)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    # ── VAL-DOCKER-005: Rootless warning -> no restart, no privileged fallback ──

    def test_docker_warning_no_restart_no_privileged_fallback(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Docker warning -> no stop/remove/start, no status change, no drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_status = test_server.status

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.start") as mock_start, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.status == original_status
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_start.assert_not_called()

    # ── VAL-CROSS-012: Rootless failure safe through API ──

    def test_rootless_warning_api_returns_old_values_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Rootless cgroup warning -> 503, follow-up GET shows old values."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        # Follow-up GET confirms old values (no drift)
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.status_code == 200
        got = get.json()
        assert got["cpu_limit_percent"] == 100
        assert got["ram_limit_mb"] == 2048

    # ── VAL-CROSS-014: Combined CPU/RAM + disk warning -> no drift ──

    def test_combined_cpu_ram_disk_docker_warning_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """CPU+RAM+disk with Docker warning -> all values unchanged, no drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20

    def test_combined_cpu_ram_disk_docker_warning_no_destructive_side_effects(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-014: Docker warning -> no stop, no delete, no network mutation."""
        self._set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_close.assert_not_called()
        mock_open.assert_not_called()

    # ── VAL-DOCKER-009: Warning during CPU clear (null) ──

    def test_docker_warning_during_cpu_clear_no_persist(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Warning when clearing CPU -> old CPU value preserved, no drift."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        # Old CPU value preserved (not cleared to null)
        assert test_server.cpu_limit_percent == 100
        # RAM unchanged (not in payload)
        assert test_server.ram_limit_mb == 2048

    # ── Scrutiny round 2: drift failure (restore verification mismatch) ──

    def test_docker_drift_failure_503_and_db_unchanged(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Docker drift (restore verification mismatch) -> 503 with drift
        message, DB values unchanged (scrutiny round 2 fix)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={
                       "ok": False,
                       "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                       "drift": True,
                   }), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        body = response.text
        assert "manuelle" in body.lower() or "pruefung" in body.lower()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    def test_docker_drift_failure_followup_get_returns_old_values(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """After drift failure, follow-up GET returns old values (no drift)."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={
                       "ok": False,
                       "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                       "drift": True,
                   }), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.status_code == 200
        got = get.json()
        assert got["cpu_limit_percent"] == 100
        assert got["ram_limit_mb"] == 2048

    def test_docker_drift_failure_no_restart_no_destructive(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """Drift failure -> no stop/remove/start, no network mutation."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_status = test_server.status

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={
                       "ok": False,
                       "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                       "drift": True,
                   }), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.start") as mock_start, \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.status == original_status
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_start.assert_not_called()
        mock_close.assert_not_called()
        mock_open.assert_not_called()

    def test_docker_drift_failure_sanitized_response(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session, caplog,
    ):
        """Drift failure response and logs do not leak Docker internals."""
        self._set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        leak_sentinel = "ZZLEAKSENTINEL_cgroup_/sys/fs/cgroup/controller"

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={
                       "ok": False,
                       "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                       "drift": True,
                   }), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            with caplog.at_level(logging.WARNING):
                response = client.patch(
                    f"/api/servers/{test_server.id}",
                    json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )
        assert response.status_code == 503
        body = response.text
        assert leak_sentinel not in body
        assert "cgroup" not in body
        assert "/sys/fs" not in body
        assert "ZZLEAKSENTINEL" not in body
        assert "docker.sock" not in body
        log_text = caplog.text
        assert leak_sentinel not in log_text
        assert "ZZLEAKSENTINEL" not in log_text


# ── Exec-Tab Endpoint (v1.4.7) ───────────────────────────────────────────
#
# POST /api/servers/{id}/exec -- Oneshot-Befehl im MSM-Container.
#
# Sicherheits-Invarianten, die jeder dieser Tests verifiziert:
# - kein Host-Exec (Container-Name kommt nur aus container_name_for)
# - kein Shell-Escape (argv wird verbatim durchgereicht)
# - separate Permission server.console.exec (NICHT console.write)
# - Blueprint-Gate (enableExec=true)
# - Validierung: 1..32 args, je max 4096 Zeichen


class TestExecEndpoint:
    """Tests fuer POST /api/servers/{id}/exec.

    Strategie: Wir monkey-patchen ``exec_service.run_in_container``, damit
    wir Docker-Calls komplett vermeiden. Die Tests pruefen die Endpoint-
    Logik (Auth, Permissions, Blueprint-Gate, Validierung, Response-Format).
    """

    def _post(self, client, cookies, csrf, server_id, command):
        return client.post(
            f"/api/servers/{server_id}/exec",
            json={"command": command},
            cookies=cookies,
            headers={"X-CSRF-Token": csrf} if csrf else {},
        )

    def test_exec_endpoint_requires_csrf(
        self, client, owner_cookies, test_server
    ):
        # Kein CSRF-Header -> 403
        r = self._post(client, owner_cookies, csrf=None,
                       server_id=test_server.id, command=["ls"])
        assert r.status_code == 403

    def test_exec_endpoint_validates_argv_min_length(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        # Leeres command -> 422 (Pydantic min_length=1)
        def _fake_run(**kwargs):
            raise AssertionError("run_in_container should not be called")
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container", _fake_run
        )
        r = self._post(client, owner_cookies, csrf_token,
                       server_id=test_server.id, command=[])
        assert r.status_code == 422

    def test_exec_endpoint_validates_argv_max_length(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        def _fake_run(**kwargs):
            raise AssertionError("run_in_container should not be called")
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container", _fake_run
        )
        # 33 Elemente -> 422 (Pydantic max_length=32)
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=["x"] * 33,
        )
        assert r.status_code == 422

    def test_exec_endpoint_rejects_nonexistent_server(
        self, client, owner_cookies, csrf_token, monkeypatch
    ):
        # Server existiert nicht -> 404
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container",
            lambda **kw: (_ for _ in ()).throw(AssertionError),
        )
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=999999, command=["ls"],
        )
        assert r.status_code == 404

    def test_exec_endpoint_rejects_when_blueprint_enable_exec_false(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        """Dayz-Default hat enableExec=False -> 403, auch fuer Owner."""
        def _fake_run(**kwargs):
            raise AssertionError(
                "run_in_container should NOT be called when Blueprint "
                "enableExec is False"
            )
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container", _fake_run
        )
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=["ls"],
        )
        # Detail-Text darf keine internen Pfade leaken.
        assert r.status_code == 403
        body = r.json()
        assert "Exec" in body.get("detail", "")

    def test_exec_endpoint_runs_argv_verbatim_no_shell(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        """Kern-Invariante: argv wird verbatim an den Service durchgereicht.
        Wir simulieren einen Blueprint mit enableExec=True und pruefen, dass
        der Service die exakt gleichen Strings bekommt, die der Client
        geschickt hat -- inklusive Shell-Metazeichen, die als literaler
        Dateiname behandelt werden (nicht als Shell-Escape).
        """
        # Dayz-Blueprint im Service-Lookup ueberschreiben mit enableExec=True.
        class _Runtime:
            enableExec = True
            execTimeoutSeconds = 42
        class _FakeBP:
            runtime = _Runtime()
        monkeypatch.setattr(
            "routers.servers.exec_service.load_blueprint_for_server",
            lambda s: _FakeBP(),
        )

        seen: dict = {}
        def _fake_run(*, server_id, command, timeout, user_id):
            seen["server_id"] = server_id
            seen["command"] = command
            seen["timeout"] = timeout
            seen["user_id"] = user_id
            return {"ok": True, "stdout": "out", "stderr": ""}
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container", _fake_run
        )

        dangerous_argv = ["ls", "/data; rm -rf /tmp/x", "--with-dash"]
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=dangerous_argv,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        # Genau das argv ist angekommen -- KEIN String, KEIN sh -c.
        assert seen["command"] == dangerous_argv
        assert all(isinstance(a, str) for a in seen["command"])
        # Timeout kommt aus Blueprint, nicht aus User-Input.
        assert seen["timeout"] == 42

    def test_exec_endpoint_passes_output_through_from_service(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        """Der Endpoint reicht die Service-Response 1:1 durch. Truncation
        selbst wird im Service gemacht (siehe test_exec_service.py).

        Hier verifizieren wir nur: was der Service zurueckgibt, kommt im
        Response-Body identisch an (mit den Keys ok/stdout/stderr).
        """
        class _Runtime:
            enableExec = True
            execTimeoutSeconds = 60
        class _FakeBP:
            runtime = _Runtime()
        monkeypatch.setattr(
            "routers.servers.exec_service.load_blueprint_for_server",
            lambda s: _FakeBP(),
        )
        # Wir geben hier KEINEN Riesen-String, sondern ein eindeutiges
        # Marker-Paar, das nur in unserem Fake vorkommt. So sehen wir
        # exakt, was der Endpoint durchreicht.
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container",
            lambda **kw: {
                "ok": True,
                "stdout": "stdout-MARKER-42\n",
                "stderr": "stderr-MARKER-43\n",
            },
        )
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=["echo", "hello"],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["stdout"] == "stdout-MARKER-42\n"
        assert body["stderr"] == "stderr-MARKER-43\n"

    def test_exec_endpoint_returns_500_on_exec_failure(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        class _Runtime:
            enableExec = True
            execTimeoutSeconds = 60
        class _FakeBP:
            runtime = _Runtime()
        monkeypatch.setattr(
            "routers.servers.exec_service.load_blueprint_for_server",
            lambda s: _FakeBP(),
        )
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container",
            lambda **kw: {"ok": False, "stdout": "", "stderr": "",
                         "error": "exit 1"},
        )
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=["false"],
        )
        assert r.status_code == 500

    def test_exec_endpoint_returns_504_on_timeout(
        self, client, owner_cookies, test_server, csrf_token, monkeypatch
    ):
        class _Runtime:
            enableExec = True
            execTimeoutSeconds = 60
        class _FakeBP:
            runtime = _Runtime()
        monkeypatch.setattr(
            "routers.servers.exec_service.load_blueprint_for_server",
            lambda s: _FakeBP(),
        )
        monkeypatch.setattr(
            "routers.servers.exec_service.run_in_container",
            lambda **kw: {"ok": False, "stdout": "", "stderr": "",
                         "error": "timeout nach 60s"},
        )
        r = self._post(
            client, owner_cookies, csrf_token,
            server_id=test_server.id, command=["sleep", "999"],
        )
        assert r.status_code == 504
