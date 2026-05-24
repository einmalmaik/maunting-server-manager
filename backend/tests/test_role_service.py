"""Tests fuer services.role_service.

Prueft Rollen-CRUD und ensure_system_roles (Idempotenz + admin-Self-Heal).
"""
import pytest
from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import role_service
from services.permission_catalog import ALL_KEYS, SYSTEM_ROLE_ADMIN, SYSTEM_ROLE_USER


class TestEnsureSystemRoles:
    def test_creates_admin_and_user(self, db: Session):
        # Built-ins werden in conftest seed-mode angelegt; loeschen + neu rufen.
        db.query(Role).delete()
        db.commit()
        admin, user = role_service.ensure_system_roles(db)
        assert admin.name == SYSTEM_ROLE_ADMIN
        assert user.name == SYSTEM_ROLE_USER
        assert admin.is_system is True
        assert user.is_system is True

    def test_idempotent_double_call(self, db: Session):
        # Aus conftest schon vorhanden
        before = db.query(Role).count()
        role_service.ensure_system_roles(db)
        after = db.query(Role).count()
        assert before == after == 2

    def test_admin_has_all_keys_after_seed(self, db: Session):
        admin = role_service.get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        keys = role_service.role_permission_keys(db, admin.id)
        assert set(keys) == ALL_KEYS

    def test_admin_self_heals_after_extra_key_removed(self, db: Session):
        # Simuliere: ein admin-Key wurde manuell aus der DB entfernt.
        admin = role_service.get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        target = (
            db.query(RolePermission)
            .filter(RolePermission.role_id == admin.id, RolePermission.permission_key == "servers.delete")
            .first()
        )
        assert target is not None
        db.delete(target)
        db.commit()
        # Re-run sollte Key wieder anlegen.
        role_service.ensure_system_roles(db)
        keys = role_service.role_permission_keys(db, admin.id)
        assert "servers.delete" in keys

    def test_user_role_is_empty(self, db: Session):
        user_role = role_service.get_role_by_name(db, SYSTEM_ROLE_USER)
        assert role_service.role_permission_keys(db, user_role.id) == []


class TestCreateUpdateDeleteRole:
    def test_create_role(self, db: Session):
        role = role_service.create_role(db, "moderator", "Test", ["users.read", "system.view"])
        assert role.name == "moderator"
        keys = role_service.role_permission_keys(db, role.id)
        assert set(keys) == {"users.read", "system.view"}

    def test_create_role_filters_unknown_keys(self, db: Session):
        role = role_service.create_role(db, "filtered", None, ["users.read", "bogus.key"])
        keys = role_service.role_permission_keys(db, role.id)
        assert "users.read" in keys
        assert "bogus.key" not in keys

    def test_create_with_reserved_name_fails(self, db: Session):
        with pytest.raises(ValueError):
            role_service.create_role(db, SYSTEM_ROLE_ADMIN, None, [])
        with pytest.raises(ValueError):
            role_service.create_role(db, SYSTEM_ROLE_USER, None, [])

    def test_update_role(self, db: Session):
        role = role_service.create_role(db, "mod", "desc", ["users.read"])
        updated = role_service.update_role(db, role, "mod-renamed", "neu", ["system.view"])
        assert updated.name == "mod-renamed"
        assert updated.description == "neu"
        keys = role_service.role_permission_keys(db, role.id)
        assert keys == ["system.view"]

    def test_system_role_name_immutable(self, db: Session):
        admin = role_service.get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        role_service.update_role(db, admin, "supreme", None, None)
        admin_after = role_service.get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        assert admin_after is not None  # Name unveraendert

    def test_delete_custom_role(self, db: Session):
        role = role_service.create_role(db, "throwaway", None, [])
        role_service.delete_role(db, role)
        assert role_service.get_role_by_name(db, "throwaway") is None

    def test_delete_system_role_blocked(self, db: Session):
        admin = role_service.get_role_by_name(db, SYSTEM_ROLE_ADMIN)
        with pytest.raises(ValueError):
            role_service.delete_role(db, admin)

    def test_delete_role_in_use_blocked(self, db: Session, regular_user: User):
        role = role_service.create_role(db, "blocking", None, [])
        regular_user.role_id = role.id
        db.commit()
        with pytest.raises(ValueError):
            role_service.delete_role(db, role)
