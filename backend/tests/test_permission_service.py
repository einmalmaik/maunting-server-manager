"""Tests fuer services.permission_service.

Prueft die zentrale Permission-Logik:
- Owner-Bypass (is_owner=True)
- Globale Permissions via Rolle
- Server-Permissions via Rolle (pauschal) oder Delegation
- list_visible_servers / set_user_server_permissions
"""
from sqlalchemy.orm import Session

from models import Role, RolePermission, Server, ServerPermission, User
from services import permission_service
from services.role_service import ensure_system_roles, get_role_by_name


def _make_server(db: Session, name: str = "S") -> Server:
    server = Server(name=name, game_type="dayz", install_dir=f"/tmp/{name}", status="stopped")
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


class TestHasGlobalPermission:
    def test_owner_bypass(self, db: Session, owner_user: User):
        assert permission_service.has_global_permission(db, owner_user, "servers.delete") is True

    def test_user_without_role_denied(self, db: Session, regular_user: User):
        regular_user.role_id = None
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is False

    def test_admin_role_grants_all_global(self, db: Session, regular_user: User):
        admin = get_role_by_name(db, "admin")
        assert admin is not None
        regular_user.role_id = admin.id
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is True
        assert permission_service.has_global_permission(db, regular_user, "servers.delete") is True

    def test_user_role_has_no_global(self, db: Session, regular_user: User):
        user_role = get_role_by_name(db, "user")
        assert user_role is not None
        regular_user.role_id = user_role.id
        db.commit()
        assert permission_service.has_global_permission(db, regular_user, "servers.create") is False


class TestHasServerPermission:
    def test_owner_bypass(self, db: Session, owner_user: User, test_server: Server):
        assert permission_service.has_server_permission(db, owner_user, test_server.id, "server.start") is True

    def test_user_with_delegation(self, db: Session, regular_user: User, test_server: Server):
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.start"))
        db.commit()
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.start") is True
        # Andere Permission: nein
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.stop") is False

    def test_role_grants_blanket(self, db: Session, regular_user: User, test_server: Server):
        admin = get_role_by_name(db, "admin")
        regular_user.role_id = admin.id
        db.commit()
        # Admin hat alle Server-Keys pauschal
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.start") is True
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.files.delete") is True

    def test_no_role_no_delegation_denied(self, db: Session, regular_user: User, test_server: Server):
        regular_user.role_id = None
        db.commit()
        assert permission_service.has_server_permission(db, regular_user, test_server.id, "server.view") is False


class TestListVisibleServers:
    def test_owner_sees_all(self, db: Session, owner_user: User, test_server: Server):
        another = _make_server(db, "another")
        result = permission_service.list_visible_servers(db, owner_user)
        ids = {s.id for s in result}
        assert test_server.id in ids and another.id in ids

    def test_role_with_server_view_sees_all(self, db: Session, regular_user: User, test_server: Server):
        # Gib der user-Rolle pauschal `server.view`
        user_role = get_role_by_name(db, "user")
        db.add(RolePermission(role_id=user_role.id, permission_key="server.view"))
        db.commit()
        regular_user.role_id = user_role.id
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        assert any(s.id == test_server.id for s in result)

    def test_only_servers_with_delegation_visible(self, db: Session, regular_user: User, test_server: Server):
        other = _make_server(db, "other")
        db.add(ServerPermission(user_id=regular_user.id, server_id=test_server.id, permission_key="server.view"))
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        ids = {s.id for s in result}
        assert test_server.id in ids
        assert other.id not in ids

    def test_no_permissions_no_servers(self, db: Session, regular_user: User, test_server: Server):
        regular_user.role_id = None
        db.commit()
        result = permission_service.list_visible_servers(db, regular_user)
        assert result == []


class TestSetUserServerPermissions:
    def test_creates_delegations(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id,
            ["server.view", "server.start", "server.stop"],
            granted_by=owner_user.id,
        )
        assert set(keys) == {"server.view", "server.start", "server.stop"}
        rows = db.query(ServerPermission).filter(
            ServerPermission.user_id == regular_user.id,
            ServerPermission.server_id == test_server.id,
        ).all()
        assert len(rows) == 3

    def test_overwrites_existing(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id, ["server.view", "server.start"], granted_by=owner_user.id
        )
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id, ["server.stop"], granted_by=owner_user.id
        )
        assert keys == ["server.stop"]
        rows = db.query(ServerPermission).filter(
            ServerPermission.user_id == regular_user.id,
            ServerPermission.server_id == test_server.id,
        ).all()
        assert len(rows) == 1
        assert rows[0].permission_key == "server.stop"

    def test_unknown_keys_ignored(self, db: Session, regular_user: User, test_server: Server, owner_user: User):
        keys = permission_service.set_user_server_permissions(
            db, regular_user.id, test_server.id,
            ["server.view", "bogus.key", "servers.create"],  # servers.create ist global, kein server-key
            granted_by=owner_user.id,
        )
        assert keys == ["server.view"]
