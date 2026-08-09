from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from database import get_db
from models import (
    AuditLog,
    BackupCode,
    JwtBlacklist,
    RefreshToken,
    Server,
    ServerPermission,
    User,
)
from schemas.user import AdminUserCreate, UserResponse, UserUpdate
from schemas.role import (
    AssignRoleRequest,
    AssignRolesRequest,
    ServerPermissionsRequest,
    ServerPermissionsResponse,
)
from dependencies import require_global, verify_csrf
from services import AuthService, EmailService
from services.email_verification_service import EmailVerificationService
from services.permission_service import (
    has_global_permission,
    has_server_permission,
    list_user_server_permission_keys,
    set_user_server_permissions,
)
from services.permission_catalog import SYSTEM_ROLE_ADMIN, SYSTEM_ROLE_USER
from services.role_service import (
    effective_user_role_permission_keys,
    get_role,
    get_role_by_name,
    role_permission_keys,
    set_user_roles,
)
from services import audit_service, postgres_service
from services.postgres_service import PostgresServiceError

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _ensure_no_global_escalation(
    db: Session, actor: User, required_keys: list[str]
) -> None:
    """Non-Owner darf nur Aktionen ausloesen, die Permissions verlangen,
    die er selbst global besitzt — sonst Eskalation."""
    if actor.is_owner:
        return
    missing = sorted(
        {k for k in required_keys if not has_global_permission(db, actor, k)}
    )
    if missing:
        raise HTTPException(
            status_code=403,
            detail=(
                "Du kannst nur Permissions vergeben/zuweisen, die du selbst "
                f"besitzt. Fehlend: {missing}"
            ),
        )


def _ensure_no_server_escalation(
    db: Session,
    actor: User,
    server_id: int,
    required_keys: list[str],
) -> None:
    """Non-Owner darf einem Sub-User auf einem Server nur die Server-Keys
    delegieren, die er auf diesem Server selbst hat (per Rolle pauschal oder
    via eigene Per-Server-Delegation)."""
    if actor.is_owner:
        return
    missing = sorted(
        {k for k in required_keys if not has_server_permission(db, actor, server_id, k)}
    )
    if missing:
        raise HTTPException(
            status_code=403,
            detail=(
                "Du kannst auf diesem Server nur Permissions delegieren, die "
                f"du selbst besitzt. Fehlend: {missing}"
            ),
        )


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.read")),
) -> list[User]:
    return db.query(User).all()


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.read")),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    req: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    # Owner-Account ist hart geschuetzt — niemand ausser dem Owner selbst
    # darf is_active, email oder 2FA des Owners aendern.
    if user.is_owner and not actor.is_owner:
        raise HTTPException(status_code=403, detail="Owner-Account kann nur vom Owner geaendert werden")
    # Selbst der Owner darf den Owner-Account nicht deaktivieren — sonst
    # waere das Panel nach dem Logout dauerhaft ausgesperrt (kein
    # Super-Admin-Recovery vorhanden).
    if user.is_owner and req.is_active is False:
        raise HTTPException(status_code=400, detail="Owner-Account darf nicht deaktiviert werden")
    if req.email is not None:
        user.email = req.email
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.two_factor_enabled is not None:
        user.two_factor_enabled = req.two_factor_enabled
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        if not actor.is_owner:
            raise HTTPException(status_code=403, detail="Nur ein Owner kann einen anderen Owner-Account löschen")
        owner_count = db.query(User).filter(User.is_owner == True, User.is_active == True).count()
        if owner_count <= 1:
            raise HTTPException(status_code=403, detail="Owner kann nicht gelöscht werden")

    # Selbst-Loeschen verhindern — Account wuerde sofort wegbrechen, Session-
    # Cookies wuerden ins Leere zeigen und der User koennte nicht mehr
    # eingreifen, falls die Aktion versehentlich passiert.
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen")

    # Eskalations-Schutz: Wer einen User loescht, dessen Rolle Keys haelt, die
    # man selbst nicht hat, koennte indirekt Berechtigungen verschieben
    # (z.B. ein Non-Owner-Admin loescht einen Admin). Nur Subset zulassen.
    _ensure_no_global_escalation(
        db,
        actor,
        effective_user_role_permission_keys(db, user),
    )

    # FK-Cleanup: AuditLogs entkoppeln, Sessions & BackupCodes loeschen.
    db.query(AuditLog).filter(AuditLog.user_id == user_id).update(
        {AuditLog.user_id: None}, synchronize_session=False
    )
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id).delete(synchronize_session=False)
    db.query(JwtBlacklist).filter(JwtBlacklist.user_id == user_id).delete(synchronize_session=False)
    db.query(BackupCode).filter(BackupCode.user_id == user_id).delete(synchronize_session=False)
    db.flush()

    db.delete(user)
    db.commit()
    return {"message": "User gelöscht"}


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user_admin(
    req: AdminUserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.manage")),
    _: None = Depends(verify_csrf),
) -> User:
    # Autorisierung VOR jeder DB-Aenderung: ein Non-Owner darf keinen
    # Owner-Account anlegen. Frueher hat create_user() den User bereits
    # committet, bevor die 403 geprueft wurde — das hinterliess phantom
    # Accounts mit dem gewuenschten Username.
    if req.is_owner and not actor.is_owner:
        raise HTTPException(status_code=403, detail="Nur Owner kann is_owner setzen")
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")
    user = AuthService.create_user(db, req.username, req.email, req.password)
    if req.is_owner:
        user.is_owner = True
    else:
        # Sicherer Default: System-Rolle `user` (entspricht der Lifespan-Migration
        # fuer bestehende Accounts; verhindert Accounts mit role_id=NULL).
        default_role = get_role_by_name(db, SYSTEM_ROLE_USER)
        if default_role is not None:
            user.role_id = default_role.id
    if req.auto_verify:
        user.email_verified = True
    else:
        if EmailService.is_configured():
            code = EmailVerificationService.create_verification(db, req.email, "setup")
            await EmailService.send_verification_code_email(req.email, req.username, code)
    db.commit()
    db.refresh(user)
    if not user.is_owner and user.role_id is not None:
        set_user_roles(db, user, [user.role_id])
    return user


# ── Rollen-Zuweisung ──────────────────────────────────────────────────


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def assign_role(
    user_id: int,
    req: AssignRoleRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    """Kompatibilitätsroute: ersetzt die Rollenmengen durch höchstens eine Rolle."""
    role_ids = [] if req.role_id is None else [req.role_id]
    return _assign_roles(user_id, role_ids, db, actor)


@router.put("/users/{user_id}/roles", response_model=UserResponse)
def assign_roles(
    user_id: int,
    req: AssignRolesRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    """Ersetzt die globalen Rollen eines Benutzers durch eine validierte Menge."""
    return _assign_roles(user_id, req.role_ids, db, actor)


def _assign_roles(
    user_id: int,
    requested_role_ids: list[int],
    db: Session,
    actor: User,
) -> User:
    """Prüft Eskalationsgrenzen und speichert eine Multi-Role-Zuweisung."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        raise HTTPException(status_code=400, detail="Owner-Account hat keine zuweisbare Rolle")
    # Self-Lockout-Schutz: ein User darf seine eigene Rolle nicht aendern.
    # Ohne diesen Guard koennte ein Admin sich versehentlich oder durch
    # Drittparteien (CSRF, kompromittierte Session) zum User downgraden und
    # sich damit selbst aussperren. Rollenwechsel passiert immer durch einen
    # anderen Account mit `users.permissions.manage`.
    if user.id == actor.id:
        raise HTTPException(
            status_code=400,
            detail="Du kannst deine eigene Rolle nicht ändern",
        )
    # Auch das Entfernen der aktuellen Rolle ist eine Eskalations-Aktion: ein
    # Non-Owner darf einem User keine Rolle wegnehmen, deren Keys er selbst
    # nicht besitzt (sonst koennte er einen Admin-Account "entwaffnen").
    current_keys = effective_user_role_permission_keys(db, user)
    _ensure_no_global_escalation(db, actor, current_keys)

    desired_role_ids = sorted(set(requested_role_ids))
    desired_keys: set[str] = set()
    for role_id in desired_role_ids:
        role = get_role(db, role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
        # Zuweisung der `admin`-System-Rolle ist nur dem Owner erlaubt
        # (verhindert Privilege-Escalation ueber `users.permissions.manage`).
        if role.is_system and role.name == SYSTEM_ROLE_ADMIN and not actor.is_owner:
            raise HTTPException(
                status_code=403,
                detail="Nur Owner kann die admin-Rolle zuweisen",
            )
        # Generalisiertes Eskalationsverbot: Actor muss alle Keys der
        # Ziel-Rolle selbst global besitzen — sonst koennte er sich (oder
        # andere) ueber eine Custom-Rolle hochziehen.
        desired_keys.update(role_permission_keys(db, role.id))
    _ensure_no_global_escalation(db, actor, sorted(desired_keys))

    try:
        set_user_roles(db, user, desired_role_ids, commit=False)
        audit_service.record_privileged_action(
            db,
            user_id=actor.id,
            action="user.roles.updated",
            target_type="user",
            target_id=user.id,
            details={"role_ids": desired_role_ids},
        )
        db.commit()
        db.refresh(user)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Rollenzuweisung konnte wegen einer gleichzeitigen Änderung nicht gespeichert werden",
        ) from exc
    return user


# ── Server-Permissions (Per-User-per-Server-Delegation) ───────────────


@router.get(
    "/users/{user_id}/server-permissions/{server_id}",
    response_model=ServerPermissionsResponse,
)
def get_server_permissions(
    user_id: int,
    server_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.permissions.manage")),
) -> ServerPermissionsResponse:
    if not db.query(User.id).filter(User.id == user_id).first():
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if not db.query(Server.id).filter(Server.id == server_id).first():
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    keys = list_user_server_permission_keys(db, user_id, server_id)
    return ServerPermissionsResponse(server_id=server_id, permissions=sorted(keys))


@router.put(
    "/users/{user_id}/server-permissions/{server_id}",
    response_model=ServerPermissionsResponse,
)
async def set_server_permissions(
    user_id: int,
    server_id: int,
    req: ServerPermissionsRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    _: None = Depends(verify_csrf),
) -> ServerPermissionsResponse:
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if target_user.is_owner:
        raise HTTPException(status_code=400, detail="Owner braucht keine Permissions")
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    # Non-Owner darf auf einem Server nur Keys delegieren, die er selbst auf
    # diesem Server besitzt — sonst kann ein User mit nur
    # `users.permissions.manage` (ohne eigene Server-Rechte) beliebige
    # Server-Aktionen an andere weiterreichen.
    _ensure_no_server_escalation(db, actor, server_id, req.permissions)
    # De-Eskalations-Schutz: Keys, die durch das Set entfernt werden,
    # zaehlen ebenfalls als Mutation. Sonst koennte ein User ohne eigene
    # Server-Rechte einem anderen User per leerem Set die Rechte entziehen.
    existing_keys = list_user_server_permission_keys(db, user_id, server_id)
    removed = [k for k in existing_keys if k not in set(req.permissions)]
    if removed:
        _ensure_no_server_escalation(db, actor, server_id, removed)

    had_any = bool(existing_keys)

    keys = set_user_server_permissions(
        db, user_id, server_id, req.permissions, granted_by=actor.id
    )

    if not had_any and keys and EmailService.is_configured() and target_user.email_notifications:
        await EmailService.send_user_added_to_server_notification(
            target_user.email, target_user.username, server.name, actor.username
        )

    return ServerPermissionsResponse(server_id=server_id, permissions=keys)


@router.delete(
    "/users/{user_id}/server-permissions/{server_id}",
    status_code=204,
)
def revoke_server_permissions(
    user_id: int,
    server_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> None:
    # Spiegelt set_server_permissions(permissions=[]): Actor muss die
    # bestehenden Keys auf diesem Server selbst besitzen, sonst kann er
    # nicht entwaffnen.
    existing_keys = list_user_server_permission_keys(db, user_id, server_id)
    if existing_keys:
        _ensure_no_server_escalation(db, actor, server_id, list(existing_keys))
    db.query(ServerPermission).filter(
        ServerPermission.user_id == user_id,
        ServerPermission.server_id == server_id,
    ).delete()
    db.commit()


@router.get("/users/{user_id}/server-permissions", response_model=list[ServerPermissionsResponse])
def list_server_permissions_for_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("users.permissions.manage")),
) -> list[ServerPermissionsResponse]:
    rows = (
        db.query(ServerPermission.server_id, ServerPermission.permission_key)
        .filter(ServerPermission.user_id == user_id)
        .all()
    )
    grouped: dict[int, list[str]] = {}
    for server_id, key in rows:
        grouped.setdefault(server_id, []).append(key)
    return [
        ServerPermissionsResponse(server_id=sid, permissions=sorted(keys))
        for sid, keys in sorted(grouped.items())
    ]


# ── SaaS-Betrieb: Audit-Liste + Managed-Postgres-Admin-Rotation ─────────────


class AuditLogOut(BaseModel):
    """Oeffentliche Audit-Darstellung ohne Secrets."""

    id: int
    user_id: int | None
    action: str
    target_type: str | None
    target_id: str | None
    origin: str
    correlation_id: str | None
    details: str | None
    created_at: datetime | None

    class Config:
        from_attributes = True


class ManagedPostgresAdminRotateOut(BaseModel):
    ok: bool
    admin_user: str
    nodes_updated: list[int]
    nodes_skipped: list[int]


@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_admin_audit_logs(
    db: Session = Depends(get_db),
    _: User = Depends(require_global("system.audit.read")),
    limit: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, max_length=64),
    target_type: str | None = Query(None, max_length=64),
    target_id: str | None = Query(None, max_length=64),
) -> list[AuditLog]:
    """Listet privilegierte Operator-Aktionen. Unberechtigt: 403 (kein leeres OK)."""
    try:
        return audit_service.list_audit_logs(
            db,
            limit=limit,
            action=action,
            target_type=target_type,
            target_id=target_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/managed-postgres/rotate-admin",
    response_model=ManagedPostgresAdminRotateOut,
)
def rotate_managed_postgres_admin(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("system.secrets.rotate")),
    _: None = Depends(verify_csrf),
) -> dict:
    """Rotiert das Cluster-Admin-Passwort (msm_admin) auf allen Nodes + Panel-Secret.

    Antwort enthaelt niemals das neue Passwort. Fehler: 400/503 mit Klartext.
    """
    try:
        result = postgres_service.rotate_cluster_admin_password(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PostgresServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Admin-Passwort-Rotation unerwartet fehlgeschlagen.",
        ) from None

    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="postgres.admin.rotate",
        target_type="managed_postgres",
        target_id=None,
        details={
            "nodes_updated": result.get("nodes_updated") or [],
            "nodes_skipped": result.get("nodes_skipped") or [],
            "admin_user": result.get("admin_user"),
        },
        commit=True,
    )
    return result
