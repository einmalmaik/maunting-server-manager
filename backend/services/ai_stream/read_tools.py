# -*- coding: utf-8 -*-
"""Ausfuehrung und Rundenlogik fuer Lese-Werkzeuge."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from database import SessionLocal, engine
from models import AiMessage, AiToolResult, User
import services.ai_stream as ai_stream
from services import ai_run_broker, ai_run_service, audit_service
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_action_service import angebotene_werkzeuge, execute_read_tool
from services.ai_chat_service import get_owned_conversation
from services.ai_context_service import anlagenwissen_nachtrag, message_character_count
from services.ai_proposal_service import AufgabenKontext, GuardianKontext
from services.ai_redaction import ist_geheimer_schluessel, redact_freetext, redact_sensitive_text
from services.ai_stream.context import familie_aus_zustand
from services.ai_stream.types import (
    MAX_GLEICHE_AUFRUFE,
    MAX_GLEICHE_POLLING_AUFRUFE,
    MAX_TOOL_CALLS,
    POLLING_WERKZEUGE,
    _FREITEXT_WERKZEUGE,
    _Vorbereitung,
)
from services.ai_tool_registry import (
    CHAT_INTERACTION_TOOLS,
    DESKTOP_TOOLS,
    GEHIRN_TOOLS,
    GUARDIAN_HEILUNG_TOOLS,
    NUR_WORKER,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    SKILL_TOOLS,
    WERKZEUGE,
    WORKER_STEUERUNG,
    WRITE_TOOLS,
    aufgaben_tools,
    worker_ausschluss,
)
from services.openai_compatible_adapter import StreamUsage

logger = logging.getLogger(__name__)


def _serverbezug(eintraege: list[dict]) -> int | None:
    """Welchen Server hat diese Runde zuletzt **nachweislich** angefasst?

    Nachweislich heisst: der Aufruf ist durchgelaufen. Jedes serverbezogene
    Lesewerkzeug geht durch `_resolve_server`, und das laedt den Server und
    prueft `server.view`. Eine erfolgreiche Rueckkehr ist damit der Beleg.

    Der Ausschluss gescheiterter Aufrufe ist der Kern und keine Feinheit. Genau
    dort scheitert `_resolve_server` ja — bei einer erfundenen oder fremden
    Nummer. Zaehlte ein Fehlschlag mit, koennte sich das Modell Serverbezug
    erfinden, indem es eine beliebige Nummer nennt und den Fehler hinnimmt.

    Umgekehrt kostet ein Aufruf, der erst *nach* der Rechtepruefung scheitert
    (Datei nicht gefunden), hier hoechstens einen Bezug, den die naechste Runde
    ohnehin nachliefert. Zu vorsichtig ist an dieser Stelle die richtige
    Richtung.

    Der letzte gewinnt: fragt das Modell in einer Runde nach mehreren Servern,
    ist der zuletzt gelesene der, bei dem es geblieben ist.
    """
    for eintrag in reversed(eintraege):
        if eintrag.get("failed") or eintrag.get("tool_name") not in SERVER_READ_TOOLS:
            continue
        server_id = eintrag.get("server_id")
        if isinstance(server_id, int):
            return server_id
    return None


def _ergebnis_schwaerzen(wert, *, freitext: bool = False):
    """Laeuft ein Werkzeugergebnis durch und schwaerzt jede Zeichenkette darin.

    Rekursiv ueber Woerterbuecher und Listen, weil Ergebnisse verschachtelt sind
    (`{"incidents": [{"description": ...}]}`). Schluessel bleiben unberuehrt: sie
    stammen aus dem Code, nicht aus den Daten, und ein geschwaerzter Schluessel
    machte das Ergebnis fuer das Modell unlesbar.

    **Der Schluessel entscheidet aber ueber seinen Wert.** Die Muster in
    `ai_redaction` sind auf Zuweisungstext ausgelegt — sie brauchen Schluessel
    *und* Trennzeichen in derselben Zeichenkette. Genau diesen Zusammenhang
    zerlegt die Rekursion: `read_blueprint` liefert
    ``{"runtime": {"env": {"RCON_PASSWORD": "hunter2"}}}``, und weitergereicht
    wurde nur ``"hunter2"`` — darauf passt kein Muster, und das Passwort ging im
    Klartext an den Modellanbieter und in `ai_tool_results`. Der als "einziger
    Ausgang" gebaute Punkt hielt seine Zusage damit ausgerechnet fuer die
    haeufigste Form eines Geheimnisses nicht.

    Zahlen und Wahrheitswerte bleiben, wie sie sind — ein Port ist kein Geheimnis
    und eine Groesse in Megabyte auch nicht.
    """
    schwaerzen = redact_freetext if freitext else redact_sensitive_text
    if isinstance(wert, str):
        return schwaerzen(wert)
    if isinstance(wert, dict):
        ergebnis = {}
        for k, v in wert.items():
            if ist_geheimer_schluessel(k):
                # Der ganze Teilbaum, nicht nur eine Zeichenkette: unter
                # `credentials` kann ein Woerterbuch stehen, dessen Werte
                # harmlose Schluessel wie `user` und `pass` tragen. Weiter
                # hineinzusteigen hiesse, sich auf die inneren Namen zu
                # verlassen — und der aeussere hat schon gesagt, worum es geht.
                ergebnis[k] = "[REDACTED]"
                continue
            ergebnis[k] = _ergebnis_schwaerzen(v, freitext=freitext)
        return ergebnis
    if isinstance(wert, list):
        return [_ergebnis_schwaerzen(v, freitext=freitext) for v in wert]
    return wert


def _werkzeug_nebenlaeufigkeit() -> int:
    """Wieviele Lesewerkzeuge gleichzeitig laufen duerfen.

    Auf **PostgreSQL** — der einzigen unterstuetzten Betriebsdatenbank
    (`database_policy.validate_panel_database_url`) — holt sich jeder Aufruf
    seine eigene Verbindung aus dem Pool. Acht gleichzeitig passen bequem neben
    den gewoehnlichen Anfragen des Panels; der Rest wartet kurz, statt den Pool
    leerzuraeumen.

    Auf **SQLite** teilen sich alle Sitzungen eine einzige Verbindung
    (`StaticPool` in der Testsuite, `SingletonThreadPool` sonst). Zwei
    Transaktionen gleichzeitig darauf sind keine Nebenlaeufigkeit, sondern ein
    Datenfehler: der Commit der einen schliesst die offene Arbeit der anderen
    mit ab. Dort laeuft deshalb einer nach dem anderen.

    **Der wichtigere Teil geht dabei nicht verloren.** Auch bei eins laufen die
    Aufrufe durch `asyncio.to_thread`, und genau das war das eigentliche
    Problem: sie hingen bisher *auf* der Ereignisschleife. Neun Aufrufe zu drei
    Sekunden legten den ganzen Prozess siebenundzwanzig Sekunden lahm —
    gemessen, nicht vermutet. Die Gleichzeitigkeit ist der zweite Gewinn, nicht
    der erste.
    """
    return 1 if str(engine.url).startswith("sqlite") else 8


def _servernummer(call) -> int | None:
    """Die Servernummer eines Aufrufs, sofern eine echte Zahl dabei steht.

    Die Argumente kommen vom Modell: dort kann genauso gut ``"12"``, eine Liste
    oder gar nichts stehen. Für die Anzeige zählt nur eine Zahl — alles andere
    ist ``None`` und damit "kein Serverbezug".
    """
    nummer = call.arguments.get("server_id")
    return nummer if isinstance(nummer, int) else None


def _werkzeuge_ansagen(run_id: str | None, calls) -> None:
    """Sagt an, welche Werkzeuge gleich laufen — vor dem ersten Ergebnis.

    Ohne diese Ansage erfährt die Oberfläche einen Werkzeugnamen erst zusammen
    mit dem Ergebnis (`tool`). Genau davor liegt die längste Stille des Laufs,
    und sie war bisher textlos.

    **Eigener Name und flüchtig.** `veroeffentlichen` hängt jedes `tool` an den
    Abzug (ai_run_broker.py). Ein früheres `tool` stünde nach dem Wiederanhängen
    doppelt und dauerhaft im Verlauf. `tool_plan` kennt der Vermittler nicht —
    es geht an die Zuhörer und sonst nirgends. Wer sich mitten im Lauf neu
    anhängt, sieht es deshalb nicht; das ist richtig so, denn es ist keine
    Tatsache über die Antwort, sondern eine Anzeige während der Arbeit.

    `call_id` und nicht der Name ist der Schlüssel: bis zu acht Werkzeuge laufen
    gleichzeitig, und dasselbe Werkzeug kann in einer Runde zweimal vorkommen.
    Argumente gehen bewusst **nicht** mit — Pfade, Dateinamen und Adressen haben
    in einer Anzeigezeile nichts verloren.

    **Genau einmal je Runde, und für jede Art von Werkzeug dieselbe Ansage.**
    Die Oberfläche ersetzt ihren Zustand mit jeder Ansage, sie ergänzt ihn
    nicht; zwei Ansagen in einer Runde hiessen also, die zweite vergisst die
    erste. Der Ablauf gibt das her: eine Runde ist entweder eine Leserunde oder
    eine Schreibrunde. Mischt das Modell beides, laufen nur die Lesewerkzeuge,
    und die Schreibaufrufe gehen unausgeführt mit Begründung an das Modell
    zurück — angesagt wird dann nur, was wirklich läuft.

    Gerufen wird ausschliesslich auf der Ereignisschleife, auch im Schreibpfad,
    dessen eigentliche Arbeit in einem Thread liegt: `veroeffentlichen` schreibt
    in eine gewöhnliche `asyncio.Queue` und verträgt keinen zweiten Schreiber.
    """
    if run_id is None or not calls:
        return
    ai_run_broker.veroeffentlichen(run_id, "tool_plan", {"aufrufe": [
        {
            "call_id": call.id,
            "tool_name": call.name,
            "server_id": _servernummer(call),
        }
        for call in calls
    ]})


def _anzeigeeintrag(call, wert, fehlgeschlagen: str | None) -> dict:
    """Was der Benutzer im Verlauf sehen soll: welches Werkzeug lief und womit.

    Bewusst ohne das Ergebnis — ein Logausschnitt gehoert nicht ungefragt in den
    sichtbaren Verlauf, und die Antwort fasst ihn ohnehin zusammen.
    """
    eintrag = {
        "tool_name": call.name,
        "server_id": _servernummer(call),
        # Ein gescheiterter Aufruf gehoert sichtbar in den Verlauf. Sonst wirkt
        # eine Antwort vollstaendig, der eine Auskunft fehlt.
        **({"failed": True} if fehlgeschlagen else {}),
        # Die Gruppe entscheidet ueber das Symbol im Verlauf. Sie stand seit
        # jeher in `ai_tool_registry`, verliess das Backend aber nie — das
        # Frontend riet sie an einem hartkodierten `tool_name === 'remember'`
        # nach und lag bei `search_memory` und `forget_memory` daneben. Eine
        # Zeile hier entfernt eine Abschrift, statt eine hinzuzufuegen.
        **(
            {"gruppe": WERKZEUGE[call.name].gruppe}
            if WERKZEUGE.get(call.name) is not None and WERKZEUGE[call.name].gruppe
            else {}
        ),
    }
    # Bei Skills gehoert der Name in den Verlauf, nicht nur "read_skill". Der
    # Betreiber will sehen, *welche* erlernte Vorgehensweise gegriffen hat —
    # sonst wirkt eine Antwort, die aus einem Skill entstanden ist, wie geraten.
    # Der Schluessel kommt aus dem Ergebnis und nicht aus den Argumenten: dort
    # ist er bereits normalisiert und gegen die Sichtbarkeit geprueft.
    if call.name in SKILL_TOOLS and isinstance(wert, dict):
        eintrag["skill_key"] = wert.get("skill_key")
        eintrag["skill_name"] = wert.get("name")
        eintrag["skill_status"] = wert.get("status")
        eintrag["skill_learned"] = bool(wert.get("learned"))
    if call.name == "analyze_region" and isinstance(wert, dict):
        eintrag["geo_analysis"] = wert
    if call.name == "control_region_camera" and isinstance(wert, dict):
        eintrag["geo_camera"] = wert
    if call.name == "web_search" and isinstance(wert, dict) and isinstance(wert.get("results"), list):
        eintrag["web_results"] = wert["results"]
    return eintrag


def _werkzeug_ausfuehren(
    user_id: int, call, herkunft: str = "panel", familie: str | None = None,
    prefetch_session_id: str | None = None, fast_region: bool = False,
) -> tuple[object, str | None]:
    """Genau **ein** Lesewerkzeug, in eigener Sitzung und eigenem Thread.

    Eine Sitzung je Aufruf und nicht eine geteilte fuer die ganze Runde: eine
    SQLAlchemy-Sitzung gehoert dem Thread, der sie geoeffnet hat. Sie
    weiterzureichen waere genau die Art Fehler, die erst unter Last auffaellt.

    Der Commit steht hier, weil manches "Lese"-Werkzeug schreibt: `remember`
    legt einen Eintrag an, `learn_skill` einen Skill, `forget_memory` loescht.
    Frueher committete die gemeinsame Sitzung am Ende der Runde fuer alle
    zusammen; jetzt steht jeder Aufruf fuer sich. Das ist die bessere
    Aufteilung — ein gescheiterter Nachbaraufruf nimmt einem gemerkten Namen
    nicht mehr die Speicherung.

    ``herkunft`` ist die Welt des **aufrufenden** Laufs und geht genau ein
    Werkzeug etwas an: `worker_start` vererbt sie an den Auftrag, den es
    anlegt. Sie kommt aus dem Laufzustand und nie aus den Argumenten des
    Modells — sonst schriebe sich ein Lauf selbst einen Rechner zu, den er
    nicht hat.

    ``familie`` fährt daneben mit und aus demselben Grund. Die Herkunft sagt
    „aus der App", die Familie sagt „aus **dieser** App", und beides zusammen
    adressiert erst einen Rechner. Ein Auftrag, der nur die Herkunft erbt,
    bekommt seine Desktop-Werkzeuge zurück — seine Aufträge holt aber wieder
    der Rechner ab, der zuerst fragt (`desktop_job_service.naechster`). Auch
    sie stammt aus dem Laufzustand und nie aus den Argumenten: welches Gerät
    gefragt hat, ist eine Tatsache des Laufs und keine Behauptung des Modells.
    """
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        try:
            wert = ai_stream.execute_read_tool(
                db, user=user, tool_name=call.name, arguments=call.arguments,
                herkunft=herkunft, familie=familie, prefetch_session_id=prefetch_session_id,
                fast_region=fast_region,
            )
            if call.name == "analyze_region" and isinstance(wert, dict) and wert.get("news_status") == "pending" and wert.get("status") == "success":
                try:
                    enriched = ai_stream.execute_read_tool(
                        db, user=user, tool_name=call.name, arguments=call.arguments,
                        herkunft=herkunft, familie=familie, prefetch_session_id=prefetch_session_id,
                        fast_region=False,
                    )
                    if isinstance(enriched, dict) and enriched.get("status") == "success":
                        wert = enriched
                except Exception:
                    pass
            db.commit()
        except (AiActionValidationError, HTTPException) as exc:
            # Fehlendes Recht, fremde Server-ID, ungueltige Argumente. Das
            # Modell soll es erfahren und weitermachen koennen; frueher riss ein
            # solcher Aufruf die gesamte Antwort ab.
            #
            # **`HTTPException` gehoert dazu**, auch wenn sie nach Weboberflaeche
            # klingt: die drei Dateiwerkzeuge melden darueber ihre haeufigsten
            # Faelle — `read_config` wirft 404 fuer "Datei nicht gefunden", 400
            # fuer "kein regulaeres Ding" und 413 fuer "zu gross",
            # `list_server_files` und `search_server_files` 503, wenn der Node
            # gerade nicht erreichbar ist. Ein geratener Pfad — der eigene
            # Katalogtext warnt ausdruecklich davor — oder ein Node im Neustart
            # beendete damit den ganzen Lauf, in einer unbeaufsichtigten Heilung
            # samt einem der acht Reparaturanlaeufe. Ein Formfehler kostet eine
            # Runde, nie die Antwort.
            #
            # Geschwaerzt wie jedes andere Ergebnis auch. Der Fehlertext geht an
            # den Anbieter **und** ueber `AiToolResult` in jede Folgerunde, und
            # er zitiert oft ein Argument des Modells woertlich zurueck ("Kein
            # Handler fuer Werkzeug: …") — das Modell wiederum hat fremden
            # Logtext gelesen. Der Schreibpfad schwaerzt denselben Text seit
            # jeher (`_persist_write_proposals`); hier lief er am Choke Point
            # vorbei, weil dieser Zweig vor ihm zurueckkehrt.
            db.rollback()
            grund = redact_sensitive_text(
                str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
            )
            return {"error": grund}, grund
    # **Der Choke Point.** Hier — und nur hier — verlaesst ein Werkzeugergebnis
    # das Panel Richtung Anbieter und Datenbank.
    #
    # Geschwaerzt wurde bisher in den Handlern, jeder fuer sich. Das ist neunmal
    # dieselbe Entscheidung an neun Orten, und wer einen zehnten Handler
    # schreibt, vergisst sie: `read_blueprint`, `list_server_files`,
    # `search_workshop_mods`, `read_skill` und `read_docs` gaben ihre Inhalte
    # ungefiltert weiter. Dateinamen von der Platte und Titel aus dem
    # Steam-Workshop sind Fremdtext wie Logzeilen auch.
    #
    # Die Handler behalten ihre eigenen Aufrufe. Doppelt zu schwaerzen kostet
    # nichts — `[REDACTED]` enthaelt keines der Muster — und die Aufrufe dort
    # sagen weiterhin, wo Fremdtext herkommt.
    #
    # Ausserhalb der Sitzung, weil Schwaerzen reine Textarbeit ist und eine
    # offene Transaktion waehrenddessen nichts zu suchen hat.
    return _ergebnis_schwaerzen(wert, freitext=call.name in _FREITEXT_WERKZEUGE), None


def voice_werkzeug_ausfuehren(
    user_id: int,
    call,
    *,
    conversation_id: str | None = None,
    herkunft: str = "panel",
    familie: str | None = None,
) -> tuple[object, str | None, dict, list[dict]]:
    """Gemeinsamer, autoritativer Werkzeugpfad für Voice-Transporte.

    Realtime bekommt keinen zweiten Katalog und keinen verkürzten RBAC-Pfad.
    Das Ergebnis ist bereits am bestehenden Choke Point geschwärzt; die
    Anzeigeprojektion enthält weiterhin keine Argumente oder Rohresultate.
    """
    if call.name == "execute_server_action":
        from services.ai_voice.voice_dispatcher import dispatch_voice_action
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        return dispatch_voice_action(
            user_id,
            arguments,
            conversation_id=conversation_id,
            herkunft=herkunft,
            familie=familie,
        )

    if call.name in WRITE_TOOLS:
        if call.name not in CHAT_INTERACTION_TOOLS or not conversation_id:
            fehler = "Das Gehirn delegiert Server-Aktionen an einen Worker"
            wert = {"error": fehler}
            return wert, fehler, _anzeigeeintrag(call, wert, fehler), []
        # Dieselbe Vorschlagserzeugung wie im Chat. Es entstehen keine
        # Chatnachrichten; die Unterhaltung dient nur als Besitzergrenze für
        # Karte, Audit und spätere Bestätigung.
        from services.ai_stream.write_tools import _persist_write_proposals

        vorschlaege = _persist_write_proposals(
            user_id=user_id,
            conversation_id=conversation_id,
            tool_calls=[call],
            correlation_id=str(uuid4()),
            run_id=None,
        )
        fehler = next(
            (str(v.get("error")) for v in vorschlaege if v.get("error")),
            None,
        )
        wert = {"proposals": vorschlaege}
        return wert, fehler, _anzeigeeintrag(call, wert, fehler), vorschlaege

    wert, fehler = _werkzeug_ausfuehren(
        user_id, call, herkunft=herkunft, familie=familie
    )
    return wert, fehler, _anzeigeeintrag(call, wert, fehler), []


def _gueltige_spekulative_argumente(call) -> bool:
    """Prueft nur die enge, nebenwirkungsfreie Vorstart-Allowlist.

    Die fachliche Autoritaet bleibt `execute_read_tool`. Diese kleine
    Vorpruefung verhindert lediglich, dass offensichtlich unvollstaendige oder
    falsch typisierte Anbieterargumente schon waehrend des Stroms Arbeit
    ausloesen. Neue Read-Tools werden hier absichtlich nicht automatisch
    zugelassen.
    """
    argumente = call.arguments
    if not isinstance(argumente, dict):
        return False
    if call.name == "read_server_status":
        server_id = argumente.get("server_id")
        return set(argumente) == {"server_id"} and isinstance(server_id, int) and not isinstance(server_id, bool) and server_id > 0
    if call.name == "search_memory":
        return set(argumente) == {"query"} and isinstance(argumente.get("query"), str) and bool(argumente["query"].strip())
    if call.name == "web_search":
        if set(argumente) - {"query", "count", "server_id"}:
            return False
        if not isinstance(argumente.get("query"), str) or not argumente["query"].strip():
            return False
        count = argumente.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
            return False
        server_id = argumente.get("server_id")
        return server_id is None or (isinstance(server_id, int) and not isinstance(server_id, bool) and server_id > 0)
    if call.name == "analyze_region":
        return (
            set(argumente) <= {"location", "camera"}
            and isinstance(argumente.get("location"), str)
            and bool(argumente["location"].strip())
            and argumente.get("camera", "focus") in {"overview", "focus", "detail"}
        )
    if call.name == "control_region_camera":
        action = argumente.get("action")
        if action == "focus_location":
            return (
                set(argumente) == {"action", "location"}
                and isinstance(argumente.get("location"), str)
                and bool(argumente["location"].strip())
            )
        return set(argumente) == {"action"} and action in {"zoom_in", "zoom_out", "overview"}
    if call.name == "calendar_read":
        if set(argumente) - {"start_date", "end_date", "calendar_id"}:
            return False
        if any(
            value is not None and not isinstance(value, str)
            for key in ("start_date", "end_date")
            if (value := argumente.get(key))
        ):
            return False
        kalender_id = argumente.get("calendar_id")
        return kalender_id is None or (isinstance(kalender_id, int) and not isinstance(kalender_id, bool) and kalender_id > 0)
    return False


def _fruehstart_lesewerkzeug_erlaubt(
    *, run_id: str, user_id: int, conversation_id: str, call, angebotene_werkzeuge: frozenset[str]
) -> bool:
    """Ob ein fertiger Provider-Call schon waehrend des Stroms laufen darf."""
    from services import ai_autonomy_service
    from services.ai_intent_classifier import is_side_effect_free

    if (
        call.name not in angebotene_werkzeuge
        or not is_side_effect_free(call.name)
        or not _gueltige_spekulative_argumente(call)
        or ai_run_broker.lauf_status(run_id) != "running"
    ):
        return False
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return False
        if get_owned_conversation(db, conversation_id, user) is None:
            return False
        return ai_autonomy_service.autonomy_allows(
            db,
            user=user,
            server_id=_servernummer(call),
            tool_name=call.name,
        )


async def _lesewerkzeug_ausfuehren(
    *, user_id: int, call, herkunft: str, familie: str | None,
    prefetch_session_id: str | None, schloss: asyncio.Semaphore,
) -> tuple[object, object, dict]:
    def _ausfuehren():
        import sys
        fn = getattr(sys.modules.get("services.ai_stream_service"), "_werkzeug_ausfuehren", None) or ai_stream._werkzeug_ausfuehren
        try:
            return fn(user_id, call, herkunft, familie, prefetch_session_id, True)
        except TypeError:
            try:
                return fn(user_id, call, herkunft, familie)
            except TypeError:
                return fn(user_id, call)

    async with schloss:
        started_at = time.perf_counter()
        outcome = "ok"
        try:
            wert, fehlgeschlagen = await asyncio.wait_for(
                asyncio.to_thread(_ausfuehren),
                timeout=ai_stream.WERKZEUG_ZEITGRENZE,
            )
            if fehlgeschlagen:
                outcome = "error"
        except TimeoutError:
            outcome = "timeout"
            grund = (
                f"Der Aufruf antwortete nicht innerhalb von "
                f"{ai_stream.WERKZEUG_ZEITGRENZE:.0f} Sekunden und wurde nicht "
                "weiter abgewartet. Er kann im Hintergrund noch "
                "durchlaufen — wiederhole ihn nicht blind, sondern prüfe "
                "erst nach, ob er gewirkt hat."
            )
            wert, fehlgeschlagen = {"error": grund}, grund
        finally:
            from services.ai_latency_metrics import metrics

            metrics.record(
                "ai_stream", "read_tool_execution",
                (time.perf_counter() - started_at) * 1000,
                outcome,
            )
    return call, wert, _anzeigeeintrag(call, wert, fehlgeschlagen)


def _aufrufnachricht(calls, text: str | None = None) -> dict:
    """Der Assistentenzug, der die Werkzeugaufrufe trägt.

    Reine Datenform gegenüber dem Anbieter, keine Logik. Sie stand dreimal
    wörtlich gleich in dieser Datei — bei den Lesewerkzeugen, bei der
    abgewiesenen Rückfrage und beim Rückfluss der Schreibaufrufe. Eine
    Anpassung des Formats musste an drei Orten gleich landen; bleibt eine
    zurück, weist der Anbieter genau die Runde ab, in der ein Schreibaufruf
    beantwortet wird.

    ``text`` ist das, was das Modell in derselben Anbieterantwort **neben**
    den Aufrufen gesagt hat („Ich schaue mir erst die Logs an …"). Hier stand
    fest ``content: None`` — der Text erreichte den Benutzer, aber nie wieder
    das Modell: in der Folgerunde kannte es seine eigenen Ansagen, Zwischen-
    schlüsse und Zusagen nicht und wiederholte oder widersprach ihnen. Das
    Format erlaubt Text neben ``tool_calls`` ausdrücklich.
    """
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=True),
                },
            }
            for call in calls
        ],
    }


def _rundenfehler_nachrichten(
    tool_calls, rundentext: str | None, *, code: str, hinweis: str
) -> list[dict]:
    """Die **ganze** Runde als Fehler beantworten: Aufrufnachricht plus Absagen.

    Fünf Stellen dieser Datei verwarfen eine Runde und schrieben dafür dieselben
    zehn Zeilen ab — abgewiesene Rückfrage, Formfehler an der Rückfrage,
    Formfehler an `wait_until`, erschöpftes Budget vor einem Desktop-Auftrag und
    der Schreibversuch des Gehirns. Was sie durchsetzen, ist keine Formsache,
    sondern eine Protokoll-Invariante: zu **jeder** `tool_call_id` gehört genau
    eine Antwort, sonst weist der Anbieter die nächste Anfrage ab. Eine
    Invariante, die an fünf Stellen per Abschrift gilt, fällt bei der sechsten —
    dort vergisst jemand die Schleife und beantwortet nur den einen Aufruf, und
    der Lauf scheitert erst beim Anbieter mit einer unverständlichen Meldung
    statt im eigenen Code.

    ``code`` ist der maschinenlesbare Grund (`AI_ASK_INVALID`,
    `AI_WAIT_INVALID`, …), ``hinweis`` der Satz **an das Modell**: was schief
    lief und wie es weitergeht.

    Nicht hierher gehören die zwei gemischten Fälle (`AI_RUN_PARKED`), in denen
    ein Aufruf gelingt und die übrigen abgesagt werden — sie sind kein reines
    Fehlermuster.
    """
    nachrichten: list[dict] = [_aufrufnachricht(tool_calls, rundentext)]
    for call in tool_calls:
        nachrichten.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(
                {"error": code, "message": hinweis},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        })
    return nachrichten


def _runde_zaehlen(zustand: dict, rundendeckel: int) -> bool:
    """Zählt diese Runde und sagt, ob das Budget damit gerissen ist.

    Das Rundenbudget (`MAX_TOOL_ROUNDS`) ist eine tragende Schutzgrenze des
    Laufs, seine Buchführung stand aber achtmal von Hand in dieser Datei —
    mehrfach mit einer eigenen Fassung des Vergleichs daneben. Zählt ein neuer
    Behandlungspfad die Runde nicht oder prüft er mit ``>=`` statt ``>``,
    bekommt ein Lauf eine Runde geschenkt oder verliert eine, und niemand merkt
    es: jede Stelle sieht für sich plausibel aus.

    Die Aufrufer bauen aus dem Wahrheitswert weiterhin ihr eigenes
    Ergebnisobjekt (`_FragenErgebnis`, `_WartenErgebnis`, …) — die
    unterschiedlichen Rückgabetypen sind kein Hindernis, gemeinsam sind nur
    Zählung und Vergleich. Die zwei parkenden Pfade rufen sie ebenfalls und
    verwerfen das Ergebnis: sie zählen die Runde, aber der Deckel entscheidet
    dort nichts mehr, weil das Segment ohnehin endet.
    """
    zustand["rounds"] = int(zustand.get("rounds", 0)) + 1
    return zustand["rounds"] > rundendeckel


def _aussortieren(tool_calls: list, deferred: list, *, erlaubt, grund: str) -> list:
    """Aussortieren statt werfen: was nicht darf, wandert mit Begruendung weg.

    Der Aufruf laeuft nicht, aber das Modell bekommt eine Antwort und arbeitet
    weiter. Ein `raise` an dieser Stelle riss frueher ganze Heilungslaeufe ab —
    ein einziges Werkzeug ausserhalb der Menge beendete den Lauf mit 'failed',
    der gestoerte Server blieb stehen, und der Bericht an den Betreiber war
    leer.

    ``erlaubt`` bekommt den Werkzeugnamen und antwortet mit ja oder nein. Die
    vier Rollenschnitte darunter unterscheiden sich genau darin und sonst in
    nichts: drei fragen eine Erlaubtliste, einer eine Sperrliste.
    """
    behalten = []
    for call in tool_calls:
        if erlaubt(call.name):
            behalten.append(call)
        else:
            deferred.append((call, grund))
    return behalten


async def _tool_followup_messages(
    *, user_id: int, conversation_id: str, tool_calls, deferred=(),
    correlation_id: str | None = None, run_id: str | None = None,
    guardian: GuardianKontext | None = None,
    aufgabe: AufgabenKontext | None = None,
    rolle: str = "voll",
    herkunft: str = "panel",
    familie: str | None = None,
    prefetch_session_id: str | None = None,
    anlagenwissen_noetig: bool = True,
    rundentext: str | None = None,
    frage_id: str | None = None,
    provider_messages: list[dict] | None = None,
    vorab_aufgaben: dict[str, asyncio.Task] | None = None,
    schloss: asyncio.Semaphore | None = None,
    call_reihenfolge=None,
) -> tuple[list[dict], list[dict], dict | None]:
    """Fuehrt Lesewerkzeuge aus und baut daraus die Folge-Nachrichten.

    **Nebenlaeufig und neben der Ereignisschleife, nicht auf ihr.** Diese
    Funktion war einmal synchron und wurde geradewegs aus `segment_ausfuehren`
    gerufen. Gemessen hiess das: neun Werkzeugaufrufe zu drei Sekunden ergaben
    siebenundzwanzig Sekunden Laufzeit **und** siebenundzwanzig Sekunden, in
    denen der Prozess keine einzige andere Anfrage beantwortete — von niemandem.
    Genau das war die Beobachtung des Betreibers: "ich habe von jedem Server ein
    Backup erstellen lassen, danach hat die Seite nicht mehr geladen".

    Jeder Aufruf laeuft jetzt in `asyncio.to_thread` mit eigener Sitzung,
    gemeinsam ueber `asyncio.gather`. Die Obergrenze steht in
    `_werkzeug_nebenlaeufigkeit` und haengt an der Datenbank.

    **Das Ereignis geht raus, sobald der einzelne Aufruf fertig ist** — nicht
    erst, wenn die ganze Runde durch ist. Vorher erschienen alle Chips
    gleichzeitig nach der letzten Antwort; jetzt erscheint jeder, sobald er
    etwas bedeutet.

    ``deferred`` sind Paare aus Aufruf und Begruendung: Aufrufe, die in dieser
    Runde bewusst **nicht** laufen — ein Schreibwerkzeug, das das Modell mit
    Lesewerkzeugen vermischt hat, oder ein Aufruf ueber der Rundengrenze. Sie
    bekommen trotzdem eine Antwort: das Protokoll verlangt zu jeder
    `tool_call_id` genau ein Ergebnis, und ohne Begruendung wuesste das Modell
    nicht, warum sein Aufruf verschwunden ist.

    Ein **einzelner** fehlgeschlagener Aufruf beendet den Stream nicht. Fragt
    das Modell nebenbei nach einem Server, den der Benutzer nicht sehen darf,
    ist das eine Auskunft an das Modell — kein Grund, dem Benutzer die ganze
    Antwort wegzunehmen. Die Rechtepruefung hat ihre Arbeit getan: ausgefuehrt
    wurde nichts.

    Und ein **einzelner** hängender Aufruf hält die Antwort nicht mehr fest:
    `WERKZEUG_ZEITGRENZE` deckelt jeden für sich. Was danach zurückgeht, ist
    keine Fehlermeldung, sondern eine Aussage über den Zustand — der Aufruf
    läuft im Threadpool weiter, und was er schreibt, schreibt er. Warum das so
    formuliert ist und was es nicht leistet, steht bei der Konstanten.

    Der dritte Rueckgabewert ist ein **Nachtrag zum Kontext**: das Wissen der
    Anlage, um die es in dieser Runde ging. Vorher konnte es nicht dabei sein —
    beim Anlegen des Laufs war der Server noch nicht bekannt. Ist nichts
    nachzureichen, ist er None.

    ``anlagenwissen_noetig=False`` heißt: der Lauf hat das Anlagenwissen schon
    bekommen, das Lesen kann entfallen. Es entfiel bisher nicht — die
    Entdopplung stand erst beim Aufrufer, und ab der zweiten Werkzeugrunde wurde
    das Ergebnis weggeworfen, nachdem es das ganze sichtbare Gedächtnis gelesen,
    jede Zeile einzeln gegen die Rechte geprüft und über den Sidecar
    entschlüsselt hatte. Teurer als die Arbeit war die Nebenwirkung:
    `server_shared_context` zählt bei jedem Aufruf `use_count` hoch, und der
    Zähler entscheidet beim nächsten Engpass mit, was bleibt.

    ``frage_id`` ist die Benutzernachricht dieses Laufs. Der Nachtrag wählt und
    **zählt** nach der Frage; ohne sie fiel sein ``query`` auf ``""`` zurück,
    jeder Reiz war null, und die Zählschleife in `server_shared_context` war auf
    diesem Weg unerreichbar — ausgerechnet das Wissen, das nur über den Nachtrag
    ankommt (der Normalfall bei der ersten Frage zu einer Anlage), wurde nie als
    gebraucht vermerkt.

    Übergeben wird die **Kennung** und nicht der Text: `arbeitsspeicher_leeren`
    räumt am Ende eines Laufs alles Wörtliche aus `state_json`, weil dort auch
    der entschlüsselte Gedächtnisblock stünde. Ein Laufzustand, der die Frage
    zusätzlich im Klartext trüge, hätte genau diese Zusage gebrochen. Gelesen
    wird sie deshalb dort, wo sie ohnehin liegt, und nur wenn der Nachtrag
    wirklich ansteht.
    """
    deferred = [(call, reason) for call, reason in deferred]
    if len(tool_calls) + len(deferred) > MAX_TOOL_CALLS:
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    if any(call.name not in READ_TOOLS for call in tool_calls):
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    # Der Rollen-Spiegel (docs/agentic-framework.md, §7). Der Katalogschnitt
    # in `_werkzeuge_und_grenze` ist Fuehrung, keine Zusage — die Schranke
    # steht hier, je Aufruf, nach demselben Muster wie bei Guardian und
    # Aufgabe darunter: **aussortiert, nicht geworfen**. Ohne diesen Spiegel
    # koennte ein Gehirn-Lauf einen halluzinierten Server-Werkzeugnamen
    # durchbekommen, und die Invariante "das Gehirn hat strukturell keine
    # Aussenwirkung" hinge allein an einer Bitte an das Modell.
    if rolle == "gehirn":
        tool_calls = _aussortieren(
            tool_calls, deferred,
            erlaubt=lambda name: name in GEHIRN_TOOLS,
            grund=(
                "Dieses Werkzeug steht dem Gehirn nicht zur Verfügung. "
                "Der Aufruf lief nicht — gib die Arbeit mit worker_start "
                "als Auftrag in den Hintergrund."
            ),
        )
    else:
        if rolle == "worker":
            rollen_gesperrt = worker_ausschluss()
            rollen_grund = (
                "Dieses Werkzeug steht einem Worker nicht zur Verfügung. "
                "Der Aufruf lief nicht — arbeite ohne ihn weiter."
            )
        else:
            rollen_gesperrt = WORKER_STEUERUNG | NUR_WORKER
            rollen_grund = (
                "Dieses Werkzeug gehört zum Hintergrund-Betrieb und steht in "
                "diesem Lauf nicht zur Verfügung. Der Aufruf lief nicht — "
                "arbeite ohne ihn weiter."
            )
        tool_calls = _aussortieren(
            tool_calls, deferred,
            erlaubt=lambda name: name not in rollen_gesperrt,
            grund=rollen_grund,
        )
    # Der Herkunfts-Spiegel, gleich neben dem Rollen-Spiegel und aus demselben
    # Grund. Nur noch **eine** Richtung, seit die Matrix umgedreht ist
    # (`ai_tool_registry.herkunft_schnitt`): aus dem Panel erreicht kein
    # Werkzeug den Rechner des Benutzers, auch kein halluzinierter Name. Der
    # Katalogschnitt ist Fuehrung, die Schranke steht hier. Aussortiert statt
    # geworfen — der Lauf soll ohne den Aufruf weiterarbeiten.
    if herkunft != "desktop":
        tool_calls = _aussortieren(
            tool_calls, deferred,
            erlaubt=lambda name: name not in DESKTOP_TOOLS,
            grund=(
                "Werkzeuge für den Rechner des Benutzers laufen nur aus der "
                "Smart-System-App. Der Aufruf lief nicht — arbeite ohne ihn "
                "weiter."
            ),
        )
    if guardian is not None:
        # In einer Heilung ist die Werkzeugmenge kleiner und der Server fest.
        # Beides steht hier und nicht im Prompt: die Eingabe dieses Laufs stammt
        # teilweise aus Serverlogs, also aus Text, den ein Spieler geschrieben
        # haben kann. Eine Regel, die das Modell befolgen *soll*, ist gegen so
        # etwas keine Schranke.
        #
        # Ein Werkzeug außerhalb der Menge wird **aussortiert, nicht geworfen**:
        # der Aufruf wandert mit Begründung nach `deferred`, das Modell bekommt
        # eine Antwort und arbeitet weiter. Vorher riss ein einziges solches
        # Werkzeug den ganzen Heilungslauf ab — die Ausnahme verließ diese
        # Funktion, wurde im Fehlerbehandler zu `AI_TOOL_REJECTED` und beendete
        # den Lauf mit 'failed'. Der gestörte Server blieb stehen, und der
        # Bericht an den Betreiber war leer. Ausgerechnet `read_skill` ist so
        # ein Fall: es liegt in `READ_TOOLS`, kommt an der Prüfung oben vorbei,
        # und der Systemprompt bewirbt es. Die Allowlist bleibt unverändert
        # scharf — ausgeführt wird nach wie vor nichts.
        tool_calls = _aussortieren(
            tool_calls, deferred,
            erlaubt=lambda name: name in GUARDIAN_HEILUNG_TOOLS,
            grund=(
                "Dieses Werkzeug steht in einer Guardian-Heilung nicht zur "
                "Verfügung. Der Aufruf lief nicht — arbeite ohne ihn weiter."
            ),
        )
        # Die Serverbindung erst danach und in einer eigenen Schleife: sie
        # **wirft**, und sie soll nur die Aufrufe treffen, die ueberhaupt
        # laufen duerften. Ein fremder Server in einer Heilung ist keine
        # Nachlaessigkeit des Modells, sondern der Fall, in dem der Lauf
        # stehenbleiben soll — deshalb wandert dieser Zweig nicht mit ins
        # Aussortieren.
        for call in tool_calls:
            genannt = call.arguments.get("server_id")
            if call.name in SERVER_READ_TOOLS and genannt is not None:
                # `int(genannt)` stand hier ohne Typpruefung. Die Argumente
                # kommen aus dem Modell, und ein `"abc"`, eine Liste oder ein
                # Woerterbuch ergaben dort einen blanken `ValueError` bzw.
                # `TypeError` — keine `AiActionValidationError`, mit der das
                # Modell etwas anfangen kann, sondern ein Abbruch des ganzen
                # Laufs. In einer unbeaufsichtigten Heilung heisst das: der
                # Server bleibt stehen, weil das Modell einmal ein Argument
                # falsch getippt hat. Der Vorschlagspfad prueft an derselben
                # Stelle laengst mit `isinstance`.
                try:
                    nummer = int(genannt) if not isinstance(genannt, bool) else None
                except (TypeError, ValueError):
                    nummer = None
                if nummer is None or nummer != guardian.server_id:
                    raise AiActionValidationError(
                        "In einer Guardian-Heilung ist nur der betroffene Server erlaubt"
                    )
    if aufgabe is not None:
        # Dieselbe Durchsetzung wie oben, mit einem Unterschied: **keine**
        # Serverbindung. Ein stehender Auftrag gehoert keinem Server — "sieh
        # nach meinen Servern" meint alle, die der Benutzer sehen darf, und
        # welche das sind, entscheidet `_resolve_server` bei jedem Aufruf
        # ohnehin einzeln.
        #
        # Aussortiert statt geworfen, aus demselben Grund wie oben: der
        # nächtliche Auftrag soll an einem Werkzeug scheitern, nicht am Lauf.
        menge = aufgaben_tools(aufgabe.kind)
        tool_calls = _aussortieren(
            tool_calls, deferred,
            erlaubt=lambda name: name in menge,
            grund=(
                "Dieses Werkzeug steht in einer geplanten Aufgabe nicht zur "
                "Verfügung. Der Aufruf lief nicht — arbeite ohne ihn weiter."
            ),
        )
    # Zugriff und Unterhaltung **einmal** pruefen, bevor irgendetwas laeuft.
    # Eine kurze eigene Transaktion: die Ausfuehrung bringt jetzt ihre eigenen
    # Sitzungen mit, und eine ueber die ganze Runde offene waere genau das, was
    # hier abgeschafft wird.
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        if get_owned_conversation(db, conversation_id, user) is None:
            raise AiActionValidationError("Unterhaltung ist nicht mehr verfuegbar")

    geordnete_aufrufe = []
    bekannte_call_ids: set[str] = set()
    for call in call_reihenfolge or [*tool_calls, *(item[0] for item in deferred)]:
        if call.id not in bekannte_call_ids:
            bekannte_call_ids.add(call.id)
            geordnete_aufrufe.append(call)
    assistant_call = _aufrufnachricht(geordnete_aufrufe, rundentext)

    # ── Ausfuehren ───────────────────────────────────────────────────────
    #
    # Das Schloss entsteht je Runde und nicht als Modulwert. Ein
    # `asyncio.Semaphore` bindet sich an die Ereignisschleife, die ihn zuerst
    # benutzt; die Testsuite legt je Test eine neue an, und ein
    # weitergereichter Wert waere dort ein Fehler, der erst beim zweiten Test
    # auffaellt. Die aeussere Grenze halten ohnehin der Standard-Threadpool und
    # der Verbindungspool.
    breite = ai_stream._werkzeug_nebenlaeufigkeit()
    schloss = schloss or asyncio.Semaphore(breite)
    vorab_aufgaben = vorab_aufgaben or {}

    async def _einer(call):
        vorab = vorab_aufgaben.pop(call.id, None)
        if vorab is not None:
            try:
                ergebnis = await vorab
            except Exception as exc:
                # Eine fruehe Aufgabe ist nur eine Latenzoptimierung. Fiel sie
                # technisch aus, folgt der normale, bereits freigegebene Weg.
                logger.info(
                    "Fruehes Lesewerkzeug wird regulär wiederholt tool=%s error=%s",
                    call.name,
                    type(exc).__name__,
                )
                ergebnis = await _lesewerkzeug_ausfuehren(
                    user_id=user_id,
                    call=call,
                    herkunft=herkunft,
                    familie=familie,
                    prefetch_session_id=prefetch_session_id,
                    schloss=schloss,
                )
        else:
            ergebnis = await _lesewerkzeug_ausfuehren(
                user_id=user_id,
                call=call,
                herkunft=herkunft,
                familie=familie,
                prefetch_session_id=prefetch_session_id,
                schloss=schloss,
            )
        _, wert, anzeige = ergebnis
        # **Sofort melden.** Hier stand nichts — die Chips gingen erst raus,
        # nachdem die ganze Runde fertig war (die Schleife im Aufrufer). Bei
        # neun Aufrufen sah der Benutzer siebenundzwanzig Sekunden nichts und
        # dann alles auf einmal.
        if run_id is not None:
            ai_run_broker.veroeffentlichen(run_id, "tool", anzeige)
        return call, wert, anzeige

    antworten: dict[str, dict] = {}
    display: list[dict] = []
    behalten: list[tuple[object, object]] = []
    spent = 0
    erledigt = 0
    offen = list(tool_calls)

    # **Ansagen, bevor es läuft.** Hier stehen die Aufrufe der Runde fest —
    # aussortiert ist aussortiert, geprüft ist geprüft —, und ausgeführt ist
    # noch keiner. Es ist die einzige Stelle, an der beides gleichzeitig gilt.
    # Warum die Ansage so aussieht, wie sie aussieht, steht bei `_werkzeuge_ansagen`.
    _werkzeuge_ansagen(run_id, offen)

    # **In Wellen, nicht alles auf einmal.** Das Budget hat zwei Aufgaben, und
    # nur eine davon ist der Kontext.
    #
    # Die andere ist der Node: `read_server_logs` liefert bis zu 24.000 Zeichen,
    # zwei davon fuellen die Runde. Ein Modell, das zehn Logauszuege nebeneinander
    # anfordert, hat frueher **zwei** ausgeloest — die Pruefung stand vor der
    # Ausfuehrung. Ein blosses `gather` ueber alle haette zehn Docker-Aufrufe
    # gemacht und acht Ergebnisse weggeworfen. Gleich schnell, dreifache Last auf
    # einem Rechner, der nebenbei Spieleserver bedient.
    #
    # Eine Welle ist so breit wie die Nebenlaeufigkeit. Der Normalfall — bis zu
    # acht Aufrufe — laeuft damit vollstaendig gleichzeitig, und ein
    # durchgedrehtes Modell wird nach der ersten Welle gebremst. Auf SQLite ist
    # die Breite eins; dort ergibt sich exakt das alte Verhalten, was die
    # bestehenden Zusagen der Testsuite unangetastet laesst.
    while offen:
        if erledigt and spent >= ai_stream.MAX_TOOL_RESULT_CHARS_PER_ROUND:
            for call in offen:
                deferred.append((call, (
                    "Fuer diese Runde war kein Platz mehr. Der Aufruf lief "
                    "nicht — stelle ihn in der naechsten Runde erneut."
                )))
            break
        welle, offen = offen[:breite], offen[breite:]
        # `gather` haelt die **Aufrufreihenfolge** in seinem Ergebnis fest, auch
        # wenn die Aufrufe in beliebiger Reihenfolge fertig werden. Das ist
        # wichtig: das Budget und `_serverbezug` ("der letzte gewinnt") haengen
        # daran. Gemeldet wird trotzdem in Fertigstellungsreihenfolge — das ist
        # die Reihenfolge, in der es etwas zu sehen gibt.
        for call, wert, anzeige in await asyncio.gather(
            *(_einer(call) for call in welle)
        ):
            erledigt += 1
            # Jeder ausgefuehrte Aufruf gehoert in den Verlauf und ins
            # Protokoll — auch der, dessen Ergebnis gleich nicht mehr in die
            # Runde passt. Er ist gelaufen, und das ist die Tatsache.
            display.append(anzeige)
            behalten.append((call, wert))
            # Das Ergebnis wird ausdruecklich als unvertrauenswuerdig
            # gekennzeichnet. Genau hier kommt der Text an, den ein Spieler ueber
            # den Chat eines Gameservers in dessen Log geschrieben hat:
            # read_server_logs liefert bis zu 24.000 Zeichen, die vollstaendig
            # von aussen stammen koennen. Anhaenge tragen dieses Label seit jeher
            # (ai_attachment_service), Tool-Ergebnisse bisher nicht — obwohl sie
            # der offenere Kanal sind.
            serialized = json.dumps(
                {"untrusted": True, "tool": call.name, "data": wert},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            # Der erste Aufruf kommt immer durch: sonst kaeme ein einzelner
            # grosser Logauszug nie an.
            #
            # Innerhalb einer Welle kann das Budget erst **nach** der Ausfuehrung
            # reissen — die Groesse steht vorher nicht fest. Die Begruendung sagt
            # deshalb etwas anderes als die oben: dort lief der Aufruf nicht,
            # hier lief er und sein Ergebnis passte nicht mehr. Ein Modell, das
            # die falsche Auskunft bekommt, holte ein bereits erledigtes
            # `remember` ein zweites Mal.
            if erledigt > 1 and spent >= ai_stream.MAX_TOOL_RESULT_CHARS_PER_ROUND:
                deferred.append((call, (
                    "Der Aufruf lief, aber sein Ergebnis passte nicht mehr in "
                    "diese Runde. Frag gezielter nach — weniger Zeilen, engerer "
                    "Pfad — oder hol es in der naechsten Runde."
                )))
                continue
            spent += len(serialized)
            antworten[call.id] = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": serialized,
            }
    # Erst hier: die Schleife oben legt selbst weitere Aufrufe zurueck, sobald
    # das Budget aufgebraucht ist. Wuerden die Absagen vorher erzeugt, blieben
    # genau diese `tool_call_id` ohne Antwort — und manche Anbieter weisen die
    # naechste Anfrage deswegen ab.
    for call, reason in deferred:
        antworten[call.id] = {
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps({
                "executed": False, "reason": reason,
            }, ensure_ascii=True, separators=(",", ":")),
        }
    # Der Anbieter erwartet genau eine Antwort je Call. Die sichtbaren
    # Ergebnisse erscheinen weiterhin bei Fertigstellung, der Rueckkanal
    # bleibt jedoch in der Reihenfolge des vom Anbieter gelieferten Calls.
    results = [assistant_call, *(antworten[call.id] for call in geordnete_aufrufe)]

    def _festhalten() -> dict | None:
        """Protokoll, Ergebnisse und Serverbezug — in **einer** Transaktion.

        Auch das laeuft im Thread. Es ist wenig Arbeit, aber
        `anlagenwissen_nachtrag` liest das Betriebswissen einer Anlage, und die
        Ereignisschleife hat waehrenddessen Besseres zu tun.
        """
        with SessionLocal() as db:
            user_jetzt = db.get(User, user_id)
            conversation = (
                get_owned_conversation(db, conversation_id, user_jetzt)
                if user_jetzt is not None else None
            )
            if conversation is None:
                # Der Chat wurde waehrend der Runde geleert. Die Werkzeuge sind
                # gelaufen, ihre Ergebnisse gehen trotzdem ans Modell zurueck —
                # nur festzuhalten gibt es nichts mehr.
                return None
            # Persistieren, damit eine Rueckfrage im selben Chat die gerade
            # gelesenen Daten noch sieht. Ohne das musste das Modell sie neu
            # holen — oder antwortete ohne sie, obwohl es sie selbst geholt hatte.
            for call, wert in behalten:
                db.add(AiToolResult(
                    id=str(uuid4()),
                    conversation_id=conversation.id,
                    run_id=run_id,
                    tool_name=call.name,
                    result_json=json.dumps(
                        wert, ensure_ascii=True, separators=(",", ":")
                    ),
                ))
            _lesezugriffe_protokollieren(
                db, user_id=user_id, eintraege=display, correlation_id=correlation_id
            )
            # In derselben Sitzung und demselben Commit wie das
            # Zugriffsprotokoll. Beide halten dieselbe Tatsache fest — in
            # welchen Server die KI gesehen hat —, und beide sollen entweder
            # stehen oder nicht.
            bezug = _serverbezug(display)
            ai_run_service.serverbezug_merken(db, run_id=run_id, server_id=bezug)
            # Jetzt erst steht fest, um welche Anlage es geht. Ihr Betriebswissen
            # gehoert in **diese** Runde und nicht erst in die naechste Nachricht:
            # sonst antwortet das Modell auf "warum kommt keiner rein?" ohne den
            # Satz, der die Antwort ist.
            #
            # `nachtrag` faehrt als dritter Rueckgabewert mit, statt hier an
            # `provider_messages` zu haengen: die Funktion kennt die Liste nicht,
            # und sie soll sie auch nicht kennen — sie fuehrt Werkzeuge aus.
            nachtrag = None
            if bezug is not None and anlagenwissen_noetig:
                # Die Frage erst hier holen — sie wird nur fuer die Auswahl und
                # den Nutzungszaehler des Anlagenwissens gebraucht.
                nachricht = (
                    db.get(AiMessage, frage_id) if frage_id is not None else None
                )
                query = ""
                if nachricht is not None and nachricht.content:
                    query = str(nachricht.content)
                elif provider_messages:
                    for msg in reversed(provider_messages):
                        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), str):
                            query = msg["content"]
                            break
                import re
                query = re.sub(r"^\[\d{2}\.\d{2}\.\s*\d{2}:\d{2}\]\s*", "", query)
                import sys
                _mod = sys.modules.get("services.ai_stream_service")
                _fn = getattr(_mod, "anlagenwissen_nachtrag", None) or ai_stream.anlagenwissen_nachtrag
                nachtrag = _fn(
                    db,
                    user_id=user_id,
                    server_id=bezug,
                    query=query,
                )
            db.commit()
            return nachtrag

    nachtrag = await asyncio.to_thread(_festhalten)
    return results, display, nachtrag


def _lesezugriffe_protokollieren(
    db, *, user_id: int, eintraege: list[dict], correlation_id: str | None
) -> None:
    """Haelt fest, in welche Server die KI hineingesehen hat.

    Die Schreibseite hinterliess vier Audit-Eintraege je Aktion, die Leseseite
    keinen einzigen. Damit war die Frage "hat die KI meine Logs gelesen?" nicht
    beantwortbar — obwohl `read_server_logs` bis zu 24.000 Zeichen aus einem
    fremden Server holt und `list_server_files` dessen Verzeichnisbaum.

    Protokolliert werden **serverbezogene** Lesewerkzeuge. Globale Aufrufe
    (`list_my_servers`, `read_skill`, `search_memory`) bleiben draussen: sie
    bewegen sich im eigenen Bereich des Benutzers, und Gedaechtnis wie Skills
    fuehren ohnehin ihr eigenes Protokoll.

    Entdoppelt je Runde: fragt das Modell neun Server nebeneinander ab, sind das
    neun Eintraege — fragt es denselben Server neunmal, ist es einer. Ein
    Protokoll, das man wegen Rauschen nicht mehr liest, ist keins.
    """
    gesehen: dict[tuple[str, int], int] = {}
    for eintrag in eintraege:
        name = eintrag.get("tool_name")
        server_id = eintrag.get("server_id")
        if name not in SERVER_READ_TOOLS or not isinstance(server_id, int):
            continue
        schluessel = (name, server_id)
        gesehen[schluessel] = gesehen.get(schluessel, 0) + 1
    for (name, server_id), anzahl in gesehen.items():
        audit_service.record_privileged_action(
            db,
            user_id=user_id,
            action="ai.tool.read",
            target_type="server",
            target_id=server_id,
            details={
                "tool": name,
                **({"count": anzahl} if anzahl > 1 else {}),
            },
            origin="ai",
            correlation_id=correlation_id,
        )


def _ablehnung_protokollieren(
    *, user_id: int, tool_name: str, grund: str, correlation_id: str
) -> None:
    """Haelt einen abgelehnten Schreibversuch fest — in **eigener** Sitzung.

    Eigene Sitzung, weil die Ablehnung den Rollback der laufenden Runde
    ueberleben muss. Genau daran ist der Nachweis bisher gescheitert: die
    Transaktion, in der ein abgelehnter Aufruf entsteht, wird nie committet.

    Ein versuchter Schreibzugriff auf etwas, das jemand nicht anfassen darf, ist
    das Ereignis, das ins Protokoll gehoert — auch und gerade dann, wenn er
    nicht durchging.

    Der Grund wird redigiert und gekuerzt. Er stammt bei fast allen Werkzeugen
    aus einer festen Menge eigener Meldungen; bei `propose_blueprint_change`
    kann jedoch ein vom Modell gewaehlter Pfad im Text landen, und dieses Modell
    hat seinerseits fremden Logtext gelesen. Ein Auditeintrag ist kein Ort fuer
    ungefilterten Fremdtext.

    Scheitert das Protokollieren selbst, bleibt es bei der Ablehnung — sie darf
    nicht daran haengen, ob nebenbei ein Eintrag geschrieben werden konnte.
    """
    try:
        with SessionLocal() as protokoll:
            audit_service.record_privileged_action(
                protokoll,
                user_id=user_id,
                action="ai.action.rejected",
                target_type="ai_action",
                target_id=None,
                details={
                    "tool": tool_name,
                    "reason": redact_sensitive_text(grund)[:200],
                },
                origin="ai",
                correlation_id=correlation_id,
                commit=True,
            )
    except Exception:
        logger.warning("Ablehnung konnte nicht protokolliert werden tool=%s", tool_name)


async def _leserunde_ausfuehren(
    *,
    user_id: int,
    conversation_id: str,
    current_usage: StreamUsage,
    deferred_calls: list,
    vorbereitung: "_Vorbereitung",
    run_id: str,
    guardian: "GuardianKontext | None",
    aufgabe: "AufgabenKontext | None",
    rolle: str,
    herkunft: str,
    zustand: dict,
    rundentext: str,
    provider_messages: list[dict],
    chunks: list[str],
    thoughts: list[str],
    denknaht: str,
    vorab_aufgaben: dict[str, asyncio.Task] | None = None,
    schloss: asyncio.Semaphore | None = None,
    call_reihenfolge=None,
) -> str:
    """Die Lesephase einer Runde: Werkzeuge ausfuehren, Folgen anhaengen.

    Haengt Folgenachrichten und (einmal je Lauf) das Anlagenwissen an
    `provider_messages` an und setzt die Rundennaht in `chunks` — alles in
    place, die Listen sind dieselben Objekte wie im Orchestrator. Zurueck
    kommt nur die `denknaht`: der bestellte Absatz fuer den ersten
    Gedanken der naechsten Runde.
    """
    followup, used_tools, nachtrag = await ai_stream._tool_followup_messages(
        user_id=user_id,
        conversation_id=conversation_id,
        tool_calls=current_usage.tool_calls,
        deferred=deferred_calls,
        correlation_id=vorbereitung.request_id,
        run_id=run_id,
        guardian=guardian,
        aufgabe=aufgabe,
        rolle=rolle,
        herkunft=herkunft,
        # Das Gerät neben der Welt, und aus dem Zustand statt aus einem
        # Parameter — dieselbe Quelle wie in `_desktop_behandeln` eine Phase
        # weiter oben. Hier läuft vielleicht das fünfte Segment eines Laufs,
        # der vor Minuten in der App begonnen hat; die Anfrage von damals gibt
        # es nicht mehr, wohl aber ihre eingefrorene Familie. Gebraucht wird
        # sie von genau einem Werkzeug: `worker_start` vererbt sie an den
        # Auftrag, den es anlegt. Ohne diese Zeile begänne **jeder**
        # Worker-Lauf mit familie=None — und weil `worker_antwort` die Familie
        # seines Vorgängers weiterreicht, bliebe das für immer so.
        familie=familie_aus_zustand(zustand),
        prefetch_session_id=(
            str(zustand["prefetch_session_id"])
            if zustand.get("prefetch_session_id") else None
        ),
        # Nur solange der Lauf es noch nicht bekommen hat. Die
        # Entscheidung fällt weiterhin unten — dort steht die Marke —,
        # aber gelesen wird jetzt gar nicht erst, was ohnehin
        # weggeworfen würde. Ein Gehirn bekommt es nie: Anlagenwissen
        # ist Serverwissen, und das gehoert den Workern (§7).
        anlagenwissen_noetig=(
            rolle != "gehirn" and not zustand.get("anlagenwissen_gereicht")
        ),
        rundentext=rundentext,
        # Die Frage des Laufs, nicht die dieser Runde: das Anlagenwissen waehlt
        # und zaehlt danach, und was dieser Lauf wissen wollte, steht am Anfang.
        # Als Kennung, damit der Laufzustand keinen Klartext dazubekommt.
        frage_id=zustand.get("user_message_id"),
        provider_messages=provider_messages,
        vorab_aufgaben=vorab_aufgaben,
        schloss=schloss,
        call_reihenfolge=call_reihenfolge,
    )
    provider_messages.extend(followup)
    # Das Betriebswissen der Anlage, sobald feststeht welche. Genau
    # einmal je Lauf: `zustand` ueberlebt die Unterbrechung, und nach
    # einer Bestaetigung wuerde es sonst erneut angehaengt — dieselben
    # Zeilen ein zweites Mal, mit anderem Zaehlerstand daneben.
    if nachtrag is not None and not zustand.get("anlagenwissen_gereicht"):
        zustand["anlagenwissen_gereicht"] = True
        provider_messages.append(nachtrag)
    # Hier stand die Schleife, die alle Werkzeugchips auf einmal
    # herausgab — **nach** der ganzen Runde. Sie ist nach
    # `_tool_followup_messages` gewandert und meldet jeden Aufruf,
    # sobald er fertig ist. `used_tools` bleibt der Rueckgabewert, weil
    # es weiterhin das Protokoll und den Serverbezug traegt.
    #
    # Und hier endet ein Absatz. `chunks` sammelt den Text **aller**
    # Runden in einer flachen Liste, und `"".join(chunks)` weiter unten
    # klebte deshalb den Schlusssatz dieser Runde an das erste Zeichen
    # der naechsten: „…damit die Mail nur bestaetigte Informationen
    # enthaelt.Ich pruefe jetzt den Status…“ — so stand es in einer
    # Berichtsmail. Innerhalb einer Runde ist das nahtlose Aneinander
    # richtig (es sind Token-Bruchstuecke), zwischen zwei Runden liegt
    # ein Werkzeugaufruf, und `MITREDEN` verlangt davor einen ganzen
    # Satz. Der Umbruch gehoert also genau hierhin, an die Naht.
    if chunks and not chunks[-1].endswith("\n"):
        chunks.append("\n\n")
    # Und dasselbe für den Denktext, der über alle Runden in derselben
    # flachen Liste liegt und mit `"".join(thoughts)` genauso
    # zusammenklebte. `thoughts and …` ist Pflicht: bei abgeschaltetem
    # Denken ist die Liste leer, und ein führender Umbruch wäre ein
    # neuer Fehler.
    #
    # Bestellt statt gesetzt: der Umbruch geht oben zusammen mit dem
    # ersten Gedanken der nächsten Runde als `reasoning`-Ereignis
    # hinaus, damit der Live-Text Zeichen für Zeichen derselbe bleibt
    # wie der gespeicherte. Hängte man ihn hier gleich an `thoughts`
    # und veröffentlichte ihn als eigenes Ereignis, entstünde beim
    # Vermittler ein Denkabschnitt aus nichts als zwei Umbrüchen — und
    # denkt die nächste Runde nicht mehr, zeichnet die Oberfläche
    # daraus einen leeren Kasten „Nachgedacht“.
    if thoughts and not thoughts[-1].endswith("\n"):
        denknaht = "\n\n"
    return denknaht

