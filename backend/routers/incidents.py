import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import get_current_user, require_server_permission
from models import AiGuardianNotice, AiRun, Incident, User
from services.change_timeline_service import log_change_event

router = APIRouter(prefix="/api/servers/{server_id}/incidents", tags=["incidents"])


def _ki_stand(db: Session, *, incident_ids: list[int], user: User) -> dict[int, dict]:
    """Was die KI je Vorfall veranlasst hat — in **einer** Abfrage.

    Der Plan sah dafuer eine eigene Route je Vorfall vor. Der Guardian-Reiter
    listet aber die ganze Historie: das waeren so viele Anfragen wie Zeilen, bei
    einem Server mit langer Vorgeschichte also dutzende. Die Auskunft haengt an
    genau der Liste, die hier ohnehin gebaut wird, und `server.view` gilt fuer
    beides gleich — deshalb steht sie hier und nicht daneben.

    `mine` entscheidet ueber den Verweis in den Chat. Es gibt eine Unterhaltung
    je Benutzer; der Lauf eines anderen Freigebers ist fuer den Betrachter nicht
    zu oeffnen, und ein Link, der ins Leere fuehrt, waere schlechter als keiner.
    Der **Name** des Freigebers steht bewusst nicht drin: wer Autonomie erteilt
    hat, ist keine Auskunft, die `server.view` einschliesst.
    """
    if not incident_ids:
        return {}
    zeilen = (
        db.query(AiGuardianNotice, AiRun)
        .outerjoin(AiRun, AiRun.id == AiGuardianNotice.run_id)
        .filter(AiGuardianNotice.incident_id.in_(incident_ids))
        .order_by(AiGuardianNotice.created_at.asc())
        .all()
    )
    stand: dict[int, dict] = {}
    for notiz, lauf in zeilen:
        vorher = stand.get(notiz.incident_id)
        # Mehrere Freigeber sind moeglich. Ein Heilungslauf ist die staerkere
        # Aussage als eine blosse Erwaehnung im Chat und gewinnt deshalb, egal
        # welche Zeile aelter ist.
        if vorher is not None and vorher["mode"] == "healing" and notiz.mode != "healing":
            continue
        stand[notiz.incident_id] = {
            "mode": notiz.mode,
            "run_status": lauf.status if lauf is not None else None,
            "mine": notiz.user_id == user.id,
            "at": notiz.created_at,
        }
    return stand


@router.get("")
def list_incidents(
    server_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_server_permission(user, server_id, db, "server.view")
    incidents = (
        db.query(Incident)
        .filter(Incident.server_id == server_id)
        .order_by(Incident.created_at.desc())
        .all()
    )
    ki = _ki_stand(db, incident_ids=[inc.id for inc in incidents], user=user)
    res = []
    for inc in incidents:
        attempts_list = []
        if inc.attempts:
            try:
                attempts_list = json.loads(inc.attempts)
            except Exception:
                attempts_list = []
        res.append({
            "id": inc.id,
            "title": inc.title,
            "description": inc.description,
            "type": inc.type,
            "status": inc.status,
            "fingerprint": inc.fingerprint,
            "created_at": inc.created_at,
            "resolved_at": inc.resolved_at,
            "attempts": attempts_list,
            "ai": ki.get(inc.id),
        })
    return res


@router.post("/{inc_id}/resolve")
def resolve_incident(
    server_id: int,
    inc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_server_permission(user, server_id, db, "server.start")
    incident = (
        db.query(Incident)
        .filter(Incident.id == inc_id, Incident.server_id == server_id)
        .first()
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident nicht gefunden")

    import uuid
    from models import Server
    from services.guardian_state_service import prepare_quarantine_clear

    previous_status = incident.status
    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)

    server = db.query(Server).filter(Server.id == server_id).first()
    if server:
        if server.guardian_observed_state == "quarantined" or previous_status == "quarantined":
            prepare_quarantine_clear(server, operation_id=str(uuid.uuid4()))
            # Let the next Guardian sync update the observed state
            # instead of forcing it here to avoid Panel/Agent desync.
        server.guardian_sync_error_statistics = None

    log_change_event(
        db,
        server_id,
        "recovery",
        f"Incident '{incident.title}' manuell als gelöst markiert.",
        commit=False,
    )

    db.commit()
    return {"ok": True}
