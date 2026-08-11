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
from datetime import datetime, timezone
import json
import logging
import threading
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from models import AiActionProposal, AiRun, User
# Die Wartezustaende kommen aus dem Modell und nicht aus einer Literalkopie:
# wer dort einen Zustand ergaenzt, soll ihn hier nicht ein zweites Mal
# eintragen muessen — sonst sieht die Konstante wie die Wahrheitsquelle aus und
# ist doch nur eine Beschreibung.
from models.ai_run import WARTEND


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
    }


def zustand_lesen(run: AiRun) -> dict:
    if not run.state_json:
        return leerer_zustand([], request_id=str(uuid4()))
    try:
        geladen = json.loads(run.state_json)
    except (TypeError, ValueError):
        logger.warning("Laufzustand unlesbar run_id=%s", run.id)
        return leerer_zustand([], request_id=str(uuid4()))
    if not isinstance(geladen, dict):
        return leerer_zustand([], request_id=str(uuid4()))
    grund = leerer_zustand([], request_id=str(uuid4()))
    grund.update(geladen)
    return grund


def zustand_schreiben(run: AiRun, zustand: dict) -> None:
    run.state_json = json.dumps(zustand, ensure_ascii=True, separators=(",", ":"))
    run.updated_at = _jetzt()


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
        run.updated_at = _jetzt()
    return erbe


def aktiver_lauf(db: Session, *, user_id: int) -> AiRun | None:
    """Der juengste Lauf, der noch etwas vorhat. Fuer Glocke und Wiederanschluss."""
    return (
        db.query(AiRun)
        .filter(
            AiRun.user_id == user_id,
            AiRun.status.in_(("running", *WARTEND)),
        )
        .order_by(AiRun.created_at.desc())
        .first()
    )


def eigener_lauf(db: Session, run_id: str, user: User) -> AiRun | None:
    return (
        db.query(AiRun)
        .filter(AiRun.id == run_id, AiRun.user_id == user.id)
        .first()
    )


def darf_fortsetzen(db: Session, run: AiRun) -> bool:
    """Wartet dieser Lauf noch auf Vorschlaege, die niemand entschieden hat?

    Ein Lauf wird erst geweckt, wenn **alle** Vorschlaege seiner Runde
    entschieden sind. Sonst liefe er los, waehrend die zweite Karte noch offen
    im Chat steht — und meldete eine halbe Arbeit als fertig.
    """
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
    der Mensch seinen Teil getan hat.
    """
    run = db.get(AiRun, run_id)
    if run is None or not darf_fortsetzen(db, run):
        return False
    run.status = "running"
    run.stop_reason = None
    run.updated_at = _jetzt()
    db.commit()
    # Erst melden, dann planen — und beides **vor** der Antwort auf den
    # Bestaetigungsaufruf. Die Oberflaeche haengt sich unmittelbar danach an;
    # saehe sie den Lauf dort noch als "geparkt", wuerde sie sofort wieder
    # aufhoeren und die Fortsetzung verpassen.
    from services import ai_run_broker

    ai_run_broker.eroeffnen(run.id)
    ai_run_broker.veroeffentlichen(
        run.id, "run", {"run_id": run.id, "status": "running", "stop_reason": None}
    )
    if not _aufgabe_planen(run.id):
        # Keine Anwendung, also niemand, der das Segment ausfuehren koennte. Der
        # Lauf faellt in den Wartezustand zurueck, statt als "laufend" liegen zu
        # bleiben und beim naechsten Start als abgebrochen zu gelten.
        run.status = "waiting_confirmation"
        run.updated_at = _jetzt()
        db.commit()
        # Die Meldung oben zuruecknehmen, sonst wartet die Oberflaeche auf eine
        # Fortsetzung, die nie anlaeuft.
        ai_run_broker.veroeffentlichen(
            run.id,
            "run",
            {"run_id": run.id, "status": "waiting_confirmation",
             "stop_reason": "no_runtime"},
        )
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
    """
    laeufe = db.query(AiRun).filter(AiRun.status == "running").all()
    for run in laeufe:
        run.status = "failed"
        run.stop_reason = "process_restart"
        run.updated_at = _jetzt()
    if laeufe:
        db.commit()
    return len(laeufe)


def zuruecksetzen_fuer_tests() -> None:
    _AUFGABEN.clear()
    # Sonst schleppt der naechste Test die Platzbelegung des vorherigen mit und
    # bekaeme ein "schon unterwegs" fuer einen Lauf, den es nicht mehr gibt.
    _platz_freigeben_alle()


def _platz_freigeben_alle() -> None:
    with _PLANUNGSSCHLOSS:
        _GEPLANT.clear()
