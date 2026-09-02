"""Der Lauf als dauerhaftes Ding: anlegen, parken, aufwecken, aufraeumen.

Dieser Dienst besitzt den Lauf. Die Schleife selbst — Anbieter fragen, Werkzeuge
ausfuehren, Vorschlaege anlegen — steht weiterhin in ``ai_stream_service``; hier
steht nur, **wann** sie laeuft und **was zwischen zwei Laeufen ueberlebt**.

Die Aufgabenteilung, damit sie nicht wieder verschwimmt:

* ``ai_run_service``  — Lebenslauf, Zustand, Planung. Kennt keinen Anbieter.
* ``ai_stream_service`` — ein Segment ausfuehren. Kennt keinen Zeitplan.
* ``ai_run_broker``   — wer darf zusehen. Kennt weder das eine noch das andere.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import threading
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiActionProposal, AiProvider, AiRun, User
# Die Wartezustaende kommen aus dem Modell und nicht aus einer Literalkopie:
# wer dort einen Zustand ergaenzt, soll ihn hier nicht ein zweites Mal
# eintragen muessen — sonst sieht die Konstante wie die Wahrheitsquelle aus und
# ist doch nur eine Beschreibung.
from models.ai_run import BEENDET, WARTEND

if TYPE_CHECKING:
    # Nur fuer die Signatur von `Vorflug`. Zur Laufzeit bleibt der Import spaet
    # im Funktionsrumpf — ein harter Import waere der naechste Importzyklus.
    from services.ai_context_window import Fenster


logger = logging.getLogger(__name__)

# Der Lauf laeuft auf der Ereignisschleife der Anwendung, nicht am Request. Beide
# Werte werden beim Start gesetzt (``main.py``) und danach nur gelesen.
#
# Ohne sie kann ein Lauf nicht geplant werden — dann arbeitet die KI wie frueher
# im Request und stirbt mit ihm. Das ist der Zustand in der Testsuite, in der es
# keine Anwendung gibt: dort werden Segmente direkt abgewartet.
_SCHLEIFE: asyncio.AbstractEventLoop | None = None
_HTTP: httpx.AsyncClient | None = None

# Harte Referenzen auf die laufenden Aufgaben. Ohne sie darf der Sammler eine
# Aufgabe abraeumen, auf die niemand mehr zeigt — asyncio haelt selbst nur eine
# schwache Referenz, und der Lauf verschwaende mitten in der Arbeit.
_AUFGABEN: dict[str, asyncio.Task] = {}

# Wer plant, belegt den Platz **sofort** — im eigenen Thread, unter Schloss.
#
# _AUFGABEN taugt dafuer nicht: der Eintrag entsteht erst, wenn die
# Ereignisschleife die Koroutine `_starten` ausfuehrt. Zwei Bestaetigungen, die
# in zwei Threadpool-Arbeitern ankommen, sahen darum beide "nichts unterwegs"
# und planten je ein Segment — zwei Anbieteraufrufe auf demselben Zustand, zwei
# Abrechnungen (die zweite scheitert an AiUsageConflict und wirft den bereits
# beantworteten Lauf auf 'failed') und dieselbe Schreibaktion zweimal.
#
# Ein gewoehnliches Set unter threading.Lock, kein Framework: der Zustand ist
# eine Menge von Kennungen, und die Sperre wird nur um zwei Zeilen gehalten, in
# denen nichts wartet.
_GEPLANT: set[str] = set()
_PLANUNGSSCHLOSS = threading.Lock()


def laufzeit_setzen(
    schleife: asyncio.AbstractEventLoop | None, http: httpx.AsyncClient | None
) -> None:
    global _SCHLEIFE, _HTTP
    _SCHLEIFE = schleife
    _HTTP = http


def http_client() -> httpx.AsyncClient | None:
    return _HTTP


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


# ── Zustand ──────────────────────────────────────────────────────────────
#
# Das Arbeitsgedaechtnis der Schleife. Es lag frueher ausschliesslich in der
# lokalen Variablen `provider_messages` und verschwand mit dem Generator — der
# eigentliche Grund, warum eine Fortsetzung unmoeglich war.


def leerer_zustand(
    provider_messages: list[dict], *, request_id: str, user_message_id: str | None = None
) -> dict:
    return {
        "provider_messages": provider_messages,
        # Die Nachricht des Menschen, die diesen Lauf ausgeloest hat. Sie steht
        # hier, weil die Oberflaeche ihre Blase optimistisch zeichnet, bevor der
        # Server eine Kennung vergeben hat — ohne diesen Wert traegt sie fuer
        # immer eine erfundene, und die Anhaenge dieser Frage finden ihre
        # Nachricht nicht.
        "user_message_id": user_message_id,
        # Budgetzaehler ueber den **ganzen** Lauf, nicht je Segment. Sonst
        # bekaeme ein Lauf durch jede Bestaetigung ein frisches Budget geschenkt.
        "rounds": 0,
        "write_rounds": 0,
        "tool_calls": 0,
        # Die Anfragekennung des laufenden Segments. Jede Fortsetzung bekommt
        # eine eigene: sie ist der Idempotenzschluessel der Nachricht und der
        # Verbrauchszeile.
        "request_id": request_id,
        "usage_event_id": None,
        # Was gerade auf einen Menschen wartet: die Vorschlaege der geparkten
        # Runde. Beim Aufwecken wird daraus die Meldung, wie der Mensch
        # entschieden hat.
        "pending": None,
        # Welcher Lesewerkzeugaufruf wie oft lief — Grundlage der
        # Schleifenerkennung. Wird ueber eine Rueckfrage hinweg vererbt, sonst
        # zaehlt jede Klaerung wieder bei null.
        "tool_signatures": {},
        # Der Guardian-Rahmen, wenn ein Vorfall diesen Lauf geweckt hat: ein
        # Woerterbuch aus `server_id`, `incident_id` und `incident_created_at`.
        # `None` heisst: ein Mensch hat getippt, es gelten die gewoehnlichen
        # Regeln.
        #
        # Er steht im Zustand und nicht in einer Spalte, weil er genau das ist,
        # was dieser Zustand beschreibt — "was zwischen zwei Laeufen ueberlebt".
        # Eine Fortsetzung nach einer Bestaetigung Stunden spaeter muss unter
        # denselben Verschaerfungen laufen wie der erste Zug; haenge man ihn an
        # den Aufruf, ginge er bei der ersten Fortsetzung verloren.
        "guardian": None,
        # Welche Guardian-Vorfaelle diesem Lauf zur Erwaehnung mitgegeben
        # wurden. Erst wenn der Lauf endet, gelten sie als besprochen — bricht
        # er ab, bleibt der Vorfall vorgemerkt und kommt beim naechsten Mal
        # wieder.
        "guardian_briefed": [],
        # Ob der Ergebnisbericht dieses Heilungslaufs schon hinausgegangen ist.
        # Der Abschluss wird aus zwei Richtungen gerufen — vom regulaeren Ende
        # und vom Waechter fuer den bereits beendeten Lauf —, und beide koennen
        # denselben Lauf treffen, wenn ein Mensch mitten in eine Heilung
        # hineinschreibt. Zwei Mails zu demselben Vorfall waeren schlimmer als
        # eine ausgebliebene Wiederholung: der Betreiber weiss dann nicht, ob es
        # zwei Vorgaenge waren.
        "guardian_berichtet": False,
        # Wieviele Zeichen Kontext das Modell dieses Laufs traegt, oder None,
        # wenn der Katalog es nicht kennt. Steht im Zustand und nicht in einer
        # Konstante, weil jede Fortsetzung mit demselben Budget rechnen muss wie
        # der erste Zug — auch wenn der Betreiber inzwischen ein anderes Modell
        # eingestellt hat.
        "context_chars": None,
    }


def zustand_lesen(run: AiRun) -> dict:
    """Das Arbeitsgedaechtnis eines Laufs — und die Marke, wenn es fehlt.

    Kein ``state_json`` heisst: frischer Lauf, der leere Zustand ist die
    Wahrheit. Ein **vorhandenes, aber unlesbares** ist etwas anderes, und der
    leere Zustand ist dafuer die falsche Antwort: in ihm steht keine Rolle,
    kein Guardian- und kein Aufgabenrahmen. Ein Worker- oder Heilungslauf liefe
    damit als gewoehnlicher Chatlauf weiter — mit dem vollen Werkzeugsatz, ohne
    Serverbindung und ohne jemanden, der mitliest. Der Verlust des Rahmens ist
    die gefaehrliche Richtung, nicht die sichere (dieselbe Ueberlegung wie bei
    `ai_stream_service.guardian_aus_zustand`).

    Geworfen wird trotzdem nicht: diese Funktion hat auch reine Anzeigepfade
    als Aufrufer, und die sollen einen kaputten Zustand zeigen koennen statt
    mit 500 zu antworten. Stattdessen traegt der Rueckfall die Marke
    ``unlesbar``; wer damit **arbeiten** will, steigt daran aus.
    """
    if not run.state_json:
        return leerer_zustand([], request_id=str(uuid4()))
    try:
        geladen = json.loads(run.state_json)
    except (TypeError, ValueError):
        logger.warning("Laufzustand unlesbar run_id=%s", run.id)
        return _unlesbarer_zustand()
    if not isinstance(geladen, dict):
        logger.warning("Laufzustand ist kein Woerterbuch run_id=%s", run.id)
        return _unlesbarer_zustand()
    grund = leerer_zustand([], request_id=str(uuid4()))
    grund.update(geladen)
    return grund


def _unlesbarer_zustand() -> dict:
    zustand = leerer_zustand([], request_id=str(uuid4()))
    zustand["unlesbar"] = True
    return zustand


def zustand_schreiben(run: AiRun, zustand: dict) -> None:
    run.state_json = json.dumps(zustand, ensure_ascii=True, separators=(",", ":"))
    run.updated_at = _jetzt()


def arbeitsspeicher_leeren(run: AiRun, zustand: dict | None = None) -> dict:
    """Wirft die Provider-Nachrichten eines **beendeten** Laufs weg.

    ``provider_messages`` ist das Arbeitsgedächtnis der Schleife, und darin
    steht der entschlüsselte Gedächtnisblock des Benutzers im Klartext —
    `build_provider_messages` hängt ihn als eigene Nachricht an. `state_json`
    ist eine gewöhnliche Textspalte, und es gab keinen Weg, der sie je wieder
    leerte: weder `forget_memory`, das die verschlüsselte Zeile in
    `ai_memory_entries` entfernt, noch das Leeren des Chatverlaufs. Ein Eintrag,
    den der Benutzer nur über Profil > Memory hinterlegt und später gelöscht
    hat, stand danach dauerhaft im Klartext daneben — die Verschlüsselung mit
    scope-gebundener AAD war gegen einen Datenbankleser damit wirkungslos, und
    "wer sein Gedächtnis löschen will, will es ganz löschen" traf nicht zu.

    **Nur bei einem Endzustand.** Ein geparkter Lauf (`waiting_*`) braucht seine
    Nachrichten für die Fortsetzung nach der Bestätigung; ihm den Speicher zu
    nehmen hieße, ihn zu töten. Die Prüfung steht deshalb hier drin und nicht
    bei den Aufrufern — es sind drei, und einer würde sie irgendwann vergessen.

    Gibt den Zustand zurück, damit der Aufrufer mit **demselben** Wörterbuch
    weiterarbeitet. Schriebe er danach seine eigene, noch volle Fassung zurück
    (die Nachbereitung tut genau das, wenn sie eine Berichtsmarke setzt), wäre
    der Klartext wieder da.
    """
    if zustand is None:
        zustand = zustand_lesen(run)
    if run.status not in BEENDET:
        return zustand
    zustand["provider_messages"] = []
    zustand_schreiben(run, zustand)
    return zustand


# ── Lebenslauf ───────────────────────────────────────────────────────────


def lauf_anlegen(
    db: Session,
    *,
    conversation_id: str,
    user_id: int,
    provider_id: int,
    message_id: str,
    reasoning: bool,
    zustand: dict,
    reasoning_effort: str | None = None,
    last_server_id: int | None = None,
) -> AiRun:
    run = AiRun(
        id=str(uuid4()),
        conversation_id=conversation_id,
        user_id=user_id,
        provider_id=provider_id,
        status="running",
        message_id=message_id,
        reasoning=reasoning,
        reasoning_effort=reasoning_effort,
        last_server_id=last_server_id,
    )
    zustand_schreiben(run, zustand)
    db.add(run)
    db.flush()
    return run


def vorgaenger_abloesen(db: Session, *, conversation_id: str) -> dict:
    """Beendet offene Laeufe derselben Unterhaltung — und gibt weiter, was zaehlt.

    Schreibt der Benutzer eine neue Nachricht, statt einen Vorschlag zu
    bestaetigen, hat er die Richtung gewechselt. Ein alter Lauf, der Minuten
    spaeter durch einen nachtraeglichen Klick aufwacht und in denselben Chat
    weiterschreibt, waere ein Geist. Der **Vorschlag** bleibt davon unberuehrt
    und ausfuehrbar; nur aufwecken wird er niemanden mehr.

    Ein Lauf im Zustand ``waiting_user`` ist aber etwas anderes: der hat nicht
    aufgegeben, sondern **gefragt** — und die neue Nachricht ist die Antwort.
    Ihn "abgebrochen, ueberholt" zu nennen waere im Protokoll schlicht falsch.
    Er gilt als abgeschlossen, Grund ``answered``.

    Und er vererbt seine **Schleifensignaturen**. Ohne das setzt jede Rueckfrage
    die Schleifenerkennung zurueck: das Modell liest dieselbe Datei, fragt etwas,
    liest sie nach der Antwort erneut — und wieder von vorn gezaehlt. Die
    Rundenbudgets erbt der Nachfolger dagegen **nicht**: eine Klaerung ist kein
    Fehler des Benutzers, und ihn dafuer mit einem halben Budget zu bestrafen
    waere die falsche Lehre.

    Abloesen **haelt an**, es etikettiert nicht nur um. Bisher wurde nur die
    Zeile umgeschrieben; die asyncio-Aufgabe des Vorgaengers lief weiter, fuehrte
    Werkzeuge aus, legte Vorschlaege in eine Unterhaltung, die inzwischen dem
    Nachfolger gehoerte, und meldete sich am Ende als 'completed' zurueck — der
    Geist, den dieser Docstring auszuschliessen behauptet. Zugestellt wird der
    Abbruch erst am naechsten Haltepunkt der Ereignisschleife, also nach dem
    Commit dieses Aufrufers und nie mitten in dessen Transaktion.
    """
    betroffen = (
        db.query(AiRun)
        .filter(
            AiRun.conversation_id == conversation_id,
            AiRun.status.in_(("running", *WARTEND)),
        )
        .order_by(AiRun.created_at.asc())
        .all()
    )
    erbe: dict = {}
    for run in betroffen:
        if run.status == "waiting_user":
            run.status = "completed"
            run.stop_reason = "answered"
            signaturen = zustand_lesen(run).get("tool_signatures") or {}
            if isinstance(signaturen, dict):
                erbe.update(signaturen)
        else:
            run.status = "cancelled"
            run.stop_reason = "superseded"
            # Der Status allein hat noch nie etwas gestoppt.
            aufgabe_abbrechen(run.id)
        # Beendet heißt beendet: das Arbeitsgedächtnis wird nicht mehr
        # gebraucht und trägt den entschlüsselten Gedächtnisblock.
        arbeitsspeicher_leeren(run)
        run.updated_at = _jetzt()
    return erbe


def letzter_serverbezug(db: Session, *, conversation_id: str) -> int | None:
    """Um welchen Server ging es in dieser Unterhaltung zuletzt?

    Gefragt wird beim Anlegen des naechsten Laufs. "Und jetzt starte ihn neu"
    nennt keinen Server; gemeint ist der aus der Frage davor. Ohne dieses Erbe
    endete der Bezug an jeder Nachrichtengrenze, und ein Chat ueber genau einen
    Server haette abwechselnd Bezug und keinen.

    Bewusst der juengste Lauf **mit** einem Bezug und nicht der juengste Lauf
    ueberhaupt: dazwischen liegen Laeufe, die gar kein Werkzeug angefasst haben
    ("danke!"), und die duerfen ein laufendes Thema nicht abraeumen.

    Ebenso bewusst ohne Ruecksicht auf den Status. Ein abgebrochener oder
    fehlgeschlagener Lauf hat trotzdem in einen Server hineingesehen — der
    Bezug ist damit belegt, unabhaengig davon, wie der Lauf ausging.

    Zeigt der Bezug auf einen inzwischen geloeschten Server, steht dort NULL
    (`ON DELETE SET NULL`), und die Suche geht von selbst einen Lauf weiter
    zurueck. Ein Recht wird hier trotzdem nicht vererbt: was der Benutzer sehen
    darf, entscheidet weiterhin die Stelle, die den Bezug benutzt.
    """
    zeile = (
        db.query(AiRun.last_server_id)
        .filter(
            AiRun.conversation_id == conversation_id,
            AiRun.last_server_id.isnot(None),
        )
        .order_by(AiRun.created_at.desc())
        .first()
    )
    return zeile[0] if zeile is not None else None


def serverbezug_merken(db: Session, *, run_id: str | None, server_id: int | None) -> None:
    """Haelt am Lauf fest, welchen Server er nachweislich angefasst hat.

    Nur vorwaerts: ein bereits gesetzter Bezug wird ueberschrieben, ein
    vorhandener aber nicht durch ``None`` geloescht. Eine Runde ohne
    serverbezogenes Werkzeug sagt nichts ueber das Thema aus — sie ist stumm,
    nicht widersprechend.

    ``run_id is None`` kommt in der Testsuite vor, wo Segmente ohne Lauf
    ausgefuehrt werden. Dann gibt es nichts zu merken.
    """
    if run_id is None or server_id is None:
        return
    run = db.get(AiRun, run_id)
    if run is None or run.last_server_id == server_id:
        return
    run.last_server_id = server_id
    run.updated_at = _jetzt()


def aktiver_lauf(
    db: Session,
    *,
    user_id: int,
    kind: str | None = None,
    conversation_id: str | None = None,
) -> AiRun | None:
    """Der juengste Lauf, der noch etwas vorhat. Fuer Glocke und Wiederanschluss.

    ``kind`` schraenkt auf ein Fenster ein — und **jeder** Aufrufer, der eine
    Entscheidung darauf stuetzt, muss es setzen, seit es mehr als eines gibt.

    Ohne die Einschraenkung beantwortet diese Funktion "laeuft irgendwo etwas
    fuer diesen Menschen?", und darauf gibt es keine brauchbare Reaktion mehr.
    Der Chat haengte sich an den Reparaturlauf und zeichnete dessen Verlauf in
    das Fenster des Menschen — genau das Symptom, dessentwegen die Fenster
    getrennt wurden. Und umgekehrt liesse ein geparkter Reparaturlauf keine
    weitere Reparatur und keinen faelligen Auftrag mehr beginnen, auf **allen**
    Anlagen dieses Benutzers.

    Gefragt wird ueber die **Art** und nicht ueber eine Kennung. Fuer
    ``primary`` und ``guardian`` ist beides dasselbe (der partielle Index
    `uq_ai_conversations_user_kind` erzwingt eine Zeile je Art) — aber wer
    nach der Art fragt, muss die Zeile nicht vorher anlegen lassen. Fuer
    ``worker`` gilt das **nicht** mehr: es gibt beliebig viele Fenster, und
    diese Funktion liefert nur den juengsten offenen Lauf darueber. Wer einen
    bestimmten Auftrag meint, fragt ueber dessen ``conversation_id``
    (`ai_worker_service.aktive_worker` zaehlt sie alle).

    ``None`` bleibt erlaubt und heisst weiterhin "ueber alle Fenster". Es gibt
    eine Frage, die so gestellt gehoert: die Glocke will wissen, ob ueberhaupt
    etwas laeuft. Wer sie stellt, muss anschliessend selbst entscheiden, wohin
    er zeigt — ein stundenlang geparkter Worker (``waiting_wake``) zaehlt hier
    bewusst als "etwas laeuft": er ist ein offener Auftrag, und die Antwort
    traegt ``kind``, damit die Oberflaeche ihn als solchen zeigt statt als
    haengenden Chat.
    """
    query = db.query(AiRun).filter(
        AiRun.user_id == user_id,
        AiRun.status.in_(("running", *WARTEND)),
    )
    if conversation_id is not None:
        # Der Weg fuer Worker-Fenster: die Art ist dort mehrdeutig, die
        # Kennung nicht. Der user_id-Filter oben bleibt die Besitzpruefung —
        # ein fremdes Fenster liefert schlicht nichts.
        query = query.filter(AiRun.conversation_id == conversation_id)
    if kind is not None:
        from models import AiConversation

        query = query.join(
            AiConversation, AiConversation.id == AiRun.conversation_id
        ).filter(AiConversation.kind == kind)
    return query.order_by(AiRun.created_at.desc()).first()


def eigener_lauf(db: Session, run_id: str, user: User) -> AiRun | None:
    return (
        db.query(AiRun)
        .filter(AiRun.id == run_id, AiRun.user_id == user.id)
        .first()
    )


def eigene_laeufe_abbrechen(
    *, user_id: int, run_ids: set[str], grund: str = "user_abort"
) -> int:
    """Bricht ausschließlich aktive, dem Benutzer gehörende Läufe ab.

    Der Aufrufer liefert nur Kennungen, die er selbst im Sitzungsfluss erhalten
    hat. Die Besitzprüfung bleibt trotzdem im Service: ein WebSocket oder eine
    künftige Audio-Integration darf nie über eine lokale Liste fremde Läufe
    abbrechen können.
    """

    if not run_ids:
        return 0
    beendet_ohne_task: list[str] = []
    abgebrochen = 0
    with SessionLocal() as db:
        benutzer = db.get(User, user_id)
        if benutzer is None:
            return 0
        for run_id in run_ids:
            run = eigener_lauf(db, run_id, benutzer)
            if run is None or run.status in BEENDET:
                continue
            run.status = "cancelled"
            run.stop_reason = grund
            run.wake_at = None
            run.message_id = None
            arbeitsspeicher_leeren(run)
            run.updated_at = _jetzt()
            if not aufgabe_abbrechen(run.id):
                beendet_ohne_task.append(run.id)
            abgebrochen += 1
        db.commit()
    # Läufe in einer aktiven asyncio-Aufgabe veröffentlichen ihren Endzustand
    # in deren bestehenden Cancellation-Pfad. Wartende Läufe haben keine Aufgabe
    # und müssen ihre Beobachter hier zuverlässig wecken.
    for run_id in beendet_ohne_task:
        _broker_abschliessen(run_id, status="cancelled", stop_reason=grund)
    return abgebrochen


def darf_fortsetzen(db: Session, run: AiRun) -> bool:
    """Wartet dieser Lauf noch auf Vorschlaege, die niemand entschieden hat?

    Ein Lauf wird erst geweckt, wenn **alle** Vorschlaege seiner Runde
    entschieden sind. Sonst liefe er los, waehrend die zweite Karte noch offen
    im Chat steht — und meldete eine halbe Arbeit als fertig.
    """
    if run.status == "waiting_wake":
        # Der Zustand erlaubt das Wecken immer — **ob** geweckt wird, wissen
        # nur die Aufrufer: der Takt filtert auf faellige `wake_at`, und
        # `finish_lifecycle_task` ist selbst das Ereignis, auf das gewartet
        # wurde. Eine Fristpruefung hier waere eine zweite Uhr.
        return True
    if run.status != "waiting_confirmation":
        return False
    offen = (
        db.query(AiActionProposal.id)
        .filter(
            AiActionProposal.run_id == run.id,
            AiActionProposal.status.in_(("proposed", "confirmed")),
        )
        .count()
    )
    return offen == 0


# ── Planung ──────────────────────────────────────────────────────────────


def _platz_belegen(run_id: str) -> bool:
    """Belegt den Planungsplatz eines Laufs. ``False`` heisst: einer war schneller."""
    with _PLANUNGSSCHLOSS:
        # Ein Platz gilt als frei, sobald die Aufgabe fertig ist. Der
        # done_callback raeumt ihn erst einen Schleifendurchlauf spaeter
        # (`call_soon`), und eine Bestaetigung, die genau dazwischen ankommt —
        # POST /api/ai/actions/{id}/execute ist ein synchrones `def` und laeuft
        # nebenlaeufig im Threadpool —, saehe sonst ein "schon unterwegs", das
        # niemand mehr einloest: der Lauf steht dann fuer immer auf 'running'.
        #
        # Zwischen dieser Pruefung und dem Eintrag in `_AUFGABEN` ist `aufgabe`
        # None; der Platz bleibt dort belegt, das eigentliche Fenster also zu.
        aufgabe = _AUFGABEN.get(run_id)
        if run_id in _GEPLANT and not (aufgabe is not None and aufgabe.done()):
            return False
        _GEPLANT.add(run_id)
        return True


def _platz_freigeben(run_id: str) -> None:
    with _PLANUNGSSCHLOSS:
        _GEPLANT.discard(run_id)


def _aufgabe_planen(run_id: str) -> bool:
    """Plant ein Segment auf der Ereignisschleife der Anwendung.

    Rueckgabe ``False`` heisst: hier laeuft keine Anwendung (Testsuite,
    Verwaltungsskript). Der Aufrufer entscheidet dann selbst, ob er das Segment
    abwartet — er bekommt jedenfalls keine stille Nichtausfuehrung.

    Der Aufruf ist **thread-sicher**: die Bestaetigung kommt aus einem
    gewoehnlichen, synchronen FastAPI-Endpunkt, der in einem Arbeitsthread
    laeuft. ``run_coroutine_threadsafe`` ist die Bruecke zurueck auf die
    Schleife, auf der der HTTP-Client des Anbieters lebt.

    Und der Platz wird **vor** der Uebergabe belegt, nicht danach. Frueher
    fragte diese Stelle ``_AUFGABEN`` — ein Verzeichnis, in das erst die
    Koroutine ``_starten`` eintraegt, also erst spaeter und in einem anderen
    Thread. Zwei gleichzeitige Bestaetigungen sahen deshalb beide "nichts
    unterwegs", und der Kommentar unten beschrieb ein Ziel, das die Pruefung
    gar nicht erreichen konnte.
    """
    schleife = _SCHLEIFE
    if schleife is None or schleife.is_closed():
        return False
    if not _platz_belegen(run_id):
        # Schon unterwegs. Zwei Segmente desselben Laufs gleichzeitig waeren
        # zwei Schreiber auf einem Zustand.
        return True

    from services.ai_stream_service import segment_ausfuehren

    async def _starten() -> None:
        aufgabe = asyncio.ensure_future(segment_ausfuehren(run_id))
        _AUFGABEN[run_id] = aufgabe

        def _fertig(_: asyncio.Task) -> None:
            _AUFGABEN.pop(run_id, None)
            # Erst mit dem Ende der Arbeit ist der Platz wieder frei. Frueher
            # freigegeben waere die naechste Planung wieder ein zweiter
            # Schreiber auf demselben Zustand.
            _platz_freigeben(run_id)

        aufgabe.add_done_callback(_fertig)

    try:
        laufende_schleife = asyncio.get_running_loop()
    except RuntimeError:
        laufende_schleife = None
    try:
        if laufende_schleife is schleife:
            # Schon auf der richtigen Schleife (der Streamendpunkt selbst).
            asyncio.ensure_future(_starten())
        else:
            asyncio.run_coroutine_threadsafe(_starten(), schleife)
    except RuntimeError:
        # Die Schleife ist zwischen Pruefung und Uebergabe gestorben. Den Platz
        # zurueckgeben — sonst gaelte dieser Lauf fuer immer als "unterwegs"
        # und liesse sich nie wieder wecken.
        _platz_freigeben(run_id)
        return False
    return True


def aufgabe_abbrechen(run_id: str) -> bool:
    """Haelt die laufende Aufgabe eines Laufs an — aus jedem Thread heraus.

    Das Gegenstueck zum Planen und die fehlende Haelfte des Abloesens. Ein Lauf,
    dessen Zeile auf 'cancelled' steht, dessen Aufgabe aber weiterarbeitet, ist
    der schlechteste aller Zustaende: er fuehrt Werkzeuge aus, legt Vorschlaege
    in eine Unterhaltung, die dem Nachfolger gehoert, und antwortet **nach** der
    neuen Frage des Benutzers.

    Der Abbruch ist eine Bitte, kein Befehl — asyncio stellt ihn erst am
    naechsten Haltepunkt zu. Deshalb steht er nicht allein: ein Endzustand
    bleibt endgueltig (``_lauf_abschliessen``), und vor einer Schreibrunde wird
    nachgesehen, wem der Lauf noch gehoert (``segment_ausfuehren``).
    """
    aufgabe = _AUFGABEN.get(run_id)
    if aufgabe is None or aufgabe.done():
        return False
    schleife = _SCHLEIFE
    try:
        laufende_schleife = asyncio.get_running_loop()
    except RuntimeError:
        laufende_schleife = None
    if schleife is None or laufende_schleife is schleife:
        aufgabe.cancel()
    else:
        # ``Task.cancel`` gehoert der Schleife, nicht dem Arbeitsthread, aus dem
        # eine Bestaetigung oder eine neue Nachricht hereinkommt.
        schleife.call_soon_threadsafe(aufgabe.cancel)
    return True


def lauf_starten(run_id: str) -> bool:
    return _aufgabe_planen(run_id)


def lauf_fortsetzen(db: Session, *, run_id: str) -> bool:
    """Weckt einen geparkten Lauf, wenn seine Vorschlaege entschieden sind.

    Das ist die Antwort auf "die KI arbeitet nach dem Bestaetigen nicht weiter".
    Gerufen wird sie aus ``execute_proposal`` — also genau in dem Moment, in dem
    der Mensch seinen Teil getan hat. Seit ``waiting_wake`` auch vom Takt, von
    `finish_lifecycle_task` und vom Startabgleich: **alle** Weckwege laufen
    durch diese eine Stelle, deshalb sitzt hier auch die Rechte-Neupruefung der
    Worker — kein Aufrufer kann sie vergessen.
    """
    run = db.get(AiRun, run_id)
    if run is None or not darf_fortsetzen(db, run):
        return False
    if not _wecken_erlaubt(db, run):
        return False
    # Den Vorzustand merken: der no_runtime-Rueckfall unten muss **ihn**
    # wiederherstellen. Ein gewecktes waiting_wake, das auf
    # waiting_confirmation zurueckfiele, waere eine Zustandsluege — es hat
    # null offene Vorschlaege, und jeder spaetere Bestaetigungspfad weckte es
    # faelschlich.
    vorher = run.status
    run.status = "running"
    run.stop_reason = None
    # Die Frist ist eingeloest — ob die Uhr geweckt hat oder ein Ereignis
    # frueher kam. Stehenbliebe sie, weckte der Takt denselben Lauf im
    # naechsten Durchlauf erneut.
    run.wake_at = None
    run.updated_at = _jetzt()
    db.commit()
    # Erst melden, dann planen — und beides **vor** der Antwort auf den
    # Bestaetigungsaufruf. Die Oberflaeche haengt sich unmittelbar danach an;
    # saehe sie den Lauf dort noch als "geparkt", wuerde sie sofort wieder
    # aufhoeren und die Fortsetzung verpassen.
    _broker_melden(run.id, status="running", stop_reason=None)
    if not _aufgabe_planen(run.id):
        # Keine Anwendung, also niemand, der das Segment ausfuehren koennte. Der
        # Lauf faellt in **seinen** Wartezustand zurueck, statt als "laufend"
        # liegen zu bleiben und beim naechsten Start als abgebrochen zu gelten.
        run.status = vorher
        run.updated_at = _jetzt()
        db.commit()
        # Die Meldung oben zuruecknehmen, sonst wartet die Oberflaeche auf eine
        # Fortsetzung, die nie anlaeuft.
        _broker_melden(run.id, status=vorher, stop_reason="no_runtime")
        return False
    return True


def _wecken_erlaubt(db: Session, run: AiRun) -> bool:
    """Die Rechte-Neupruefung beim Wecken eines Worker-Laufs.

    Dasselbe Muster wie bei den faelligen Aufgaben (`aufgabenlauf_starten`):
    zwischen Parken und Wecken koennen Stunden liegen, und ein Recht, das
    inzwischen entzogen wurde, gilt. Ein Lauf hat aber kein ``enabled`` —
    bei Wegfall endet er ehrlich als ``cancelled`` mit benanntem Grund, und
    der Mensch erfaehrt es ueber die Meldestelle.

    Nur Worker-Fenster: die uebrigen Laeufe wecken Menschen ueber
    Bestaetigungen, und deren Rechte prueft der Vorschlagspfad ohnehin je
    Aufruf. Spaete Imports — dieser Dienst ist bewusst importarm.
    """
    from models import AiConversation, User
    from services import permission_service

    fenster = db.get(AiConversation, run.conversation_id)
    if fenster is None or fenster.kind != "worker":
        return True

    user = db.get(User, run.user_id)
    grund: str | None = None
    if user is None or not user.is_active:
        grund = "benutzer_inaktiv"
    elif not permission_service.has_global_permission(db, user, "ai.chat.use"):
        grund = "kein_chatrecht"
    elif not permission_service.has_global_permission(db, user, "ai.background.use"):
        grund = "berechtigung_entzogen"
    if grund is None:
        return True

    run.status = "cancelled"
    run.stop_reason = grund
    run.wake_at = None
    arbeitsspeicher_leeren(run)
    run.updated_at = _jetzt()
    db.commit()
    _broker_melden(run.id, status="cancelled", stop_reason=grund)
    logger.info("Worker-Lauf nicht geweckt (run_id=%s): %s", run.id, grund)
    if user is not None and grund != "benutzer_inaktiv":
        # Der Mensch soll erfahren, dass sein Auftrag nicht weiterlief — als
        # Meldung, nie als stiller Schwund. Ein Fehler hier darf das Wecken
        # der uebrigen Laeufe nicht mitnehmen.
        try:
            from services import ai_meldestelle

            rahmen = zustand_lesen(run).get("worker") or {}
            ai_meldestelle.melden(
                db,
                user=user,
                text=(
                    f'Der Auftrag "{rahmen.get("titel") or "Auftrag"}" wurde '
                    "angehalten: die Berechtigung für Hintergrund-Worker "
                    "fehlt inzwischen."
                ),
                kanal=str(rahmen.get("kanal") or "chat"),
                worker_id=run.conversation_id,
                worker_titel=rahmen.get("titel"),
            )
        except Exception:
            db.rollback()
            logger.warning("Meldung zum entzogenen Recht fehlgeschlagen run_id=%s", run.id)
    return False


#: Wieviele geparkte Laeufe ein Takt-Durchlauf hoechstens weckt — je Handgriff.
#: Dieselbe Ueberlegung wie MAX_AUFGABEN_JE_DURCHLAUF bei den Aufgaben: jeder
#: geweckte Lauf ist ein Anbieteraufruf, und der Takt kommt jede Minute wieder.
MAX_WECKEN_JE_DURCHLAUF = 20


def faellige_wecken(db: Session) -> int:
    """Der Takt-Handgriff: weckt Laeufe, deren ``wake_at`` verstrichen ist.

    ``wake_at IS NULL`` heisst "nur ein Ereignis weckt" und wird hier nie
    angefasst. Ohne Laufzeit passiert gar nichts — sonst fiele jeder Lauf in
    den no_runtime-Rueckfall und produzierte je Takt zwei Commits und zwei
    Broker-Meldungen Rauschen.

    Ein zu spaetes Wecken (Panel war aus) wird bewusst nicht uebersprungen,
    anders als bei den Aufgaben mit ihrem MAX_VERZUG: eine Aufgabe hat den
    naechsten Termin, ein geparkter Lauf hat nur diesen einen. Der Lauf sieht
    die Uhr im Lageblock und beurteilt selbst, was von seinem Plan noch gilt.
    """
    if http_client() is None:
        return 0
    faellige = (
        db.query(AiRun)
        .filter(AiRun.status == "waiting_wake", AiRun.wake_at.isnot(None),
                AiRun.wake_at <= _jetzt())
        .order_by(AiRun.wake_at.asc())
        .limit(MAX_WECKEN_JE_DURCHLAUF)
        .all()
    )
    geweckt = 0
    for run in faellige:
        try:
            if lauf_fortsetzen(db, run_id=run.id):
                geweckt += 1
        except Exception:
            db.rollback()
            logger.warning("Wecken fehlgeschlagen run_id=%s", run.id)
    return geweckt


def verpuffte_bestaetigungen_wecken(db: Session) -> int:
    """Läufe auf ``waiting_confirmation``, die nichts Offenes mehr haben.

    Der Zwilling von `desktop_job_service._verpuffte_wecken`, und er entsteht
    aus demselben Rennen. Die Karte steht im Chat, sobald die Schreibrunde den
    Vorschlag veröffentlicht hat — bis der Lauf **parkt**, liegen aber noch
    eine Schlussrunde und das ganze `_finalize_stream` dazwischen. Bestätigt
    ein Mensch in dieser Spanne, ruft `execute_proposal` `lauf_fortsetzen`, und
    `darf_fortsetzen` weist es ab, weil der Lauf noch auf 'running' steht. Der
    Weckruf ist damit weg, danach weckt ihn niemand mehr, und der Lauf parkte
    für immer — mit einer ausgeführten Aktion, über die er nie berichtet hat.

    Nachgeholt wird es hier und nicht im geparkt-Zweig selbst: dort läge
    zwischen dem Park-Commit und dem Ende der asyncio-Aufgabe ein zusätzlicher
    `await`, und genau in dem Fenster fände der Weckruf den Segmentplatz belegt
    (`_platz_belegen`) — der Lauf stünde danach dauerhaft auf 'running', also
    schlimmer als vorher. Im Takt ist die Aufgabe des Laufs längst beendet.

    Ausgewählt wird über dieselbe Frage, die `darf_fortsetzen` stellt, nur in
    SQL: kein Vorschlag mehr auf 'proposed' oder 'confirmed'. Das trifft neben
    dem Rennen einen zweiten Fall, und der gehört ausdrücklich dazu — endet
    eine Reparaturkampagne, entwertet `ai_guardian_repair_service` die offene
    Freigabe und setzt ihren Vorschlag auf 'expired', ohne den Lauf anzufassen.
    Auch der muss aufwachen: sonst zählte er über `aktiver_lauf` für immer als
    beschäftigt und ließe keine weitere Heilung dieses Benutzers mehr beginnen.
    """
    if http_client() is None:
        return 0
    # Die Auswahl gehört **vor** die Obergrenze und damit in die Abfrage: der
    # gewöhnliche Fall ist ein Lauf, der völlig zu Recht auf einen Klick
    # wartet, und zwanzig davon verdeckten sonst jeden, der wirklich hängt.
    # Korreliertes EXISTS wie in `ai_guardian_service` — ein `NOT IN` über die
    # nullbare `run_id` liefert, sobald ein Vorschlag ohne Lauf dabei ist,
    # überhaupt keine Zeile mehr.
    offene_vorschlaege = (
        db.query(AiActionProposal.id)
        .filter(
            AiActionProposal.run_id == AiRun.id,
            AiActionProposal.status.in_(("proposed", "confirmed")),
        )
        .exists()
    )
    schlafend = (
        db.query(AiRun)
        .filter(AiRun.status == "waiting_confirmation", ~offene_vorschlaege)
        .order_by(AiRun.updated_at.asc())
        .limit(MAX_WECKEN_JE_DURCHLAUF)
        .all()
    )
    geweckt = 0
    for run in schlafend:
        try:
            if lauf_fortsetzen(db, run_id=run.id):
                geweckt += 1
                logger.info("Verpuffte Bestaetigung nachgeholt run_id=%s", run.id)
        except Exception:
            db.rollback()
            logger.warning("Verpuffte Bestaetigung nicht nachgeholt run_id=%s", run.id)
    return geweckt


def _broker_melden(run_id: str, *, status: str, stop_reason: str | None) -> None:
    """Kanal eroeffnen und den Laufzustand melden — aus jedem Thread heraus.

    Der Vermittler vertraegt nur **einen** Schreiber, und der sitzt auf der
    Ereignisschleife (`ai_run_broker.veroeffentlichen`). `lauf_fortsetzen`
    laeuft aber im Threadpool eines synchronen Bestaetigungs-Endpunkts; ein
    direkter Aufruf von dort mutierte `_KANAELE` und die Warteschlangen,
    waehrend die Schleife dieselben Strukturen bedient — im Rennen gingen
    Wakeups des `run: running`-Ereignisses verloren. Derselbe Handschlag wie
    in `aufgabe_abbrechen`: auf der Schleife direkt, sonst per
    ``call_soon_threadsafe``. Die Reihenfolge bleibt gewahrt — Rueckrufe
    laufen in Einreihungsreihenfolge, also meldet „running" vor allem, was
    die geplante Aufgabe danach veroeffentlicht.
    """
    from services import ai_run_broker

    def _senden() -> None:
        ai_run_broker.eroeffnen(run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "run",
            {"run_id": run_id, "status": status, "stop_reason": stop_reason},
        )

    schleife = _SCHLEIFE
    try:
        laufende_schleife = asyncio.get_running_loop()
    except RuntimeError:
        laufende_schleife = None
    # `is_closed` wie in `_platz_belegen`: eine gemerkte, aber geschlossene
    # Schleife (Shutdown, Testsuite) bedient nichts mehr — dann gibt es auch
    # keinen zweiten Schreiber, und der direkte Weg ist gefahrlos. Ohne die
    # Pruefung riss `call_soon_threadsafe` mit "Event loop is closed" das
    # Wecken ab, obwohl die Datenbankarbeit laengst getan war.
    if schleife is None or laufende_schleife is schleife or schleife.is_closed():
        _senden()
    else:
        schleife.call_soon_threadsafe(_senden)


def _broker_abschliessen(run_id: str, *, status: str, stop_reason: str | None) -> None:
    """Meldet und schließt einen wartenden Lauf auf der Broker-Ereignisschleife."""

    from services import ai_run_broker

    def _senden() -> None:
        ai_run_broker.eroeffnen(run_id)
        ai_run_broker.veroeffentlichen(
            run_id,
            "run",
            {"run_id": run_id, "status": status, "stop_reason": stop_reason},
        )
        ai_run_broker.beenden(run_id)

    schleife = _SCHLEIFE
    try:
        laufende_schleife = asyncio.get_running_loop()
    except RuntimeError:
        laufende_schleife = None
    if schleife is None or laufende_schleife is schleife or schleife.is_closed():
        _senden()
    else:
        schleife.call_soon_threadsafe(_senden)


# ── Unbeaufsichtigter Laufstart ──────────────────────────────────────────
#
# Guardian-Heilung und faellige Auftraege bauen denselben Laufstart nach, den
# sonst der Streamendpunkt fuehrt. Ein gemeinsames Geruest haben die beiden
# bewusst nicht (siehe `aufgabenlauf_starten`) — hier wohnen nur die zwei
# Segmente, die in beiden Aufrufern woertlich gleich waren: der Vorflug vor
# `lauf_beginnen` und der Anlauf-Schwanz danach.


@dataclass(frozen=True)
class Vorflug:
    """Was `lauf_beginnen` vom Anbieter wissen muss — vorab ermittelt."""

    anbieter: AiProvider
    denken: bool
    stufe: str | None
    fenster: Fenster


async def vorflug(
    client: httpx.AsyncClient, db: Session, user: User
) -> tuple[Vorflug | None, AiProvider | None]:
    """Der Vorflug eines unbeaufsichtigten Laufs: Anbieter, Denkstufe, Fenster.

    ``None`` als erster Wert heisst woertlich: es wurde nichts angelegt und
    nichts verbraucht. Darauf verlassen sich die Aufrufer — die Reparatur bucht
    ihren Versuchszaehler nur dann zurueck.

    Geloggt wird hier nichts: die Aufrufer nennen in ihren Zeilen den eigenen
    Anlass (Guardian-Heilung vs. Aufgabenlauf) und unterscheiden den Grund. Der
    zweite Wert traegt dafuer den gefundenen Anbieter mit, denn die Zeile
    "ohne API-Schluessel" braucht dessen Kennung:

    * ``(None, None)``        — kein Anbieter eingestellt.
    * ``(None, anbieter)``    — Anbieter da, aber ohne Betreiber-Schluessel.
    * ``(vorflug, anbieter)`` — vollstaendig, der Lauf kann beginnen.
    """
    # Spaete Imports: die drei Dienste haengen ihrerseits an halben Diensten
    # dieses Pakets — ein harter Import hier waere der naechste Importzyklus.
    from services import ai_context_window, ai_provider_service, ai_reasoning

    anbieter = ai_provider_service.anbieter_ohne_auswahl(db, user)
    if anbieter is None:
        return None, None
    if anbieter.requires_api_key and not anbieter.operator_api_key_encrypted:
        return None, anbieter

    denken, stufe = await ai_reasoning.vorgabe(
        client, db, user=user, provider=anbieter, aktiv=False, wunsch=None
    )
    fenster = await ai_context_window.ermitteln(
        client, anbieter, db=db, user_id=user.id
    )
    return Vorflug(anbieter=anbieter, denken=denken, stufe=stufe, fenster=fenster), anbieter


def anlauf(db: Session, run: AiRun) -> bool:
    """Der Anlauf-Schwanz: Kanal eroeffnen, Segment planen — oder ehrlich scheitern.

    `lauf_starten` hat — anders als `lauf_fortsetzen` — keinen Rueckfall. Ohne
    die Korrektur hier stuende der Lauf bis zum naechsten Prozessstart auf
    'running' und blockierte ueber `aktiver_lauf` jede weitere Heilung und jede
    weitere Aufgabe dieses Benutzers, weil der Lauf als beschaeftigt gaelte.
    """
    from services import ai_run_broker

    ai_run_broker.eroeffnen(run.id)
    if not lauf_starten(run.id):
        run.status = "failed"
        run.stop_reason = "no_runtime"
        db.commit()
        return False
    return True


# ── Wiederanlauf nach einem Prozessneustart ──────────────────────────────


def unterbrochene_laeufe_abgleichen(db: Session) -> int:
    """Ein Lauf im Zustand ``running`` hat den Neustart nicht ueberlebt.

    Er wird als fehlgeschlagen markiert und nicht etwa fortgesetzt: sein
    Arbeitsgedaechtnis endet mitten in einer Anbieterantwort, und wir wissen
    nicht, ob ein Werkzeug schon gelaufen ist. Ein halber Werkzeugaufruf, blind
    wiederholt, ist die schlechtere Wahl als ein ehrlicher Abbruch.

    Geparkte Laeufe (``waiting_*``) bleiben unangetastet — die warten auf einen
    Menschen und haben nichts Offenes in der Luft.

    Und sie werden **nachbereitet** wie jeder andere Endzustand auch. Hier stand
    nur der Statuswechsel, und damit umging ausgerechnet dieser Weg die Stelle,
    an der die Berichtsmails hängen: fällt das Panel während einer
    Guardian-Heilung oder eines fälligen Auftrags aus, sagt `ai_guardian_report`
    einen Bericht "bei jedem Endzustand" zu — und keiner ging hinaus. Der Server
    stand weiter, und der Betreiber erfuhr nichts davon.
    """
    laeufe = db.query(AiRun).filter(AiRun.status == "running").all()
    for run in laeufe:
        run.status = "failed"
        run.stop_reason = "process_restart"
        arbeitsspeicher_leeren(run)
        run.updated_at = _jetzt()
    if not laeufe:
        return 0
    db.commit()

    # **Nach** dem Commit, nicht davor: die Nachbereitung berichtet über den
    # Endzustand, und der soll festgeschrieben sein, bevor eine Mail ihn
    # behauptet. Verzögerter Import wie in `_aufgabe_planen` — die Schleife
    # selbst gehört dem Stream, dieser Dienst kennt sie nur beim Namen.
    from services.ai_stream_service import _lauf_nachbereiten

    for run in laeufe:
        # Ein misslungener Bericht darf die übrigen nicht mitnehmen. Der
        # Abgleich läuft beim Start des Panels; eine Ausnahme hier hieße, dass
        # das Panel nicht hochkommt.
        try:
            _lauf_nachbereiten(db, run, None)
        except Exception:
            db.rollback()
            logger.warning("Lauf nach Neustart nicht nachbereitet run_id=%s", run.id)
    return len(laeufe)


#: Der Inhalt eines Wiederanlaufs. Eine Panel-Meldung, kein Nutzersatz — und
#: ausdruecklich der Pruefauftrag aus docs/agentic-framework.md: die
#: persistierte Unterhaltung ist der Checkpoint, nicht das Gedaechtnis des
#: gestorbenen Prozesses.
_PRUEFAUFTRAG = (
    "Meldung des Panels (nicht vom Benutzer geschrieben): das Panel wurde neu "
    "gestartet, dein voriger Lauf zu diesem Auftrag wurde dabei unterbrochen. "
    "Der Auftrag und alles bisher Getane stehen im Verlauf dieser "
    "Unterhaltung. Prüfe zuerst den Stand — im Verlauf und, wo nötig, an "
    "den Systemen — und wiederhole nichts blind, was bereits geschehen ist. "
    "Führe den Auftrag dann zu Ende."
)


async def worker_wiederanlauf_saehen(db: Session) -> int:
    """Saet nach einem Neustart je unterbrochenem Worker **einen** neuen Lauf.

    Laeuft im Lifespan unmittelbar nach `unterbrochene_laeufe_abgleichen`. Der
    dort gesetzte ``failed/process_restart``-Endzustand bleibt woertlich
    stehen — er ist der ehrliche Beleg. Der Wiederanlauf ist ein **neuer**
    Lauf in derselben Worker-Unterhaltung, mit dem Pruefauftrag als Inhalt.

    Die Zusage "maximal ein automatischer Wiederanlauf" erzwingt der Zaehler
    ``anlauf`` im Worker-Rahmen des Nachfolgers, nicht eine zweite Tabelle:
    betrachtet wird nur der **juengste** Lauf eines Fensters, und wer schon
    mit ``anlauf >= 1`` gestorben ist, wird nicht erneut gesaet — der Mensch
    bekommt stattdessen eine Meldung.
    """
    from models import AiConversation

    client = http_client()
    if client is None:
        return 0

    kandidaten = (
        db.query(AiRun)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(
            AiConversation.kind == "worker",
            AiRun.status == "failed",
            AiRun.stop_reason == "process_restart",
        )
        .order_by(AiRun.created_at.asc())
        .all()
    )
    gesaet = 0
    for run in kandidaten:
        # Nur der juengste Lauf seines Fensters zaehlt: aeltere failed-Zeilen
        # stammen aus frueheren Neustarts und wurden damals behandelt.
        juengster = (
            db.query(AiRun.id)
            .filter(AiRun.conversation_id == run.conversation_id)
            .order_by(AiRun.created_at.desc())
            .first()
        )
        if juengster is None or juengster[0] != run.id:
            continue
        rahmen = zustand_lesen(run).get("worker")
        if not isinstance(rahmen, dict):
            continue
        try:
            if await _wiederanlauf_versuchen(db, run, dict(rahmen)):
                gesaet += 1
        except Exception:
            db.rollback()
            logger.warning("Wiederanlauf nicht gesaet run_id=%s", run.id)
    return gesaet


async def _wiederanlauf_versuchen(db: Session, run: AiRun, rahmen: dict) -> bool:
    """Ein einzelner Wiederanlauf — oder eine ehrliche Meldung, warum nicht."""
    from models import AiConversation, User
    from services import ai_meldestelle, permission_service
    from services.ai_stream_service import (
        familie_aus_zustand,
        herkunft_aus_zustand,
        lauf_beginnen,
        rolle_aus_zustand,
    )

    user = db.get(User, run.user_id)
    titel = str(rahmen.get("titel") or "Auftrag")
    kanal = str(rahmen.get("kanal") or "chat")

    def _melden(text: str) -> None:
        if user is None:
            return
        try:
            ai_meldestelle.melden(
                db, user=user, text=text, kanal=kanal,
                worker_id=run.conversation_id, worker_titel=titel,
            )
        except Exception:
            db.rollback()
            logger.warning("Wiederanlauf-Meldung fehlgeschlagen run_id=%s", run.id)

    if int(rahmen.get("anlauf", 0) or 0) >= 1:
        # Schon einmal automatisch wiederangelaufen und wieder gestorben —
        # ab hier entscheidet ein Mensch. Der Endzustand steht bereits.
        _melden(
            f'Der Auftrag "{titel}" wurde durch einen Neustart erneut '
            "unterbrochen und wird nicht noch einmal automatisch "
            "aufgenommen. Der bisherige Stand steht im Auftragsverlauf."
        )
        return False
    if user is None or not user.is_active:
        return False
    if not permission_service.has_global_permission(db, user, "ai.chat.use") or (
        not permission_service.has_global_permission(db, user, "ai.background.use")
    ):
        _melden(
            f'Der Auftrag "{titel}" konnte nach dem Neustart nicht wieder '
            "aufgenommen werden: die Berechtigung für Hintergrund-Worker "
            "fehlt inzwischen."
        )
        return False

    client = http_client()
    flug, anbieter = await vorflug(client, db, user)
    if flug is None:
        _melden(
            f'Der Auftrag "{titel}" konnte nach dem Neustart nicht wieder '
            "aufgenommen werden: es steht kein KI-Zugang bereit."
        )
        return False

    conversation = db.get(AiConversation, run.conversation_id)
    if conversation is None:
        return False
    # Die Denkstufe der Worker kommt aus der Betreiber-Konfiguration des
    # Zugangs, nicht aus dem Vorflug (der beantwortet die Chat-Frage).
    stufe = flug.anbieter.worker_reasoning_effort
    # Die Welt des Auftrags wandert mit, wie der Rahmen darunter auch. Ein
    # Auftrag aus der Smart-System-App verloere sonst beim Wiederanlauf seine
    # Desktop-Werkzeuge und meldete dem Benutzer, er koenne auf dessen Rechner
    # nicht zugreifen — obwohl sich nur das Panel neu gestartet hat. Laeuft
    # der Rechner nicht mehr, verfaellt ein Desktop-Auftrag mit seiner Frist;
    # das ist die ehrlichere Auskunft.
    #
    # Dasselbe fuer die **Rolle**: ohne sie leitete `_rolle_ableiten` sie neu
    # aus der Fensterart ab, und weil stehende Aufgaben seit dem 20.08.2026
    # ebenfalls in Worker-Fenstern laufen, wuerde ein wiederangelaufener
    # Aufgabenlauf zum Worker. Er verloere damit den Aufgaben-Werkzeugschnitt
    # und wuerde in der Schreibrunde auf einen Klick parken, den bei einem um
    # drei Uhr faellig gewordenen Auftrag niemand tut (siehe `niemand_da`).
    #
    # Und die **Familie** wandert aus demselben Grund mit wie die Herkunft:
    # die eine sagt „aus der App", die andere „aus dieser App", und erst
    # zusammen adressieren sie einen Rechner. Der Neustart des Panels ist der
    # einzige Grund, warum dieser Lauf noch einmal anfängt — der Mensch sitzt
    # unverändert vor demselben Gerät. Fällt die Kennung hier weg, wären die
    # Desktop-Aufträge des Nachfolgers wieder für jedes gekoppelte Gerät
    # abholbar (`desktop_job_service.naechster`), und zwar dauerhaft: eine
    # Familie bekommt ein Lauf nur beim Anlegen.
    alter_zustand = zustand_lesen(run)
    neuer, fehler = lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=flug.anbieter,
        request_id=uuid4(),
        content=_PRUEFAUFTRAG,
        reasoning=bool(stufe),
        reasoning_effort=stufe,
        context_chars=flug.fenster.zeichen if flug.fenster.bekannt else None,
        herkunft=herkunft_aus_zustand(alter_zustand),
        familie=familie_aus_zustand(alter_zustand),
        rolle=rolle_aus_zustand(alter_zustand),
        guardian_briefing_unterdruecken=True,
        unbeaufsichtigt=True,
        # Der Wiederanlauf ist eine Panel-Meldung an die KI ("das Panel wurde
        # neu gestartet, nimm den Auftrag wieder auf") und kein Satz des
        # Benutzers. Er soll den Auftrag fortsetzen sehen, nicht die Notiz,
        # mit der das Panel ihn dazu bringt.
        intern=True,
    )
    if neuer is None:
        _melden(
            f'Der Auftrag "{titel}" konnte nach dem Neustart nicht wieder '
            "aufgenommen werden ("
            + (fehler or ("unbekannt",))[0]
            + "). Der bisherige Stand steht im Auftragsverlauf."
        )
        return False

    # Der Rahmen nach `lauf_beginnen` — Rollback-Sicherheit wie ueberall. Der
    # Zaehler wandert **in den Nachfolger**: stirbt auch er im Neustart, sieht
    # der naechste Abgleich anlauf=1 und saet nicht mehr.
    zustand = zustand_lesen(neuer)
    zustand["worker"] = {**rahmen, "anlauf": int(rahmen.get("anlauf", 0) or 0) + 1}
    # War der gestorbene Lauf ein stehender Auftrag (Aufgaben laufen seit
    # 20.08.2026 in Worker-Fenstern), muss dessen Rahmen mitwandern: ohne ihn
    # verloere der Nachfolger den Aufgaben-Werkzeugschnitt und den
    # E-Mail-Bericht — der Verlust des Rahmens ist die gefaehrliche Richtung.
    if isinstance(alter_zustand.get("aufgabe"), dict):
        zustand["aufgabe"] = dict(alter_zustand["aufgabe"])
    zustand_schreiben(neuer, zustand)
    db.commit()

    if not anlauf(db, neuer):
        return False
    logger.info(
        "Worker wiederangelaufen (conversation_id=%s, run_id=%s)",
        run.conversation_id, neuer.id,
    )
    return True


def zuruecksetzen_fuer_tests() -> None:
    _AUFGABEN.clear()
    # Sonst schleppt der naechste Test die Platzbelegung des vorherigen mit und
    # bekaeme ein "schon unterwegs" fuer einen Lauf, den es nicht mehr gibt.
    _platz_freigeben_alle()


def _platz_freigeben_alle() -> None:
    with _PLANUNGSSCHLOSS:
        _GEPLANT.clear()
