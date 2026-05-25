from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Server, ServerPermission, User
from schemas.user import AdminUserCreate, UserResponse, UserUpdate
from schemas.role import (
    AssignRoleRequest,
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
    get_role,
    get_role_by_name,
    role_permission_keys,
)

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
        raise HTTPException(status_code=403, detail="Owner kann nicht gelöscht werden")
    # Selbst-Loeschen verhindern — Account wuerde sofort wegbrechen, Session-
    # Cookies wuerden ins Leere zeigen und der User koennte nicht mehr
    # eingreifen, falls die Aktion versehentlich passiert.
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen")
    # Eskalations-Schutz: Wer einen User loescht, dessen Rolle Keys haelt, die
    # man selbst nicht hat, koennte indirekt Berechtigungen verschieben
    # (z.B. ein Non-Owner-Admin loescht einen Admin). Nur Subset zulassen.
    if user.role_id is not None:
        target_keys = role_permission_keys(db, user.role_id)
        _ensure_no_global_escalation(db, actor, target_keys)
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
    if AuthService.get_user_by_username(db, req.username):
        raise HTTPException(status_code=400, detail="Username bereits vergeben")
    if AuthService.get_user_by_email(db, req.email):
        raise HTTPException(status_code=400, detail="E-Mail bereits vergeben")
    user = AuthService.create_user(db, req.username, req.email, req.password)
    # is_owner setzen nur durch Owner selbst (Bootstrap-Override).
    if req.is_owner:
        if not actor.is_owner:
            raise HTTPException(status_code=403, detail="Nur Owner kann is_owner setzen")
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
            detail="Du kannst deine eigene Rolle nicht aendern",
        )
    # Auch das Entfernen der aktuellen Rolle ist eine Eskalations-Aktion: ein
    # Non-Owner darf einem User keine Rolle wegnehmen, deren Keys er selbst
    # nicht besitzt (sonst koennte er einen Admin-Account "entwaffnen").
    if user.role_id is not None:
        current_keys = role_permission_keys(db, user.role_id)
        _ensure_no_global_escalation(db, actor, current_keys)
    if req.role_id is not None:
        role = get_role(db, req.role_id)
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
        _ensure_no_global_escalation(
            db, actor, role_permission_keys(db, role.id)
        )
    user.role_id = req.role_id
    db.commit()
    db.refresh(user)
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
