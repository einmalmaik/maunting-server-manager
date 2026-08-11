"""Die Schleife eines Laufs: Anbieter fragen, Werkzeuge fahren, Vorschlaege anlegen.

Der Zug der KI hing frueher an einem HTTP-Request — er war ein Generator, den
der Browser am Leben hielt. Hier ist er ein **Segment eines Laufs**
(``segment_ausfuehren``): er holt seinen Zustand aus der Datenbank, arbeitet, und
legt ihn wieder ab. Wer zusieht, geht ihn nichts an; dafuer ist
``ai_run_broker`` da.

Damit die Aufgabenteilung nicht wieder verschwimmt:

* ``ai_run_service``   — wann ein Segment laeuft und was zwischen zweien ueberlebt.
* ``ai_stream_service`` — was **in** einem Segment passiert. Kennt keinen Zeitplan.
* ``ai_run_broker``    — wer zusehen darf. Kennt keines von beiden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import AsyncIterator
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    AiActionProposal, AiMessage, AiProvider, AiRun, AiToolResult, AiUsageEvent, User,
)
from models.ai_run import BEENDET as AUSGELAUFEN
from services.ai_chat_service import get_owned_conversation
from services import ai_attachment_service, ai_run_broker, ai_run_service, audit_service
from services.ai_action_errors import (
    AiActionStateError,
    AiActionValidationError,
)
from services.ai_action_service import (
    execute_read_tool,
    provider_tool_definitions,
    question_payload,
)
from services.ai_proposal_service import (
    create_proposal,
    execute_autonomously,
    proposal_response,
)
from services.ai_tool_registry import (
    ASK_TOOLS,
    READ_TOOLS,
    SERVER_READ_TOOLS,
    SKILL_TOOLS,
    WRITE_TOOLS,
)
from services.ai_context_service import (
    anlagenwissen_nachtrag,
    build_provider_messages,
    estimate_reserved_tokens,
    message_character_count,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_provider_service import estimate_cost_microunits, resolve_api_key
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
    reserve_ai_usage,
)
from services.ai_limit_service import TOKEN_LIMIT_MAX
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    MAX_REASONING_CHARS,
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)
# Wieviel Ergebnistext eine Runde hoechstens erzeugen darf.
#
# Die Grenze war frueher eine feste Anzahl Aufrufe. Das war das falsche Mass:
# zwanzig Statusabfragen sind zusammen kleiner als ein einziger Logauszug, und
# `read_server_logs` liefert bis zu 24.000 Zeichen. Eine Zahl behandelt beide
# gleich und wird dadurch entweder zu eng (die KI kann nicht durchfragen) oder
# zu weit (ein halbes Kontextfenster in einer Runde).
#
# Gezaehlt wird deshalb das, was tatsaechlich knapp ist. Billige Aufrufe laufen
# alle; sobald das Budget aufgebraucht ist, werden die restlichen vertagt statt
# abgewiesen. Rund 48.000 Zeichen sind grob 12.000 Tokens — Platz fuer etwa
# dreissig Statusabfragen oder zwei volle Logauszuege.
MAX_TOOL_RESULT_CHARS_PER_ROUND = 48_000
# Absolute Reissleine gegen ein durchgedrehtes Modell. Kein Mensch stellt eine
# Frage, die mehr als das rechtfertigt; wer mehr schickt, antwortet nicht
# gruendlich, sondern fehlerhaft.
MAX_TOOL_CALLS = 32
# Leserunden **je Lauf**, nicht je Nachricht.
#
# Hier standen vier. Das war die Zahl aus der Zeit, in der ein Zug eine Frage
# beantwortete: lesen, lesen, antworten. Fuer einen Auftrag wie "richte den
# Server ein, stell das ein, starte ihn und sag Bescheid" ist sie zu klein —
# die KI kam bis zur Haelfte und musste aufhoeren, obwohl sie wusste, was noch
# fehlte. Genau die Beschwerde: *"die muss das wirklich komplett bis zum Ende
# machen, Aufgaben zu Ende bringen, Ende zu Ende."*
#
# Sechzehn ist keine Beliebigkeit, sondern die Obergrenze der Anbieteraufrufe
# eines Laufs: mehr als sechzehn Leserunden hat noch keine Diagnose gebraucht,
# und die Grenze bricht nicht ab, sondern nimmt die Werkzeuge weg. Das Modell
# antwortet dann aus dem, was es hat.
MAX_TOOL_ROUNDS = 16
# Schreibrunden je Lauf. Zwei reichten fuer "pass die Config an und starte
# danach" — aber nicht fuer eine Einrichtung aus Anlegen, Konfigurieren,
# Starten und Melden. Acht deckt jede Bitte ab, die ein Mensch in einem Absatz
# formuliert, und bleibt weit unter dem, was ein durchgedrehtes Modell braeuchte,
# um Schaden anzurichten — jede einzelne Aktion durchlaeuft weiterhin
# Rechtepruefung und, wo noetig, die Bestaetigung eines Menschen.
MAX_WRITE_ROUNDS = 8
# Wie oft derselbe Lesewerkzeugaufruf mit **denselben** Argumenten laufen darf.
# Ein Modell, das die gleiche Auskunft zum dritten Mal holt, bekommt keine neue
# Antwort — es haengt. Der Aufruf wird dann nicht ausgefuehrt, sondern begruendet
# abgelehnt: eine Grenze, die erklaert, statt einer, die abbricht.
MAX_GLEICHE_AUFRUFE = 3


def sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _finalize_stream(
    *,
    message_id: str,
    usage_event_id: int,
    content: str,
    provider_total_tokens: int | None,
    estimated_actual_tokens: int,
    failed: bool,
    had_output: bool,
    token_price_cents_per_million: int | None = None,
    reasoning: str = "",
    question: dict | None = None,
) -> None:
    with SessionLocal() as db:
        message = db.get(AiMessage, message_id)
        usage_event = db.get(AiUsageEvent, usage_event_id)
        if usage_event is None:
            # Ohne Verbrauchszeile gibt es nichts mehr abzurechnen.
            logger.warning("AI usage event missing at finalization message_id=%s", message_id)
            return
        if message is not None:
            message.content = content
            # Denkschritte werden mitgespeichert, damit der aufklappbare Block
            # nach einem Neuladen der Seite noch da ist. Redigiert wie jeder
            # andere Modelltext auch: ein Modell kann in seinen Ueberlegungen
            # genauso einen Key wiederholen wie in der Antwort.
            message.reasoning = (
                redact_sensitive_text(reasoning)[:MAX_REASONING_CHARS] or None
            )
            # Die Rueckfrage gehoert zur Nachricht. Sie ist bereits durch
            # `question_payload()` geprueft, gekuerzt und redigiert — hier wird
            # nur noch abgelegt.
            if question is not None:
                message.question_json = json.dumps(
                    question, ensure_ascii=True, separators=(",", ":")
                )
            message.status = "failed" if failed else "complete"
        else:
            # Die Nachricht wurde waehrend des Streams entfernt (z. B. Chat
            # geloescht). Die Reservierung muss trotzdem abgeschlossen werden:
            # sonst bliebe sie dauerhaft "reserved" und wuerde Kontingent sowie
            # einen Nebenlaeufigkeitsplatz des Benutzers permanent blockieren.
            logger.warning("AI message missing at finalization message_id=%s", message_id)
        if failed and not had_output:
            fail_ai_usage(db, usage_event)
        else:
            # Nach partieller Ausgabe darf Verbrauch nicht als null verbucht
            # werden. Ohne finale Provider-Usage gilt konservativ die Reserve.
            actual_tokens = provider_total_tokens
            if actual_tokens is None:
                actual_tokens = (
                    usage_event.reserved_tokens if failed else estimated_actual_tokens
                )
            accounted_tokens = min(TOKEN_LIMIT_MAX, max(0, actual_tokens))
            # Kosten folgen den tatsaechlich verbuchten Tokens. Ohne gepflegten
            # Preis bleibt die Reserve (null) stehen; nie weniger als reserviert,
            # damit eine Ueberschreitung nicht nachtraeglich verschwindet.
            actual_cost = usage_event.reserved_cost_microunits
            if token_price_cents_per_million:
                actual_cost = max(
                    actual_cost,
                    (accounted_tokens * int(token_price_cents_per_million)) // 100,
                )
            complete_ai_usage(
                db,
                usage_event,
                actual_tokens=accounted_tokens,
                actual_cost_microunits=actual_cost,
            )
        db.commit()


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


def _tool_followup_messages(
    *, user_id: int, conversation_id: str, tool_calls, deferred=(),
    correlation_id: str | None = None, run_id: str | None = None,
) -> tuple[list[dict], list[dict], dict | None]:
    """Fuehrt Lesewerkzeuge aus und baut daraus die Folge-Nachrichten.

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

    Der dritte Rueckgabewert ist ein **Nachtrag zum Kontext**: das Wissen der
    Anlage, um die es in dieser Runde ging. Vorher konnte es nicht dabei sein —
    beim Anlegen des Laufs war der Server noch nicht bekannt. Ist nichts
    nachzureichen, ist er None.
    """
    deferred = [(call, reason) for call, reason in deferred]
    if len(tool_calls) + len(deferred) > MAX_TOOL_CALLS:
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    if any(call.name not in READ_TOOLS for call in tool_calls):
        raise AiActionValidationError("Ungueltige Read-Tool-Sequenz")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            raise AiActionValidationError("Unterhaltung ist nicht mehr verfuegbar")
        assistant_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=True),
                    },
                }
                for call in [*tool_calls, *(item[0] for item in deferred)]
            ],
        }
        results: list[dict] = [assistant_call]
        # Was der Benutzer im Chat sehen soll: welches Werkzeug lief und womit.
        # Bewusst ohne das Ergebnis — ein Logausschnitt gehoert nicht ungefragt
        # in den sichtbaren Verlauf, und die Antwort fasst ihn ohnehin zusammen.
        display: list[dict] = []
        spent = 0
        for index, call in enumerate(tool_calls):
            # Budget statt Stueckzahl. Wer schon etwas bekommen hat und das
            # Budget ausgeschoepft sieht, hoert auf — der Rest wird vertagt,
            # nicht abgewiesen. Der erste Aufruf laeuft immer: sonst kaeme ein
            # einzelner grosser Logauszug nie durch.
            if index > 0 and spent >= MAX_TOOL_RESULT_CHARS_PER_ROUND:
                deferred.append((call, (
                    "Fuer diese Runde war kein Platz mehr. Der Aufruf lief "
                    "nicht — stelle ihn in der naechsten Runde erneut."
                )))
                continue
            failed_reason: str | None = None
            try:
                value = execute_read_tool(
                    db,
                    user=user,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
            except AiActionValidationError as exc:
                # Fehlendes Recht, fremde Server-ID, ungueltige Argumente. Das
                # Modell soll es erfahren und weitermachen koennen; frueher riss
                # ein solcher Aufruf die gesamte Antwort ab.
                failed_reason = str(exc)
                value = {"error": failed_reason}
            # Persistieren, damit eine Rueckfrage im selben Chat die gerade
            # gelesenen Daten noch sieht. Ohne das musste das Modell sie neu
            # holen — oder antwortete ohne sie, obwohl es sie selbst geholt hatte.
            db.add(AiToolResult(
                id=str(uuid4()),
                conversation_id=conversation.id,
                tool_name=call.name,
                result_json=json.dumps(value, ensure_ascii=True, separators=(",", ":")),
            ))
            # Das Ergebnis wird ausdruecklich als unvertrauenswuerdig gekennzeichnet.
            # Genau hier kommt der Text an, den ein Spieler ueber den Chat eines
            # Gameservers in dessen Log geschrieben hat: read_server_logs liefert
            # bis zu 24.000 Zeichen, die vollstaendig von aussen stammen koennen.
            # Anhaenge tragen dieses Label seit jeher (ai_attachment_service),
            # Tool-Ergebnisse bisher nicht — obwohl sie der offenere Kanal sind.
            serialized = json.dumps(
                {"untrusted": True, "tool": call.name, "data": value},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            spent += len(serialized)
            results.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": serialized,
            })
            entry = {
                "tool_name": call.name,
                "server_id": call.arguments.get("server_id")
                if isinstance(call.arguments.get("server_id"), int)
                else None,
                # Ein gescheiterter Aufruf gehoert sichtbar in den Verlauf.
                # Sonst wirkt eine Antwort vollstaendig, der eine Auskunft fehlt.
                **({"failed": True} if failed_reason else {}),
            }
            # Bei Skills gehoert der Name in den Verlauf, nicht nur "read_skill".
            # Der Betreiber will sehen, *welche* erlernte Vorgehensweise
            # gegriffen hat — sonst wirkt eine Antwort, die aus einem Skill
            # entstanden ist, wie geraten. Der Schluessel kommt aus dem
            # Ergebnis und nicht aus den Argumenten: dort ist er bereits
            # normalisiert und gegen die Sichtbarkeit geprueft.
            if call.name in SKILL_TOOLS and isinstance(value, dict):
                entry["skill_key"] = value.get("skill_key")
                entry["skill_name"] = value.get("name")
                entry["skill_status"] = value.get("status")
                entry["skill_learned"] = bool(value.get("learned"))
            display.append(entry)
        # Erst hier: die Ausfuehrungsschleife oben legt selbst weitere Aufrufe
        # zurueck, sobald das Budget aufgebraucht ist. Wuerden die Absagen
        # vorher erzeugt, blieben genau diese `tool_call_id` ohne Antwort — und
        # manche Anbieter weisen die naechste Anfrage deswegen ab.
        for call, reason in deferred:
            results.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps({
                    "executed": False, "reason": reason,
                }, ensure_ascii=True, separators=(",", ":")),
            })
        _lesezugriffe_protokollieren(
            db, user_id=user_id, eintraege=display, correlation_id=correlation_id
        )
        # In derselben Sitzung und demselben Commit wie das Zugriffsprotokoll.
        # Beide halten dieselbe Tatsache fest — in welchen Server die KI gesehen
        # hat —, und beide sollen entweder stehen oder nicht.
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
        nachtrag = (
            anlagenwissen_nachtrag(db, user_id=user_id, server_id=bezug)
            if bezug is not None
            else None
        )
        db.commit()
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


def _vorschlag_ereignis(proposal: AiActionProposal) -> dict:
    """Ein Vorschlag als SSE-Nutzlast — derselbe Vertrag wie die REST-Antwort.

    Hier stand frueher ein handgebautes Dict aus sechs Feldern. Der Vertrag hat
    fuenfzehn, und zwei der fehlenden — `reason` und `expected_effect` — stehen
    auf der Karte, mit der ein Mensch einen Schreibvorgang freigibt. Live blieb
    sie deshalb ohne Begruendung; erst ein Neuladen holte sie nach.

    Schwerer wog der zweite Weg: dasselbe Dict landet im Abzug des Laufs
    (`ai_run_broker`), und ein Chat, der sich an einen wartenden Lauf
    wiederanhaengt, **ersetzt** damit den vollstaendigen Vorschlag aus der
    REST-Liste. Die gerade noch sichtbare Begruendung verschwand vor den Augen
    des Benutzers.

    `mode="json"` ist Pflicht: `created_at` ist ein `datetime`, und `sse_event`
    serialisiert mit `json.dumps` — ohne Umwandlung scheitert nicht die Anzeige,
    sondern der Stream.
    """
    return proposal_response(proposal).model_dump(mode="json")


def _persist_write_proposals(
    *, user_id: int, conversation_id: str, tool_calls, correlation_id: str, run_id: str | None = None
) -> list[dict]:
    if len(tool_calls) > MAX_TOOL_CALLS or any(call.name not in WRITE_TOOLS for call in tool_calls):
        raise AiActionValidationError("Ungueltige Write-Tool-Sequenz")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise AiActionValidationError("AI-Zugriff wurde entzogen")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            raise AiActionValidationError("Unterhaltung ist nicht mehr verfuegbar")
        proposals = []
        for call in tool_calls:
            try:
                proposals.append(create_proposal(
                    db,
                    user=user,
                    conversation=conversation,
                    tool_name=call.name,
                    arguments=call.arguments,
                    correlation_id=correlation_id,
                ))
            except AiActionValidationError as exc:
                # Der Versuch wird protokolliert, dann fliegt der Fehler weiter
                # wie bisher — dieser Schritt aendert **nichts** am Verhalten des
                # Laufs, er macht ihn nur nachvollziehbar.
                #
                # Vorher hinterliess eine Ablehnung keinerlei Spur: das Audit
                # entsteht in `create_proposal` erst nach `db.add`, und die
                # Session hier committet erst nach der ganzen Schleife. Wer den
                # Grund suchte, fand weder einen Vorschlag noch einen
                # Auditeintrag — und selbst ein in derselben Runde bereits
                # gelungener Vorschlag verschwand im Rollback mit.
                _ablehnung_protokollieren(
                    user_id=user_id,
                    tool_name=call.name,
                    grund=str(exc),
                    correlation_id=correlation_id,
                )
                raise
        # Der Rueckweg: welcher Lauf wartet auf diesen Vorschlag. Ohne ihn
        # wuesste der Bestaetigungsknopf spaeter nicht, wen er aufwecken soll.
        for proposal in proposals:
            proposal.run_id = run_id
        # Auch ein Schreibvorschlag belegt den Serverbezug: `create_proposal`
        # geht durch dieselbe `_resolve_server`-Pruefung wie ein Lesewerkzeug.
        # Ohne diese Zeile verloere ein Lauf sein Thema ausgerechnet dann, wenn
        # er am meisten damit vorhat — etwa wenn das Modell eine gelesene
        # Konfiguration direkt aendern will.
        #
        # `propose_server_create` traegt hier noch keine Nummer; sie entsteht
        # erst bei der Ausfuehrung. Das ist kein Verlust: ueber einen Server,
        # den es gerade erst gibt, weiss noch niemand etwas.
        ai_run_service.serverbezug_merken(
            db,
            run_id=run_id,
            server_id=next(
                (p.server_id for p in reversed(proposals) if p.server_id is not None),
                None,
            ),
        )
        db.commit()
        results: list[dict] = []
        # Feste Kopien: `execute_autonomously` committet und rollt bei einem
        # Fehler zurueck. Ein danach noch gehaltenes ORM-Objekt waere abgelaufen.
        #
        # Kopiert wird der **vollstaendige** Vorschlag statt einer Handvoll
        # Felder. Er ist zugleich die Rueckfallebene fuer den Fall, dass die
        # Zeile nach der Ausfuehrung nicht mehr auffindbar ist.
        summaries = [
            (proposal.id, bool(proposal.autonomous), _vorschlag_ereignis(proposal))
            for proposal in proposals
        ]
        for proposal_id, autonomous, vorher in summaries:
            error_code: str | None = None
            if autonomous:
                # Sofort ausfuehren — aber ueber denselben Pfad wie eine
                # bestaetigte Aktion. Scheitert sie, endet das nicht den Stream:
                # der Benutzer soll die Antwort samt Fehlergrund sehen.
                try:
                    execute_autonomously(db, proposal_id=proposal_id, user=user)
                except AiActionStateError as exc:
                    error_code = exc.code
                except Exception:
                    logger.warning("Autonome AI-Aktion fehlgeschlagen id=%s", proposal_id)
                    error_code = "AI_ACTION_EXECUTION_FAILED"
            current = db.get(AiActionProposal, proposal_id)
            # Nach der Ausfuehrung noch einmal serialisieren: Status, `task_id`
            # und — bei einer Servererstellung — die `server_id` entstehen erst
            # dabei. Fehlt die Zeile, bleibt der Abzug von vorher; er ist
            # vollstaendig und traegt nur einen anderen Status.
            ereignis = (
                _vorschlag_ereignis(current) if current is not None
                else {**vorher, "status": "failed"}
            )
            if error_code:
                # Zustandsfehler wie `AI_ACTION_SERVER_BUSY` oder eine
                # abgelaufene Bestaetigung entstehen, **bevor** ueberhaupt
                # ausgefuehrt wird. Sie stehen deshalb nicht an der Zeile und
                # gingen ohne diese Zeile verloren.
                ereignis["error_code"] = error_code
            results.append(ereignis)
        return results


def _write_followup_messages(
    *, conversation_id: str, tool_calls, proposals: list[dict]
) -> list[dict]:
    """Gibt dem Modell zurueck, was aus seinen Schreib-Aufrufen geworden ist.

    Ohne diesen Rueckfluss endete ein Schreibvorgang stumm: das Modell hatte
    nur einen Werkzeugaufruf abgegeben und nie erfahren, ob er durchging. Die
    Antwortnachricht blieb leer ("Keine Antwort erhalten"), und — schwerer
    wiegend — der naechste Zug sah eine Historie ohne jede Spur der Aktion.
    Ein blosses "danke" wirkte dort wie eine noch offene Bitte, und das Modell
    stoppte denselben Server ein zweites Mal.

    Der Ergebnistext wird zusaetzlich als `AiToolResult` abgelegt. Die
    Abschlussrunde koennte auch ohne Text enden; die Zeile stellt sicher, dass
    die Historie den Vorgang trotzdem kennt.
    """
    outcome_by_tool: dict[str, list[dict]] = {}
    for proposal in proposals:
        outcome_by_tool.setdefault(proposal["tool_name"], []).append({
            "status": proposal.get("status"),
            "autonomous": proposal.get("autonomous"),
            "server_id": proposal.get("server_id"),
            **({"error_code": proposal["error_code"]} if proposal.get("error_code") else {}),
        })

    assistant_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=True),
                },
            }
            for call in tool_calls
        ],
    }
    messages: list[dict] = [assistant_call]
    for call in tool_calls:
        outcomes = outcome_by_tool.get(call.name, [])
        # `succeeded` heisst ausgefuehrt, `proposed` heisst: wartet auf den
        # Menschen. Die Unterscheidung muss beim Modell ankommen, sonst meldet
        # es einen Vorschlag als erledigt.
        payload = {"tool": call.name, "outcomes": outcomes}
        messages.append({
            "role": "tool",
            "tool_call_id": call.id,
            "content": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        })

    try:
        with SessionLocal() as db:
            for tool_name, outcomes in outcome_by_tool.items():
                db.add(AiToolResult(
                    id=str(uuid4()),
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    result_json=json.dumps(
                        {"outcomes": outcomes}, ensure_ascii=True, separators=(",", ":")
                    ),
                ))
            db.commit()
    except Exception:
        # Die Spur in der Historie ist wichtig, aber nicht wichtiger als die
        # Antwort. Scheitert das Schreiben, laeuft die Abschlussrunde trotzdem.
        logger.warning(
            "Ergebnis der Schreibaktion nicht persistiert conversation_id=%s", conversation_id
        )
    return messages


@dataclass(frozen=True)
class _Vorbereitung:
    """Alles, was ein Segment braucht — in einer kurzen Transaktion geholt."""

    run_id: str
    user_id: int
    conversation_id: str
    provider: AiProvider
    api_key: str | None
    message_id: str
    usage_event_id: int
    request_id: str
    reasoning: bool
    reasoning_effort: str | None
    token_price_cents_per_million: int | None
    zustand: dict


def _vorschlag_ergebnisse(db, proposal_ids: list[str]) -> list[dict]:
    """Was aus den Vorschlaegen einer geparkten Runde geworden ist.

    Genau die Auskunft, die das Modell beim Aufwecken braucht: hat der Mensch
    zugestimmt, ist die Aktion gelaufen, ist sie gescheitert. Ohne sie wuesste
    es nur, dass es etwas vorgeschlagen hat.
    """
    ergebnisse: list[dict] = []
    for proposal_id in proposal_ids:
        row = db.get(AiActionProposal, proposal_id)
        if row is None:
            ergebnisse.append({
                "tool_name": "unknown",
                "status": "failed",
                "autonomous": False,
                "server_id": None,
                "error_code": "AI_ACTION_NOT_FOUND",
            })
            continue
        ergebnisse.append({
            "tool_name": row.tool_name,
            "status": row.status,
            "autonomous": bool(row.autonomous),
            "server_id": row.server_id,
            **({"error_code": row.error_code} if row.error_code else {}),
        })
    return ergebnisse


def _aktionsmeldung(ergebnisse: list[dict]) -> dict:
    """Die Entscheidung des Menschen, als Meldung des Panels an das Modell.

    Ausdruecklich als Panel-Meldung beschriftet und nicht als Satz des
    Benutzers: das Modell soll nicht glauben, jemand haette ihm das getippt.
    """
    return {
        "role": "user",
        "content": (
            "Meldung des Panels (nicht vom Benutzer geschrieben): Der Benutzer "
            "hat ueber die vorgeschlagenen Aktionen entschieden. Ergebnis:\n"
            + json.dumps(ergebnisse, ensure_ascii=True, separators=(",", ":"))
            + "\n\nArbeite die Aufgabe von hier aus weiter. Ist sie damit "
            "erledigt, sag es kurz; fehlt noch ein Schritt, mach ihn."
        ),
    }


def _segment_beginnen(db, run: AiRun, zustand: dict) -> tuple[str, int, str] | None:
    """Legt Nachricht und Verbrauchszeile fuer ein neues Segment an.

    Jede Fortsetzung nach einer Bestaetigung schreibt eine **eigene** Nachricht.
    Das ist Absicht: "ich stelle um, bitte bestaetigen" und "erledigt, der
    Server laeuft" sind zwei Aussagen. Sie in eine Blase zu zwingen haette
    bedeutet, den Text nachtraeglich zu veraendern — und den Zeitpunkt zu
    verwischen, an dem der Mensch zugestimmt hat.
    """
    provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
    if provider is None:
        return None
    request_id = str(uuid4())
    usage_event = reserve_ai_usage(
        db,
        db.get(User, run.user_id),
        request_id=UUID(request_id),
        estimated_tokens=estimate_reserved_tokens(zustand["provider_messages"]),
        estimated_cost_microunits=estimate_cost_microunits(
            provider, estimate_reserved_tokens(zustand["provider_messages"])
        ),
        server_id=None,
        provider_id=provider.id,
        model=provider.default_model,
    )
    message_id = str(uuid4())
    db.add(AiMessage(
        id=message_id,
        conversation_id=run.conversation_id,
        role="assistant",
        content="",
        status="streaming",
        provider_id=provider.id,
        model=provider.default_model,
        request_id=request_id,
    ))
    db.flush()
    return message_id, usage_event.id, request_id


def _segment_vorbereiten(run_id: str) -> tuple[_Vorbereitung | None, tuple[str, str] | None]:
    """Holt den Lauf aus der Datenbank und macht ihn lauffaehig.

    Die Sitzung ist absichtlich kurz: waehrend der Anbieter streamt, darf keine
    Transaktion offen stehen. Genau dafuer ist der Zustand persistiert.
    """
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return None, ("AI_RUN_NOT_FOUND", "ai.chat.errors.notFound")
        if run.status not in {"running"}:
            # Zwischen Planung und Ausfuehrung hat sich etwas geaendert — etwa
            # eine neue Nachricht, die den Lauf ueberholt hat.
            return None, None
        user = db.get(User, run.user_id)
        if user is None or not user.is_active:
            return None, ("AI_ACCESS_REVOKED", "ai.chat.errors.access")
        provider = db.get(AiProvider, run.provider_id) if run.provider_id else None
        if provider is None or not provider.enabled:
            return None, ("AI_RESOURCE_NOT_FOUND", "ai.chat.errors.notFound")

        zustand = ai_run_service.zustand_lesen(run)

        # Fortsetzung: der Lauf erfaehrt, wie der Mensch entschieden hat.
        #
        # Bewusst **kein** zweites Werkzeugergebnis: die `tool_call_id` der
        # geparkten Runde hat ihre Antwort schon bekommen ("wartet auf
        # Bestaetigung"), und das Protokoll erlaubt nur eine je Aufruf. Die
        # Entscheidung ist auch kein Werkzeugergebnis, sondern eine Meldung des
        # Panels — und genau als solche gekennzeichnet. Dasselbe Muster nutzt
        # `ai_context_service` bereits fuer frueher gelesene Werkzeugergebnisse.
        pending = zustand.get("pending")
        if pending:
            ergebnisse = _vorschlag_ergebnisse(db, list(pending.get("proposal_ids", [])))
            zustand["provider_messages"].append(_aktionsmeldung(ergebnisse))
            zustand["pending"] = None

        try:
            api_key = resolve_api_key(db, provider, user.id)
        except DisSidecarError:
            return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.chat.errors.credential")
        if provider.requires_api_key and not api_key:
            return None, ("AI_PROVIDER_KEY_MISSING", "ai.chat.errors.keyMissing")

        if run.message_id:
            nachricht = db.get(AiMessage, run.message_id)
            message_id = run.message_id
            usage_event_id = zustand.get("usage_event_id")
            request_id = zustand.get("request_id") or (
                nachricht.request_id if nachricht is not None else str(uuid4())
            )
            if usage_event_id is None:
                # Der Startpfad legt beides an; fehlt die Verbuchung, ist der
                # Zustand kaputt und ein neues Segment ehrlicher als raten.
                run.message_id = None
        if not run.message_id:
            # `_segment_beginnen` reserviert Verbrauch — und das kann scheitern.
            # Ohne diese Behandlung verliess eine `AiQuotaExceeded` die ganze
            # Funktion und damit auch `segment_ausfuehren`: die asyncio-Aufgabe
            # starb, `_lauf_abschliessen` lief nie, kein `error` ging an den
            # Vermittler. Der Lauf stand danach **fuer immer** auf `running`,
            # und die Oberflaeche zeigte einen tippenden Assistenten, der nie
            # wieder etwas sagt.
            #
            # Der haeufigste Weg dorthin ist kein Sonderfall: ein Lauf parkt auf
            # `waiting_confirmation`, in der Zwischenzeit ist das Tageskontingent
            # aufgebraucht, der Mensch bestaetigt — und genau dann greift die
            # Reservierung ins Leere.
            #
            # Dieselben Fehlerformen wie in `lauf_beginnen`, damit ein
            # erschoepftes Kontingent an beiden Stellen gleich heisst.
            try:
                begonnen = _segment_beginnen(db, run, zustand)
            except AiUsageConflict:
                db.rollback()
                return None, ("AI_REQUEST_CONFLICT", "ai.chat.errors.requestConflict")
            except AiQuotaExceeded as exc:
                db.rollback()
                return None, (f"AI_QUOTA_{exc.reason.upper()}", "ai.chat.errors.quota")
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "AI-Segment konnte nicht beginnen run_id=%s error=%s",
                    run_id, type(exc).__name__,
                )
                return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
            if begonnen is None:
                return None, ("AI_RESOURCE_NOT_FOUND", "ai.chat.errors.notFound")
            message_id, usage_event_id, request_id = begonnen
            run.message_id = message_id
            zustand["usage_event_id"] = usage_event_id
            zustand["request_id"] = request_id

        ai_run_service.zustand_schreiben(run, zustand)
        db.commit()
        db.refresh(provider)
        db.expunge(provider)
        return _Vorbereitung(
            run_id=run_id,
            user_id=run.user_id,
            conversation_id=run.conversation_id,
            provider=provider,
            api_key=api_key,
            message_id=message_id,
            usage_event_id=int(usage_event_id),
            request_id=str(request_id),
            reasoning=bool(run.reasoning),
            # Aus dem Lauf, nicht neu berechnet: eine Fortsetzung nach einer
            # Bestaetigung muss dieselbe Tiefe verwenden wie der erste Zug.
            # Neu klemmen hiesse, dass ein zwischenzeitlich geaenderter
            # Rollendeckel mitten in einer Aufgabe wirkt.
            reasoning_effort=run.reasoning_effort,
            token_price_cents_per_million=provider.token_price_cents_per_million,
            zustand=zustand,
        ), None


def _lauf_abschliessen(
    run_id: str, *, status: str, stop_reason: str, zustand: dict | None = None
) -> None:
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        if run is None:
            return
        if run.status in AUSGELAUFEN:
            # Ein Endzustand ist endgueltig — auch gegenueber dem eigenen Segment,
            # und auch dann, wenn der Zweitschreiber denselben Status meldet.
            #
            # Die Zusatzbedingung `run.status != status`, die hier naheliegt,
            # waere ausgerechnet im Zielfall falsch: nach `vorgaenger_abloesen`
            # steht der Lauf auf 'cancelled/superseded', und der abgebrochene
            # Vorgaenger meldet aus seinem CancelledError-Zweig ebenfalls
            # 'cancelled', nur mit stop_reason 'cancelled'. Der Status waere
            # gleich, der Waechter fiele durch, und 'superseded' ginge verloren
            # — also genau die Unterscheidung, die dieser Waechter schuetzen
            # soll: wurde der Lauf ueberholt, oder fuhr das Panel herunter?
            #
            # Der Fall aus dem Betrieb: der Benutzer schiebt waehrend des Streams
            # eine zweite Nachricht nach. `vorgaenger_abloesen` schreibt diesen
            # Lauf auf 'cancelled/superseded', der Abbruch seiner Aufgabe wird
            # aber erst am naechsten Haltepunkt zugestellt. Brachte sie ihre
            # Runde vorher zu Ende, meldete sie hier 'completed/done' und
            # ueberschrieb den Abbruch: im Protokoll stand ein Lauf als erledigt,
            # den der Benutzer laengst ueberholt hatte.
            #
            # Gemeldet wird der **tatsaechliche** Zustand und nicht der
            # gewuenschte: die Oberflaeche soll den Abbruch sehen und nicht auf
            # eine Antwort warten, die nicht mehr kommt.
            ai_run_broker.veroeffentlichen(
                run_id,
                "run",
                {"run_id": run_id, "status": run.status, "stop_reason": run.stop_reason},
            )
            ai_run_broker.beenden(run_id)
            return
        run.status = status
        run.stop_reason = stop_reason
        if zustand is not None:
            ai_run_service.zustand_schreiben(run, zustand)
        if status != "running":
            # Ein geparkter oder beendeter Lauf hat kein laufendes Segment mehr.
            # Die naechste Fortsetzung legt eine neue Nachricht an.
            run.message_id = None
        run.updated_at = datetime.now(timezone.utc)
        db.commit()
    ai_run_broker.veroeffentlichen(
        run_id, "run", {"run_id": run_id, "status": status, "stop_reason": stop_reason}
    )
    if status in AUSGELAUFEN:
        ai_run_broker.beenden(run_id)


def _werkzeug_signatur(name: str, argumente: dict) -> str:
    return name + "|" + json.dumps(argumente, ensure_ascii=True, sort_keys=True)


async def segment_ausfuehren(run_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Fuehrt einen Lauf aus, bis er fertig ist, fragt oder auf einen Menschen wartet.

    Das ist der frueher `stream_conversation_reply` genannte Ablauf — mit dem
    einen Unterschied, der alles aendert: er haengt an keinem Request mehr.
    Ergebnisse gehen an den Vermittler (``ai_run_broker``), nicht an einen
    Generator. Wer zusieht, ist dem Lauf gleichgueltig.
    """
    # Die Vorbereitung stand ausserhalb jeder Absicherung. Fiel dort etwas um,
    # verliess die Ausnahme diese Koroutine, die asyncio-Aufgabe starb still,
    # und der Lauf blieb auf `running` stehen — ohne Ereignis, ohne Abschluss,
    # ohne Aufraeumen. Ein Lauf, der nie endet, ist schlimmer als einer, der
    # scheitert: der Benutzer sieht einen tippenden Assistenten und wartet.
    #
    # Die bekannten Fehler behandelt `_segment_vorbereiten` selbst und gibt sie
    # als Tupel zurueck. Dieser Block ist fuer das Uebrige da.
    try:
        vorbereitung, fehler = _segment_vorbereiten(run_id)
    except Exception as exc:
        logger.exception("AI-Segment-Vorbereitung abgebrochen run_id=%s", run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "error",
            {"code": "AI_PREPARATION_FAILED", "message_key": "ai.chat.errors.unavailable"},
        )
        _lauf_abschliessen(
            run_id, status="failed", stop_reason=f"AI_PREPARATION_FAILED:{type(exc).__name__}"
        )
        return
    if vorbereitung is None:
        if fehler is not None:
            code, message_key = fehler
            ai_run_broker.veroeffentlichen(
                run_id, "error", {"code": code, "message_key": message_key}
            )
            _lauf_abschliessen(run_id, status="failed", stop_reason=code)
        return

    # Der Client kommt normalerweise aus dem Prozess (beim Start gesetzt). Als
    # Parameter ist er ueberreichbar, damit ein Test das Segment ausfuehren kann,
    # ohne eine ganze Anwendung hochzufahren.
    client = client or ai_run_service.http_client()
    if client is None:
        logger.error("Kein AI-HTTP-Client, Lauf kann nicht arbeiten run_id=%s", run_id)
        ai_run_broker.veroeffentlichen(
            run_id, "error", {"code": "AI_RUNTIME_UNAVAILABLE", "message_key": "ai.chat.errors.unavailable"}
        )
        _lauf_abschliessen(run_id, status="failed", stop_reason="AI_RUNTIME_UNAVAILABLE")
        return

    zustand = vorbereitung.zustand
    provider_messages: list[dict] = zustand["provider_messages"]
    conversation_id = vorbereitung.conversation_id
    user_id = vorbereitung.user_id
    message_id = vorbereitung.message_id

    ai_run_broker.neues_segment(run_id)
    ai_run_broker.veroeffentlichen(
        run_id,
        "message",
        {
            "message_id": message_id,
            "request_id": vorbereitung.request_id,
            "run_id": run_id,
            # Die Kennung der **Benutzer**nachricht dieses Laufs. Die Oberflaeche
            # stellt eine Blase optimistisch dar, bevor der Server eine ID
            # vergeben hat; ohne diesen Wert traegt sie fuer immer eine
            # erfundene, und die Anhaenge dieser Frage fanden ihre Nachricht
            # nicht.
            "user_message_id": zustand.get("user_message_id"),
        },
    )

    chunks: list[str] = []
    thoughts: list[str] = []
    usage = StreamUsage()
    abgerechnet = False
    gestellte_frage: dict | None = None
    geparkt = False
    # Wurde dieser Lauf waehrend der Arbeit von einer neuen Nachricht abgeloest?
    # Dann gehoert er nicht mehr uns: abgerechnet wird noch ehrlich, geschrieben
    # wird nichts mehr.
    abgeloest = False
    # Endete der Lauf, weil ihm die Runden ausgingen? Ein solcher Lauf sieht im
    # Ergebnis aus wie einer, der fertig war — er ist es aber nicht, und der
    # Unterschied gehoert ins Protokoll. Genau dafuer stand `stop_reason`
    # ('budget') im Modell und wurde nie gesetzt.
    budget_erschoepft = False
    try:
        tools = provider_tool_definitions()
        current_usage = usage
        signaturen: dict[str, int] = dict(zustand.get("tool_signatures") or {})
        while True:
            async for chunk in stream_chat_completion(
                client,
                provider=vorbereitung.provider,
                api_key=vorbereitung.api_key,
                messages=provider_messages,
                usage=current_usage,
                tools=tools,
                reasoning=vorbereitung.reasoning,
                reasoning_effort=vorbereitung.reasoning_effort,
            ):
                if chunk.kind == "reasoning":
                    thoughts.append(chunk.text)
                    ai_run_broker.veroeffentlichen(run_id, "reasoning", {"content": chunk.text})
                    continue
                chunks.append(chunk.text)
                ai_run_broker.veroeffentlichen(run_id, "delta", {"content": chunk.text})
            if current_usage is not usage:
                usage.total_tokens = (
                    usage.total_tokens + current_usage.total_tokens
                    if usage.total_tokens is not None and current_usage.total_tokens is not None
                    else usage.total_tokens or current_usage.total_tokens
                )
            if not current_usage.tool_calls:
                break
            if tools is None:
                # Diese Runde wurde ohne Werkzeugliste angefragt — sie ist die
                # abschliessende. Meldet der Anbieter trotzdem Werkzeugaufrufe,
                # ist das keine Anfrage, die wir erfuellen: wir haben nichts
                # angeboten. Frueher war `tools = None` nur eine Bitte, und ein
                # Anbieter, der sich nicht daran hielt, hielt den Lauf endlos
                # offen. Hier steht die Grenze auf unserer Seite.
                logger.warning(
                    "Anbieter meldet Werkzeugaufrufe ohne angebotene Werkzeuge, "
                    "werden verworfen run_id=%s anzahl=%d",
                    run_id, len(current_usage.tool_calls),
                )
                break

            # Eine Rueckfrage beendet das Segment: ab hier ist der Mensch dran,
            # und seine Antwort kommt als gewoehnliche Nachricht zurueck.
            frage = next(
                (call for call in current_usage.tool_calls if call.name in ASK_TOOLS),
                None,
            )
            if frage is not None:
                gestellte_frage = question_payload(frage.arguments)
                ai_run_broker.veroeffentlichen(run_id, "question", gestellte_frage)
                break

            kinds = {
                "read" if call.name in READ_TOOLS else "write" if call.name in WRITE_TOOLS else "unknown"
                for call in current_usage.tool_calls
            }
            if kinds == {"write"}:
                # Ein abgeloester Lauf darf die Aussenwelt nicht mehr anfassen.
                #
                # Zwischen dem Beginn dieses Segments und dieser Runde koennen
                # Minuten liegen. Schreibt der Benutzer in der Zwischenzeit etwas
                # Neues, steht dieser Lauf auf 'cancelled' — sein Abbruch wird
                # aber erst am naechsten Haltepunkt zugestellt, und zwischen dem
                # Ende des Anbieterstroms und `_persist_write_proposals` liegt
                # keiner. Ohne diese Frage legte ein ueberholter Lauf noch
                # Vorschlaege in die Unterhaltung und fuehrte autonome Aktionen
                # am Server tatsaechlich aus.
                #
                # Gefragt wird genau hier und nicht in jeder Runde: das ist der
                # einzige Punkt, an dem der Lauf etwas veraendert.
                if _lauf_status(run_id) != "running":
                    abgeloest = True
                    break
                proposals = _persist_write_proposals(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    correlation_id=vorbereitung.request_id,
                    run_id=run_id,
                )
                for proposal in proposals:
                    ai_run_broker.veroeffentlichen(
                        run_id, "action" if proposal.get("autonomous") else "proposal", proposal
                    )
                # Das Ergebnis geht **immer** zurueck ans Modell, auch wenn es
                # nur "wartet auf den Menschen" lautet. Das Protokoll verlangt zu
                # jeder `tool_call_id` genau eine Antwort — und das Modell soll
                # den Vorgang in Worte fassen koennen, statt eine leere Blase
                # ueber der Bestaetigungskarte zu hinterlassen.
                provider_messages.extend(_write_followup_messages(
                    conversation_id=conversation_id,
                    tool_calls=current_usage.tool_calls,
                    proposals=proposals,
                ))
                zustand["write_rounds"] = int(zustand.get("write_rounds", 0)) + 1

                # **Der Punkt, an dem der Lauf frueher endete.** Wartet auch nur
                # ein Vorschlag auf einen Menschen, wird geparkt statt
                # aufgegeben: der Zustand geht in die Datenbank, und die
                # Bestaetigung weckt genau hier wieder auf.
                #
                # Geparkt wird aber erst **nach** einer letzten Runde ohne
                # Werkzeuge. Sonst stuende die Karte im Chat und darueber
                # nichts — der Benutzer soll lesen, was da bestaetigt werden
                # will und warum.
                offen = [
                    proposal["id"] for proposal in proposals
                    if proposal.get("status") in {"proposed", "confirmed"}
                ]
                if offen:
                    zustand["pending"] = {
                        "proposal_ids": [proposal["id"] for proposal in proposals],
                    }
                    geparkt = True
                    tools = None
                    current_usage = StreamUsage()
                    continue
                # Alles ausgefuehrt? Dann darf der Lauf weiterarbeiten. Das ist
                # der Unterschied zwischen "eine Aktion abgeben" und "eine
                # Aufgabe erledigen": erst wenn Schritt eins nachweislich lief,
                # ergibt Schritt zwei ueberhaupt Sinn.
                ausgefuehrt = bool(proposals) and all(
                    proposal.get("status") in {"succeeded", "executing"}
                    and not proposal.get("error_code")
                    for proposal in proposals
                )
                if not (ausgefuehrt and zustand["write_rounds"] < MAX_WRITE_ROUNDS):
                    # Nur wenn die Runde *ausgefuehrt* wurde und trotzdem Schluss
                    # ist, lag es am Budget. Endet sie, weil ein Vorschlag offen
                    # blieb, wartet der Lauf auf einen Menschen — das ist kein
                    # aufgebrauchtes Budget.
                    if ausgefuehrt:
                        budget_erschoepft = True
                    tools = None
                current_usage = StreamUsage()
                continue
            if "unknown" in kinds:
                raise AiProviderRequestError("AI_PROVIDER_TOOL_SEQUENCE_INVALID")

            deferred_calls: list = []
            if kinds == {"read", "write"}:
                # Gemischte Runde: die Lesewerkzeuge laufen, die Schreibaufrufe
                # bekommen eine Absage mit Begruendung und werden nachgeholt.
                deferred_calls = [
                    (call, (
                        "Schreibaktionen laufen in einer eigenen Runde. Lies "
                        "erst zu Ende und rufe die Aktion danach allein auf."
                    ))
                    for call in current_usage.tool_calls if call.name in WRITE_TOOLS
                ]
                current_usage.tool_calls = [
                    call for call in current_usage.tool_calls if call.name in READ_TOOLS
                ]

            # Schleifenerkennung — **ueber Runden hinweg**, nicht innerhalb einer.
            #
            # Das ist der ganze Unterschied: neun Statusabfragen nebeneinander
            # sind eine gruendliche Bestandsaufnahme ("laufen alle Server?"),
            # dieselbe Abfrage in der vierten Runde hintereinander ist ein
            # haengendes Modell. Gezaehlt wird deshalb je Runde einmal je
            # Signatur, und geprueft wird gegen die Runden davor.
            wiederholt: list = []
            frisch: list = []
            for call in current_usage.tool_calls:
                gezaehlt = signaturen.get(_werkzeug_signatur(call.name, call.arguments), 0)
                if gezaehlt >= MAX_GLEICHE_AUFRUFE:
                    wiederholt.append((call, (
                        "Dieser Aufruf lief mit genau diesen Argumenten bereits in "
                        f"{gezaehlt} Runden und liefert nichts Neues. Arbeite mit dem, "
                        "was du hast, oder frage den Benutzer."
                    )))
                    continue
                frisch.append(call)
            for signatur in {
                _werkzeug_signatur(call.name, call.arguments) for call in frisch
            }:
                signaturen[signatur] = signaturen.get(signatur, 0) + 1
            current_usage.tool_calls = frisch
            deferred_calls.extend(wiederholt)

            zustand["rounds"] = int(zustand.get("rounds", 0)) + 1
            if zustand["rounds"] > MAX_TOOL_ROUNDS:
                # Ein Assistent, der abbricht *weil* er gruendlich war, ist
                # schlechter als einer, der mit dem Vorhandenen antwortet. Ab
                # hier gibt es keine Werkzeuge mehr, aber eine Antwort.
                logger.info(
                    "AI-Werkzeugrunden erschoepft, letzte Antwort ohne Werkzeuge run_id=%s",
                    run_id,
                )
                budget_erschoepft = True
                tools = None
                current_usage = StreamUsage()
                continue
            if not current_usage.tool_calls and not deferred_calls:
                break
            followup, used_tools, nachtrag = _tool_followup_messages(
                user_id=user_id,
                conversation_id=conversation_id,
                tool_calls=current_usage.tool_calls,
                deferred=deferred_calls,
                correlation_id=vorbereitung.request_id,
                run_id=run_id,
            )
            provider_messages.extend(followup)
            # Das Betriebswissen der Anlage, sobald feststeht welche. Genau
            # einmal je Lauf: `zustand` ueberlebt die Unterbrechung, und nach
            # einer Bestaetigung wuerde es sonst erneut angehaengt — dieselben
            # Zeilen ein zweites Mal, mit anderem Zaehlerstand daneben.
            if nachtrag is not None and not zustand.get("anlagenwissen_gereicht"):
                zustand["anlagenwissen_gereicht"] = True
                provider_messages.append(nachtrag)
            for used in used_tools:
                ai_run_broker.veroeffentlichen(run_id, "tool", used)
            current_usage = StreamUsage()

        if abgeloest:
            # Kein `done` an den Vermittler und kein Zustand in die Datenbank:
            # der Nachfolger arbeitet bereits in derselben Unterhaltung, und ein
            # zweiter Schreiber auf demselben Lauf waere genau der Geist, den das
            # Abloesen verhindern soll.
            #
            # Die Verbrauchszeile wird trotzdem geschlossen — nicht, weil hier
            # etwas abzurechnen waere, sondern weil eine offene Reservierung
            # Kontingent und Nebenlaeuferplatz des Benutzers dauerhaft blockiert.
            # Kam Text durch, wird er abgerechnet; kam keiner durch, und das ist
            # bei einer reinen Schreibrunde der Normalfall, gibt `had_output`
            # False und die Reserve wird freigegeben.
            _finalize_stream(
                message_id=message_id,
                usage_event_id=vorbereitung.usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
            abgerechnet = True
            # Schreibt nichts um: `_lauf_abschliessen` laesst Endzustaende stehen
            # und meldet der Oberflaeche den tatsaechlichen.
            _lauf_abschliessen(run_id, status="cancelled", stop_reason="superseded")
            return

        zustand["tool_signatures"] = signaturen
        zustand["provider_messages"] = provider_messages
        complete_content = "".join(chunks)
        estimated_actual = max(
            1,
            (message_character_count(provider_messages) + len(complete_content) + 3) // 4,
        )
        _finalize_stream(
            message_id=message_id,
            usage_event_id=vorbereitung.usage_event_id,
            content=complete_content,
            provider_total_tokens=usage.total_tokens,
            estimated_actual_tokens=estimated_actual,
            failed=False,
            # Eine Rueckfrage ist eine vollwertige Antwort, und ein Vorschlag
            # ebenso. Ohne das galten sie als "nichts geliefert" — genau der
            # Fall, in dem der Chat "Keine Antwort erhalten" anzeigte.
            had_output=bool(chunks) or gestellte_frage is not None or geparkt,
            token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
            reasoning="".join(thoughts),
            question=gestellte_frage,
        )
        abgerechnet = True
        ai_run_broker.veroeffentlichen(run_id, "done", {"message_id": message_id})

        if geparkt:
            _lauf_abschliessen(
                run_id,
                status="waiting_confirmation",
                stop_reason="awaiting_confirmation",
                zustand=zustand,
            )
            return
        if gestellte_frage is not None:
            _lauf_abschliessen(
                run_id, status="waiting_user", stop_reason="question", zustand=zustand
            )
            return
        _lauf_abschliessen(
            run_id,
            status="completed",
            # "budget" heisst: die KI hatte noch etwas vor, durfte aber nicht
            # mehr. Sie hat aus dem geantwortet, was sie hatte — das ist eine
            # Antwort, aber keine erledigte Aufgabe, und wer das Protokoll liest
            # soll den Unterschied sehen.
            stop_reason="budget" if budget_erschoepft else "done",
            zustand=zustand,
        )

        # Erst jetzt falten — der Benutzer hat seine Antwort und wartet nicht auf
        # die Zusammenfassung.
        try:
            from services.ai_compaction_service import compact_conversation

            if await compact_conversation(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                provider_id=vorbereitung.provider.id,
            ):
                ai_run_broker.veroeffentlichen(
                    run_id, "compacted", {"conversation_id": conversation_id}
                )
        except Exception as exc:
            logger.info("AI-Kompression uebersprungen error=%s", type(exc).__name__)
    except asyncio.CancelledError:
        # Der Prozess faehrt herunter. Nicht mehr als ehrlich abschliessen.
        if not abgerechnet:
            _finalize_stream(
                message_id=message_id,
                usage_event_id=vorbereitung.usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
            _lauf_abschliessen(run_id, status="cancelled", stop_reason="cancelled")
        raise
    except Exception as exc:
        if isinstance(exc, AiProviderRequestError):
            code, message_key = exc.code, "ai.chat.errors.provider"
        elif isinstance(exc, AiActionValidationError):
            # Der Grund gehoert ins Log. Vorher stand hier gar nichts: der
            # Benutzer sah `AI_TOOL_REJECTED` und der Betreiber hatte keine
            # Moeglichkeit herauszufinden, welcher Aufruf woran gescheitert war.
            #
            # Redigiert und gekuerzt, obwohl die Meldungen fast alle aus einer
            # festen Menge stammen: bei `propose_blueprint_change` kann ein vom
            # Modell gewaehlter Pfad woertlich im Text landen
            # (`blueprint_service.py`), und dieses Modell hat seinerseits
            # fremden Logtext gelesen. Ein Panel-Log ist kein Ort fuer
            # ungefilterten Fremdtext.
            logger.warning(
                "AI-Werkzeugaufruf abgelehnt run_id=%s grund=%s",
                run_id, redact_sensitive_text(str(exc))[:200],
            )
            code, message_key = "AI_TOOL_REJECTED", "ai.chat.errors.toolRejected"
        else:
            logger.warning("AI-Lauf fehlgeschlagen error=%s", type(exc).__name__)
            code, message_key = "AI_STREAM_FAILED", "ai.chat.errors.unavailable"
        if not abgerechnet:
            _finalize_stream(
                message_id=message_id,
                usage_event_id=vorbereitung.usage_event_id,
                content="".join(chunks),
                provider_total_tokens=usage.total_tokens,
                estimated_actual_tokens=0,
                failed=True,
                had_output=bool(chunks),
                token_price_cents_per_million=vorbereitung.token_price_cents_per_million,
                reasoning="".join(thoughts),
            )
        ai_run_broker.veroeffentlichen(run_id, "error", {"code": code, "message_key": message_key})
        _lauf_abschliessen(run_id, status="failed", stop_reason=code)


def lauf_beginnen(
    db,
    *,
    user: User,
    conversation,
    provider: AiProvider,
    request_id: UUID,
    content: str,
    reasoning: bool,
    reasoning_effort: str | None = None,
) -> tuple[AiRun | None, tuple[str, str] | None]:
    """Legt einen Lauf an: Benutzernachricht, Kontingent, Antwortnachricht.

    Bewusst **synchron im Request** und nicht im Hintergrund. Ein
    ueberschrittenes Kontingent, ein fehlender Schluessel oder eine doppelt
    gesendete Anfrage sind Dinge, die der Benutzer sofort erfahren soll — nicht
    Sekunden spaeter aus einem Ereignisstrom. Erst wenn all das durch ist,
    beginnt die eigentliche Arbeit, und ab da haengt sie an nichts mehr.
    """
    safe_content = redact_sensitive_text(content).strip()
    if not safe_content:
        return None, ("AI_MESSAGE_EMPTY", "ai.chat.errors.empty")
    try:
        # Wer eine neue Nachricht schreibt, statt einen Vorschlag zu bestaetigen,
        # hat die Richtung gewechselt. Ein alter, geparkter Lauf darf danach
        # nicht mehr in denselben Chat weiterschreiben.
        #
        # War der Vorgaenger dagegen eine **Rueckfrage**, ist diese Nachricht die
        # Antwort darauf. Dann erbt der neue Lauf dessen Schleifensignaturen:
        # ohne das liest das Modell nach jeder Klaerung dieselbe Datei wieder von
        # vorn, und die Erkennung greift nie.
        geerbte_signaturen = ai_run_service.vorgaenger_abloesen(
            db, conversation_id=conversation.id
        )

        benutzernachricht_id = str(uuid4())
        db.add(AiMessage(
            id=benutzernachricht_id,
            conversation_id=conversation.id,
            role="user",
            content=safe_content,
            status="complete",
        ))
        # Hochgeladene Anhaenge gehoeren ab jetzt zu **dieser** Frage. Vorher
        # hingen sie nur an der Unterhaltung: sie blieben als Chip stehen und
        # gingen bei jeder weiteren Frage erneut mit, bis sie aus den letzten
        # fuenf herausfielen.
        ai_attachment_service.bind_to_message(
            db, conversation_id=conversation.id, user_id=user.id,
            message_id=benutzernachricht_id,
        )
        db.flush()
        # Das Thema laeuft weiter, auch wenn der Lauf wechselt: "und jetzt
        # starte ihn neu" nennt keinen Server, gemeint ist der aus der Frage
        # davor. Einmal ermittelt und zweimal gebraucht — fuer den Kontext
        # dieser Nachricht und als Startwert des neuen Laufs.
        serverbezug = ai_run_service.letzter_serverbezug(
            db, conversation_id=conversation.id
        )
        provider_messages = build_provider_messages(
            db, conversation, query=safe_content, server_id=serverbezug
        )
        estimated_tokens = estimate_reserved_tokens(provider_messages)
        usage_event = reserve_ai_usage(
            db,
            user,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimate_cost_microunits(provider, estimated_tokens),
            server_id=None,
            provider_id=provider.id,
            model=provider.default_model,
        )
        message_id = str(uuid4())
        db.add(AiMessage(
            id=message_id,
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="streaming",
            provider_id=provider.id,
            model=provider.default_model,
            request_id=str(request_id),
        ))
        conversation.updated_at = datetime.now(timezone.utc)

        zustand = ai_run_service.leerer_zustand(
            provider_messages,
            request_id=str(request_id),
            user_message_id=benutzernachricht_id,
        )
        zustand["usage_event_id"] = usage_event.id
        zustand["tool_signatures"] = geerbte_signaturen
        run = ai_run_service.lauf_anlegen(
            db,
            conversation_id=conversation.id,
            user_id=user.id,
            provider_id=provider.id,
            message_id=message_id,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            zustand=zustand,
            last_server_id=serverbezug,
        )
        db.commit()
        return run, None
    except IntegrityError:
        db.rollback()
        return None, ("AI_REQUEST_CONFLICT", "ai.chat.errors.requestConflict")
    except AiUsageConflict:
        db.rollback()
        return None, ("AI_REQUEST_CONFLICT", "ai.chat.errors.requestConflict")
    except AiQuotaExceeded as exc:
        db.rollback()
        return None, (f"AI_QUOTA_{exc.reason.upper()}", "ai.chat.errors.quota")
    except DisSidecarError:
        db.rollback()
        return None, ("AI_CREDENTIAL_UNAVAILABLE", "ai.chat.errors.credential")
    except Exception as exc:
        db.rollback()
        logger.warning("AI-Lauf konnte nicht beginnen error=%s", type(exc).__name__)
        return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")


async def lauf_verfolgen(run_id: str, *, abo=None) -> AsyncIterator[str]:
    """Der Datenstrom zum Browser — ein **Fenster** auf den Lauf, nicht sein Motor.

    Bricht diese Verbindung ab, passiert dem Lauf nichts. Genau das war die
    Beschwerde: *"wenn ich den Browser schliesse, bricht die Anfrage ab."* Sie
    bricht jetzt nur noch die Anzeige ab.

    Wer sich spaeter wieder anhaengt, bekommt zuerst einen ``snapshot`` mit dem
    vollstaendigen bisherigen Stand und danach die Fortsetzung live.
    """
    # ``abo`` kommt vom Endpunkt, wenn dieser bereits **vor** dem Start des Laufs
    # abonniert hat. Das schliesst ein Wettrennen, das sonst unvermeidbar waere:
    # der Lauf arbeitet auf der Ereignisschleife los, waehrend der Rumpf der
    # Antwort erst beim ersten Lesen anlaeuft — die ersten Zeichen waeren durch,
    # bevor jemand zuhoert.
    abo = abo or ai_run_broker.abonnieren(run_id)
    if abo is None:
        # Der Lauf arbeitet in diesem Prozess nicht (mehr). Der Verlauf steht in
        # der Datenbank; die Oberflaeche laedt ihn ohnehin beim Oeffnen.
        yield sse_event("run", {"run_id": run_id, "status": _lauf_status(run_id), "live": False})
        return
    abzug, warteschlange = abo
    yield sse_event("snapshot", abzug.als_ereignis())
    if abzug.status != "running":
        # Der Lauf ruht bereits — er wartet auf einen Menschen oder ist fertig.
        # Die Verbindung offenzuhalten waere ein Warten auf nichts, und im
        # Browser bliebe die Eingabe gesperrt, solange "es laeuft noch" gilt.
        ai_run_broker.abmelden(run_id, warteschlange)
        return
    try:
        while True:
            # Erst leerlaufen lassen, dann aufhoeren. Die Reihenfolge ist der
            # ganze Punkt: der Rumpf einer StreamingResponse laeuft oft erst an,
            # wenn der Lauf schon fertig ist. Ein "laeuft der noch?" vor dem
            # Auslesen wuerde dann eine volle Warteschlange wegwerfen und nur
            # den (leeren) Abzug vom Zeitpunkt des Abonnements zeigen.
            if warteschlange.empty() and not ai_run_broker.laeuft(run_id):
                break
            ereignis, daten = await warteschlange.get()
            if ereignis is None:
                break
            if ereignis == "segment":
                # Ein neues Segment beginnt: der Text davor gehoert zur
                # abgeschlossenen Nachricht und darf nicht weiterwachsen.
                yield sse_event("segment", daten)
                continue
            yield sse_event(ereignis, daten)
            if ereignis == "run" and daten.get("status") != "running":
                # Fertig **oder** geparkt. Beides beendet die Anzeige: ein
                # geparkter Lauf tut von selbst nichts mehr, und eine offene
                # Verbindung wuerde im Browser als "arbeitet noch" gelesen —
                # die Eingabe bliebe gesperrt, obwohl der Mensch dran ist.
                break
    finally:
        ai_run_broker.abmelden(run_id, warteschlange)


def _lauf_status(run_id: str) -> str:
    with SessionLocal() as db:
        run = db.get(AiRun, run_id)
        return run.status if run is not None else "failed"
