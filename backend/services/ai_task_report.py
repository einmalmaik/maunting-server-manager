"""Der Bericht nach einem faelligen Auftrag, der ohne den Benutzer lief.

Eigenes Modul und nicht Teil von `ai_task_service`, aus demselben Grund wie bei
`ai_guardian_report`: das eine startet Laeufe, das andere schliesst sie ab, und
sie werden aus verschiedenen Richtungen gerufen — der Start aus dem Scheduler,
der Bericht aus `_lauf_abschliessen` im Stream. Zusammengelegt haetten sie einen
Importzyklus ueber `ai_stream_service` gebildet, den man nur mit einem
verzoegerten Import haette aufloesen koennen. Dieselbe Konstruktion hat in
diesem Projekt schon einmal das Panel zum Stillstand gebracht.

Verschickt wird bei **jedem** Endzustand, nicht nur bei Erfolg. Ein Auftrag, der
still scheitert, ist schlimmer als gar keiner: der Betreiber verlaesst sich auf
etwas, das seit Wochen nicht mehr laeuft.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import AiMessage, AiRun, AiTask, User
from services.ai_redaction import redact_sensitive_text


logger = logging.getLogger(__name__)

#: Wieviel vom Abschlusstext des Modells in die Mail geht. Genug fuer eine
#: Zusammenfassung, zu wenig fuer einen abgeschriebenen Logauszug.
MAX_BERICHT_ZEICHEN = 4000

#: Welche Endzustaende als "geschafft" gelten. Ausschliesslich `completed` — ein
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
    text = redact_sensitive_text(str(zeile.content)).strip()

    # **Von hinten**, nicht von vorne. Vorher stand hier `[:MAX_BERICHT_ZEICHEN]`,
    # und das war genau verkehrt herum: `content` ist das Protokoll des ganzen
    # Laufs, und `MITREDEN` verlangt vor jedem Werkzeugaufruf einen Satz. Vorne
    # steht deshalb "Ich pruefe jetzt den Status aller Server", hinten steht das
    # Ergebnis. Wer vorne abschneidet, verschickt die Ansagen und laesst den
    # Bericht weg — so geschehen in einer Mail an den Betreiber.
    #
    # Der letzte Absatz ist die Schlussfassung, seit die Runden durch eine
    # Leerzeile getrennt sind. Ist er zu duenn, um allein zu stehen, geht der
    # Schluss des ganzen Textes hinaus.
    absaetze = [teil.strip() for teil in text.split("\n\n") if teil.strip()]
    if absaetze and len(absaetze[-1]) >= 80:
        return absaetze[-1][-MAX_BERICHT_ZEICHEN:]
    return text[-MAX_BERICHT_ZEICHEN:]


def bericht_versenden(db: Session, *, run: AiRun, zustand: dict) -> None:
    """Stellt den Bericht dieses Aufgabenlaufs zu.

    Tut nichts bei ``channel="chat"`` — dann wollte der Benutzer ausdruecklich
    keine Mail, und der Verlauf steht ohnehin im Chat. Der umgekehrte Fall gilt
    **nicht**: ``channel="email"`` heisst *zusaetzlich*, nicht *ausschliesslich*.
    Der Chat ist der Ort, an dem der Lauf immer landet; die Mail ist die
    Benachrichtigung darueber.

    Empfaenger ist der Besitzer der Aufgabe. Eine andere Adresse gibt es nicht
    und soll es nicht geben: MSM verschickt keine Post an Dritte, weil ein
    Modell einen Namen genannt hat.
    """
    from services import ai_mail
    from services.ai_task_service import plan_text

    rahmen = zustand.get("aufgabe") or {}
    task_id = rahmen.get("task_id")
    if not task_id:
        return
    if rahmen.get("channel") == "chat":
        return

    user = db.get(User, run.user_id)
    if user is None:
        return
    aufgabe = db.get(AiTask, str(task_id))

    adresse = ai_mail.empfaenger(db, user)
    if adresse is None:
        return

    # Der Name kommt aus dem **Rahmen**, wenn die Zeile inzwischen weg ist.
    # Zwischen dem Start und dem Ende eines Laufs koennen Minuten liegen, und in
    # denen kann der Betreiber die Aufgabe im Chat geloescht haben. Der Bericht
    # ueber den letzten Lauf geht trotzdem hinaus — er beschreibt etwas, das
    # tatsaechlich passiert ist.
    titel = str((aufgabe.title if aufgabe is not None else rahmen.get("title")) or "")
    plan = plan_text(aufgabe) if aufgabe is not None else "einmalig"

    bericht = _abschlusstext(db, run)
    if not bericht:
        bericht = (
            "Der Assistent hat keine Zusammenfassung hinterlassen. "
            "Der Verlauf steht im KI-Chat des Panels."
        )

    _zustellen(
        to=adresse,
        username=str(user.username),
        task_title=titel,
        plan_text=plan,
        geschafft=run.status in ERFOLG,
        bericht=bericht,
    )


def _zustellen(**felder) -> None:
    """Waehlt die Mail aus; den Versand macht `ai_mail`.

    Eigene Funktion aus demselben Grund wie bei `ai_guardian_report`: sie ist
    die Stelle, an der die Tests den Versand abfangen, und das einzige Stueck,
    das **aufgabenspezifisch** ist. Thread, Ereignisschleife und die Auswertung
    des Rueckgabewerts stehen einmal in `ai_mail.zustellen`.
    """
    from services import ai_mail
    from services.email_service import EmailService

    ai_mail.zustellen(
        lambda: EmailService.send_ai_task_report(**felder),
        name="ai-task-report",
    )
