"""Benutzereigene Zugangsdaten und ihre Bindung an einen Server.

Drei klar getrennte Zugriffsebenen:

- ``/api/credentials/me`` — jeder angemeldete Benutzer verwaltet seinen eigenen
  Tresor. Keine zusaetzliche Berechtigung noetig: es sind seine Daten, und ein
  Kunde muss ohne Operator-Hilfe sein Steam-Konto hinterlegen koennen.
- ``/api/servers/{id}/credentials`` — Lesen mit ``server.view``, Binden mit
  ``server.credentials.manage``. Gebunden werden darf nur ein Credential, das
  dem Handelnden selbst gehoert.
- ``/api/credentials/policy`` — der Betreiber entscheidet, ob ein Server ohne
  eigene Bindung den panelweiten Zugang mitbenutzen darf.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from database import get_db
from dependencies import (
    get_current_user,
    require_global,
    require_server_permission,
    verify_csrf,
)
from models import Server, User
from schemas.credential import (
    PanelFallbackSetting,
    ServerCredentialBindingWrite,
    ServerCredentialStatus,
    UserCredentialResponse,
    UserCredentialWrite,
)
from services import audit_service, credential_service
from services.credential_service import CredentialError
from services.dis_client import DisSidecarError


router = APIRouter(prefix="/api", tags=["credentials"])


def _response(row) -> UserCredentialResponse:
    return UserCredentialResponse(
        id=row.id,
        kind=row.kind,
        label=row.label,
        username=row.username,
        secret_hint=row.secret_hint,
        updated_at=row.updated_at,
    )


def _config_error(exc: CredentialError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _require_server(db: Session, server_id: int) -> Server:
    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


# ── Eigener Tresor ─────────────────────────────────────────────────────────


@router.get("/credentials/me", response_model=list[UserCredentialResponse])
def list_my_credentials(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[UserCredentialResponse]:
    return [_response(row) for row in credential_service.list_user_credentials(db, user.id)]


@router.put("/credentials/me", response_model=UserCredentialResponse)
def upsert_my_credential(
    payload: UserCredentialWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> UserCredentialResponse:
    """Legt ein eigenes Credential an oder rotiert sein Geheimnis."""
    try:
        credential = credential_service.upsert_user_credential(
            db,
            user_id=user.id,
            kind=payload.kind,
            label=payload.label,
            username=payload.username,
            secret=payload.secret.get_secret_value(),
        )
        audit_service.record_privileged_action(
            db,
            user_id=user.id,
            action="credential.saved",
            target_type="user_credential",
            target_id=credential.id,
            # Bewusst ohne Label-Freitext und ohne jeden Teil des Geheimnisses.
            details={"kind": credential.kind},
        )
        db.commit()
        db.refresh(credential)
    except CredentialError as exc:
        db.rollback()
        raise _config_error(exc) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="Verschluesselungsdienst nicht erreichbar"
        ) from exc
    return _response(credential)


@router.delete("/credentials/me/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_credential(
    credential_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> Response:
    credential_service.delete_user_credential(
        db, user_id=user.id, credential_id=credential_id
    )
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="credential.deleted",
        target_type="user_credential",
        target_id=credential_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Serverbindung ──────────────────────────────────────────────────────────


@router.get(
    "/servers/{server_id}/credentials", response_model=list[ServerCredentialStatus]
)
def read_server_credentials(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ServerCredentialStatus]:
    """Welche Zugangsdaten dieser Server braucht und woher sie aktuell kommen."""
    require_server_permission(user, server_id, db, "server.view")
    _require_server(db, server_id)
    required = set(credential_service.required_kinds_for_server(db, server_id))
    from models import CREDENTIAL_KINDS

    rows: list[ServerCredentialStatus] = []
    for kind in CREDENTIAL_KINDS:
        described = credential_service.describe_for_server(db, server_id, kind)
        rows.append(ServerCredentialStatus(required=kind in required, **described))
    return rows


@router.put("/servers/{server_id}/credentials", response_model=list[ServerCredentialStatus])
def bind_server_credential(
    server_id: int,
    payload: ServerCredentialBindingWrite,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> list[ServerCredentialStatus]:
    require_server_permission(user, server_id, db, "server.credentials.manage")
    _require_server(db, server_id)
    try:
        credential_service.set_binding(
            db,
            server_id=server_id,
            kind=payload.kind,
            credential_id=payload.credential_id,
            actor=user,
        )
        audit_service.record_privileged_action(
            db,
            user_id=user.id,
            action="server.credential.bound",
            target_type="server",
            target_id=server_id,
            details={"kind": payload.kind, "cleared": payload.credential_id is None},
        )
        db.commit()
    except CredentialError as exc:
        db.rollback()
        raise _config_error(exc) from exc
    return read_server_credentials(server_id, db=db, user=user)


# ── Betreiberpolicy ────────────────────────────────────────────────────────


@router.get("/credentials/policy", response_model=PanelFallbackSetting)
def read_policy(
    _: User = Depends(require_global("panel.settings.read")),
) -> PanelFallbackSetting:
    return PanelFallbackSetting(
        allow_panel_fallback=credential_service.panel_fallback_allowed()
    )


@router.put("/credentials/policy", response_model=PanelFallbackSetting)
def update_policy(
    payload: PanelFallbackSetting,
    db: Session = Depends(get_db),
    actor: User = Depends(require_global("panel.settings.write")),
    _: None = Depends(verify_csrf),
) -> PanelFallbackSetting:
    """Schaltet den zentralen Fallback fuer Server ohne eigene Bindung.

    Aus bedeutet: ein Server ohne zugewiesene Zugangsdaten laeuft nicht mit dem
    Zugang des Betreibers, sondern meldet einen verstaendlichen Fehler. Das ist
    die Einstellung, die ein Hoster braucht.
    """
    credential_service.set_panel_fallback_allowed(payload.allow_panel_fallback)
    audit_service.record_privileged_action(
        db,
        user_id=actor.id,
        action="credential.policy.updated",
        target_type="panel",
        details={"allow_panel_fallback": payload.allow_panel_fallback},
    )
    db.commit()
    return PanelFallbackSetting(allow_panel_fallback=payload.allow_panel_fallback)
