"""Gesprochene Vorschlagsbestätigung über den bestehenden Guardian-Pfad."""

from __future__ import annotations

import contextlib
import logging

from database import SessionLocal
from models import AiConversation, AiRun, User

logger = logging.getLogger(__name__)


def vorschlag_ausfuehren(*, user_id: int, kennung: str) -> tuple[bool, str | None]:
    """Bestätigt und führt einen eigenen Vorschlag wie der Chat-Klick aus."""

    from services import ai_action_errors, ai_proposal_service, ai_run_service

    with SessionLocal() as db:
        benutzer = db.get(User, user_id)
        if benutzer is None:
            return False, None
        try:
            vorschlag = ai_proposal_service.owned_proposal(db, kennung, benutzer)
            if vorschlag is None:
                logger.info("Gesprochene Bestaetigung fuer fremden Vorschlag user=%s", user_id)
                return False, None
            lauf_id = getattr(vorschlag, "run_id", None)
            _, token = ai_proposal_service.confirm_proposal(
                db, proposal_id=kennung, user=benutzer
            )
            ai_proposal_service.execute_proposal(
                db, proposal_id=kennung, user=benutzer, confirmation_token=token
            )
            db.commit()
            fortgesetzt: str | None = None
            if lauf_id:
                with contextlib.suppress(Exception):
                    if ai_run_service.lauf_fortsetzen(db, run_id=lauf_id):
                        fortgesetzt = lauf_id
                    db.commit()
            if fortgesetzt:
                lauf = db.get(AiRun, fortgesetzt)
                fenster = db.get(AiConversation, lauf.conversation_id) if lauf else None
                if fenster is not None and fenster.kind == "worker":
                    fortgesetzt = None
            return True, fortgesetzt
        except ai_action_errors.AiActionStateError as fehler:
            db.rollback()
            logger.info(
                "Gesprochene Bestaetigung abgewiesen user=%s code=%s",
                user_id,
                fehler.args[0] if fehler.args else "?",
            )
        except Exception:
            db.rollback()
            logger.warning("Gesprochene Bestaetigung gescheitert user=%s", user_id)
    return False, None
