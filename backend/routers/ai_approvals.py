"""Freigabe per E-Mail — der einzige Endpunkt ohne Anmeldung im KI-Bereich.

Er muss ohne Anmeldung auskommen: der Empfaenger sitzt am Telefon, im Urlaub,
und soll auf einen Link in einer Mail tippen. Das Token **ist** die
Berechtigung — kurzlebig, einmal verwendbar, an genau einen Vorschlag gebunden.

Drei Regeln, die diesen Router von jedem anderen unterscheiden:

* **Kein GET, das ausfuehrt.** ``GET`` liefert nur die Beschreibung des
  Vorgangs; entschieden wird per ``POST``. Mailscanner und Vorschaudienste
  klicken Links, und ein GET, das ausfuehrt, waere ein Servereingriff durch
  einen Virenscanner. Genau die Form des Passwort-Resets.
* **Eine einzige Fehlermeldung** fuer unbekannt, abgelaufen und verbraucht.
  Drei verschiedene Antworten sagten einem Fremden, welche Token es gibt.
* **Kein CSRF-Schutz, und trotzdem kein CSRF-Loch.** Der ``POST`` traegt keine
  Cookie-Authentifizierung; er wirkt ausschliesslich durch das Token im Pfad.
  Eine fremde Seite kann ihn nicht sinnvoll ausloesen, ohne das Token zu
  kennen — und wer es kennt, braucht keinen Umweg.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import AiActionProposal, Server
from services import ai_approval_service
from services.ai_action_errors import AiActionStateError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/approvals", tags=["ai-approvals"])

#: Dieselbe Antwort fuer unbekannt, abgelaufen und verbraucht.
_UNGUELTIG = "ai.errors.codes.AI_APPROVAL_INVALID"


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


def _kopfzeilen(response: Response) -> None:
    """Der Link steht in einer Mail — er darf nirgends haengenbleiben.

    ``no-store`` haelt die Seite aus Zwischenspeichern heraus (ein geteiltes
    Geraet, ein Proxy), ``no-referrer`` verhindert, dass das Token im
    ``Referer`` an jedes nachgeladene Bild geht.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.get("/{token}")
def read_approval(
    token: str,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """Was ansteht — ohne irgendetwas zu tun.

    Die Seite zeigt Werkzeug, Server und die Vorschau des Vorschlags. Die
    Vorschau ist dieselbe, die im Panel auf der Bestaetigungskarte stuende:
    `preview_json` traegt nur redigierte Angaben, die Nutzlast selbst ist
    verschluesselt und wird hier nicht angefasst.
    """
    _kopfzeilen(response)
    zeile = ai_approval_service.freigabe_lesen(db, token)
    if zeile is None:
        raise HTTPException(status_code=404, detail=_UNGUELTIG)

    proposal = (
        db.query(AiActionProposal)
        .filter(AiActionProposal.id == zeile.proposal_id)
        .first()
    )
    if proposal is None or proposal.status not in ("proposed", "confirmed"):
        # Der Vorschlag ist inzwischen ausgefuehrt, abgelaufen oder gescheitert.
        # Fuer den Empfaenger ist das dasselbe wie ein ungueltiges Token: es
        # gibt nichts mehr zu entscheiden.
        raise HTTPException(status_code=404, detail=_UNGUELTIG)

    server_name = None
    if proposal.server_id is not None:
        server = db.query(Server).filter(Server.id == proposal.server_id).first()
        server_name = getattr(server, "name", None)

    from services.ai_proposal_service import proposal_response

    karte = proposal_response(proposal)
    return {
        "tool_name": proposal.tool_name,
        "server_name": server_name,
        "reason": karte.reason,
        "expected_effect": karte.expected_effect,
        "preview": karte.preview,
        "expires_at": zeile.expires_at,
    }


@router.post("/{token}/decide")
def decide_approval(
    token: str,
    payload: ApprovalDecision,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Freigeben oder ablehnen.

    Die eigentliche Arbeit steht in `ai_approval_service.entscheiden`: der
    Einmalverbrauch als bedingtes UPDATE, danach `confirm_proposal` und
    `execute_proposal` ganz normal. Alle Schranken des Panelwegs gelten
    unveraendert — die Rechtepruefungen, die Backup-Schranke, der Server-Mutex.
    Eine Mail ersetzt den Klick, nicht die Pruefung.
    """
    _kopfzeilen(response)
    try:
        return ai_approval_service.entscheiden(
            db, token=token, entscheidung=payload.decision
        )
    except AiActionStateError as exc:
        db.rollback()
        code = str(exc)
        if code == "AI_APPROVAL_INVALID":
            raise HTTPException(status_code=404, detail=_UNGUELTIG) from exc
        # Alles andere ist ein echter Zustandsfehler des Vorschlags — ein
        # entzogenes Recht, eine fehlende Backup-Sicherung, ein belegter Server.
        # Der Empfaenger soll ihn erfahren; er ist der Grund, warum seine
        # Zustimmung nicht gereicht hat.
        raise HTTPException(
            status_code=409, detail=f"ai.errors.codes.{code}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(
            "Freigabe-Entscheidung gescheitert: %s", type(exc).__name__, exc_info=True
        )
        raise HTTPException(
            status_code=500, detail="ai.errors.codes.AI_APPROVAL_FAILED"
        ) from exc
