"""Tests fuer den Setup-Router (Schritt 10.3).

Deckt die 5 Endpoints unter /api/setup/* ab:
- GET  /pending-restores
- POST /pending-restores/discard
- POST /restore-orphan/{idx}
- GET  /migration-status
- POST /migration-cancel

Test-Kategorien:
- Auth: permission + CSRF enforcement
- Happy-Path: leere Items, Items vorhanden, korrekte Response-Struktur
- Edge cases: idx out of range, no cloud provider, error propagation
- Security: Sanitization von Provider-Errors, Config-Injection-Schutz in
  _set_env_flag
- Background-Task: restore-orphan kickt einen Task, schlaegt nicht fehl
  wenn Loop nicht laeuft
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


# ── Helper ──────────────────────────────────────────────────────────────


def _make_fake_metadata(
    *,
    server_id: int = 1,
    server_name: str = "Mein Server",
    game_type: str = "minecraft",
    cpu_limit_percent: int | None = 200,
    ram_limit_mb: int | None = 4096,
    disk_limit_gb: int | None = 50,
    size_mb: int | None = 1024,
    ports: list[dict] | None = None,
    panel_version: str = "v1.6.0",
    created_at: str = "2026-06-06T15:30:00Z",
):
    """Baut ein Fake-BackupMetadata mit allen Defaults."""
    from services.backup_provider import BackupMetadata

    return BackupMetadata(
        backup_version=1,
        server_id=server_id,
        server_name=server_name,
        game_type=game_type,
        created_at=created_at,
        panel_version=panel_version,
        cpu_limit_percent=cpu_limit_percent,
        ram_limit_mb=ram_limit_mb,
        disk_limit_gb=disk_limit_gb,
        public_bind_ip=None,
        ports=ports or [
            {"role": "game", "port": 25565, "protocol": "tcp"},
            {"role": "rcon", "port": 25575, "protocol": "tcp"},
        ],
        size_mb=size_mb,
    )


@pytest.fixture
def cloud_provider_settings(monkeypatch):
    """Setzt backup_provider='s3' damit Endpoints Cloud-Pfade nutzen."""
    from services import backup_migration_service as mod

    monkeypatch.setattr(
        "services.backup_migration_service.settings.backup_provider", "s3"
    )
    monkeypatch.setattr(
        "routers.setup.settings.backup_provider", "s3"
    )
    return mod


@pytest.fixture
def tmp_env_path(tmp_path, monkeypatch):
    """Monkey-patcht ENV_PATH in routers.setup auf tmp_path/.env."""
    import routers.setup as setup_router

    fake_env = tmp_path / ".env"
    monkeypatch.setattr(setup_router, "ENV_PATH", fake_env)
    return fake_env


# ── GET /api/setup/pending-restores ─────────────────────────────────────


class TestGetPendingRestores:
    """Tests fuer GET /api/setup/pending-restores."""

    def test_returns_empty_for_local_provider(
        self, client: TestClient, owner_cookies: dict
    ):
        """Lokaler Provider -> pending=false, items=[] (kein Probe-Call)."""
        from routers import setup as setup_router

        # backup_provider=local (default in conftest)
        res = client.get(
            "/api/setup/pending-restores", cookies=owner_cookies
        )
        assert res.status_code == 200
        body = res.json()
        assert body["pending"] is False
        assert body["items"] == []
        assert body["error"] is None
        assert body["provider"] == "local"

    def test_returns_items_for_cloud_provider(
        self, client: TestClient, owner_cookies: dict, cloud_provider_settings
    ):
        """Cloud-Provider mit Metadata -> pending=true, items gefuellt."""
        fake_items = [
            _make_fake_metadata(server_id=1, server_name="Server A"),
            _make_fake_metadata(server_id=2, server_name="Server B"),
        ]
        with patch(
            "routers.setup.probe_cloud_backups", return_value=fake_items
        ):
            res = client.get(
                "/api/setup/pending-restores", cookies=owner_cookies
            )

        assert res.status_code == 200
        body = res.json()
        assert body["pending"] is True
        assert len(body["items"]) == 2
        assert body["items"][0]["server_name"] == "Server A"
        assert body["items"][0]["cpu_limit_percent"] == 200
        assert body["items"][1]["server_name"] == "Server B"
        assert body["provider"] == "s3"

    def test_sanitizes_provider_error(
        self, client: TestClient, owner_cookies: dict, cloud_provider_settings
    ):
        """Probe-Fehler -> error-Message enthaelt nur Typ-Name, keine Tokens."""
        with patch(
            "routers.setup.probe_cloud_backups",
            side_effect=Exception("AKIA12345SECRET /home/leak"),
        ):
            res = client.get(
                "/api/setup/pending-restores", cookies=owner_cookies
            )

        assert res.status_code == 200
        body = res.json()
        assert body["pending"] is False
        assert body["items"] == []
        # Sanitization: type-Name ja, Message-Details nein
        assert body["error"] is not None
        assert "AKIA" not in body["error"]
        assert "/home/leak" not in body["error"]
        assert "Exception" in body["error"]

    def test_requires_panel_settings_read_permission(
        self, client: TestClient, regular_user, user_cookies: dict
    ):
        """Regular User ohne panel.settings.read -> 403."""
        res = client.get(
            "/api/setup/pending-restores", cookies=user_cookies
        )
        assert res.status_code == 403


# ── POST /api/setup/pending-restores/discard ───────────────────────────


class TestDiscardPendingRestores:
    """Tests fuer POST /pending-restores/discard."""

    def test_sets_env_flag(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        tmp_env_path: Path,
    ):
        """discard setzt MSM_PENDING_CLOUD_RESTORE=0 in .env."""
        # Initiale .env mit pending=1
        tmp_env_path.write_text(
            'MSM_PENDING_CLOUD_RESTORE="1"\n', encoding="utf-8"
        )

        res = client.post(
            "/api/setup/pending-restores/discard",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True

        # .env wurde geschrieben
        content = tmp_env_path.read_text(encoding="utf-8")
        assert "MSM_PENDING_CLOUD_RESTORE=0" in content

    def test_requires_csrf(
        self, client: TestClient, owner_cookies: dict, tmp_env_path: Path
    ):
        """Ohne CSRF-Token -> 403."""
        res = client.post(
            "/api/setup/pending-restores/discard",
            cookies=owner_cookies,
        )
        assert res.status_code == 403

    def test_requires_panel_settings_write_permission(
        self,
        client: TestClient,
        regular_user,
        user_cookies: dict,
        user_csrf_token: str,
    ):
        """Regular User ohne panel.settings.write -> 403."""
        res = client.post(
            "/api/setup/pending-restores/discard",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403


# ── _set_env_flag unit-tests (Security) ──────────────────────────────


class TestSetEnvFlagSanitization:
    """Tests fuer _set_env_flag - Config-Injection-Schutz."""

    def test_rejects_unsafe_value(
        self, client: TestClient, tmp_env_path: Path
    ):
        """Nicht-alphanumerische Werte werden abgelehnt."""
        from routers.setup import _set_env_flag

        with pytest.raises(ValueError, match="Unsafe env-flag value"):
            _set_env_flag("MSM_X", "AKIA1234; rm -rf /")

    def test_rejects_unsafe_flag_name(
        self, client: TestClient, tmp_env_path: Path
    ):
        """Nicht-MSM_-prefix-werte werden abgelehnt."""
        from routers.setup import _set_env_flag

        with pytest.raises(ValueError, match="Unsafe env-flag name"):
            _set_env_flag("NOT_MSM_FLAG", "0")

    def test_appends_new_flag(
        self, client: TestClient, tmp_env_path: Path
    ):
        """Wenn Flag nicht existiert, wird er angehaengt."""
        from routers.setup import _set_env_flag

        tmp_env_path.write_text("OTHER_FLAG=foo\n", encoding="utf-8")
        _set_env_flag("MSM_NEW_FLAG", "1")

        content = tmp_env_path.read_text(encoding="utf-8")
        assert "MSM_NEW_FLAG=1" in content
        assert "OTHER_FLAG=foo" in content  # Andere Vars unangetastet

    def test_replaces_existing_flag(
        self, client: TestClient, tmp_env_path: Path
    ):
        """Wenn Flag existiert, wird nur die Zeile ersetzt."""
        from routers.setup import _set_env_flag

        tmp_env_path.write_text(
            "MSM_X=0\nOTHER=foo\nMSM_X=ignored\n", encoding="utf-8"
        )
        _set_env_flag("MSM_X", "1")

        content = tmp_env_path.read_text(encoding="utf-8")
        # Achtung: nur die ERSTE Vorkommen wird ersetzt (re.MULTILINE-Pattern
        # + sub ohne count). Das ist OK weil Flags in .env nur einmal
        # vorkommen duerfen.
        assert "MSM_X=1" in content
        assert "OTHER=foo" in content


# ── POST /api/setup/restore-orphan/{idx} ──────────────────────────────


class TestRestoreOrphan:
    """Tests fuer POST /restore-orphan/{idx}."""

    def test_returns_404_for_negative_idx(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        res = client.post(
            "/api/setup/restore-orphan/-1",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 404

    def test_returns_404_for_idx_beyond_items(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        cloud_provider_settings,
    ):
        """idx >= len(items) -> 404."""
        with patch("routers.setup.probe_cloud_backups", return_value=[]):
            res = client.post(
                "/api/setup/restore-orphan/0",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert res.status_code == 404

    def test_returns_503_when_provider_fails(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        cloud_provider_settings,
    ):
        """Probe wirft Exception -> 503 mit sanitized message."""
        with patch(
            "routers.setup.probe_cloud_backups",
            side_effect=Exception("AKIA12345SECRET /home/leak"),
        ):
            res = client.post(
                "/api/setup/restore-orphan/0",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )
        assert res.status_code == 503
        body = res.json()
        assert "AKIA" not in body["detail"]
        assert "/home/leak" not in body["detail"]
        assert "Exception" in body["detail"]

    def test_creates_server_and_returns_202(
        self,
        client: TestClient,
        db: Session,
        owner_cookies: dict,
        csrf_token: str,
        cloud_provider_settings,
    ):
        """Happy Path: Server + Backup angelegt, 202 + server_id."""
        from models import Backup, Server

        meta = _make_fake_metadata(server_id=42, server_name="To Restore")

        # restore_backup in Background-Task mocken, damit kein echter
        # Provider-Call laeuft (Tests haben keinen echten Provider).
        with patch("routers.setup.probe_cloud_backups", return_value=[meta]), \
             patch("routers.setup._do_restore_in_background") as mock_restore:
            res = client.post(
                "/api/setup/restore-orphan/0",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert res.status_code == 202, f"Expected 202, got {res.status_code}: {res.text}"
        body = res.json()
        assert body["server_name"] == "To Restore"
        assert body["status"] == "creating"
        assert body["server_id"] > 0
        assert body["backup_id"] > 0
        # Background-Task wurde aufgerufen
        mock_restore.assert_called_once()

        # Server-Row existiert
        server = (
            db.query(Server)
            .filter(Server.id == body["server_id"])
            .first()
        )
        assert server is not None
        assert server.name == "To Restore"
        assert server.status == "creating"
        # Public bind_ip wird IGNORIERT
        assert server.public_bind_ip is None
        # CPU/RAM/Disk aus Metadata
        assert server.cpu_limit_percent == 200
        assert server.ram_limit_mb == 4096
        assert server.disk_limit_gb == 50

        # Backup-Row existiert mit remote_key
        backup = (
            db.query(Backup)
            .filter(Backup.id == body["backup_id"])
            .first()
        )
        assert backup is not None
        assert backup.provider == "s3"
        assert backup.remote_key == meta.remote_key

    def test_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.post(
            "/api/setup/restore-orphan/0", cookies=owner_cookies
        )
        assert res.status_code == 403

    def test_requires_panel_settings_write_permission(
        self,
        client: TestClient,
        regular_user,
        user_cookies: dict,
        user_csrf_token: str,
    ):
        res = client.post(
            "/api/setup/restore-orphan/0",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403


# ── GET /api/setup/migration-status ────────────────────────────────────


class TestMigrationStatus:
    """Tests fuer GET /migration-status."""

    def test_returns_idle_initially(
        self, client: TestClient, owner_cookies: dict
    ):
        """Ohne laufende Migration -> status=idle, total=0."""
        from services.backup_migration_service import (
            get_migration_service,
            reset_migration_service,
        )
        reset_migration_service()  # Sicherstellen dass Singleton frisch

        res = client.get("/api/setup/migration-status", cookies=owner_cookies)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "idle"
        assert body["total"] == 0
        assert body["migrated"] == 0

    def test_returns_running_status(
        self, client: TestClient, owner_cookies: dict
    ):
        """Mit laufender Migration -> status=running + Progress-Felder."""
        from services.backup_migration_service import (
            MigrationStatus,
            get_migration_service,
            reset_migration_service,
        )
        reset_migration_service()
        svc = get_migration_service()
        # Manuell Progress setzen (privates Feld direkt — Test-Setup)
        svc._progress.status = MigrationStatus.RUNNING
        svc._progress.total = 5
        svc._progress.migrated = 2

        res = client.get("/api/setup/migration-status", cookies=owner_cookies)
        body = res.json()
        assert body["status"] == "running"
        assert body["total"] == 5
        assert body["migrated"] == 2

    def test_requires_panel_settings_read_permission(
        self, client: TestClient, regular_user, user_cookies: dict
    ):
        res = client.get(
            "/api/setup/migration-status", cookies=user_cookies
        )
        assert res.status_code == 403


# ── POST /api/setup/migration-cancel ───────────────────────────────────


class TestMigrationCancel:
    """Tests fuer POST /migration-cancel."""

    def test_calls_svc_cancel(
        self, client: TestClient, owner_cookies: dict, csrf_token: str
    ):
        """cancel-Endpoint ruft svc.cancel() auf."""
        from services.backup_migration_service import (
            get_migration_service,
            reset_migration_service,
        )
        reset_migration_service()
        svc = get_migration_service()
        assert not svc._cancel_event.is_set()  # noch nicht gesetzt

        res = client.post(
            "/api/setup/migration-cancel",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200
        assert res.json()["ok"] is True
        assert svc._cancel_event.is_set()  # jetzt gesetzt

    def test_requires_csrf(
        self, client: TestClient, owner_cookies: dict
    ):
        res = client.post(
            "/api/setup/migration-cancel", cookies=owner_cookies
        )
        assert res.status_code == 403

    def test_requires_panel_settings_write_permission(
        self,
        client: TestClient,
        regular_user,
        user_cookies: dict,
        user_csrf_token: str,
    ):
        res = client.post(
            "/api/setup/migration-cancel",
            cookies=user_cookies,
            headers={"X-CSRF-Token": user_csrf_token},
        )
        assert res.status_code == 403
