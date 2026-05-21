"""Tests for mods router: CRUD, permissions, CSRF."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, Server, Mod, Permission


class TestListMods:
    def test_owner_can_list(self, client: TestClient, owner_user: User, owner_cookies: dict, test_server: Server):
        response = client.get(f"/api/mods/{test_server.id}", cookies=owner_cookies)
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_user_with_permission_can_list(self, client: TestClient, regular_user: User, user_cookies: dict, test_server: Server, user_permission: Permission):
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
