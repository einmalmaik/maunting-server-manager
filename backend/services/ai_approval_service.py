"""Freigabe per E-Mail — fuer die Faelle, in denen niemand am Panel sitzt.

Ein unbeaufsichtigter Lauf endete bisher an dem Punkt, an dem einer seiner
Vorschlaege eine Bestaetigung brauchte: alle offenen Vorschlaege zurueck, Lauf
zu Ende, Mail mit "konnte nicht behoben werden". Das war richtig, solange
niemand antworten konnte. Ist eine Adresse hinterlegt, ist es falsch — der
Betreiber ist im Urlaub, nicht abwesend.

**Die Reichweite bleibt genau die alte.** Gefragt wird nur nach dem, was der
autonome Modus ohnehin nie ohne Klick tut (`ALWAYS_CONFIRM_TOOLS`) und nach dem
Fall, dass `max_actions_per_hour` mitten in der Reparatur ausgeht. Ohne erteilte
Autonomie-Freigabe aendert sich nichts: kein Lauf, kein Anbieteraufruf, keine
Mail. Und `autonomy_allows` wird nie angefasst — eine E-Mail-Freigabe ist ein
dritter Zustand ("ein Mensch hat zugestimmt, ausser Haus") und wird als solcher
auditiert, nie als Flag am Grant.

Der Ablauf ist der des Passwort-Resets, nicht der eines Hoster-Callbacks:
``GET`` zeigt eine Seite, erst ein ``POST`` entscheidet. Mailscanner und
Vorschaudienste klicken Links; ein GET, das ausfuehrt, waere ein Neustart durch
einen Virenscanner.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models import AiActionApproval, AiActionProposal, Server, User
from models.ai_action_approval import hash_approval_token


logger = logging.getLogger(__name__)

#: Wie lange ein Freigabelink gilt.
#:
#: 24 Stunden, und das ist eine Abwaegung in beide Richtungen. Kuerzer waere die
#: Mail wertlos fuer jemanden, der abends aufs Telefon sieht; laenger stuende ein
#: Link auf einen Servereingriff tagelang in einem Postfach, und der Anlass —
#: ein offener Vorfall — waere laengst ein anderer. Der Reparaturauftrag hat
#: ohnehin eine eigene Frist (`FRIST_STUNDEN`), die frueher greift.
GUELTIG_STUNDEN = 24


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _utc(wert: datetime | None) -> datetime | None:
    """SQLite gibt naive Zeitstempel zurueck — hier wird wieder UTC daraus."""
    if wert is None:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def offene_freigabe(db: Session, *, user_id: int) -> AiActionApproval | None:
    """Die noch gueltige, unverbrauchte Freigabe dieses Benutzers — oder ``None``.

    Nach ``created_at`` absteigend, damit bei einem Rennen die juengste gewinnt.
    Verbrauchte und abgelaufene Zeilen zaehlen nicht: sie sind Geschichte, keine
    offene Frage.
    """
    return (
        db.query(AiActionApproval)
        .filter(
            AiActionApproval.user_id == user_id,
            AiActionApproval.consumed_at.is_(None),
            AiActionApproval.expires_at > _jetzt(),
        )
        .order_by(AiActionApproval.created_at.desc())
        .first()
    )


def freigabe_anfordern(
    db: Session,
    *,
    proposal: AiActionProposal,
    user: User,
    run_id: str | None,
) -> bool:
    """Praegt ein Token und reiht die Freigabemail ein. ``True``, wenn beides ging.

    **Das Token entsteht hier und nicht beim Senden.** Der Postausgang
    wiederholt einen Versand bis zu mehrfach; wuerde jeder Versuch ein Token
    praegen, gaebe es mehrere gueltige Links auf denselben Vorschlag, und der
    Einmalverbrauch waere eine Behauptung. Deshalb steht der Link auch schon im
    **Rueckfalltext**, der beim Einreihen gerendert wird: ab dem zweiten Versuch
    ruft der Postausgang das Modell nicht mehr, und eine Freigabemail ohne Link
    ist keine.

    Gibt ``False`` zurueck, wenn keine Adresse hinterlegt ist, wenn schon eine
    Freigabe offen ist oder wenn die Mail nicht in den Ausgangskorb kam. Der
    Aufrufer faellt dann auf das alte Verhalten zurueck — zuruecknehmen und
    beenden. Dieser Rueckfall muss bleiben: ohne Zustellweg kann niemand
    antworten, und ein Lauf, der auf eine Antwort wartet, die nie kommen kann,
    haengt bis zu seiner Frist.
    """
    from services import ai_mail
    from services.email_service import EmailService

    adresse = ai_mail.empfaenger(db, user)
    if not adresse:
        logger.info(
            "Freigabemail unterbleibt: keine Adresse user_id=%s proposal=%s",
            user.id, proposal.id,
        )
        return False

    # **Eine offene Frage je Benutzer, und die Grenze hält auch im Rennen.**
    # Ohne sie schriebe ein Lauf, der in jeder Runde denselben Vorschlag neu
    # aufsetzt, eine Mail je Runde; der Empfänger bekäme acht gleichlautende
    # Nachrichten und wüsste bei keiner, ob sie noch gilt. Und weil derselbe
    # Benutzer mehrere unbeaufsichtigte Läufe gleichzeitig haben kann — bis zu
    # drei Worker plus die Guardian-Heilung, jeder in einer eigenen Sitzung —,
    # muss zwischen dem Nachsehen und dem Anlegen dieselbe Zeilensperre liegen,
    # die `ai_usage_service.reserve_ai_usage` benutzt. Sie hält bis zum Commit
    # weiter unten; danach sieht der zweite Thread die Zeile des ersten.
    db.query(User.id).filter(User.id == user.id).with_for_update().one()
    if offene_freigabe(db, user_id=user.id) is not None:
        logger.info(
            "Freigabemail unterbleibt: es wartet schon eine user_id=%s", user.id
        )
        return False

    server_name = ""
    if proposal.server_id is not None:
        server = db.query(Server).filter(Server.id == proposal.server_id).first()
        server_name = str(getattr(server, "name", "") or "")

    token = secrets.token_urlsafe(32)
    zeile = AiActionApproval(
        id=str(uuid.uuid4()),
        token_hash=hash_approval_token(token),
        proposal_id=proposal.id,
        run_id=run_id,
        user_id=user.id,
        expires_at=_jetzt() + timedelta(hours=GUELTIG_STUNDEN),
    )
    db.add(zeile)
    db.commit()

    rahmen = EmailService.ai_rahmen_freigabe(
        str(getattr(user, "username", "") or ""),
        tool_name=str(proposal.tool_name or ""),
        server_name=server_name,
        token=token,
        stunden=GUELTIG_STUNDEN,
    )
    betreff, rueckfall_text, rueckfall_html = EmailService.ai_mail_rendern(
        rahmen, mailtext=None, rueckfall=str(rahmen.get("rueckfall") or "")
    )
    eingereiht = ai_mail.einreihen(
        db,
        user_id=user.id,
        anlass="ai-freigabe",
        betreff=betreff,
        text=rueckfall_text,
        html=rueckfall_html,
        # **Kein `fakten`-Feld.** Ohne es laesst der Postausgang das Modell gar
        # nicht erst schreiben, und genau das ist hier richtig: diese Mail ist
        # keine Erzaehlung, sondern eine Frage mit einem Knopf darunter. Ein
        # umformulierter Text koennte den Anlass verschieben, ohne dass jemand
        # es merkt — und der Empfaenger entscheidet danach ueber einen Eingriff.
        rahmen=rahmen,
    )
    if not eingereiht:
        # Ohne Mail gibt es niemanden, der antworten koennte. Das Token wieder
        # wegzunehmen ist wichtiger als es zu behalten: eine offene Freigabe
        # ohne Link gilt als die eine offene Frage und blockiert damit jeden
        # weiteren Versuch.
        db.delete(zeile)
        db.commit()
        logger.warning(
            "Freigabemail nicht eingereiht, Token verworfen proposal=%s", proposal.id
        )
        return False

    logger.info(
        "Freigabe per Mail angefordert proposal=%s approval=%s", proposal.id, zeile.id
    )
    return True


def freigabe_lesen(db: Session, token: str) -> AiActionApproval | None:
    """Die Zeile zu einem Token — ohne sie zu verbrauchen.

    Fuer die Anzeigeseite. Abgelaufene und verbrauchte Zeilen kommen hier
    **nicht** heraus: die Seite soll dieselbe Auskunft geben wie der
    Entscheidungsendpunkt, und der kennt genau einen Fehler.
    """
    if not token:
        return None
    zeile = (
        db.query(AiActionApproval)
        .filter(AiActionApproval.token_hash == hash_approval_token(token))
        .first()
    )
    if zeile is None:
        return None
    if zeile.consumed_at is not None:
        return None
    if (_utc(zeile.expires_at) or _jetzt()) <= _jetzt():
        return None
    return zeile


def _anspruch_nehmen(db: Session, zeile: AiActionApproval, entscheidung: str) -> bool:
    """Verbraucht die Zeile — atomar, per bedingtem UPDATE.

    Zwei gleichzeitige Klicks auf denselben Link duerfen nicht zwei
    Ausfuehrungen ergeben. Auf SQLite gibt es kein ``SELECT ... FOR UPDATE``;
    die Bedingung ``consumed_at IS NULL`` im UPDATE ist die Sperre, und die
    Zeilenzahl der Antwort sagt, wer gewonnen hat.
    """
    jetzt = _jetzt()
    getroffen = (
        db.query(AiActionApproval)
        .filter(
            AiActionApproval.id == zeile.id,
            AiActionApproval.consumed_at.is_(None),
        )
        .update(
            {"consumed_at": jetzt, "decision": entscheidung},
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(getroffen)


def entscheiden(db: Session, *, token: str, entscheidung: str) -> dict:
    """Freigeben oder ablehnen — der eine Weg, auf dem eine Mail wirkt.

    Freigeben ruft `confirm_proposal` und `execute_proposal` ganz normal auf.
    Die drei Rechtepruefungen, die Backup-Schranke, der Server-Mutex und der
    atomare Verbrauch des Bestaetigungstokens laufen damit unveraendert — eine
    Freigabe per Mail umgeht **keine** dieser Schranken, sie ersetzt nur den
    Klick. Wer das Recht inzwischen verloren hat, kommt hier nicht durch, auch
    wenn er den Link noch hat.

    Ablehnen setzt den Vorschlag auf ``expired``. Beides weckt danach den Lauf:
    ein abgelehnter Vorschlag ist fuer die KI genauso eine Auskunft wie ein
    ausgefuehrter, und ohne das Wecken stuende der Lauf bis zu seiner Frist.
    """
    from services import ai_proposal_service, audit_service
    from services.ai_action_errors import AiActionStateError

    if entscheidung not in ("approved", "rejected"):
        raise AiActionStateError("AI_APPROVAL_INVALID")

    zeile = freigabe_lesen(db, token)
    if zeile is None:
        # **Ein einziger Fehler fuer unbekannt, abgelaufen und verbraucht.**
        # Drei verschiedene Meldungen sagten einem Fremden, welche Token es
        # gibt.
        raise AiActionStateError("AI_APPROVAL_INVALID")

    if not _anspruch_nehmen(db, zeile, entscheidung):
        raise AiActionStateError("AI_APPROVAL_INVALID")

    # **Ab hier ist das Token verbrannt.** Jeder Ausstieg unterhalb dieser
    # Zeile muss den geparkten Lauf trotzdem wecken, sonst stünde er bis zu
    # seiner Frist auf `waiting_confirmation` — und weil
    # `ai_guardian_repair_service._wartet_auf_freigabe` an `consumed_at IS NULL`
    # misst, gälte der Auftrag beim Fristende als aufgegeben statt als
    # eskaliert: der Betreiber erführe nicht einmal, dass eine Freigabe offen
    # war. Deshalb steht die Laufkennung schon hier bereit, vor dem Vorschlag.
    run_id = zeile.run_id

    user = db.query(User).filter(User.id == zeile.user_id).first()
    if user is None or not getattr(user, "is_active", False):
        _lauf_wecken(db, run_id)
        raise AiActionStateError("AI_APPROVAL_INVALID")

    proposal = (
        db.query(AiActionProposal)
        .filter(AiActionProposal.id == zeile.proposal_id)
        .first()
    )
    if proposal is None:
        _lauf_wecken(db, run_id)
        raise AiActionStateError("AI_ACTION_NOT_FOUND")

    run_id = run_id or proposal.run_id

    if entscheidung == "rejected":
        if proposal.status in ("proposed", "confirmed"):
            proposal.status = "expired"
            proposal.confirmation_token_hash = None
            proposal.error_code = "AI_APPROVAL_REJECTED"
            db.commit()
        audit_service.record_privileged_action(
            db,
            user_id=user.id,
            action="ai.action.approval.rejected",
            target_type="ai_action_proposal",
            target_id=None,
            details={
                "proposal_id": proposal.id,
                "tool_name": proposal.tool_name,
                "confirmed_via": "email",
            },
            correlation_id=proposal.correlation_id,
        )
        db.commit()
        _lauf_wecken(db, run_id)
        return {"decision": "rejected", "tool_name": proposal.tool_name}

    try:
        bestaetigt, bestaetigungstoken = ai_proposal_service.confirm_proposal(
            db, proposal_id=proposal.id, user=user
        )
    except Exception:
        db.rollback()
        # Derselbe Griff wie bei der Ausführung weiter unten. Das Token bleibt
        # bewusst verbraucht — es zurückzugeben öffnete das Einmal-Rennen
        # wieder. Der Gewinn ist, dass der wartende Lauf die Absage erfährt:
        # er kann weiterarbeiten und, weil die Zeile jetzt `consumed_at` trägt,
        # über `offene_freigabe` sofort eine neue Freigabe anfordern.
        _lauf_wecken(db, run_id)
        raise
    audit_service.record_privileged_action(
        db,
        user_id=user.id,
        action="ai.action.approval.approved",
        target_type="ai_action_proposal",
        target_id=None,
        # `confirmed_via` ist der ganze Punkt dieses Eintrags: im Audit muss
        # unterscheidbar bleiben, ob jemand im Panel geklickt oder aus dem
        # Urlaub eine Mail beantwortet hat.
        details={
            "proposal_id": bestaetigt.id,
            "tool_name": bestaetigt.tool_name,
            "confirmed_via": "email",
        },
        correlation_id=bestaetigt.correlation_id,
    )
    db.commit()

    try:
        ausgefuehrt, ergebnis = ai_proposal_service.execute_proposal(
            db,
            proposal_id=bestaetigt.id,
            user=user,
            confirmation_token=bestaetigungstoken,
        )
    except Exception:
        db.rollback()
        # Der Fehlschlag steht bereits an der Vorschlagszeile — `execute_proposal`
        # committet ihn, bevor es wirft. Der Lauf darf ihn erfahren: eine
        # gescheiterte Ausfuehrung ist genau der Moment, in dem die KI etwas
        # anderes versuchen soll.
        _lauf_wecken(db, run_id)
        raise

    _lauf_wecken(db, ausgefuehrt.run_id or run_id)
    return {
        "decision": "approved",
        "tool_name": ausgefuehrt.tool_name,
        "result": ergebnis,
    }


def _lauf_wecken(db: Session, run_id: str | None) -> None:
    """Weckt den Lauf, der auf diese Entscheidung gewartet hat.

    Wie in `routers/ai_actions._lauf_wecken`, und aus demselben Grund bei
    Zustimmung **und** Ablehnung: bliebe der Lauf geparkt, waere die Reparatur
    beendet, ohne dass irgendwo stuende, warum. Scheitert das Wecken selbst,
    bleibt es bei der Entscheidung — sie darf nicht daran haengen, ob die KI
    danach noch etwas vorhat.
    """
    if not run_id:
        return
    from services import ai_run_service

    try:
        ai_run_service.lauf_fortsetzen(db, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "AI-Lauf nach E-Mail-Entscheidung nicht fortgesetzt run_id=%s", run_id
        )


def abgelaufene_aufraeumen(db: Session) -> int:
    """Raeumt abgelaufene und verbrauchte Freigaben weg.

    Wie `cleanup_expired` bei den Login-Challenges. Verbrauchte Zeilen gehen
    mit: die Tatsache, dass jemand zugestimmt hat, steht im Audit — hier stuende
    sie ein zweites Mal, mit einem Tokenhash daneben, den niemand mehr braucht.
    """
    jetzt = _jetzt()
    entfernt = (
        db.query(AiActionApproval)
        .filter(
            (AiActionApproval.expires_at <= jetzt)
            | (AiActionApproval.consumed_at.isnot(None))
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    if entfernt:
        logger.info("Abgelaufene KI-Freigaben entfernt anzahl=%d", entfernt)
    return int(entfernt)
