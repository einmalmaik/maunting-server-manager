from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user
from models import ServerPermission, User
from schemas import (
    MePermissionsResponse,
    PermissionCatalogResponse,
    PermissionDefResponse,
)
from services.permission_catalog import GLOBAL_PERMISSIONS, SERVER_PERMISSIONS
from services.permission_service import list_user_effective_global_keys

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


@router.get("/catalog", response_model=PermissionCatalogResponse)
def get_catalog(_: User = Depends(get_current_user)) -> PermissionCatalogResponse:
    return PermissionCatalogResponse(
        global_permissions=[
            PermissionDefResponse(key=p.key, group=p.group, label=p.label)
            for p in GLOBAL_PERMISSIONS
        ],
        server_permissions=[
            PermissionDefResponse(key=p.key, group=p.group, label=p.label)
            for p in SERVER_PERMISSIONS
        ],
    )


@router.get("/me", response_model=MePermissionsResponse)
def get_my_permissions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MePermissionsResponse:
    """Effektive Permissions des aktuell eingeloggten Users.

    Frontend nutzt das, um Buttons/Routen UX-seitig zu verstecken. Backend
    prueft jeden Call zusaetzlich nochmal selbst.
    """
    global_keys = list_user_effective_global_keys(db, user)
    server_perms: dict[int, list[str]] = {}
    rows = (
        db.query(ServerPermission.server_id, ServerPermission.permission_key)
        .filter(ServerPermission.user_id == user.id)
        .all()
    )
    for server_id, key in rows:
        server_perms.setdefault(server_id, []).append(key)
    for sid in server_perms:
        server_perms[sid].sort()

    return MePermissionsResponse(
        is_owner=user.is_owner,
        role_id=user.role_id,
        role_name=user.role.name if user.role else None,
        global_keys=global_keys,
        server_keys=server_perms,
    )
