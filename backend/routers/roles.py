from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_global, verify_csrf
from models import User
from schemas import RoleCreate, RoleResponse, RoleUpdate
from services import rechtevergabe_service, role_service

router = APIRouter(prefix="/api/roles", tags=["roles"])


def _http(fehler: rechtevergabe_service.RechteFehler) -> HTTPException:
    """Die Grenzen stehen in `rechtevergabe_service` — dieselben ruft die KI."""
    return HTTPException(status_code=fehler.status_code, detail=fehler.detail)


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
    """Jeder eingeloggte User darf die Rollen-Liste lesen.

    **Was hier wirklich herausgeht:** `_to_response` haengt an jede Rolle ihre
    tatsaechlich zugewiesenen Permission-Keys. Der Docstring behauptete bis
    09/2026 "nur Namen/Beschreibung" und stimmte damit nicht mit dem Code
    ueberein — nachgemessen antwortet die Route einem Konto ohne jedes
    globale Recht mit 200 und der vollstaendigen Rechtezuordnung aller Rollen.

    Das ist mehr als `/api/permissions/catalog`: der Katalog nennt die
    *moeglichen* Keys, diese Route die *dieser Instanz tatsaechlich
    vergebenen*. Wer sie enger ziehen will, braucht ein `require_global` —
    das ist eine bewusste Betreiberentscheidung, keine stille Aenderung.
    Welches Konto welche Rolle traegt, verraet sie nicht, und Aenderungen
    bleiben per `roles.manage` geschuetzt.
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
    try:
        role = rechtevergabe_service.create_role(
            db, actor, req.name, req.description, req.permissions
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None
    return _to_response(db, role)


@router.patch("/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: int,
    req: RoleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("roles.manage")),
    __: None = Depends(verify_csrf),
) -> RoleResponse:
    try:
        role = rechtevergabe_service.update_role(
            db, actor, role_id, req.name, req.description, req.permissions
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None
    return _to_response(db, role)


@router.delete("/{role_id}", status_code=204)
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("roles.manage")),
    __: None = Depends(verify_csrf),
) -> None:
    try:
        rechtevergabe_service.delete_role(db, actor, role_id)
    except rechtevergabe_service.RechteFehler as fehler:
        raise _http(fehler) from None
