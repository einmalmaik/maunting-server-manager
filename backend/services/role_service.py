"""Rollen-CRUD + Built-in-Seed.

`admin` und `user` sind System-Rollen (is_system=True): nicht loeschbar,
nicht umbenennbar. Die `admin`-Rolle wird beim Startup auf alle Permission-
Keys gesynct (Self-Heal, wenn neue Keys im Katalog erscheinen).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import Role, RolePermission
from services.permission_catalog import (
    ALL_KEYS,
    SYSTEM_ROLE_ADMIN,
    SYSTEM_ROLE_NAMES,
    SYSTEM_ROLE_USER,
    admin_role_keys,
    is_known_key,
)


def get_role_by_name(db: Session, name: str) -> Role | None:
    return db.query(Role).filter(Role.name == name).first()


def get_role(db: Session, role_id: int) -> Role | None:
    return db.query(Role).filter(Role.id == role_id).first()


def list_roles(db: Session) -> list[Role]:
    return db.query(Role).order_by(Role.id.asc()).all()


def role_permission_keys(db: Session, role_id: int) -> list[str]:
    rows = (
        db.query(RolePermission.permission_key)
        .filter(RolePermission.role_id == role_id)
        .all()
    )
    return sorted({r[0] for r in rows})


def _replace_role_permissions(db: Session, role_id: int, keys: list[str]) -> list[str]:
    desired = {k for k in keys if is_known_key(k)}
    existing = db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
    existing_by_key = {p.permission_key: p for p in existing}
    for key, perm in existing_by_key.items():
        if key not in desired:
            db.delete(perm)
    for key in desired:
        if key not in existing_by_key:
            db.add(RolePermission(role_id=role_id, permission_key=key))
    db.commit()
    return sorted(desired)


def create_role(
    db: Session, name: str, description: str | None, keys: list[str]
) -> Role:
    if name in SYSTEM_ROLE_NAMES:
        raise ValueError("Reservierter Rollenname")
    role = Role(name=name, description=description, is_system=False)
    db.add(role)
    db.commit()
    db.refresh(role)
    _replace_role_permissions(db, role.id, keys)
    db.refresh(role)
    return role


def update_role(
    db: Session,
    role: Role,
    name: str | None,
    description: str | None,
    keys: list[str] | None,
) -> Role:
    """is_system-Rollen: Name und is_system unveraenderlich. Permissions der
    admin-Rolle sind ebenfalls fest (alle Keys, Self-Heal). Permissions der
    user-Rolle koennen vom Owner editiert werden, falls gewuenscht.
    """
    if name is not None and not role.is_system:
        if name in SYSTEM_ROLE_NAMES:
            raise ValueError("Reservierter Rollenname")
        role.name = name
    if description is not None:
        role.description = description
    db.commit()

    if keys is not None and not (role.is_system and role.name == SYSTEM_ROLE_ADMIN):
        _replace_role_permissions(db, role.id, keys)
    db.refresh(role)
    return role


def delete_role(db: Session, role: Role) -> None:
    if role.is_system:
        raise ValueError("System-Rolle kann nicht geloescht werden")
    from models import User

    in_use = db.query(User.id).filter(User.role_id == role.id).first()
    if in_use is not None:
        raise ValueError("Rolle ist noch Usern zugewiesen")
    db.delete(role)
    db.commit()


def ensure_system_roles(db: Session) -> tuple[Role, Role]:
    """Idempotent: legt `admin` und `user` an (falls nicht vorhanden) und
    synct die `admin`-Permissions auf den aktuellen Katalog.

    Wird beim Lifespan-Startup aufgerufen.
    """
    admin = get_role_by_name(db, SYSTEM_ROLE_ADMIN)
    if admin is None:
        admin = Role(
            name=SYSTEM_ROLE_ADMIN,
            description="Vollzugriff auf alle Funktionen",
            is_system=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

    user = get_role_by_name(db, SYSTEM_ROLE_USER)
    if user is None:
        user = Role(
            name=SYSTEM_ROLE_USER,
            description="Standard-Rolle ohne globale Rechte; Server-Zugriffe via Delegation",
            is_system=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # admin-Rolle auf den aktuellen Katalog syncen
    _sync_role_permissions(db, admin.id, admin_role_keys())
    return admin, user


def _sync_role_permissions(db: Session, role_id: int, target_keys: frozenset[str]) -> None:
    existing = db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
    existing_keys = {p.permission_key for p in existing}
    to_add = target_keys - existing_keys
    to_remove = existing_keys - target_keys
    for perm in existing:
        if perm.permission_key in to_remove:
            db.delete(perm)
    for key in to_add:
        db.add(RolePermission(role_id=role_id, permission_key=key))
    if to_add or to_remove:
        db.commit()


__all__ = [
    "create_role",
    "delete_role",
    "ensure_system_roles",
    "get_role",
    "get_role_by_name",
    "list_roles",
    "role_permission_keys",
    "update_role",
]
