"""Tests fuer /api/admin/users — speziell Owner-Schutz."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import RolePermission, User
from services.permission_catalog import SYSTEM_ROLE_ADMIN
from services.role_service import get_role_by_name


def _csrf(cookies: dict) -> dict:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _promote_to_admin_role(db: Session, user: User) -> None:
    """Verleiht dem User die `admin`-System-Rolle (alle Global-Keys, inkl. users.manage)."""
    admin_role = get_role_by_name(db, SYSTEM_ROLE_ADMIN)
    assert admin_role is not None
    # ensure_system_roles seedet die admin-Permissions; falls Test-Schema sie leer haette,
    # waere die Berechtigung trotzdem ueber das ADMIN-Flag wirksam. Hier reicht role_id.
    user.role_id = admin_role.id
    db.commit()
    db.refresh(user)


class TestUpdateUserOwnerProtection:
    def test_non_owner_admin_cannot_modify_owner(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """Ein Admin (Non-Owner) darf is_active des Owners NICHT setzen."""
        _promote_to_admin_role(db, regular_user)

        r = client.patch(
            f"/api/admin/users/{owner_user.id}",
            json={"is_active": False},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403
        # Owner muss aktiv bleiben
        db.refresh(owner_user)
        assert owner_user.is_active is True

    def test_owner_can_modify_owner(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        db: Session,
    ):
        """Owner darf den eigenen Account weiterhin updaten (z. B. 2FA aktivieren)."""
        r = client.patch(
            f"/api/admin/users/{owner_user.id}",
            json={"two_factor_enabled": True},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 200
        db.refresh(owner_user)
        assert owner_user.two_factor_enabled is True

    def test_admin_can_modify_regular_user(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        owner_cookies: dict,
    ):
        """Owner-Admin darf normale User weiterhin updaten (Regression)."""
        r = client.patch(
            f"/api/admin/users/{regular_user.id}",
            json={"is_active": False},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 200
        db.refresh(regular_user)
        assert regular_user.is_active is False

    def test_owner_cannot_deactivate_self(
        self,
        client: TestClient,
        owner_user: User,
        owner_cookies: dict,
        db: Session,
    ):
        """Owner darf den eigenen Account NICHT deaktivieren — sperrt sich sonst aus."""
        r = client.patch(
            f"/api/admin/users/{owner_user.id}",
            json={"is_active": False},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 400
        db.refresh(owner_user)
        assert owner_user.is_active is True
