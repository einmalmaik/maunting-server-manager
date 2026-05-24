"""Zentrale Permission-Pruefung.

Reihenfolge:
1. Owner-Bypass (is_owner=True) -> alles erlaubt. Bootstrap-Safe.
2. Globale Rolle hat den Key (gilt auch fuer server-scoped Keys = pauschal alle Server).
3. Per-Server-Delegation (nur fuer server-scoped Keys, wenn server_id gegeben).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import RolePermission, Server, ServerPermission, User


def has_global_permission(db: Session, user: User, key: str) -> bool:
    if user.is_owner:
        return True
    if user.role_id is None:
        return False
    exists = (
        db.query(RolePermission.id)
        .filter(RolePermission.role_id == user.role_id, RolePermission.permission_key == key)
        .first()
    )
    return exists is not None


def has_server_permission(db: Session, user: User, server_id: int, key: str) -> bool:
    if user.is_owner:
        return True
    # Pauschale Rolle (z.B. admin oder Custom-Rolle mit server.* Keys)
    if user.role_id is not None:
        role_grant = (
            db.query(RolePermission.id)
            .filter(RolePermission.role_id == user.role_id, RolePermission.permission_key == key)
            .first()
        )
        if role_grant is not None:
            return True
    # Per-Server-Delegation
    delegated = (
        db.query(ServerPermission.id)
        .filter(
            ServerPermission.user_id == user.id,
            ServerPermission.server_id == server_id,
            ServerPermission.permission_key == key,
        )
        .first()
    )
    return delegated is not None


def list_visible_server_ids(db: Session, user: User) -> list[int] | None:
    """Server-IDs, die der User sehen darf. None = alle (Owner/pauschale Rolle)."""
    if user.is_owner:
        return None
    if user.role_id is not None:
        pauschal = (
            db.query(RolePermission.id)
            .filter(
                RolePermission.role_id == user.role_id,
                RolePermission.permission_key == "server.view",
            )
            .first()
        )
        if pauschal is not None:
            return None
    rows = (
        db.query(ServerPermission.server_id)
        .filter(ServerPermission.user_id == user.id)
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def list_visible_servers(db: Session, user: User) -> list[Server]:
    ids = list_visible_server_ids(db, user)
    if ids is None:
        return db.query(Server).all()
    if not ids:
        return []
    return db.query(Server).filter(Server.id.in_(ids)).all()


def list_user_server_permission_keys(
    db: Session, user_id: int, server_id: int
) -> list[str]:
    rows = (
        db.query(ServerPermission.permission_key)
        .filter(ServerPermission.user_id == user_id, ServerPermission.server_id == server_id)
        .all()
    )
    return [r[0] for r in rows]


def set_user_server_permissions(
    db: Session,
    user_id: int,
    server_id: int,
    keys: list[str],
    granted_by: int | None,
) -> list[str]:
    """Idempotent: ueberschreibt alle Server-Permissions des Users fuer diesen Server.

    Unbekannte Keys werden ignoriert (Whitelist via catalog).
    """
    from services.permission_catalog import SERVER_KEYS

    desired = {k for k in keys if k in SERVER_KEYS}

    existing = (
        db.query(ServerPermission)
        .filter(ServerPermission.user_id == user_id, ServerPermission.server_id == server_id)
        .all()
    )
    existing_by_key = {p.permission_key: p for p in existing}

    # Entfernen, was nicht mehr gewollt ist
    for key, perm in existing_by_key.items():
        if key not in desired:
            db.delete(perm)

    # Hinzufuegen, was neu ist
    for key in desired:
        if key not in existing_by_key:
            db.add(
                ServerPermission(
                    user_id=user_id,
                    server_id=server_id,
                    permission_key=key,
                    granted_by=granted_by,
                )
            )

    db.commit()
    return sorted(desired)


def list_user_effective_global_keys(db: Session, user: User) -> list[str]:
    """Globale Keys, die der User via Rolle hat (ohne Owner-Bypass auflisten)."""
    if user.role_id is None:
        return []
    rows = (
        db.query(RolePermission.permission_key)
        .filter(RolePermission.role_id == user.role_id)
        .all()
    )
    return sorted({r[0] for r in rows})
