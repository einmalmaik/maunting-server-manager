"""Tests for mods router: CRUD, permissions, CSRF."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, Server, Mod, ServerPermission


class TestListMods:
    def test_owner_can_list(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.get(f"/api/mods/{test_server.id}", cookies=owner_cookies)
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_user_with_permission_can_list(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: list[ServerPermission]):
        response = client.get(f"/api/mods/{test_server.id}", cookies=user_cookies)
        assert response.status_code == 200

    def test_user_without_permission_blocked(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server):
        response = client.get(f"/api/mods/{test_server.id}", cookies=user_cookies)
        assert response.status_code == 403

    def test_unauthorized_blocked(self, client: TestClient, test_server: Server):
        response = client.get(f"/api/mods/{test_server.id}")
        assert response.status_code == 401


class TestSubscribeMod:
    def test_owner_can_subscribe(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str):
        response = client.post(
            f"/api/mods/{test_server.id}?workshop_id=12345",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code in (200, 201)
        data = response.json()
        assert data["workshop_id"] == "12345"

    def test_duplicate_subscription_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str):
        # First subscription
        client.post(
            f"/api/mods/{test_server.id}?workshop_id=12345",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        # Duplicate
        response = client.post(
            f"/api/mods/{test_server.id}?workshop_id=12345",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 400
        assert "bereits abonniert" in response.json()["detail"]

    def test_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.post(
            f"/api/mods/{test_server.id}?workshop_id=12345",
            cookies=owner_cookies,
        )
        assert response.status_code == 403


class TestUpdateMod:
    def test_owner_can_update(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str, db: Session):
        mod = Mod(server_id=test_server.id, workshop_id="12345", name="Test Mod", load_order=0)
        db.add(mod)
        db.commit()
        db.refresh(mod)

        response = client.patch(
            f"/api/mods/{test_server.id}/{mod.id}?load_order=1",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200
        assert response.json()["load_order"] == 1

    def test_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, db: Session):
        mod = Mod(server_id=test_server.id, workshop_id="12345", name="Test Mod", load_order=0)
        db.add(mod)
        db.commit()

        response = client.patch(
            f"/api/mods/{test_server.id}/{mod.id}?load_order=1",
            cookies=owner_cookies,
        )
        assert response.status_code == 403

    def test_enable_disable_rewrites_modlist(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        mod = Mod(server_id=test_server.id, workshop_id="12345", enabled=True, load_order=0)
        db.add(mod)
        db.commit()
        db.refresh(mod)

        with patch("routers.mods.get_plugin") as mock_get_plugin:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            response = client.patch(
                f"/api/mods/{test_server.id}/{mod.id}?enabled=false",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert response.json()["enabled"] is False
        plugin.update_modlist.assert_called_once()
        assert plugin.update_modlist.call_args.args[0].id == test_server.id


class TestModInstallActions:
    def test_check_updates_marks_mod_update_pending(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        db: Session,
    ):
        mod = Mod(
            server_id=test_server.id,
            workshop_id="12345",
            name="Needs Update",
            load_order=0,
            install_status="installed",
            install_action=None,
        )
        db.add(mod)
        db.commit()

        with patch("routers.mods.get_plugin") as mock_get_plugin:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            plugin.check_for_mod_updates.return_value = [
                {
                    "workshop_id": "12345",
                    "name": "Needs Update",
                    "action": "update",
                    "reason": "newer_version_available",
                    "remote_updated": "2026-06-01T10:00:00+00:00",
                }
            ]
            response = client.post(
                f"/api/mods/{test_server.id}/check-updates",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        data = response.json()
        updated = next(item for item in data if item["workshop_id"] == "12345")
        assert updated["install_status"] == "pending"
        assert updated["install_action"] == "update"

    def test_manual_update_requires_available_update(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        db: Session,
    ):
        mod = Mod(
            server_id=test_server.id,
            workshop_id="12345",
            name="Current Mod",
            load_order=0,
            install_status="installed",
        )
        db.add(mod)
        db.commit()
        db.refresh(mod)

        with patch("routers.mods.get_plugin") as mock_get_plugin:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            plugin.check_for_mod_updates.return_value = []
            response = client.post(
                f"/api/mods/{test_server.id}/{mod.id}/install?action=update",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 400
        assert "Kein Mod-Update" in response.json()["detail"]

    def test_manual_update_queues_existing_pending_update(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        db: Session,
    ):
        mod = Mod(
            server_id=test_server.id,
            workshop_id="12345",
            name="Pending Update",
            load_order=0,
            install_status="pending",
            install_action="update",
        )
        db.add(mod)
        db.commit()
        db.refresh(mod)

        with patch("routers.mods.get_plugin") as mock_get_plugin, \
             patch("routers.mods.install_mod_bg") as mock_bg:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            plugin.check_for_mod_updates.return_value = []
            response = client.post(
                f"/api/mods/{test_server.id}/{mod.id}/install?action=update",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert response.json()["install_action"] == "update"
        mock_bg.assert_called_once()

    def test_reinstall_queues_without_available_update(
        self,
        client: TestClient,
        owner_cookies: dict,
        csrf_token: str,
        test_server: Server,
        db: Session,
    ):
        mod = Mod(
            server_id=test_server.id,
            workshop_id="12345",
            name="Installed Mod",
            load_order=0,
            install_status="installed",
        )
        db.add(mod)
        db.commit()
        db.refresh(mod)

        with patch("routers.mods.get_plugin") as mock_get_plugin, \
             patch("routers.mods.install_mod_bg") as mock_bg:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            response = client.post(
                f"/api/mods/{test_server.id}/{mod.id}/install?action=reinstall",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 200
        assert response.json()["install_status"] == "pending"
        assert response.json()["install_action"] == "reinstall"
        mock_bg.assert_called_once()


class TestUnsubscribeMod:
    def test_owner_can_unsubscribe(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str, db: Session):
        mod = Mod(server_id=test_server.id, workshop_id="12345", name="Test Mod", load_order=0)
        db.add(mod)
        db.commit()
        db.refresh(mod)

        response = client.delete(
            f"/api/mods/{test_server.id}/{mod.id}",
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200

    def test_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, db: Session):
        mod = Mod(server_id=test_server.id, workshop_id="12345", name="Test Mod", load_order=0)
        db.add(mod)
        db.commit()

        response = client.delete(
            f"/api/mods/{test_server.id}/{mod.id}",
            cookies=owner_cookies,
        )
        assert response.status_code == 403

    def test_cleanup_failure_keeps_mod_managed(
        self, client: TestClient, owner_cookies: dict, csrf_token: str, test_server: Server, db: Session
    ):
        mod = Mod(server_id=test_server.id, workshop_id="12345", load_order=0)
        db.add(mod)
        db.commit()
        mod_id = mod.id

        with patch("routers.mods.get_plugin") as mock_get_plugin:
            plugin = mock_get_plugin.return_value
            plugin.supports_mods = True
            plugin.cleanup_mod.side_effect = RuntimeError("synthetic node failure")
            response = client.delete(
                f"/api/mods/{test_server.id}/{mod_id}",
                cookies=owner_cookies,
                headers={"X-CSRF-Token": csrf_token},
            )

        assert response.status_code == 502
        assert response.json()["detail"] == "Mod-Dateien konnten nicht entfernt werden"
        db.expire_all()
        assert db.query(Mod).filter(Mod.id == mod_id).first() is not None


class TestReorderMods:
    def test_owner_can_reorder(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server, csrf_token: str, db: Session):
        mod1 = Mod(server_id=test_server.id, workshop_id="1", name="Mod 1", load_order=0)
        mod2 = Mod(server_id=test_server.id, workshop_id="2", name="Mod 2", load_order=1)
        db.add_all([mod1, mod2])
        db.commit()

        response = client.post(
            f"/api/mods/{test_server.id}/reorder",
            json=[mod2.id, mod1.id],
            cookies=owner_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_without_csrf_fails(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.post(
            f"/api/mods/{test_server.id}/reorder",
            json=[],
            cookies=owner_cookies,
        )
        assert response.status_code == 403
