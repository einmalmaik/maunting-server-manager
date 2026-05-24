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
    list_user_server_permission_keys,
    set_user_server_permissions,
)
from services.role_service import get_role

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    _: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
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
    _: User = Depends(require_global("users.manage")),
    __: None = Depends(verify_csrf),
) -> dict:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        raise HTTPException(status_code=403, detail="Owner kann nicht gelöscht werden")
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
    _: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User nicht gefunden")
    if user.is_owner:
        raise HTTPException(status_code=400, detail="Owner-Account hat keine zuweisbare Rolle")
    if req.role_id is not None:
        role = get_role(db, req.role_id)
        if not role:
            raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
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

    had_any = (
        db.query(ServerPermission.id)
        .filter(ServerPermission.user_id == user_id, ServerPermission.server_id == server_id)
        .first()
        is not None
    )

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
    _: User = Depends(require_global("users.permissions.manage")),
    __: None = Depends(verify_csrf),
) -> None:
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
