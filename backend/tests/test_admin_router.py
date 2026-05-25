"""Tests fuer /api/admin/users — speziell Owner-Schutz."""
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Role, RolePermission, Server, ServerPermission, User
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


def _attach_role_with_keys(
    db: Session, user: User, role_name: str, keys: list[str]
) -> Role:
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


class TestAssignRoleEscalation:
    """assign_role darf keine Eskalation ueber Custom-Rollen-Zuweisung ermoeglichen."""

    def test_non_owner_cannot_assign_role_with_keys_they_lack(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """User mit nur `users.permissions.manage` kann einem ANDEREN User
        keine maechtige Rolle zuweisen (Eskalations-Schutz)."""
        _attach_role_with_keys(
            db, regular_user, "permmgr", ["users.permissions.manage"]
        )
        # Owner erstellt maechtige Custom-Rolle
        powerful = Role(name="powerful", description=None, is_system=False)
        db.add(powerful)
        db.commit()
        db.refresh(powerful)
        db.add(RolePermission(role_id=powerful.id, permission_key="servers.delete"))
        db.add(RolePermission(role_id=powerful.id, permission_key="panel.settings.write"))
        db.commit()
        # Drittes User-Ziel, weil Self-Modify durch separaten Guard blockiert ist.
        from services import AuthService
        target = AuthService.create_user(db, "rt-target", "rt@test.de", "TargetP123!")
        target.email_verified = True
        db.commit()
        db.refresh(target)
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role_id": powerful.id},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403
        db.refresh(target)
        assert target.role_id != powerful.id

    def test_non_owner_cannot_remove_powerful_role(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """Non-Owner darf einem User keine Rolle wegnehmen, deren Keys er
        selbst nicht hat — sonst koennte er einen Admin "entwaffnen"."""
        _attach_role_with_keys(
            db, regular_user, "permmgr3", ["users.permissions.manage"]
        )
        # Erstelle Admin-User mit echter admin-Rolle (alle Keys)
        from services import AuthService
        target = AuthService.create_user(db, "victim", "victim@test.de", "VictimP123!")
        target.email_verified = True
        _promote_to_admin_role(db, target)
        db.commit()
        db.refresh(target)
        prev_role_id = target.role_id
        # Attacker versucht, die Admin-Rolle per role_id=null wegzunehmen
        r = client.patch(
            f"/api/admin/users/{target.id}/role",
            json={"role_id": None},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403
        # Rolle unveraendert
        db.refresh(target)
        assert target.role_id == prev_role_id

    def test_cannot_change_own_role(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
    ):
        """Self-Lockout-Schutz: ein User kann seine eigene Rolle nicht aendern
        (sonst koennte ein Admin sich selbst zum User downgraden und sich
        damit aussperren)."""
        # Promote zu admin, damit er `users.permissions.manage` global hat.
        _promote_to_admin_role(db, regular_user)
        prev_role_id = regular_user.role_id
        # Versuche, eigene Rolle auf None zu setzen
        r1 = client.patch(
            f"/api/admin/users/{regular_user.id}/role",
            json={"role_id": None},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r1.status_code == 400
        # Versuche, eigene Rolle auf user-Rolle zu setzen (Self-Downgrade)
        user_role = db.query(Role).filter(Role.name == "user").first()
        r2 = client.patch(
            f"/api/admin/users/{regular_user.id}/role",
            json={"role_id": user_role.id if user_role else 1},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r2.status_code == 400
        # Sanity: Rolle unveraendert
        db.refresh(regular_user)
        assert regular_user.role_id == prev_role_id

    def test_owner_can_assign_any_role(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        owner_cookies: dict,
    ):
        """Owner-Bypass: kann jede Rolle zuweisen."""
        powerful = Role(name="powerful2", description=None, is_system=False)
        db.add(powerful)
        db.commit()
        db.refresh(powerful)
        db.add(RolePermission(role_id=powerful.id, permission_key="servers.delete"))
        db.commit()
        r = client.patch(
            f"/api/admin/users/{regular_user.id}/role",
            json={"role_id": powerful.id},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 200
        db.refresh(regular_user)
        assert regular_user.role_id == powerful.id


class TestDeleteUserProtection:
    """delete_user: Selbstloeschung und Eskalation verhindern."""

    def test_cannot_delete_self(
        self,
        client: TestClient,
        db: Session,
        regular_user: User,
        user_cookies: dict,
    ):
        _promote_to_admin_role(db, regular_user)
        r = client.delete(
            f"/api/admin/users/{regular_user.id}",
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 400

    def test_non_admin_cannot_delete_admin_user(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """User mit nur `users.manage` darf keinen Admin loeschen."""
        _attach_role_with_keys(db, regular_user, "usrmgr", ["users.manage"])
        # Erstelle ein zweites Konto mit Admin-Rolle
        from services import AuthService
        target = AuthService.create_user(db, "admin2", "admin2@test.de", "AdminPass123!")
        target.email_verified = True
        _promote_to_admin_role(db, target)
        db.commit()
        r = client.delete(
            f"/api/admin/users/{target.id}",
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403


class TestSetServerPermissionsEscalation:
    """set_server_permissions: Actor muss server-scoped Keys selbst besitzen."""

    def test_non_owner_cannot_delegate_keys_they_lack(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """User mit `users.permissions.manage` (aber ohne Server-Perms) darf nicht delegieren."""
        _attach_role_with_keys(
            db, regular_user, "permmgr2", ["users.permissions.manage"]
        )
        # Erstelle Server
        srv = Server(
            name="test-srv", game_type="csgo", install_dir="/tmp/x",
            container_name="x", public_bind_ip="0.0.0.0",
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        # Erstelle Ziel-User
        from services import AuthService
        target = AuthService.create_user(db, "target", "target@test.de", "TargetP123!")
        target.email_verified = True
        db.commit()
        db.refresh(target)
        # Versuch, Keys zu delegieren, die man selbst nicht hat
        r = client.put(
            f"/api/admin/users/{target.id}/server-permissions/{srv.id}",
            json={"permissions": ["server.start", "server.stop", "server.files.delete"]},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403

    def test_non_owner_cannot_strip_server_keys_via_empty_set(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """De-Eskalation: leeres permissions-Set darf nicht ungeprueft
        bestehende Keys entziehen."""
        _attach_role_with_keys(
            db, regular_user, "permmgr-strip", ["users.permissions.manage"]
        )
        srv = Server(
            name="strip-srv", game_type="csgo", install_dir="/tmp/strip",
            container_name="strip", public_bind_ip="0.0.0.0",
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        from services import AuthService
        victim = AuthService.create_user(db, "victim-srv", "vs@test.de", "VictimP123!")
        victim.email_verified = True
        db.commit()
        db.refresh(victim)
        # Owner gibt Opfer per-Server-Rechte
        db.add(ServerPermission(user_id=victim.id, server_id=srv.id, permission_key="server.start"))
        db.add(ServerPermission(user_id=victim.id, server_id=srv.id, permission_key="server.stop"))
        db.commit()
        # Attacker versucht via leerem Set zu entziehen — Actor hat selbst keine Server-Keys
        r = client.put(
            f"/api/admin/users/{victim.id}/server-permissions/{srv.id}",
            json={"permissions": []},
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403
        # Sanity: Keys unveraendert
        remaining = {
            rp.permission_key
            for rp in db.query(ServerPermission)
            .filter(ServerPermission.user_id == victim.id, ServerPermission.server_id == srv.id)
            .all()
        }
        assert remaining == {"server.start", "server.stop"}

    def test_non_owner_cannot_revoke_server_keys_they_lack(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        user_cookies: dict,
    ):
        """De-Eskalation: DELETE-Revoke prueft ebenfalls die bestehenden Keys."""
        _attach_role_with_keys(
            db, regular_user, "permmgr-revoke", ["users.permissions.manage"]
        )
        srv = Server(
            name="rev-srv", game_type="csgo", install_dir="/tmp/rev",
            container_name="rev", public_bind_ip="0.0.0.0",
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        from services import AuthService
        victim = AuthService.create_user(db, "victim-rev", "vr@test.de", "VictimP123!")
        victim.email_verified = True
        db.commit()
        db.refresh(victim)
        db.add(ServerPermission(user_id=victim.id, server_id=srv.id, permission_key="server.start"))
        db.commit()
        r = client.delete(
            f"/api/admin/users/{victim.id}/server-permissions/{srv.id}",
            cookies=user_cookies,
            headers=_csrf(user_cookies),
        )
        assert r.status_code == 403
        remaining = (
            db.query(ServerPermission)
            .filter(ServerPermission.user_id == victim.id, ServerPermission.server_id == srv.id)
            .count()
        )
        assert remaining == 1

    def test_owner_can_delegate_any_server_key(
        self,
        client: TestClient,
        db: Session,
        owner_user: User,
        regular_user: User,
        owner_cookies: dict,
    ):
        """Owner kann beliebige Server-Permissions delegieren."""
        srv = Server(
            name="test-srv2", game_type="csgo", install_dir="/tmp/y",
            container_name="y", public_bind_ip="0.0.0.0",
        )
        db.add(srv)
        db.commit()
        db.refresh(srv)
        r = client.put(
            f"/api/admin/users/{regular_user.id}/server-permissions/{srv.id}",
            json={"permissions": ["server.start", "server.stop"]},
            cookies=owner_cookies,
            headers=_csrf(owner_cookies),
        )
        assert r.status_code == 200
        assert set(r.json()["permissions"]) == {"server.start", "server.stop"}
