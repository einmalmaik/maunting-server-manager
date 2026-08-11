"""Bestaetigung und Ausfuehrung persistenter AI-Aktionsvorschlaege."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiActionProposal, User
from schemas.ai_action import (
    AiActionConfirmationResponse,
    AiActionExecuteRequest,
    AiActionExecuteResponse,
    AiActionProposalResponse,
)
from services import (
    ai_action_errors,
    ai_chat_service,
    ai_proposal_service,
    ai_run_service,
)
# Die Serialisierung eines Vorschlags liegt beim Vorschlag, nicht beim Router:
# der Stream veroeffentlicht denselben Typ ueber SSE und muss dieselbe Quelle
# benutzen. Solange sie hier stand, hatte `AiActionProposal` zwei Wahrheiten —
# und die des Streams kannte weder `reason` noch `expected_effect`.
from services.ai_proposal_service import proposal_response
from services.dis_client import DisSidecarError


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai-actions"])


def _state_error(exc: ai_action_errors.AiActionStateError) -> HTTPException:
    """Macht aus einem Zustandsfehler eine Antwort, die das Panel uebersetzen kann.

    Hier standen fertige deutsche Saetze. `frontend/src/api/client.ts` schickt
    jedes `detail` durch `i18n.t()`; ein Satz ist kein Schluessel, also gab
    `parseMissingKeyHandler` (i18n.ts) ihn woertlich zurueck, und die
    Vorschlagskarte zeigt genau diesen Text bevorzugt vor ihrem eigenen
    t()-Rueckfall an. Ein Benutzer mit Sprache Englisch las deutsch.

    Deshalb liefern wir den Schluessel selbst - denselben Weg gehen
    routers/mods.py und routers/steam.py mit `errors.*` bereits. Die Saetze
    stehen in de.json und en.json unter `ai.errors.codes`; die uebrigen
    Sprachen fallen laut i18n.ts auf Englisch zurueck.

    Der 409-Zweig bleibt unveraendert: dort traegt der strukturierte Code die
    Aussage, und AiChat.tsx uebersetzt ihn ueber denselben Namensraum.
    """
    if exc.code == "AI_ACTION_NOT_FOUND":
        return HTTPException(
            status_code=404, detail="ai.errors.codes.AI_ACTION_NOT_FOUND"
        )
    if exc.code == "AI_ACTION_ACCESS_REVOKED":
        return HTTPException(
            status_code=403, detail="ai.errors.codes.AI_ACTION_ACCESS_REVOKED"
        )
    return HTTPException(status_code=409, detail={"code": exc.code})


@router.get("/conversation/actions", response_model=list[AiActionProposalResponse])
def list_conversation_actions(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> list[AiActionProposalResponse]:
    """Alle Vorschlaege der einen Unterhaltung, aeltester zuerst."""
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    rows = db.query(AiActionProposal).filter(
        AiActionProposal.conversation_id == conversation.id,
        AiActionProposal.user_id == user.id,
    ).order_by(AiActionProposal.created_at.asc()).all()
    return [proposal_response(row) for row in rows]


@router.get("/actions/{proposal_id}", response_model=AiActionProposalResponse)
def get_action(
    proposal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiActionProposalResponse:
    try:
        proposal = ai_proposal_service.owned_proposal(db, proposal_id, user)
    except ai_action_errors.AiActionStateError as exc:
        # Ein entzogenes Recht ist etwas anderes als ein verschwundener
        # Vorschlag. `_state_error` kennt den Unterschied und macht 403 daraus.
        raise _state_error(exc) from exc
    if proposal is None:
        raise HTTPException(
            status_code=404, detail="ai.errors.codes.AI_ACTION_NOT_FOUND"
        )
    return proposal_response(proposal)


@router.post(
    "/actions/{proposal_id}/confirm",
    response_model=AiActionConfirmationResponse,
)
def confirm_action(
    proposal_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiActionConfirmationResponse:
    try:
        proposal, token = ai_proposal_service.confirm_proposal(
            db, proposal_id=proposal_id, user=user
        )
        assert proposal.confirmation_expires_at is not None
        return AiActionConfirmationResponse(
            proposal_id=proposal.id,
            confirmation_token=token,
            expires_at=proposal.confirmation_expires_at,
        )
    except ai_action_errors.AiActionStateError as exc:
        db.rollback()
        raise _state_error(exc) from exc
    except DisSidecarError as exc:
        db.rollback()
        # Nicht der Vorschlag ist weg, sondern der sichere Speicher ist gerade
        # nicht erreichbar - der alte Satz sagte dem Benutzer das Falsche und
        # sagte es ausserdem nur auf Deutsch.
        raise HTTPException(
            status_code=503, detail="ai.errors.codes.AI_ACTION_STORE_UNAVAILABLE"
        ) from exc


def _lauf_wecken(db: Session, run_id: str | None) -> None:
    """Weckt den Lauf, der auf diesen Vorschlag gewartet hat.

    **Hier ging es frueher nicht weiter.** Der Mensch hatte zugestimmt, die
    Aktion lief — und der Zug, der sie vorgeschlagen hatte, existierte nicht
    mehr. Man musste eine neue Nachricht schreiben, damit die KI erfuhr, wie ihr
    eigener Vorschlag ausgegangen ist.

    Gerufen wird das bei **Erfolg und bei Fehlschlag**. Ein gescheiterter
    Neustart ist genau der Moment, in dem der Benutzer eine Aussage braucht;
    bliebe der Lauf dann geparkt, waere die Karte rot und der Chat stumm.
    Ob ueberhaupt geweckt wird, entscheidet `darf_fortsetzen`: solange noch ein
    Vorschlag derselben Runde offen ist, passiert nichts.

    Scheitert das Wecken selbst (kein laufendes Panel, Lauf ueberholt), bleibt
    es bei der ausgefuehrten Aktion — die Zustimmung des Menschen darf nicht
    daran haengen, ob die KI danach noch etwas vorhat.
    """
    if not run_id:
        return
    try:
        ai_run_service.lauf_fortsetzen(db, run_id=run_id)
    except Exception:
        logger.warning(
            "AI-Lauf konnte nach der Entscheidung nicht fortgesetzt werden run_id=%s",
            run_id,
        )


@router.post(
    "/actions/{proposal_id}/execute",
    response_model=AiActionExecuteResponse,
)
def execute_action(
    proposal_id: str,
    payload: AiActionExecuteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiActionExecuteResponse:
    # Vor dem `try` gesetzt, weil der `except`-Zweig ihn liest — und der kann
    # jetzt schon beim Nachschlagen greifen.
    run_id: str | None = None
    try:
        # Den Lauf **vorher** merken: scheitert die Ausfuehrung, ist die Zeile
        # danach zurueckgerollt und neu geladen, und der Verweis waere
        # umstaendlich wiederzubeschaffen.
        #
        # Das Nachschlagen steht ausdruecklich **im** `try`. Solange
        # `owned_proposal` bei fehlendem Recht nur `None` zurueckgab, war es
        # davor gefahrlos; seit es `AI_ACTION_ACCESS_REVOKED` wirft, waere das
        # ein ungefangener Fehler und aus einer 403 wuerde eine 500.
        vorab = ai_proposal_service.owned_proposal(db, proposal_id, user)
        run_id = vorab.run_id if vorab is not None else None
        proposal, result = ai_proposal_service.execute_proposal(
            db,
            proposal_id=proposal_id,
            user=user,
            confirmation_token=payload.confirmation_token.get_secret_value(),
        )
        antwort = AiActionExecuteResponse(
            proposal=proposal_response(proposal), result=result
        )
        _lauf_wecken(db, proposal.run_id or run_id)
        return antwort
    except ai_action_errors.AiActionStateError as exc:
        db.rollback()
        # Der Fehlschlag ist bereits an der Vorschlagszeile festgehalten
        # (`execute_proposal` committet ihn, bevor es wirft). Der Lauf darf ihn
        # deshalb erfahren und den Benutzer unterrichten.
        _lauf_wecken(db, run_id)
        raise _state_error(exc) from exc
    except DisSidecarError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503, detail="ai.errors.codes.AI_ACTION_STORE_UNAVAILABLE"
        ) from exc
