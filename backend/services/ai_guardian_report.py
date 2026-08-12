"""Der Bericht nach einer Heilung, die ohne den Benutzer lief.

Eigenes Modul und nicht Teil von `ai_guardian_service`: das eine startet
Laeufe, das andere schliesst sie ab, und sie werden aus verschiedenen Richtungen
gerufen — der Start aus dem Scheduler, der Bericht aus `_lauf_abschliessen` im
Stream. Zusammengelegt haetten sie einen Importzyklus ueber `ai_stream_service`
gebildet, den man nur mit einem verzoegerten Import haette aufloesen koennen.
Dieselbe Konstruktion hat in diesem Projekt schon einmal das Panel zum Stillstand
gebracht, als jemand den verzoegerten Import fuer Unordnung hielt.

Verschickt wird bei **jedem** Endzustand, nicht nur bei Erfolg. "Nicht
geschafft" ist fuer den Betreiber die wichtigere Nachricht von beiden: sein
Server laeuft nicht, und niemand sass davor. Ein Heilungslauf, der still
scheitert, waere die schlechteste Eigenschaft dieser ganzen Kopplung.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import AiMessage, AiRun, Backup, Incident, Server, User
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

#: Wieviel vom Abschlusstext des Modells in die Mail geht. Genug fuer eine
#: Ursache und einen Ablauf, zu wenig fuer einen abgeschriebenen Logauszug.
MAX_BERICHT_ZEICHEN = 4000

#: Welche Endzustaende als "behoben" gelten. Ausschliesslich `completed` — ein
#: abgebrochener oder fehlgeschlagener Lauf hat nichts bewiesen, auch wenn er
#: unterwegs etwas getan hat.
ERFOLG = ("completed",)


def _abschlusstext(db: Session, run: AiRun) -> str:
    """Die letzte Antwort des Modells in diesem Lauf.

    Ueber die Unterhaltung gesucht und nicht ueber `run.message_id`: die wird
    beim Abschluss auf `None` gesetzt (ein beendeter Lauf hat kein laufendes
    Segment mehr), und zum Zeitpunkt dieses Aufrufs ist das bereits geschehen.
    """
    zeile = (
        db.query(AiMessage)
        .filter(
            AiMessage.conversation_id == run.conversation_id,
            AiMessage.role == "assistant",
            AiMessage.status == "complete",
        )
        .order_by(AiMessage.created_at.desc())
        .first()
    )
    if zeile is None or not zeile.content:
        return ""
    return redact_sensitive_text(str(zeile.content))[:MAX_BERICHT_ZEICHEN]


def _utc(wert: datetime) -> datetime:
    """SQLite gibt zeitzonenlose Werte zurueck, PostgreSQL zeitzonenbehaftete.

    Ein Vergleich zwischen beiden wirft `TypeError` — hier ausgerechnet im
    Berichtspfad, also dort, wo ein Fehler bedeutet, dass der Betreiber gar
    nichts erfaehrt.
    """
    return wert.replace(tzinfo=timezone.utc) if wert.tzinfo is None else wert


def _backupname(db: Session, *, run: AiRun, server_id: int) -> str | None:
    """Das nachgewiesene Backup, das **dieser Lauf** angelegt hat — falls eines.

    Gesucht wird ueber den Vorschlag, nicht ueber ein Zeitfenster. Das ist der
    Unterschied zwischen einem Beleg und einer Vermutung, und er ist hier
    entscheidend, weil der Betreiber diesen Satz liest, ohne ihn nachzupruefen.

    Vorher stand hier "juengstes verifiziertes Backup dieses Servers seit dem
    Vorfall". Auf einem Server mit stuendlichem Automatikbackup fand die Abfrage
    damit regelmaessig ein **fremdes** Archiv — und zwar bevorzugt ein zu
    junges, weil sie absteigend sortierte. Der Ablauf: Vorfall 03:05,
    KI-Backup 03:15, Eingriff 03:20, Scheduler-Backup 03:30, Lauf endet 03:35.
    Die Mail nannte das Archiv von 03:30, das die Aenderung der KI bereits
    enthielt. Wer daraufhin zurueckrollt, macht die Aenderung nicht rueckgaengig,
    sondern zementiert sie.

    Schlimmer noch trat der Satz auch dann auf, wenn die KI ueberhaupt nichts
    gesichert hatte — ein Neustart verlangt kein Backup.

    `AiActionProposal` traegt `run_id` und `tool_name` und ist damit die Spur,
    die es braucht. Findet sich keine, steht in der Mail nichts ueber ein
    Backup — eine ausgelassene Zeile ist besser als eine falsche Zusage.
    """
    from models import AiActionProposal

    vorschlag = (
        db.query(AiActionProposal)
        .filter(
            AiActionProposal.run_id == run.id,
            AiActionProposal.tool_name == "propose_backup",
            AiActionProposal.status == "succeeded",
            AiActionProposal.server_id == server_id,
        )
        .order_by(AiActionProposal.created_at.desc())
        .first()
    )
    if vorschlag is None:
        return None

    # Der Name aus der Vorschau ist der, den die KI vergeben hat. Er steht dort
    # bereits (`_execute_backup` legt ihn ab) und muss nicht aus der
    # Backup-Tabelle geraten werden.
    name = None
    try:
        import json as _json

        vorschau = _json.loads(vorschlag.preview_json or "{}")
        name = vorschau.get("backup_name") or vorschau.get("name")
    except (TypeError, ValueError):
        name = None

    # Der Nachweis bleibt Bedingung: genannt wird nur, was auch `verified_at`
    # traegt. Ein Name ohne Nachweis waere genau die Behauptung, die diese
    # Kopplung nicht erheben soll.
    zeile = (
        db.query(Backup)
        .filter(
            Backup.server_id == server_id,
            Backup.verified_at.isnot(None),
            Backup.created_at >= _utc(vorschlag.created_at),
        )
        .order_by(Backup.created_at.asc())
        .first()
    )
    if zeile is None:
        return None
    return str(name or zeile.name or zeile.filename.rsplit("/", 1)[-1])[:120]


def bericht_versenden(db: Session, *, run: AiRun, zustand: dict) -> None:
    """Stellt den Bericht dieses Heilungslaufs zu.

    Tut nichts, wenn E-Mail nicht eingerichtet ist oder der Benutzer keine
    Benachrichtigungen will. Beides ist eine gueltige Einstellung und kein
    Fehler — der Lauf steht ohnehin im Chat.

    Empfaenger ist **der Freigeber**, also derselbe Benutzer, in dessen Namen
    gehandelt wurde. Nicht der Verteiler aus `guardian_incident_service`: der
    fragt nur `server_permissions` ab und uebersieht damit Rollen und den Owner.
    Hier ist die Zuordnung eindeutig — wer die Autonomie erteilt hat, erfaehrt,
    was damit geschehen ist.
    """
    from services.email_service import EmailService

    rahmen = zustand.get("guardian") or {}
    server_id = rahmen.get("server_id")
    incident_id = rahmen.get("incident_id")
    if not server_id or not incident_id:
        return

    user = db.get(User, run.user_id)
    server = db.get(Server, int(server_id))
    vorfall = db.get(Incident, int(incident_id))
    if user is None or server is None or vorfall is None:
        return
    if not user.email_notifications:
        return
    if not EmailService.is_configured():
        logger.info(
            "Guardian-Bericht nicht zustellbar: E-Mail nicht eingerichtet (run_id=%s)",
            run.id,
        )
        return

    # Der Zustand des **Vorfalls**, nicht die Behauptung des Modells. Ein Lauf
    # kann sauber enden und der Server trotzdem stehen; umgekehrt kann das
    # Modell einen Fehlschlag melden, waehrend Guardian den Vorfall inzwischen
    # als geloest sieht. Beides zusammen ergibt erst die Wahrheit — deshalb
    # zaehlt hier die Und-Verknuepfung.
    db.refresh(vorfall)
    geheilt = run.status in ERFOLG and vorfall.status == "resolved"

    bericht = _abschlusstext(db, run)
    if not bericht:
        bericht = (
            "Der Assistent hat keinen Abschlussbericht hinterlassen. "
            "Der Verlauf steht im KI-Chat des Panels."
        )

    adresse = user.email
    if not adresse:
        return

    _zustellen(
        to=adresse,
        username=str(user.username),
        server_name=str(server.name or ""),
        incident_type=str(vorfall.type),
        geheilt=geheilt,
        bericht=bericht,
        backup_name=_backupname(db, run=run, server_id=server.id),
    )


def _zustellen(**felder) -> None:
    """Schickt die Mail in einem eigenen Thread mit eigener Ereignisschleife.

    `_lauf_abschliessen` ist synchron und laeuft je nach Weg auf der
    Ereignisschleife der Anwendung — `asyncio.run` waere dort ein Fehler, und
    `await` geht nicht, weil die Funktion kein `async def` ist. Dasselbe Muster
    benutzt `guardian_incident_service` fuer seine Benachrichtigungen bereits.

    Der Rueckgabewert wird **ausgewertet**. `send_email` wirft nie und gibt bei
    jedem Problem `False` zurueck; ohne diese Zeile waere die Zusage "der
    Benutzer wird informiert" weder erfuellt noch pruefbar, und im Log stuende
    nichts.
    """
    from services.email_service import EmailService

    def _lauf() -> None:
        schleife = asyncio.new_event_loop()
        try:
            ok = schleife.run_until_complete(
                EmailService.send_ai_healing_report(**felder)
            )
            if not ok:
                logger.warning("Guardian-Bericht konnte nicht zugestellt werden")
        except Exception as exc:  # noqa: BLE001 - ein Mailfehler beendet keinen Lauf
            logger.warning("Guardian-Bericht fehlgeschlagen: %s", type(exc).__name__)
        finally:
            schleife.close()

    threading.Thread(target=_lauf, daemon=True, name="ai-guardian-report").start()
