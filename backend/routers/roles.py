from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import User
from schemas import RoleCreate, RoleResponse, RoleUpdate
from services import role_service
from services.permission_catalog import SYSTEM_ROLE_ADMIN
from services.permission_service import has_global_permission

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _ensure_no_escalation(
    db: Session, actor: User, requested_keys: list[str] | None
) -> None:
    """Verhindert Privilege-Escalation via Rollen-Mutation.

    Ein Non-Owner darf nur Permission-Keys vergeben, die er selbst bereits
    besitzt. Sonst koennte z. B. ein User mit nur `roles.manage` seiner
    eigenen Rolle `users.manage` oder `servers.delete` hinzufuegen.
    """
    if requested_keys is None or actor.is_owner:
        return
    missing = sorted(
        {k for k in requested_keys if not has_global_permission(db, actor, k)}
    )
    if missing:
        raise HTTPException(
            status_code=403,
            detail=(
                "Du kannst nur Permissions vergeben, die du selbst besitzt. "
                f"Fehlend: {missing}"
            ),
        )


def _to_response(db: Session, role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        permissions=role_service.role_permission_keys(db, role.id),
        created_at=role.created_at,
    )


@router.get("", response_model=list[RoleResponse])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[RoleResponse]:
    """Jeder eingeloggte User darf die Rollen-Liste lesen (nur Namen/Beschreibung).

    Volle Permission-Listen sind ohnehin nicht geheim — sie kommen auch via
    `/api/permissions/catalog`. Aenderungen sind weiterhin per `roles.manage`
    geschuetzt.
    """
    return [_to_response(db, r) for r in role_service.list_roles(db)]


@router.get("/{role_id}", response_model=RoleResponse)
def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> RoleResponse:
    role = role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    return _to_response(db, role)


@router.post("", response_model=RoleResponse, status_code=201)
def create_role(
    req: RoleCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("roles.manage")),
    __: None = Depends(verify_csrf),
) -> RoleResponse:
    if role_service.get_role_by_name(db, req.name):
        raise HTTPException(status_code=400, detail="Name bereits vergeben")
    _ensure_no_escalation(db, actor, req.permissions)
    try:
        role = role_service.create_role(db, req.name, req.description, req.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(db, role)


@router.patch("/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    req: RoleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("roles.manage")),
    __: None = Depends(verify_csrf),
) -> RoleResponse:
    role = role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    if req.name is not None and req.name != role.name:
        existing = role_service.get_role_by_name(db, req.name)
        if existing and existing.id != role.id:
            raise HTTPException(status_code=400, detail="Name bereits vergeben")
    _ensure_no_escalation(db, actor, req.permissions)
    try:
        role = role_service.update_role(db, role, req.name, req.description, req.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_response(db, role)


@router.delete("/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_global("roles.manage")),
    __: None = Depends(verify_csrf),
) -> None:
    role = role_service.get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rolle nicht gefunden")
    try:
        role_service.delete_role(db, role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
