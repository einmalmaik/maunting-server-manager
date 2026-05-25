"""Tests fuer /api/roles CRUD."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services.role_service import get_role_by_name


def _csrf(cookies: dict) -> dict:
    """Header-Set fuer state-changing Requests."""
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _assign_role_with_keys(
    db: Session, user: User, role_name: str, keys: list[str]
) -> Role:
    """Erstellt eine Custom-Rolle mit `keys` und weist sie `user` zu."""
    role = Role(name=role_name, description=None, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    for k in keys:
        db.add(RolePermission(role_id=role.id, permission_key=k))
    user.role_id = role.id
    db.commit()
    db.refresh(user)
    return role


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


class TestRolePermissionEscalation:
    """Non-Owner mit `roles.manage` darf keine Permissions vergeben, die er
    selbst nicht hat — sonst koennte er sich via Eigen-Rollen-PATCH zum
    Faktischen Admin machen."""

    def test_cannot_grant_unowned_permission_on_update(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
    ):
        # regular_user bekommt eine Custom-Rolle mit NUR `roles.manage`.
        custom = _assign_role_with_keys(
            db, regular_user, "rolemgr", ["roles.manage"]
        )
        # Versuch, der eigenen Rolle `users.manage` und `servers.delete`
        # hinzuzufuegen, muss 403 ergeben — der Actor besitzt diese Keys nicht.
        r = client.patch(
            f"/api/roles/{custom.id}",
            json={
                "name": None,
                "description": None,
                "permissions": ["roles.manage", "users.manage", "servers.delete"],
            },
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403

        # Sanity: die Rolle hat sich nicht geaendert.
        keys_after = {
            rp.permission_key
            for rp in db.query(RolePermission)
            .filter(RolePermission.role_id == custom.id)
            .all()
        }
        assert keys_after == {"roles.manage"}

    def test_can_grant_owned_permission_on_update(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
    ):
        # Actor hat `roles.manage` + `users.read` — darf eine Custom-Rolle
        # erstellen, die `users.read` enthaelt.
        _assign_role_with_keys(
            db, regular_user, "rolemgr-with-read", ["roles.manage", "users.read"]
        )
        r = client.post(
            "/api/roles",
            json={
                "name": "support",
                "description": "Read-only Support",
                "permissions": ["users.read"],
            },
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 201
        assert r.json()["permissions"] == ["users.read"]

    def test_cannot_grant_unowned_permission_on_create(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
    ):
        _assign_role_with_keys(
            db, regular_user, "rolemgr-only", ["roles.manage"]
        )
        r = client.post(
            "/api/roles",
            json={
                "name": "shadow-admin",
                "description": None,
                "permissions": ["users.manage", "servers.delete"],
            },
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403

    def test_owner_can_grant_any_permission(
        self,
        client: TestClient,
        owner_cookies: dict,
    ):
        # Owner-Bypass: jede Permission ist erlaubt, auch wenn die Permission
        # nicht ueber die Rolle laeuft.
        r = client.post(
            "/api/roles",
            json={
                "name": "full-power",
                "description": None,
                "permissions": ["users.manage", "servers.delete", "panel.settings.write"],
            },
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 201

    def test_cannot_strip_powerful_permissions_via_update(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
        owner_cookies: dict,
    ):
        """De-Eskalations-Schutz: Non-Owner mit `roles.manage` darf eine
        Custom-Rolle mit Keys, die er selbst nicht hat, nicht stripppen."""
        _assign_role_with_keys(
            db, regular_user, "rolemgr-strip", ["roles.manage"]
        )
        # Owner legt maechtige Custom-Rolle an
        r = client.post(
            "/api/roles",
            json={
                "name": "power-admin",
                "description": None,
                "permissions": ["roles.manage", "servers.delete", "panel.settings.write"],
            },
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 201
        target_role_id = r.json()["id"]
        # Non-Owner versucht, alle Keys ausser `roles.manage` zu strippen
        r2 = client.patch(
            f"/api/roles/{target_role_id}",
            json={
                "name": None,
                "description": None,
                "permissions": ["roles.manage"],
            },
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r2.status_code == 403
        # Sanity: Rolle unveraendert
        keys_after = {
            rp.permission_key
            for rp in db.query(RolePermission)
            .filter(RolePermission.role_id == target_role_id)
            .all()
        }
        assert keys_after == {"roles.manage", "servers.delete", "panel.settings.write"}

    def test_can_rename_role_without_owning_all_keys(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
        owner_cookies: dict,
    ):
        """Name/Description-only-Update (permissions=None) erfordert KEINEN
        Subset-Check auf bestehende Keys (sonst waere `roles.manage` faktisch
        unbrauchbar)."""
        _assign_role_with_keys(
            db, regular_user, "rolemgr-rename", ["roles.manage"]
        )
        r = client.post(
            "/api/roles",
            json={
                "name": "to-rename",
                "description": None,
                "permissions": ["servers.delete"],
            },
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 201
        rid = r.json()["id"]
        r2 = client.patch(
            f"/api/roles/{rid}",
            json={"name": "renamed", "description": "neue Desc", "permissions": None},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r2.status_code == 200
        assert r2.json()["name"] == "renamed"
