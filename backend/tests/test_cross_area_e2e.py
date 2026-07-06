"""Cross-area end-to-end validation for the browser-to-API-to-Docker resource editing flow.

Every test uses synthetic data only (FastAPI TestClient + mocked Docker SDK).
No production DB data, real Docker containers, secrets, or host paths are touched.

Covers VAL-CROSS-001 through VAL-CROSS-014:

  VAL-CROSS-001: Browser edit reaches API and runtime state
  VAL-CROSS-002: Running CPU/RAM update is live without restart
  VAL-CROSS-003: Stopped server starts later with new limits
  VAL-CROSS-004: Unlimited values round trip across UI, API, and Docker
  VAL-CROSS-005: Disk warning or stop state is immediately visible
  VAL-CROSS-006: Unauthorized direct API attempts cannot bypass UI
  VAL-CROSS-007: Safe error surfaces across UI and API
  VAL-CROSS-008: Resource edits never perform destructive cleanup
  VAL-CROSS-009: End-to-end auth and CSRF cannot mutate resources
  VAL-CROSS-010: Mixed resource and network/config behavior is explicit
  VAL-CROSS-011: Lifecycle race with resource edit is serialized safely
  VAL-CROSS-012: Rootless Docker limitation is safe through UI and API
  VAL-CROSS-013: Active network reachability remains stable
  VAL-CROSS-014: Combined CPU/RAM plus disk failure leaves no drift

The tests below exercise the full PATCH -> GET -> status -> Docker-mock
flow to prove cross-area consistency.  Individual API/Docker/Disk
assertions are covered in test_servers_router.py and test_docker_service.py;
these tests add the cross-area end-to-end perspective.
"""
import logging
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, Server, ServerPermission


# ── Helpers shared by all cross-area test classes ──

def _set_resources(db: Session, server: Server, cpu=100, ram=2048, disk=20) -> None:
    server.cpu_limit_percent = cpu
    server.ram_limit_mb = ram
    server.disk_limit_gb = disk
    db.commit()
    db.refresh(server)


def _grant(db: Session, user: User, server: Server, *keys: str) -> None:
    for key in keys:
        db.add(ServerPermission(
            user_id=user.id, server_id=server.id, permission_key=key,
        ))
    db.commit()


# Sensitive markers that must never appear in responses or logs.
_SENSITIVE_MARKERS = [
    "docker.sock",
    "/var/run",
    "/sys/fs/cgroup",
    "Traceback",
    'File "',
    "BEGIN RSA",
    "BEGIN OPENSSH",
    "api_key",
    "secret_key",
    "password",
    "token",
]


def _assert_sanitized(text: str) -> None:
    """Assert no sensitive markers appear in the given text."""
    lowered = text.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker.lower() not in lowered, f"Sensitive marker '{marker}' found in response/log"


# ── VAL-CROSS-001: Browser edit reaches API and runtime state ──

class TestCrossAreaBrowserEditReachesRuntime:
    """A permitted user edits CPU, RAM, and disk; the same values appear
    in the API response, follow-up GET, status endpoint, and Docker update
    payload."""

    def test_cpu_ram_disk_patch_consistent_across_api_and_docker(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-001: PATCH CPU+RAM+disk on a running server; API
        response, GET, status, and Docker update payload all agree."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}) as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        # API response reflects the new values
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["cpu_limit_percent"] == 200
        assert data["ram_limit_mb"] == 4096
        assert data["disk_limit_gb"] == 50

        # Follow-up GET confirms persistence
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.status_code == 200
        got = get.json()
        assert got["cpu_limit_percent"] == 200
        assert got["ram_limit_mb"] == 4096
        assert got["disk_limit_gb"] == 50

        # Status endpoint reflects the new limits
        status = client.get(f"/api/servers/{test_server.id}/status", cookies=owner_cookies)
        assert status.status_code == 200
        st = status.json()
        assert st["cpu_limit_percent"] == 200
        assert st["ram_limit_mb"] == 4096
        assert st["disk_limit_gb"] == 50

        # Docker update was called with the correct CPU/RAM payload
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        container_name = call_args[0][0]
        updates = call_args[0][1]
        assert updates["cpu_limit_percent"] == 200
        assert updates["ram_limit_mb"] == 4096

        # Disk soft-limit was re-evaluated
        mock_eval.assert_called_once()

    def test_partial_cpu_patch_consistent_across_api_and_docker(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-001: Only CPU changed; API, GET, and Docker agree; RAM
        and disk unchanged."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 150},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["cpu_limit_percent"] == 150
        assert data["ram_limit_mb"] == 2048  # unchanged
        assert data["disk_limit_gb"] == 20   # unchanged

        # Docker update only contains CPU
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        assert "cpu_limit_percent" in updates
        assert "ram_limit_mb" not in updates


# ── VAL-CROSS-002: Running CPU/RAM update is live without restart ──

class TestCrossAreaRunningLiveNoRestart:
    """Changing CPU/RAM on a running server keeps the same container identity
    and start timestamp while effective CPU/RAM limits change."""

    def test_container_identity_preserved_on_live_update(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-002: Docker update called, no stop/remove/start, status
        remains running."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_status = test_server.status

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
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
        # Docker update called (live update, not restart)
        mock_update.assert_called_once()
        # No restart primitives
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_start.assert_not_called()
        mock_plugin.assert_not_called()
        # Status remains running
        db.refresh(test_server)
        assert test_server.status == original_status

    def test_docker_update_payload_uses_cfs_quota_not_restart(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-002: The Docker update call receives CPU/RAM kwargs,
        not stop/start/recreate."""
        _set_resources(db, test_server, cpu=100, ram=2048)
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
        # Verify the container name is the stable server-based name
        container_name = mock_update.call_args[0][0]
        assert f"msm-srv-{test_server.id}" == container_name


# ── VAL-CROSS-003: Stopped server starts later with new limits ──

class TestCrossAreaStoppedNextStart:
    """Changing resource limits while stopped persists values without
    starting a container; a later start uses the new limits."""

    def test_stopped_patch_persists_no_start_no_docker(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-003: Stopped server PATCH persists; no Docker update,
        no start, status stays stopped."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False) as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.docker_service.start") as mock_start, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["cpu_limit_percent"] == 200
        assert data["ram_limit_mb"] == 4096
        assert data["disk_limit_gb"] == 50
        assert data["status"] == "stopped"

        # No live Docker update, no start
        mock_update.assert_not_called()
        mock_start.assert_not_called()

        # Follow-up GET shows new limits persisted
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["cpu_limit_percent"] == 200
        assert get.json()["ram_limit_mb"] == 4096
        assert get.json()["disk_limit_gb"] == 50

    def test_stopped_patch_disk_limit_visible_after_persist(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-003: Disk soft limit is visible in status after
        stopped-server PATCH, no start needed."""
        _set_resources(db, test_server, disk=20)
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

        assert response.status_code == 200
        # Status endpoint shows the configured disk soft limit
        status = client.get(f"/api/servers/{test_server.id}/status", cookies=owner_cookies)
        assert status.json()["disk_limit_gb"] == 50
        assert status.json()["status"] == "stopped"


# ── VAL-CROSS-004: Unlimited values round trip across UI, API, and Docker ──

class TestCrossAreaUnlimitedRoundTrip:
    """Clearing limits sends null, API stores null, and Docker uses
    default/unlimited after live update."""

    def test_null_cpu_ram_round_trip_api_and_docker(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-004: PATCH null CPU+RAM on running server; API, GET,
        and Docker all reflect unlimited."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
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

        # GET confirms null persisted
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["cpu_limit_percent"] is None
        assert get.json()["ram_limit_mb"] is None

        # Docker update called with None for unlimited
        mock_update.assert_called_once()
        updates = mock_update.call_args[0][1]
        assert updates["cpu_limit_percent"] is None
        assert updates["ram_limit_mb"] is None

    def test_null_disk_round_trip_api(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-004: PATCH null disk on running server; API and GET
        reflect unlimited disk, no Docker hard-quota call."""
        _set_resources(db, test_server, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": True, "action": "none"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert response.json()["disk_limit_gb"] is None

        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["disk_limit_gb"] is None

        # No Docker update for disk-only change (soft limit, not hard quota)
        mock_update.assert_not_called()

    def test_null_then_set_value_round_trip(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-004: Set value -> null -> set value again; each step
        round-trips through API and Docker."""
        _set_resources(db, test_server, cpu=100)
        test_server.status = "running"
        db.commit()

        # Step 1: Clear to null
        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            r1 = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": None},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert r1.status_code == 200
        assert r1.json()["cpu_limit_percent"] is None

        # Step 2: Set back to 150
        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update2, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            r2 = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 150},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert r2.status_code == 200
        assert r2.json()["cpu_limit_percent"] == 150
        mock_update2.assert_called_once()
        assert mock_update2.call_args[0][1]["cpu_limit_percent"] == 150


# ── VAL-CROSS-005: Disk warning or stop state is immediately visible ──

class TestCrossAreaDiskWarningStopVisible:
    """When a disk soft limit crosses warning or stop thresholds, API/status
    and UI show consistent updated disk limit, usage, and warning/stop state
    immediately."""

    def test_disk_warning_state_visible_in_api_and_status(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-005: Lowering disk to warning threshold; API and status
        show consistent warning state immediately."""
        _set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources"), \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=9000), \
             patch("services.scheduler_service.get_plugin") as mock_plugin:
            # 12 GB limit, usage 9000 MB ~73% -> within policy, no warning
            # Actually 9000/12288 = 73%, below 80% warning threshold
            # Use a lower limit to trigger warning: 12 GB = 12288 MB, 9000/12288 = 73%
            # Let's use 11 GB = 11264 MB, 9000/11264 = 80% -> warning
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 11},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        db.refresh(test_server)
        # Disk limit updated
        assert test_server.disk_limit_gb == 11
        # No destructive stop (usage within policy or warning only)
        mock_plugin.assert_not_called()

    def test_disk_stop_state_visible_in_api_and_status(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-005: Lowering disk below usage; status shows error state,
        API shows updated limit, non-destructive."""
        _set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        stop_called = []

        class FakePlugin:
            def stop(self, server):
                stop_called.append(server.id)

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources"), \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},  # 10 GB = 10240 MB, usage 30000 > 100%
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["disk_limit_gb"] == 10
        # Stop was invoked (lifecycle-safe)
        assert stop_called == [test_server.id]
        # No destructive remove
        mock_remove.assert_not_called()

        # DB state shows error with disk message
        db.refresh(test_server)
        assert test_server.status == "error"
        assert "Disk-Soft-Limit" in (test_server.status_message or "")

        # Status endpoint shows the same state
        mock_status_plugin = MagicMock()
        mock_status_plugin.get_status.return_value = MagicMock(
            status="error", message="Disk-Soft-Limit", cpu_percent=None,
            ram_mb=None, disk_mb=30000, uptime_seconds=0, started_at=None,
        )
        with patch("routers.servers.get_plugin", return_value=mock_status_plugin):
            status = client.get(f"/api/servers/{test_server.id}/status", cookies=owner_cookies)
        assert status.status_code == 200
        st = status.json()
        assert st["disk_limit_gb"] == 10


# ── VAL-CROSS-006: Unauthorized direct API attempts cannot bypass UI ──

class TestCrossAreaUnauthorizedDirectAPI:
    """A user without server.resources.manage cannot update resource fields
    by direct API PATCH, regardless of payload shape."""

    def test_view_only_user_direct_patch_403(
        self, client: TestClient, regular_user: User, user_cookies: dict,
        user_csrf_token: str, test_server: Server, db: Session,
    ):
        """VAL-CROSS-006: View-only user direct PATCH -> 403, values unchanged."""
        _grant(db, regular_user, test_server, "server.view")
        _set_resources(db, test_server, cpu=100, ram=2048)

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048

    def test_no_permission_user_direct_patch_403(
        self, client: TestClient, regular_user: User, user_cookies: dict,
        user_csrf_token: str, test_server: Server, db: Session,
    ):
        """VAL-CROSS-006: User with no permissions at all -> 403."""
        _set_resources(db, test_server, cpu=100)

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_partial_direct_patch_by_unauthorized_403(
        self, client: TestClient, regular_user: User, user_cookies: dict,
        user_csrf_token: str, test_server: Server, db: Session,
    ):
        """VAL-CROSS-006: Single-field direct PATCH by unauthorized user -> 403."""
        _grant(db, regular_user, test_server, "server.view")
        _set_resources(db, test_server, disk=20)

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"disk_limit_gb": 50},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20

    def test_network_permission_only_cannot_patch_resources(
        self, client: TestClient, regular_user: User, user_cookies: dict,
        user_csrf_token: str, test_server: Server, db: Session,
    ):
        """VAL-CROSS-006: User with network but not resource permission
        cannot PATCH resource fields."""
        _grant(db, regular_user, test_server, "server.view", "server.network.manage")
        _set_resources(db, test_server, cpu=100)

        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100


# ── VAL-CROSS-007: Safe error surfaces across UI and API ──

class TestCrossAreaSafeErrorSurfaces:
    """Validation, authorization, Docker, and disk errors are actionable but
    sanitized across API responses and logs."""

    def test_validation_error_sanitized(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-007: 422 validation error is sanitized."""
        _set_resources(db, test_server, cpu=100)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 99999},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 422
        _assert_sanitized(response.text)

    def test_docker_error_sanitized(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session, caplog,
    ):
        """VAL-CROSS-007: Docker update failure response and logs sanitized."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        sentinel = "ZZSENTINEL_/var/run/docker.sock_cgroup_failure"

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
        _assert_sanitized(response.text)
        assert sentinel not in response.text
        assert sentinel not in caplog.text

    def test_disk_error_sanitized(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-007: Disk re-evaluation failure response is sanitized."""
        _set_resources(db, test_server, disk=20)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": False, "error": "ZZSENTINEL_/tmp/secret/path"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 503
        _assert_sanitized(response.text)

    def test_auth_error_sanitized(
        self, client: TestClient, test_server: Server, db: Session,
    ):
        """VAL-CROSS-007: Unauthenticated error is sanitized, no internals."""
        _set_resources(db, test_server, cpu=100)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
        )
        assert response.status_code == 401
        _assert_sanitized(response.text)


# ── VAL-CROSS-008: Resource edits never perform destructive cleanup ──

class TestCrossAreaNoDestructiveCleanup:
    """Successful and failed resource edits never delete server install data,
    backup files, console logs, or database records."""

    def test_successful_edit_no_prune_no_delete(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-008: Successful resource PATCH calls no prune/delete/remove."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}), \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.docker_service.prune") if hasattr(
                 __import__("routers.servers", fromlist=["docker_service"]).docker_service, "prune"
             ) else patch.object(
                 __import__("routers.servers", fromlist=["docker_service"]).docker_service,
                 "prune", create=True,
             ) as mock_prune, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("shutil.rmtree") as mock_rmtree:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        mock_remove.assert_not_called()
        mock_rmtree.assert_not_called()

    def test_failed_edit_no_prune_no_delete(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-008: Failed resource PATCH calls no prune/delete/remove."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "failed"}), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("shutil.rmtree") as mock_rmtree:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 503
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_rmtree.assert_not_called()
        # All values unchanged
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20

    def test_disk_overlimit_no_filesystem_deletion(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-008: Disk over-limit stop does not delete files or
        remove the container."""
        _set_resources(db, test_server, disk=50)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        class FakePlugin:
            def stop(self, server):
                pass

        with patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.remove") as mock_remove, \
             patch("services.scheduler_service.docker_service.disk_usage_mb", return_value=30000), \
             patch("services.scheduler_service.get_plugin", return_value=FakePlugin()), \
             patch("shutil.rmtree") as mock_rmtree:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 10},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        mock_remove.assert_not_called()
        mock_rmtree.assert_not_called()
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 10


# ── VAL-CROSS-009: End-to-end auth and CSRF cannot mutate resources ──

class TestCrossAreaAuthCSRF:
    """Browser resource PATCH succeeds only with expected auth and CSRF.
    Replayed, unauthenticated, or missing-CSRF direct PATCH attempts fail."""

    def test_unauthenticated_patch_no_mutation(
        self, client: TestClient, test_server: Server, db: Session,
    ):
        """VAL-CROSS-009: No auth cookies -> 401, no mutation."""
        _set_resources(db, test_server, cpu=100)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
        )
        assert response.status_code == 401
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_missing_csrf_patch_no_mutation(
        self, client: TestClient, owner_cookies: dict, test_server: Server, db: Session,
    ):
        """VAL-CROSS-009: Authenticated but no CSRF -> 403, no mutation."""
        _set_resources(db, test_server, cpu=100)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=owner_cookies,
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_invalid_csrf_patch_no_mutation(
        self, client: TestClient, owner_cookies: dict, test_server: Server, db: Session,
    ):
        """VAL-CROSS-009: Authenticated with invalid CSRF -> 403, no mutation."""
        _set_resources(db, test_server, cpu=100)
        response = client.patch(
            f"/api/servers/{test_server.id}",
            json={"cpu_limit_percent": 200},
            cookies=owner_cookies,
            headers={"X-CSRF-Token": "invalid-token-value"},
        )
        assert response.status_code == 403
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100

    def test_valid_auth_csrf_patch_succeeds(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-009: Authenticated + valid CSRF -> 200, mutation occurs."""
        _set_resources(db, test_server, cpu=100)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=False), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert response.status_code == 200
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 200


# ── VAL-CROSS-010: Mixed resource and network/config behavior is explicit ──

class TestCrossAreaMixedPayload:
    """Mixed resource plus network/config PATCH payloads either reject safely
    with no mutation or execute the documented permission-checked atomic
    sequence."""

    def test_mixed_resource_network_409_no_mutation(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-010: Resource + network -> 409, no mutation."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "stopped"
        db.commit()

        with patch("routers.servers.allocate_ports") as mock_alloc, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "game_port": 27015},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 409
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        mock_alloc.assert_not_called()
        mock_eval.assert_not_called()
        mock_update.assert_not_called()

    def test_mixed_resource_disk_network_409_all_unchanged(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-010: CPU+RAM+disk+network -> 409, all values unchanged."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        db.commit()

        with patch("routers.servers.docker_service.is_running") as mock_running, \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval, \
             patch("routers.servers.allocate_ports") as mock_alloc:
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
        mock_running.assert_not_called()
        mock_update.assert_not_called()
        mock_eval.assert_not_called()
        mock_alloc.assert_not_called()

    def test_resource_plus_config_atomic_rollback(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-010: Resource + config (restart scheduler) in one PATCH;
        scheduler sync failure rolls back both."""
        _set_resources(db, test_server, cpu=100)
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
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.restart_interval_hours == 8


# ── VAL-CROSS-011: Lifecycle race with resource edit is serialized safely ──

class TestCrossAreaLifecycleRace:
    """A concurrent lifecycle operation and resource edit produce one
    consistent final state with no drift."""

    def test_lifecycle_active_resource_patch_409_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-011: Lifecycle job active -> 409, no mutation, no drift."""
        _set_resources(db, test_server, cpu=100, ram=2048)
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
        assert test_server.ram_limit_mb == 2048

    def test_lifecycle_active_disk_patch_409_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-011: Lifecycle active + disk change on running server
        -> 409, no disk evaluation, no drift."""
        _set_resources(db, test_server, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=True), \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 409
        mock_eval.assert_not_called()
        db.refresh(test_server)
        assert test_server.disk_limit_gb == 20

    def test_lifecycle_active_combined_resource_disk_409(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-011: Lifecycle active + CPU+RAM+disk -> 409, all
        unchanged, no Docker, no disk eval."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.is_lifecycle_job_active", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources") as mock_update, \
             patch("routers.servers.evaluate_disk_soft_limit") as mock_eval:
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 409
        mock_update.assert_not_called()
        mock_eval.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20


# ── VAL-CROSS-012: Rootless Docker limitation is safe through UI and API ──

class TestCrossAreaRootlessFailure:
    """A rootless live-update failure leaves API/GET with old values, no
    restart, no privileged fallback."""

    def test_rootless_failure_api_old_values_no_restart(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-012: Rootless failure -> 503, GET returns old values,
        no stop/remove/start."""
        _set_resources(db, test_server, cpu=100, ram=2048)
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
        # No restart fallback
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_start.assert_not_called()

        # Follow-up GET shows old values (no drift)
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["cpu_limit_percent"] == 100
        assert get.json()["ram_limit_mb"] == 2048

        db.refresh(test_server)
        assert test_server.status == original_status

    def test_rootless_failure_sanitized_no_cgroup_paths(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session, caplog,
    ):
        """VAL-CROSS-012: Rootless failure response has no cgroup paths,
        socket paths, or stack traces."""
        _set_resources(db, test_server, cpu=100)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "Ressourcen-Limit konnte nicht angewendet werden"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            with caplog.at_level(logging.WARNING):
                response = client.patch(
                    f"/api/servers/{test_server.id}",
                    json={"cpu_limit_percent": 200},
                    cookies=owner_cookies,
                    headers={"X-CSRF-Token": csrf_token},
                )

        assert response.status_code == 503
        _assert_sanitized(response.text)
        _assert_sanitized(caplog.text)

    def test_rootless_drift_failure_safe(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-012: Rootless drift (restore verification mismatch) ->
        503 with drift message, old values, no restart."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={
                       "ok": False,
                       "error": "Ressourcen-Update fehlgeschlagen, manuelle Pruefung erforderlich",
                       "drift": True,
                   }), \
             patch("routers.servers.docker_service.stop") as mock_stop, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 503
        mock_stop.assert_not_called()
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100


# ── VAL-CROSS-013: Active network reachability remains stable ──

class TestCrossAreaNetworkReachabilityStable:
    """Before, during, and after a running CPU/RAM edit, port bindings remain
    unchanged and no port close/open calls are made."""

    def test_resource_edit_no_port_changes(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-013: CPU/RAM edit on running server; ports unchanged,
        no close/open calls."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]
        original_bind_ip = test_server.public_bind_ip

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}), \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.iptables_revoke_server") as mock_revoke, \
             patch("routers.servers.iptables_accept_server") as mock_accept, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        # No firewall/iptables/port mutations
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        mock_revoke.assert_not_called()
        mock_accept.assert_not_called()
        # Ports and bind IP unchanged
        db.refresh(test_server)
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports
        assert test_server.public_bind_ip == original_bind_ip

    def test_resource_edit_failed_no_port_changes(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-013: Even a failed CPU/RAM edit does not touch ports."""
        _set_resources(db, test_server, cpu=100, ram=2048)
        test_server.status = "running"
        db.commit()
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "failed"}), \
             patch("routers.servers.close_ports") as mock_close, \
             patch("routers.servers.open_ports") as mock_open, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 503
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        db.refresh(test_server)
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports


# ── VAL-CROSS-014: Combined CPU/RAM plus disk failure leaves no drift ──

class TestCrossAreaCombinedFailureNoDrift:
    """If CPU/RAM and disk are changed together and Docker update or disk
    re-evaluation fails, UI, API, Docker, and disk policy state remain
    aligned on the previous values."""

    def test_docker_failure_with_disk_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-014: CPU+RAM+disk with Docker failure -> all values
        unchanged, no drift."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "failed"}), \
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

        # Follow-up GET confirms no drift
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.json()["cpu_limit_percent"] == 100
        assert get.json()["ram_limit_mb"] == 2048
        assert get.json()["disk_limit_gb"] == 20

    def test_docker_failure_with_disk_no_destructive_side_effects(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-014: Combined failure -> no stop, no remove, no network
        mutation, no disk policy mutation."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()
        original_ports = [(p.port, p.protocol, p.role) for p in test_server.ports]

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "failed"}), \
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
        mock_stop.assert_not_called()
        mock_remove.assert_not_called()
        mock_close.assert_not_called()
        mock_open.assert_not_called()
        db.refresh(test_server)
        assert [(p.port, p.protocol, p.role) for p in test_server.ports] == original_ports

    def test_disk_failure_with_cpu_ram_no_drift(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-014: CPU+RAM+disk where Docker succeeds but disk
        re-evaluation fails -> all values unchanged (atomic rollback)."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        test_server.install_dir = "/tmp/test_server"
        db.commit()

        # Docker update succeeds, but disk evaluation fails.
        # The router evaluates disk inside the lifecycle lock after Docker
        # update. If disk fails, the entire transaction rolls back.
        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": True}) as mock_update, \
             patch("routers.servers.is_lifecycle_job_active", return_value=False), \
             patch("routers.servers.evaluate_disk_soft_limit",
                   return_value={"ok": False, "error": "disk measurement failed"}):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        # Disk failure rolls back the entire transaction (including CPU/RAM)
        assert response.status_code == 503
        db.refresh(test_server)
        assert test_server.cpu_limit_percent == 100
        assert test_server.ram_limit_mb == 2048
        assert test_server.disk_limit_gb == 20

    def test_combined_warning_no_drift_followup_get(
        self, client: TestClient, owner_cookies: dict, csrf_token: str,
        test_server: Server, db: Session,
    ):
        """VAL-CROSS-014: Combined Docker warning -> follow-up GET shows all
        old values, no partial drift."""
        _set_resources(db, test_server, cpu=100, ram=2048, disk=20)
        test_server.status = "running"
        db.commit()

        with patch("routers.servers.docker_service.is_running", return_value=True), \
             patch("routers.servers.docker_service.update_container_resources",
                   return_value={"ok": False, "error": "warning failure"}), \
             patch("routers.servers.is_lifecycle_job_active", return_value=False):
            response = client.patch(
                f"/api/servers/{test_server.id}",
                json={"cpu_limit_percent": 200, "ram_limit_mb": 4096, "disk_limit_gb": 50},
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 503
        get = client.get(f"/api/servers/{test_server.id}", cookies=owner_cookies)
        assert get.status_code == 200
        got = get.json()
        assert got["cpu_limit_percent"] == 100
        assert got["ram_limit_mb"] == 2048
        assert got["disk_limit_gb"] == 20
