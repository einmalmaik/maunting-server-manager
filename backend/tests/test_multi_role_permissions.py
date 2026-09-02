"""Regressionstests für additive Multi-Role-Autorisierung.

Die Tests prüfen die zentrale Invariante: effektive Rechte sind die
Vereinigungsmenge aller zugewiesenen Rollen, während unbekannte oder nicht
delegierbare Rollen niemals partiell gespeichert werden.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AuditLog, Role, RolePermission, User, UserRole
from services.permission_service import has_global_permission
from services.role_service import (
    delete_role,
    effective_user_role_ids,
    set_user_roles,
)


def _csrf(cookies: dict) -> dict[str, str]:
    """Erzeugt den Double-Submit-Header aus dem synthetischen Test-Cookie."""
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _create_role(db: Session, name: str, permission: str) -> Role:
    """Legt eine kleine Testrolle mit genau einer bekannten Permission an."""
    role = Role(name=name, description=None, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    db.add(RolePermission(role_id=role.id, permission_key=permission))
    db.commit()
    return role


def test_effective_permissions_are_union_of_all_roles(
    db: Session,
    regular_user: User,
) -> None:
    """Zwei Rollen ergänzen sich, ohne dass eine die andere überschreibt."""
    reader = _create_role(db, "multi-reader", "users.read")
    observer = _create_role(db, "multi-observer", "system.view")

    assigned = set_user_roles(db, regular_user, [observer.id, reader.id])

    assert assigned == sorted([reader.id, observer.id])
    assert effective_user_role_ids(db, regular_user) == assigned
    assert has_global_permission(db, regular_user, "users.read") is True
    assert has_global_permission(db, regular_user, "system.view") is True
    assert has_global_permission(db, regular_user, "servers.delete") is False


def test_duplicate_role_ids_are_normalized(
    db: Session,
    regular_user: User,
) -> None:
    """Wiederholte IDs erzeugen keine doppelten DB-Zuordnungen."""
    role = _create_role(db, "multi-deduplicated", "users.read")

    set_user_roles(db, regular_user, [role.id, role.id, role.id])

    assignments = db.query(UserRole).filter(UserRole.user_id == regular_user.id).all()
    assert [assignment.role_id for assignment in assignments] == [role.id]


def test_unknown_role_keeps_existing_assignment_unchanged(
    db: Session,
    regular_user: User,
) -> None:
    """Eine ungültige ID scheitert vor jeder Mutation und damit atomar."""
    role = _create_role(db, "multi-stable", "users.read")
    set_user_roles(db, regular_user, [role.id])

    with pytest.raises(ValueError, match="Unbekannte Rollen-IDs"):
        set_user_roles(db, regular_user, [role.id, 999_999])

    assert effective_user_role_ids(db, regular_user) == [role.id]


def test_legacy_role_id_remains_authorized_without_assignment(
    db: Session,
    regular_user: User,
) -> None:
    """Rolling Updates verlieren vor dem Backfill keine bestehenden Rechte."""
    role = _create_role(db, "multi-legacy", "users.read")
    regular_user.role_id = role.id
    db.commit()

    assert db.query(UserRole).filter(UserRole.user_id == regular_user.id).count() == 0
    assert has_global_permission(db, regular_user, "users.read") is True


def test_assigned_role_cannot_be_deleted(
    db: Session,
    regular_user: User,
) -> None:
    """Eine ausschließlich neue Multi-Role-Zuweisung blockiert Rollenlöschung."""
    role = _create_role(db, "multi-in-use", "users.read")
    set_user_roles(db, regular_user, [role.id])
    # Beweist, dass nicht nur der Legacy-FK die Löschung blockiert.
    regular_user.role_id = None
    db.commit()

    with pytest.raises(ValueError, match="noch Usern zugewiesen"):
        delete_role(db, role)


def test_owner_can_assign_multiple_roles_via_api(
    client: TestClient,
    db: Session,
    regular_user: User,
    owner_cookies: dict,
) -> None:
    """Der neue API-Vertrag liefert Primärrolle und vollständige Rollenmenge."""
    reader = _create_role(db, "multi-api-reader", "users.read")
    observer = _create_role(db, "multi-api-observer", "system.view")

    response = client.put(
        f"/api/admin/users/{regular_user.id}/roles",
        json={"role_ids": [observer.id, reader.id]},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 200
    assert response.json()["role_ids"] == sorted([reader.id, observer.id])
    assert response.json()["role_id"] == min(reader.id, observer.id)
    audit = db.query(AuditLog).filter(AuditLog.action == "user.roles.updated").one()
    assert audit.user_id is not None
    # `target_id` ist Text — siehe Migration 20260809_02.
    assert audit.target_id == str(regular_user.id)
    assert "role_ids" in (audit.details or "")


def test_permissions_me_lists_all_roles(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
) -> None:
    """Frontend erhält alle Rollen und die vereinigten Backend-Rechte."""
    reader = _create_role(db, "multi-me-reader", "users.read")
    observer = _create_role(db, "multi-me-observer", "system.view")
    set_user_roles(db, regular_user, [reader.id, observer.id])

    response = client.get("/api/permissions/me", cookies=user_cookies)

    assert response.status_code == 200
    body = response.json()
    assert body["role_ids"] == sorted([reader.id, observer.id])
    assert body["role_names"] == ["multi-me-observer", "multi-me-reader"]
    assert set(body["global_keys"]) >= {"users.read", "system.view"}


def test_role_assignment_rejects_more_than_32_entries(
    client: TestClient,
    regular_user: User,
    owner_cookies: dict,
) -> None:
    """Extreme Eingaben werden bereits am API-Rand begrenzt."""
    response = client.put(
        f"/api/admin/users/{regular_user.id}/roles",
        json={"role_ids": list(range(1, 34))},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 422


def test_role_assignment_rejects_non_positive_ids(
    client: TestClient,
    regular_user: User,
    owner_cookies: dict,
) -> None:
    """Null und negative IDs werden als Formatfehler abgewiesen."""
    response = client.put(
        f"/api/admin/users/{regular_user.id}/roles",
        json={"role_ids": [0, -1]},
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert response.status_code == 422
