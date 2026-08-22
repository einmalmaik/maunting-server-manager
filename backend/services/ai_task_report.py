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

import json
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


def _letzte_runde(zeile: AiMessage) -> str:
    """Der Text der **letzten Werkzeugrunde**, aus der Gliederung gelesen.

    Die Gliederung (`AiMessage.sections_json`) kennt die Rundengrenze als
    Tatsache: ein Textabschnitt waechst, bis ein Werkzeug kommt, danach faengt
    ein neuer an (`ai_run_broker.Abzug.text_anhaengen`). Was nach dem letzten
    Werkzeugabschnitt steht, ist der Schlussbericht — vollstaendig, mit allem,
    was darin an Werten steht.

    Der Umweg ueber `content` konnte das nicht: dort sind die Abschnitte mit
    einer Leerzeile aneinandergesetzt (`Abzug.inhalt`), und eine Leerzeile
    sieht in Markdown genauso aus wie ein Absatz des Modells. Wer den letzten
    Absatz nahm, nahm bei einer Antwort aus Liste **plus** Schlusssatz nur den
    Schlusssatz — die Zahlen darueber fielen weg. Genau so verlor der Bericht
    am 22.08.2026 die Speicherwerte der C-Platte: der Worker hatte sie, das
    Gehirn bekam nur noch den Satz "die Pruefung ist gelaufen".

    Leer heisst: keine Gliederung (Nachricht aus der Zeit davor, oder der
    Vermittler hat seinen Kanal der Groessengrenze geopfert) oder die Antwort
    endete mit einem Werkzeug statt mit Text. Dann entscheidet der Aufrufer.
    """
    roh = getattr(zeile, "sections_json", None)
    if not roh:
        return ""
    try:
        abschnitte = json.loads(roh)
    except (TypeError, ValueError):
        return ""
    if not isinstance(abschnitte, list):
        return ""

    letztes_werkzeug = -1
    for stelle, abschnitt in enumerate(abschnitte):
        if isinstance(abschnitt, dict) and abschnitt.get("art") == "tool":
            letztes_werkzeug = stelle

    stuecke = [
        str(abschnitt.get("inhalt") or "").strip()
        for abschnitt in abschnitte[letztes_werkzeug + 1:]
        if isinstance(abschnitt, dict) and abschnitt.get("art") == "text"
    ]
    return "\n\n".join(stueck for stueck in stuecke if stueck)


def abschlusstext(db: Session, run: AiRun, zustand: dict | None = None) -> str:
    """Die letzte Antwort des Modells **in diesem Lauf**.

    Genauer: der Text der **letzten Werkzeugrunde**. Nicht das ganze Protokoll
    (vorne stehen die Ankündigungen, die `MITREDEN` vor jedem Werkzeug
    verlangt) und nicht nur dessen letzter Absatz (dann fällt bei einer
    Antwort aus Liste plus Schlusssatz die Liste weg). Die Grenze kommt aus
    der Gliederung der Nachricht, siehe `_letzte_runde`; nur wenn es die nicht
    gibt, wird geraten.

    Öffentlich und nicht `_abschlusstext`, weil `ai_guardian_report` sie
    mitbenutzt. Dort stand einmal eine wortgleiche Kopie, und sie ist genau so
    auseinandergelaufen, wie Kopien das tun: die eine wurde von vorne
    abgeschnitten, die andere von hinten — und die von vorne verschickte dem
    Betreiber die Ankündigungen statt des Ergebnisses.

    Über die Unterhaltung gesucht und nicht über `run.message_id`: die wird
    beim Abschluss auf `None` gesetzt (ein beendeter Lauf hat kein laufendes
    Segment mehr), und zum Zeitpunkt dieses Aufrufs ist das bereits geschehen.

    Der Anker ist die **eigene Benutzernachricht** dieses Laufs. Es gibt je
    Benutzer und Art genau eine Unterhaltung (`uq_ai_conversations_user_kind`),
    und in den Dauerchat schreiben Mensch und fällige Aufträge gemeinsam. Ohne
    Anker fand die Abfrage bei einem Lauf, der selbst keine fertige Antwort
    hinterlassen hat — erschöpftes Kontingent, abgebrochenes Segment —, die
    jüngste Antwort aus einem völlig anderen Zug. Der Betreiber las dann unter
    "Abschlussbericht des Laufs" seine Chatunterhaltung von gestern.

    Das Guardian-Fenster hat seit `20260816_11` eine eigene Unterhaltung, aber
    der Anker bleibt trotzdem nötig — und dort umso mehr: ein Reparaturauftrag
    hat bis zu acht Anläufe im selben Verlauf, und ohne Anker trüge der Bericht
    des sechsten die Schlussworte des siebten.

    `run.created_at` taugt als Anker **nicht**: `ai_messages` wird im selben
    Flush vor `ai_runs` eingefügt, die eigene Antwort eines Laufs trägt also
    stets eine frühere Zeit als der Lauf selbst. Die Benutzernachricht dagegen
    entsteht in einem eigenen, früheren Flush und liegt damit vor allen
    Nachrichten dieses Laufs und nach allen des Zuges davor.

    Ohne Anker — alte Läufe ohne `user_message_id` im Zustand — bleibt es beim
    bisherigen Verhalten: eine ungenaue Zuordnung ist immer noch besser als gar
    kein Bericht.
    """
    anker = None
    kennung = (zustand or {}).get("user_message_id")
    if kennung:
        anker = db.get(AiMessage, str(kennung))

    abfrage = db.query(AiMessage).filter(
        AiMessage.conversation_id == run.conversation_id,
        AiMessage.role == "assistant",
        AiMessage.status == "complete",
    )
    if anker is not None:
        abfrage = abfrage.filter(AiMessage.created_at >= anker.created_at)
    zeile = abfrage.order_by(AiMessage.created_at.desc()).first()
    if zeile is None:
        return ""

    # Zuerst die Gliederung: sie **weiss**, wo die letzte Runde anfing.
    schluss = _letzte_runde(zeile)
    if schluss:
        return redact_sensitive_text(schluss).strip()[-MAX_BERICHT_ZEICHEN:]

    if not zeile.content:
        return ""
    text = redact_sensitive_text(str(zeile.content)).strip()

    # Ab hier wird geraten, weil es nichts Besseres gibt — die Nachricht hat
    # keine Gliederung.
    #
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

    bericht = abschlusstext(db, run, zustand)
    if not bericht:
        bericht = (
            "Der Assistent hat keine Zusammenfassung hinterlassen. "
            "Der Verlauf steht im KI-Chat des Panels."
        )

    _zustellen(
        db=db,
        user_id=int(user.id),
        provider_id=run.provider_id,
        to=adresse,
        username=str(user.username),
        task_title=titel,
        plan_text=plan,
        geschafft=run.status in ERFOLG,
        bericht=bericht,
    )


def _zustellen(
    *, db, user_id: int, provider_id: int | None = None, **felder
) -> None:
    """Legt den Bericht in den Ausgangskorb. Verschickt nichts, wartet auf nichts.

    Eigene Funktion aus demselben Grund wie bei `ai_guardian_report`: sie ist
    die Stelle, an der die Tests den Versand abfangen, und das einzige Stueck,
    das **aufgabenspezifisch** ist.

    Hier stand eine Koroutine, die erst verfasste und dann verschickte, und
    `ai_mail.zustellen` machte dafuer einen Thread auf. Das ueberlebte keinen
    Neustart: stuerzte der Prozess zwischen dem Ende des Laufs und dem Versand
    ab, war der Bericht weg — und zwar der ueber den Auftrag, bei dem niemand
    davorsass. Jetzt entsteht hier nur noch eine Zeile in der Datenbank.

    Gerendert wird trotzdem schon hier, und zwar der **Rueckfall**: der feste
    Text, der hinausgeht, wenn der Verfassungsschritt im Arbeiter misslingt.
    Damit traegt die Zeile ab dem ersten Moment eine vollstaendige Mail; alles
    Weitere ist Verbesserung, nicht Voraussetzung.

    `provider_id` ist der Anbieter **dieses Laufs** und wandert im Rahmen mit.
    Ist er nicht gesetzt, sucht `ai_mail_text` beim Versand selbst einen; das
    ist derselbe Weg, den ein Lauf ohne Auswahl auch sonst geht.
    """
    from services import ai_mail
    from services.email_service import EmailService

    rahmen = EmailService.ai_rahmen_task(
        str(felder.get("username") or ""),
        task_title=str(felder.get("task_title") or ""),
        plan_text=str(felder.get("plan_text") or ""),
        geschafft=bool(felder.get("geschafft")),
    )
    rahmen["provider_id"] = provider_id
    betreff, text, html = EmailService.ai_mail_rendern(
        rahmen, rueckfall=str(felder.get("bericht") or "")
    )
    ai_mail.zustellen(
        name="ai-task-report",
        db=db,
        user_id=user_id,
        betreff=betreff,
        text=text,
        html=html,
        fakten=_fakten(felder),
        rahmen=rahmen,
    )


def _fakten(felder: dict) -> str:
    """Was das Modell ueber diese Mail wissen muss — und nichts darueber hinaus.

    Ausdruecklich **ohne** die Adresse des Empfaengers. Sie steht nur eine
    Ebene hoeher in denselben Feldern, und ein Modell, das sie im Text
    wiederholt, waere die erste Stufe eines Weges, den es hier nicht geben
    soll: MSM verschickt keine Post an Dritte, weil ein Modell einen Namen
    genannt hat. Wer die Adresse gar nicht erst zeigt, muss auch nichts
    herausfiltern.
    """
    zustand = "erledigt" if felder.get("geschafft") else "nicht abgeschlossen"
    return (
        f"Anlass: eine faellige KI-Aufgabe wurde ausgefuehrt.\n"
        f"Name der Aufgabe: {felder.get('task_title') or '(ohne Namen)'}\n"
        f"Zeitplan: {felder.get('plan_text') or 'einmalig'}\n"
        f"Ergebnis laut Panel: {zustand}\n"
        f"Abschlussbericht des Laufs:\n{felder.get('bericht') or ''}"
    )
