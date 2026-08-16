"""Administrator-controlled Guardian operations."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission, verify_csrf
from models import ChangeEvent, Server, User
from services import audit_service
from services.guardian_state_service import prepare_quarantine_clear
from services.server_lifecycle_service import sync_desired_state_to_agent


router = APIRouter(prefix="/api/servers/{server_id}/guardian", tags=["guardian"])


@router.post("/quarantine/clear", status_code=202)
def clear_quarantine(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    require_server_permission(user, server_id, db, "server.restart")
    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")

    import uuid
    operation_id = str(uuid.uuid4())
    prepare_quarantine_clear(server, operation_id=operation_id)
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="guardian.quarantine.clear",
        target_type="server",
        target_id=server.id,
        details={"operation_id": operation_id},
        correlation_id=operation_id,
    )
    db.add(
        ChangeEvent(
            server_id=server.id,
            event_type="guardian_quarantine_clear",
            description="Guardian-Quarantäne wurde zur Freigabe angefordert.",
            details=json.dumps(
                {"operation_id": operation_id},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(server)
    synchronized = sync_desired_state_to_agent(db, server)
    return {
        "ok": True,
        "operation_id": operation_id,
        "generation": server.desired_state_generation,
        "synchronized": synchronized,
    }


def _server_oder_404(db: Session, server_id: int) -> Server:
    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        raise HTTPException(status_code=404, detail="Server nicht gefunden")
    return server


def _herkunft(db: Session, server_id: int) -> dict | None:
    """Wer die geltende Uebersteuerung gesetzt hat, und wann.

    Die Quelle ist der `ChangeEvent`, den jedes Setzen und jedes Zuruecksetzen
    schreibt — es gibt also keine zweite Fassung derselben Tatsache, die
    auseinanderlaufen koennte. Deshalb hat die Spalte auch keine Begleitfelder
    fuer Datum und Urheber: sie traegt, was gilt; die Chronik traegt, wie es
    dazu kam.

    Unlesbare Details gelten als "keine Herkunft" statt als Fehler. Ein
    Reiter, der sich wegen einer kaputten Chronikzeile gar nicht mehr oeffnet,
    waere schlechter als einer, der die Herkunft weglaesst.
    """
    zeile = (
        db.query(ChangeEvent)
        .filter(
            ChangeEvent.server_id == server_id,
            ChangeEvent.event_type == "guardian_overrides",
        )
        .order_by(ChangeEvent.timestamp.desc(), ChangeEvent.id.desc())
        .first()
    )
    if zeile is None:
        return None
    try:
        details = json.loads(zeile.details or "{}")
    except (TypeError, json.JSONDecodeError):
        details = {}
    if not isinstance(details, dict):
        details = {}
    return {
        "source": str(details.get("source") or "human"),
        "incident_id": details.get("incident_id"),
        "changed_at": zeile.timestamp.isoformat() if zeile.timestamp else None,
    }


@router.get("/overrides")
def read_overrides(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Was fuer diesen Server abweichend von der Blueprint gilt.

    Sichtbarkeit ist hier kein Beiwerk. Die KI darf diese Zahlen im
    Reparaturlauf ohne Klick aendern; eine Verhaltensaenderung, die nirgends
    steht, waere schlimmer als der Vorfall, den sie beheben sollte.

    Gelesen wird durch `gelesene_uebersteuerung` — also durch dieselbe
    Saeuberung, die auch der Compiler anwendet. Der Reiter zeigt damit, was
    **wirkt**, nicht, was in der Spalte steht; bei einer von Hand verbogenen
    Zeile sind das zwei verschiedene Dinge.
    """
    require_server_permission(user, server_id, db, "server.view")
    server = _server_oder_404(db, server_id)

    from services.guardian_runtime_compiler import (
        GUARDIAN_STELLSCHRAUBEN,
        gelesene_uebersteuerung,
    )

    werte = gelesene_uebersteuerung(server)
    return {
        "overrides": werte,
        "bounds": {
            name: {"min": unten, "max": oben}
            for name, (unten, oben) in GUARDIAN_STELLSCHRAUBEN.items()
        },
        "origin": _herkunft(db, server_id) if werte else None,
    }


@router.delete("/overrides", status_code=202)
def reset_overrides(
    server_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(verify_csrf),
) -> dict:
    """Zurueck auf die Blueprint — der Knopf neben der Anzeige.

    Dasselbe Recht wie am Knopf "Server-Einstellungen aendern"
    (`server.config.write`), und derselbe Weg wie beim Setzen:
    `mark_guardian_configuration_changed` (setzt zusaetzlich
    `guardian_config_hash` auf NULL, ohne das haelt der Compiler die
    Konfiguration fuer unveraendert) und danach die Synchronisation zum
    Agenten.

    Anders als beim Setzen wird hier **nicht** zurueckgerollt, wenn die
    Synchronisation scheitert. Der Rueckweg zur Blueprint ist der Zustand, in
    dem jeder Server ohne Uebersteuerung ohnehin laeuft; ihn wegen einer
    unerreichbaren Node wieder zu verwerfen hiesse, den Betreiber in einer
    Einstellung festzuhalten, die er gerade loswerden wollte. Der naechste
    Reconcile-Takt traegt sie hinaus.
    """
    require_server_permission(user, server_id, db, "server.config.write")
    server = _server_oder_404(db, server_id)
    if not server.guardian_overrides_json:
        return {"ok": True, "overrides": {}, "generation": server.desired_state_generation}

    from services import guardian_state_service

    server.guardian_overrides_json = None
    guardian_state_service.mark_guardian_configuration_changed(server)
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="guardian.overrides.reset",
        target_type="server",
        target_id=server.id,
        details={"generation": server.desired_state_generation},
    )
    db.add(
        ChangeEvent(
            server_id=server.id,
            event_type="guardian_overrides",
            description="Guardian-Einstellungen dieses Servers auf die Blueprint zurückgesetzt.",
            details=json.dumps(
                {"overrides": {}, "source": "human"},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    db.commit()
    db.refresh(server)
    synchronized = sync_desired_state_to_agent(db, server) if server.node_id else False
    return {
        "ok": True,
        "overrides": {},
        "generation": server.desired_state_generation,
        "synchronized": synchronized,
    }
