"""Tests fuer /api/roles CRUD."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services.role_service import get_role_by_name


def _csrf(cookies: dict) -> dict:
    """Header-Set fuer state-changing Requests."""
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


class TestListRoles:
    def test_lists_system_roles(self, client: TestClient, owner_cookies: dict):
        r = client.get("/api/roles", cookies=owner_cookies)
        assert r.status_code == 200
        names = {role["name"] for role in r.json()}
        assert {"admin", "user"} <= names

    def test_requires_auth(self, client: TestClient):
        r = client.get("/api/roles")
        assert r.status_code == 401


class TestCreateRole:
    def test_owner_can_create(self, client: TestClient, owner_cookies: dict):
        r = client.post(
            "/api/roles",
            json={"name": "moderator", "description": "Mod", "permissions": ["users.read"]},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "moderator"
        assert body["permissions"] == ["users.read"]
        assert body["is_system"] is False

    def test_regular_user_blocked(self, client: TestClient, user_cookies: dict):
        r = client.post(
            "/api/roles",
            json={"name": "mod2", "description": None, "permissions": []},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403

    def test_duplicate_name_blocked(self, client: TestClient, owner_cookies: dict):
        client.post(
            "/api/roles",
            json={"name": "dup", "description": None, "permissions": []},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        r = client.post(
            "/api/roles",
            json={"name": "dup", "description": None, "permissions": []},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 400

    def test_reserved_name_blocked(self, client: TestClient, owner_cookies: dict):
        r = client.post(
            "/api/roles",
            json={"name": "admin", "description": None, "permissions": []},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 400


class TestDeleteRole:
    def test_owner_can_delete_custom(self, client: TestClient, owner_cookies: dict, db: Session):
        r = client.post(
            "/api/roles",
            json={"name": "delete-me", "description": None, "permissions": []},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        role_id = r.json()["id"]
        r2 = client.delete(f"/api/roles/{role_id}", cookies=owner_cookies, headers=_csrf(owner_cookies))
        assert r2.status_code == 204

    def test_system_role_delete_blocked(self, client: TestClient, owner_cookies: dict, db: Session):
        admin = get_role_by_name(db, "admin")
        r = client.delete(f"/api/roles/{admin.id}", cookies=owner_cookies, headers=_csrf(owner_cookies))
        assert r.status_code == 400
