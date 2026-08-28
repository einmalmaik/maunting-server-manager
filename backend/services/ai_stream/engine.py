# -*- coding: utf-8 -*-
"""Zentrale Ausfuehrungsschleife fuer AI-Streaming-Segmente."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import AsyncIterator
from uuid import UUID

from fastapi import HTTPException
import httpx

from database import SessionLocal
from models import AiRun, User
from models.ai_run import BEENDET as AUSGELAUFEN
import services.ai_stream as ai_stream
from services import (
    ai_meldestelle,
    ai_provider_service,
    ai_reasoning,
    ai_run_broker,
    ai_run_service,
    ai_worker_limits,
)
from services.ai_action_errors import AiActionValidationError
from services.ai_context_service import (
    auf_budget_kuerzen,
    message_character_count,
)
from services.ai_latency_metrics import metrics
from services.ai_proposal_service import (
    AufgabenKontext,
    GuardianKontext,
    execute_autonomously,
)
from services.ai_provider_service import resolve_api_key
from services.ai_redaction import redact_sensitive_text
from services.ai_stream.context import (
    _denken_am_modell,
    _modell_fuer,
    aufgabe_aus_zustand,
    familie_aus_zustand,
    guardian_aus_zustand,
    herkunft_aus_zustand,
    rolle_aus_zustand,
    worker_aus_zustand,
)
from services.ai_stream.interactions import (
    _desktop_behandeln,
    _fragen_behandeln,
    _warten_behandeln,
)
from services.ai_stream.lifecycle import (
    _abschnitt_fuer_ablage,
    _finalize_stream,
    _lauf_abschliessen,
    _lauf_nachbereiten,
    _runde_filtern,
    _segment_anlaufen,
    _werkzeuge_und_grenze,
)
from services.ai_stream.read_tools import (
    _fruehstart_lesewerkzeug_erlaubt,
    _lesewerkzeug_ausfuehren,
    _leserunde_ausfuehren,
    _runde_zaehlen,
    _rundenfehler_nachrichten,
    _serverbezug,
)
from services.ai_stream.types import (
    MAX_TOOL_CALLS,
    MAX_TOOL_RESULT_CHARS_PER_ROUND,
    MAX_TOOL_ROUNDS,
    MAX_WRITE_ROUNDS,
    POLLING_WERKZEUGE,
    WERKZEUG_ZEITGRENZE,
    GuardianRahmenUnlesbar,
    _Anlauf,
    _FragenErgebnis,
    _SchreibrundenErgebnis,
    _Vorbereitung,
    _WartenErgebnis,
)
from services.ai_stream.write_tools import _schreibrunde_ausfuehren
from services.ai_tool_registry import READ_TOOLS, WRITE_TOOLS
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    complete_ai_usage,
    fail_ai_usage,
)
from services.dis_client import DisSidecarError
from services.openai_compatible_adapter import (
    MAX_REASONING_CHARS,
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
    usage_addieren,
)

logger = logging.getLogger(__name__)


async def _fruehe_leseaufgaben_verwerfen(aufgaben: dict[str, asyncio.Task]) -> None:
    """Beendet fruehe Read-Tasks, ohne deren Ergebnis sichtbar werden zu lassen."""
    if not aufgaben:
        return
    for aufgabe in aufgaben.values():
        if not aufgabe.done():
            aufgabe.cancel()
    await asyncio.gather(*aufgaben.values(), return_exceptions=True)
    aufgaben.clear()


async def _fruehe_leseaufgaben_filtern(
    aufgaben: dict[str, asyncio.Task], erlaubte_call_ids: set[str]
) -> None:
    """Verwirft Ergebnisse von Calls, die die vollstaendige Runde nicht bestehen."""
    verworfen = [
        aufgaben.pop(call_id)
        for call_id in tuple(aufgaben)
        if call_id not in erlaubte_call_ids
    ]
    for aufgabe in verworfen:
        if not aufgabe.done():
            aufgabe.cancel()
    if verworfen:
        await asyncio.gather(*verworfen, return_exceptions=True)


async def segment_ausfuehren(run_id: str, *, client: httpx.AsyncClient | None = None) -> None:
    """Fuehrt einen Lauf aus, bis er fertig ist, fragt oder auf einen Menschen wartet.

    Das ist der frueher `stream_conversation_reply` genannte Ablauf — mit dem
    einen Unterschied, der alles aendert: er haengt an keinem Request mehr.
    Ergebnisse gehen an den Vermittler (``ai_run_broker``), nicht an einen
    Generator. Wer zusieht, ist dem Lauf gleichgueltig.

    Der Aufbau: `_segment_anlaufen` holt Vorbereitung und Rahmen,
    `_werkzeuge_und_grenze` schneidet den Katalog zu, und in der Schleife
    behandeln `_fragen_behandeln`, `_schreibrunde_ausfuehren`,
    `_runde_filtern` und `_leserunde_ausfuehren` je eine Phase einer Runde.
    Die Kommentare zu den einzelnen Entscheidungen stehen bei den Helfern —
    sie sind mit dem Code dorthin gewandert.
    """
    anlauf_started_at = time.perf_counter()
    anlauf = await ai_stream._segment_anlaufen(run_id, client)
    metrics.record(
        "ai_stream", "segment_preparation", (time.perf_counter() - anlauf_started_at) * 1000,
        "ok" if anlauf is not None else "stopped",
    )
    if anlauf is None:
        return
    vorbereitung = anlauf.vorbereitung
    client = anlauf.client
    zustand = anlauf.zustand
    provider_messages = anlauf.provider_messages
    conversation_id = anlauf.conversation_id
    user_id = anlauf.user_id
    message_id = anlauf.message_id
    guardian = anlauf.guardian
    aufgabe = anlauf.aufgabe
    rolle = anlauf.rolle
    herkunft = anlauf.herkunft
    worker = anlauf.worker
    unbeaufsichtigt = anlauf.unbeaufsichtigt
    # Das Rundenbudget dieses Laufs: fuer Worker der Betreiber-Deckel, sonst
    # die Hauskonstante. Einmal je Segment gelesen — der Deckel eines
    # laufenden Segments soll sich nicht mitten in der Schleife aendern.
    rundendeckel = MAX_TOOL_ROUNDS
    if rolle == "worker":
        from services import ai_worker_limits

        try:
            rundendeckel = min(
                ai_worker_limits.rundenbudget_je_worker(), MAX_TOOL_ROUNDS
            )
        except Exception:
            logger.warning("Worker-Rundenbudget nicht lesbar run_id=%s", run_id)

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
    # Der Absatz, den die nächste Denkzeile vorangestellt bekommt. Gesetzt wird
    # er an der Rundennaht ganz unten, eingelöst beim ersten Gedanken der neuen
    # Runde — genau wie beim Antworttext, nur aufgeschoben. Ohne ihn klebte der
    # letzte Gedanke einer Runde am ersten der nächsten: „…sehe ich mir zuerst
    # die Logs an.Die Logs zeigen einen Portkonflikt…“.
    denknaht = ""
    # Wie viel Denktext dieser **Lauf** schon gesammelt hat. Der Adapter zählt
    # dasselbe, aber je Anfrage: er schreibt in `usage.reasoning_chars`, und
    # `current_usage` wird nach jeder Werkzeugrunde neu angelegt — der Zähler
    # fing also jede Runde wieder bei null an. `usage_addieren` übernimmt ihn
    # ausdrücklich nicht ("die gehören der laufenden Runde und werden vom
    # Aufrufer verwaltet"); genau dieser Zähler ist der Aufrufer, der das tut.
    #
    # Ohne ihn standen bei sechzehn Runden bis zu sechzehnmal 32.000 Zeichen im
    # Vermittler und in `sections_json`, während `_finalize_stream` beim
    # Speichern auf 32.000 kürzt: der Benutzer sah live den ganzen Denkverlauf
    # und nach einem Neuladen dessen Anfang.
    gedachte_zeichen = 0
    usage = StreamUsage()
    abgerechnet = False
    gestellte_frage: dict | None = None
    geparkt = False
    # Parkt der Lauf per `wait_until` oder wartet er auf den Rechner des
    # Benutzers? Dann traegt `wecker` den Zeitpunkt, zu dem der Takt ihn
    # spaetestens weckt (dritte Parkstelle), und `parkgrund` sagt, warum.
    wecker: datetime | None = None
    parkgrund = "wait_until"
    # Wurde dieser Lauf waehrend der Arbeit von einer neuen Nachricht abgeloest?
    # Dann gehoert er nicht mehr uns: abgerechnet wird noch ehrlich, geschrieben
    # wird nichts mehr.
    abgeloest = False
    # Endete der Lauf, weil ihm die Runden ausgingen? Ein solcher Lauf sieht im
    # Ergebnis aus wie einer, der fertig war — er ist es aber nicht, und der
    # Unterschied gehoert ins Protokoll. Genau dafuer stand `stop_reason`
    # ('budget') im Modell und wurde nie gesetzt.
    budget_erschoepft = False
    fruehe_leseaufgaben: dict[str, asyncio.Task] = {}

    def _abbruch_abrechnen() -> None:
        """Der ehrliche Abschluss eines Laufs, der nicht zu Ende kam.

        Dreimal wörtlich derselbe Aufruf stand hier: abgelöst, abgebrochen,
        gescheitert. Das ist die Stelle, an der abgerechnet wird — wer ein
        Argument ergänzt und nur zwei der drei Kopien anfasst, verbucht
        abgebrochene Läufe anders als abgelöste.

        Parameterlos und unmittelbar neben den Zählern, aus denen sie liest.
        Braucht sie einmal ein Argument, ist die Dopplung die ehrlichere
        Fassung — dann gehört sie zurück an ihre drei Stellen.
        """
        ai_stream._finalize_stream(
            message_id=message_id,
            usage_event_id=vorbereitung.usage_event_id,
            content="".join(chunks),
            usage=usage,
            estimated_actual_tokens=0,
            failed=True,
            had_output=bool(chunks),
            token_price_micro_usd_per_million=vorbereitung.token_price_micro_usd_per_million,
            reasoning="".join(thoughts),
            abschnitte=ai_run_broker.abschnitte(run_id),
        )

    try:
        tools, cache_marke, kontextgrenze, denken, denkstufe = await ai_stream._werkzeuge_und_grenze(
            client=client,
            vorbereitung=vorbereitung,
            guardian=guardian,
            aufgabe=aufgabe,
            rolle=rolle,
            herkunft=herkunft,
            zustand=zustand,
        )
        angebotene_werkzeuge = frozenset(
            name
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and isinstance((name := tool["function"].get("name")), str)
        )
        # Das Modell dieses Laufs — je Rolle, siehe `_modell_fuer`. Einmal je
        # Segment: dieselbe Lebensdauer wie der Katalogzuschnitt darueber.
        modellname = _modell_fuer(vorbereitung.provider, rolle)
        # Ist die nächste Runde die abschließende? Dann darf das Modell keine
        # Werkzeuge mehr aufrufen — der Katalog geht aber weiter mit.
        #
        # Hier stand `tools = None`, und das war teuer: bei Anthropic steht der
        # Werkzeugkatalog ganz vorne im zwischengespeicherten Präfix. Fällt er
        # weg, ändert sich die Anfrage an ihrer ersten Stelle, und der Treffer
        # fällt ausgerechnet in der Runde, die den längsten Verlauf trägt.
        # Dieselbe Absicht sagt `tool_choice="none"` (OpenRouter führt den Wert
        # ausdrücklich), ohne den Präfix anzufassen.
        #
        # Die Grenze bleibt trotzdem auf unserer Seite: meldet der Anbieter
        # danach doch Werkzeugaufrufe, werden sie verworfen — siehe unten.
        letzte_runde = False
        current_usage = usage
        signaturen: dict[str, int] = dict(zustand.get("tool_signatures") or {})
        while True:
            frueh_schloss = asyncio.Semaphore(ai_stream._werkzeug_nebenlaeufigkeit())
            # Wo diese Runde im flachen Textpuffer beginnt. Daraus entsteht
            # unten `rundentext`: der Text, den das Modell in derselben
            # Anbieterantwort neben seinen Werkzeugaufrufen gesagt hat. Er geht
            # als `content` der Aufrufnachricht zurueck — sonst kennt das
            # Modell in der Folgerunde seine eigenen Ansagen und Zwischen-
            # schluesse nicht (siehe `_aufrufnachricht`).
            rundenbeginn = len(chunks)
            provider_messages = auf_budget_kuerzen(provider_messages, kontextgrenze)
            # **Direkt hinter der Kürzung**, nicht erst am Segmentende. Unter
            # Budget gibt `auf_budget_kuerzen` dieselbe Liste zurück, darüber
            # eine neue — ab der ersten Kürzung zeigte `zustand` also auf die
            # alte, während der Kommentar weiter unten sagt, `provider_messages`
            # *sei* die Liste im Zustand. Wer eine weitere Ausstiegsstelle
            # einbaut, die den Zustand vor dem Segmentende schreibt, speicherte
            # sonst einen Verlauf ohne alle seither gelesenen Werkzeugergebnisse.
            zustand["provider_messages"] = provider_messages
            provider_started_at = time.perf_counter()
            first_provider_chunk = True
            async for chunk in ai_stream.stream_chat_completion(
                client,
                provider=vorbereitung.provider,
                api_key=vorbereitung.api_key,
                messages=provider_messages,
                usage=current_usage,
                model=modellname,
                tools=tools,
                tool_choice="none" if letzte_runde else None,
                reasoning=denken,
                reasoning_effort=denkstufe,
                cache_marke=cache_marke,
            ):
                if first_provider_chunk:
                    metrics.record(
                        "ai_stream", "first_provider_chunk",
                        (time.perf_counter() - provider_started_at) * 1000,
                    )
                    first_provider_chunk = False
                if chunk.kind == "tool_start":
                    metrics.record(
                        "ai_stream", "tool_start",
                        (time.perf_counter() - provider_started_at) * 1000,
                    )
                    ai_run_broker.veroeffentlichen(run_id, "tool_start", {"tool_name": chunk.text, "spekulativ": True})
                    continue
                if chunk.kind == "tool_ready":
                    call = chunk.tool_call
                    if (
                        call is not None
                        and call.id not in fruehe_leseaufgaben
                        and await asyncio.to_thread(
                            _fruehstart_lesewerkzeug_erlaubt,
                            run_id=run_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            call=call,
                            angebotene_werkzeuge=angebotene_werkzeuge,
                        )
                    ):
                        fruehe_leseaufgaben[call.id] = asyncio.create_task(
                            _lesewerkzeug_ausfuehren(
                                user_id=user_id,
                                call=call,
                                herkunft=herkunft,
                                familie=familie_aus_zustand(zustand),
                                prefetch_session_id=(
                                    str(zustand["prefetch_session_id"])
                                    if zustand.get("prefetch_session_id") else None
                                ),
                                schloss=frueh_schloss,
                            )
                        )
                    # Vollstaendige Tool-Calls sind intern. Ihre Anzeige und
                    # Persistenz bleiben ausschliesslich in der Rundenphase.
                    continue
                if chunk.kind == "reasoning":
                    # Die Grenze steht hier und nicht erst beim Speichern.
                    # Geschnitten wird auch **innerhalb** eines Stücks: nur so
                    # ist der Denktext, den der Benutzer live gesehen hat,
                    # Zeichen für Zeichen derselbe, den er nach dem Neuladen
                    # wiederfindet.
                    rest = MAX_REASONING_CHARS - gedachte_zeichen
                    if rest <= 0:
                        continue
                    # Vorne dran der Absatz, den die vorige Rundennaht bestellt
                    # hat — erst hier, wo feststeht, dass wirklich ein zweiter
                    # Gedanke kommt. Eine Runde ohne Denktext bekommt so keinen
                    # leeren Denkkasten, und der Schlusstext keinen Umbruch am
                    # Ende, hinter dem nichts mehr steht.
                    stueck = (denknaht + chunk.text)[:rest]
                    denknaht = ""
                    gedachte_zeichen += len(stueck)
                    thoughts.append(stueck)
                    ai_run_broker.veroeffentlichen(run_id, "reasoning", {"content": stueck})
                    continue
                chunks.append(chunk.text)
                ai_run_broker.veroeffentlichen(run_id, "delta", {"content": chunk.text})
            if current_usage is not usage:
                # Jede Werkzeugrunde ist eine eigene Anbieteranfrage mit eigenem
                # Prompt — summiert, nicht ersetzt. Vorher wurden hier nur die
                # Tokens addiert; Kosten, Aufschluesselung und die Zahl der
                # Anfragen fielen weg.
                usage_addieren(usage, current_usage)
            if not current_usage.tool_calls:
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                break
            if letzte_runde:
                # Diese Runde war die abschließende: sie ging mit
                # `tool_choice="none"` hinaus. Meldet der Anbieter trotzdem
                # Werkzeugaufrufe, ist das keine Anfrage, die wir erfüllen —
                # wir haben ausdrücklich keine erlaubt. Ein Anbieter, der sich
                # nicht daran hält, hielte den Lauf sonst endlos offen; hier
                # steht die Grenze auf unserer Seite.
                logger.warning(
                    "Anbieter meldet Werkzeugaufrufe trotz tool_choice=none, "
                    "werden verworfen run_id=%s anzahl=%d",
                    run_id, len(current_usage.tool_calls),
                )
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                break

            # Was das Modell in dieser Runde neben den Aufrufen gesagt hat —
            # geht als `content` der Aufrufnachricht mit zurueck.
            rundentext = "".join(chunks[rundenbeginn:])

            fragen = ai_stream._fragen_behandeln(
                current_usage=current_usage,
                unbeaufsichtigt=unbeaufsichtigt,
                run_id=run_id,
                provider_messages=provider_messages,
                zustand=zustand,
                rundentext=rundentext,
                rolle=rolle,
                rundendeckel=rundendeckel,
            )
            if fragen is not None:
                if fragen.signal == "frage":
                    await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                    gestellte_frage = fragen.frage
                    break
                if fragen.budget_erschoepft:
                    budget_erschoepft = True
                if fragen.letzte_runde:
                    letzte_runde = True
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                current_usage = StreamUsage()
                continue

            warten = ai_stream._warten_behandeln(
                current_usage=current_usage,
                rolle=rolle,
                run_id=run_id,
                provider_messages=provider_messages,
                zustand=zustand,
                rundentext=rundentext,
                rundendeckel=rundendeckel,
            )
            if warten is not None:
                if warten.signal == "parken":
                    await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                    wecker = warten.wake_at
                    break
                if warten.budget_erschoepft:
                    budget_erschoepft = True
                if warten.letzte_runde:
                    letzte_runde = True
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                current_usage = StreamUsage()
                continue

            desktop_frist, desktop_budget = ai_stream._desktop_behandeln(
                current_usage=current_usage,
                run_id=run_id,
                user_id=user_id,
                herkunft=herkunft,
                provider_messages=provider_messages,
                zustand=zustand,
                rundentext=rundentext,
                rundendeckel=rundendeckel,
            )
            if desktop_budget:
                budget_erschoepft = True
                letzte_runde = True
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                current_usage = StreamUsage()
                continue
            if desktop_frist is not None:
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                wecker = desktop_frist
                parkgrund = "desktop_jobs"
                break

            # **Ein frei erfundener Werkzeugname kostet die Runde, nicht den
            # Lauf.** Hier stand ein `raise`, und damit endete der ganze Lauf
            # als 'failed': im Chat verlor der Benutzer die Antwort, in einer
            # Heilung brach der unbeaufsichtigte Lauf ab und verbrauchte einen
            # der acht Reparaturanläufe. Jede andere Grenzverletzung — falsche
            # Rolle, falsche Herkunft, Guardian-Menge, blindes Modell — wird
            # längst begründet beantwortet; ausgerechnet der Name, den ein
            # Modell aus injiziertem Material übernimmt (ein Beispiel-Toolcall
            # in einer Logzeile, „Vorlage schlägt Regel"), war der eine Hebel,
            # mit dem sich ein Lauf gezielt abwürgen ließ.
            #
            # Verworfen wird die **ganze** Runde und nicht nur der erfundene
            # Aufruf — dasselbe Muster wie bei der abgewiesenen Rückfrage: ein
            # Plan, der auf einem Werkzeug aufbaut, das es nicht gibt, ist als
            # Ganzes hinfällig. Ausgeführt wird nichts, die Allowlist bleibt
            # unverändert scharf, und gegen hartnäckige Wiederholung steht der
            # Rundendeckel, den diese Runde mitbezahlt.
            if any(
                call.name not in READ_TOOLS and call.name not in WRITE_TOOLS
                for call in current_usage.tool_calls
            ):
                provider_messages.extend(_rundenfehler_nachrichten(
                    current_usage.tool_calls,
                    rundentext,
                    code="AI_TOOL_UNKNOWN",
                    hinweis=(
                        "Diese Runde enthielt einen Werkzeugnamen, den es nicht "
                        "gibt, und wurde deshalb vollständig verworfen. Nutze "
                        "ausschließlich die Werkzeuge aus deinem Katalog und "
                        "rufe die Runde damit erneut auf."
                    ),
                ))
                logger.info(
                    "Erfundener Werkzeugname, Runde verworfen run_id=%s", run_id
                )
                if _runde_zaehlen(zustand, rundendeckel):
                    budget_erschoepft = True
                    letzte_runde = True
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                current_usage = StreamUsage()
                continue

            # Ab hier ist jeder Aufruf entweder lesend oder schreibend/delegierend — die
            # dritte Moeglichkeit hat die Pruefung darueber schon abgefangen.
            kinds = {
                "read" if call.name in READ_TOOLS else "write"
                for call in current_usage.tool_calls
            }
            if kinds == {"write"}:
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                schreib = await ai_stream._schreibrunde_ausfuehren(
                    run_id=run_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    vorbereitung=vorbereitung,
                    guardian=guardian,
                    aufgabe=aufgabe,
                    unbeaufsichtigt=unbeaufsichtigt,
                    rolle=rolle,
                    rundendeckel=rundendeckel,
                    rundentext=rundentext,
                    current_usage=current_usage,
                    provider_messages=provider_messages,
                    zustand=zustand,
                    chunks=chunks,
                    thoughts=thoughts,
                    denknaht=denknaht,
                )
                denknaht = schreib.denknaht
                if schreib.abgeloest:
                    abgeloest = True
                    break
                if schreib.geparkt:
                    geparkt = True
                if schreib.budget_erschoepft:
                    budget_erschoepft = True
                if schreib.letzte_runde:
                    letzte_runde = True
                current_usage = StreamUsage()
                continue

            rundenaufrufe = list(current_usage.tool_calls)
            deferred_calls, filter_signal = ai_stream._runde_filtern(
                kinds=kinds,
                current_usage=current_usage,
                signaturen=signaturen,
                zustand=zustand,
                run_id=run_id,
                rundendeckel=rundendeckel,
            )
            await _fruehe_leseaufgaben_filtern(
                fruehe_leseaufgaben,
                {call.id for call in current_usage.tool_calls},
            )
            if filter_signal == "budget":
                budget_erschoepft = True
                letzte_runde = True
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                current_usage = StreamUsage()
                continue
            if filter_signal == "fertig":
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                break

            # Pruefen, ob die verbleibenden Werkzeugaufrufe Lesewerkzeuge sind
            # UND autonom ausgefuehrt werden duerfen.
            #
            # Maunting Studios Grundsatz („Sicherheit braucht Vertrauen“):
            # Der Autonomie-Modus beschreibt, dass die KI ohne Bestaetigung arbeiten kann.
            # Ist der Autonomie-Modus an, braucht es keine Bestaetigung.
            # Ist der Autonomie-Modus aus, verlangt ausnahmslos jede Handlung und jedes
            # Werkzeug eine Bestaetigung ueber den Vorschlagspfad.
            from services import ai_autonomy_service

            with SessionLocal() as db_check:
                benutzer = db_check.get(User, user_id)
                alle_autonom = False
                if benutzer is not None:
                    alle_autonom = all(
                        call.name in READ_TOOLS
                        and ai_autonomy_service.autonomy_allows(
                            db_check,
                            user=benutzer,
                            server_id=(call.arguments or {}).get("server_id"),
                            tool_name=call.name,
                        )
                        for call in current_usage.tool_calls
                    )

            if not alle_autonom:
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                schreib = await ai_stream._schreibrunde_ausfuehren(
                    run_id=run_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    vorbereitung=vorbereitung,
                    guardian=guardian,
                    aufgabe=aufgabe,
                    unbeaufsichtigt=unbeaufsichtigt,
                    rolle=rolle,
                    rundendeckel=rundendeckel,
                    rundentext=rundentext,
                    current_usage=current_usage,
                    provider_messages=provider_messages,
                    zustand=zustand,
                    chunks=chunks,
                    thoughts=thoughts,
                    denknaht=denknaht,
                )
                denknaht = schreib.denknaht
                if schreib.abgeloest:
                    abgeloest = True
                    break
                if schreib.geparkt:
                    geparkt = True
                if schreib.budget_erschoepft:
                    budget_erschoepft = True
                if schreib.letzte_runde:
                    letzte_runde = True
                current_usage = StreamUsage()
                continue
            if filter_signal == "fertig":
                await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
                break
            denknaht = await ai_stream._leserunde_ausfuehren(
                user_id=user_id,
                conversation_id=conversation_id,
                current_usage=current_usage,
                deferred_calls=deferred_calls,
                vorbereitung=vorbereitung,
                run_id=run_id,
                guardian=guardian,
                aufgabe=aufgabe,
                rolle=rolle,
                herkunft=herkunft,
                zustand=zustand,
                rundentext=rundentext,
                provider_messages=provider_messages,
                chunks=chunks,
                thoughts=thoughts,
                denknaht=denknaht,
                vorab_aufgaben=fruehe_leseaufgaben,
                schloss=frueh_schloss,
                call_reihenfolge=rundenaufrufe,
            )
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
            _abbruch_abrechnen()
            abgerechnet = True
            # Schreibt nichts um: `_lauf_abschliessen` laesst Endzustaende stehen
            # und meldet der Oberflaeche den tatsaechlichen.
            ai_stream._lauf_abschliessen(run_id, status="cancelled", stop_reason="superseded")
            return

        zustand["tool_signatures"] = signaturen
        complete_content = "".join(chunks)
        estimated_actual = max(
            1,
            (message_character_count(provider_messages) + len(complete_content) + 3) // 4,
        )
        ai_stream._finalize_stream(
            message_id=message_id,
            usage_event_id=vorbereitung.usage_event_id,
            content=complete_content,
            usage=usage,
            estimated_actual_tokens=estimated_actual,
            failed=False,
            # Eine Rueckfrage ist eine vollwertige Antwort, und ein Vorschlag
            # ebenso — und ein geplantes Parken (`wait_until`) auch. Ohne das
            # galten sie als "nichts geliefert" — genau der Fall, in dem der
            # Chat "Keine Antwort erhalten" anzeigte.
            had_output=(
                bool(chunks) or gestellte_frage is not None or geparkt
                or wecker is not None
            ),
            token_price_micro_usd_per_million=vorbereitung.token_price_micro_usd_per_million,
            reasoning="".join(thoughts),
            abschnitte=ai_run_broker.abschnitte(run_id),
            question=gestellte_frage,
        )
        abgerechnet = True
        ai_run_broker.veroeffentlichen(run_id, "done", {"message_id": message_id})

        if geparkt:
            # Der Schlusstext der letzten Runde („Vorsicht: das Löschen
            # entfernt auch die Backups — bitte bestätige.") gehoert in den
            # gespeicherten Verlauf des Laufs. Die Fortsetzung nach der
            # Bestaetigung haengt nur `_aktionsmeldung` an; ohne diese Zeile
            # kannte das aufgeweckte Modell seine eigene Warnung und Zusage
            # nicht. `chunks[rundenbeginn:]` ist genau der Text der
            # abschliessenden Runde ohne Werkzeuge.
            schlusstext = "".join(chunks[rundenbeginn:]).strip()
            if schlusstext:
                provider_messages.append(
                    {"role": "assistant", "content": schlusstext}
                )
            ai_stream._lauf_abschliessen(
                run_id,
                status="waiting_confirmation",
                stop_reason="awaiting_confirmation",
                zustand=zustand,
            )
            return
        if wecker is not None:
            # Die dritte Parkstelle: `wait_until` hat den Lauf schlafen gelegt
            # — oder er wartet auf den Rechner des Benutzers (`parkgrund`).
            # Anders als beim Bestaetigungsparken darueber wird hier **kein**
            # Schlusstext angehaengt: das Parken faellt in derselben Runde, und
            # deren Text traegt bereits die Aufrufnachricht — ein zweites
            # Anhaengen hiesse, das Modell laese nach dem Wecken seinen eigenen
            # Satz doppelt.
            ai_stream._lauf_abschliessen(
                run_id,
                status="waiting_wake",
                stop_reason=parkgrund,
                zustand=zustand,
                wake_at=wecker,
            )
            return
        if gestellte_frage is not None:
            ai_stream._lauf_abschliessen(
                run_id, status="waiting_user", stop_reason="question", zustand=zustand
            )
            if worker is not None:
                # Die Frage eines Workers erreicht den Menschen nie direkt —
                # sie geht als Meldung mit Worker-ID an die Meldestelle, und
                # das Gehirn stellt sie in der naechsten Ruhephase
                # (docs/agentic-framework.md, §3). Erst parken, dann melden:
                # die Wahrheit ueber den Laufzustand kommt vor der Zustellung,
                # und eine gescheiterte Meldung laesst die Frage im
                # Worker-Fenster stehen, wo sie lesbar bleibt.
                try:
                    from services import ai_meldestelle

                    with SessionLocal() as db:
                        benutzer = db.get(User, user_id)
                        if benutzer is not None:
                            ai_meldestelle.melden(
                                db,
                                user=benutzer,
                                text=str(gestellte_frage.get("question") or ""),
                                art="frage",
                                kanal=str(worker.get("kanal") or "chat"),
                                worker_id=str(
                                    worker.get("conversation_id") or conversation_id
                                ),
                                worker_titel=str(worker.get("titel") or "") or None,
                                question=gestellte_frage,
                            )
                except Exception:
                    logger.warning(
                        "Worker-Frage nicht gemeldet run_id=%s", run_id
                    )
            return
        # Falten, **bevor** der Lauf abgeschlossen wird. Der Text steht bereits
        # vollständig auf dem Bildschirm: `done` ist raus und `_finalize_stream`
        # hat committet. Danach zu falten war dagegen wirkungslos — der
        # Abschluss veröffentlicht das ruhende `run`-Ereignis und schließt den
        # Kanal, `lauf_verfolgen` steigt an genau diesem Ereignis aus, und
        # `compacted` entstand erst danach. Der Hinweis "Ältere Nachrichten
        # wurden zusammengefasst" erreichte deshalb keinen Browser, und der
        # Kontextring blieb auf dem Stand von vor der Faltung stehen.
        #
        # Der Preis: die Eingabe bleibt während der Faltung gesperrt, weil die
        # Oberfläche erst beim ruhenden `run` freigibt. Vertretbar, weil
        # `compact_conversation` nur oberhalb der Marke überhaupt faltet.
        try:
            from services.ai_compaction_service import compact_conversation

            if await compact_conversation(
                client=client,
                user_id=user_id,
                conversation_id=conversation_id,
                provider_id=vorbereitung.provider.id,
                context_chars=zustand.get("context_chars"),
            ):
                ai_run_broker.veroeffentlichen(
                    run_id, "compacted", {"conversation_id": conversation_id}
                )
        except Exception as exc:
            logger.info("AI-Kompression uebersprungen error=%s", type(exc).__name__)

        ai_stream._lauf_abschliessen(
            run_id,
            status="completed",
            # "budget" heißt: die KI hatte noch etwas vor, durfte aber nicht
            # mehr. Sie hat aus dem geantwortet, was sie hatte — das ist eine
            # Antwort, aber keine erledigte Aufgabe, und wer das Protokoll liest
            # soll den Unterschied sehen.
            stop_reason="budget" if budget_erschoepft else "done",
            zustand=zustand,
        )
    except asyncio.CancelledError:
        # Der Prozess faehrt herunter. Nicht mehr als ehrlich abschliessen.
        await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
        if not abgerechnet:
            _abbruch_abrechnen()
        # Der Abschluss steht **außerhalb** der Bedingung, so wie im Zweig
        # darunter auch. Seit die Faltung vor dem Abschluss läuft, liegt genau
        # dort ein Haltepunkt zwischen "abgerechnet" und "abgeschlossen":
        # trifft der Abbruch ihn, hat der Benutzer seine Antwort, die Zeile
        # stünde aber weiter auf 'running' — und der Abgleich beim nächsten
        # Start machte daraus 'failed'. Einen bereits erreichten Endzustand
        # lässt `_lauf_abschliessen` ohnehin stehen.
        ai_stream._lauf_abschliessen(run_id, status="cancelled", stop_reason="cancelled")
        raise
    except Exception as exc:
        await _fruehe_leseaufgaben_verwerfen(fruehe_leseaufgaben)
        if isinstance(exc, AiProviderRequestError):
            code, message_key = exc.code, "ai.chat.errors.provider"
            # Der Satz des Anbieters endete hier lange im Nichts: der Adapter zog
            # ihn sorgfaeltig aus der Antwort, redigierte und kuerzte ihn — und
            # dann nahm diese Zeile nur `.code`. Uebrig blieb ein uebersetzter
            # Allgemeinplatz, waehrend der Anbieter praezise geantwortet hatte
            # („Insufficient credits", „No endpoints found for X"). Genau daran
            # ist eine Ferndiagnose gescheitert.
            #
            # Behoben ist das eine Schicht frueher: `exc.code` ist seither nicht
            # mehr pauschal `AI_PROVIDER_REQUEST_REJECTED`, sondern benennt den
            # Fall (`AI_PROVIDER_PAYMENT_REQUIRED`, `AI_PROVIDER_RATE_LIMITED`,
            # `AI_PROVIDER_AUTH_FAILED`), und zu jedem Code steht ein eigener
            # Satz in `de.json`. Der Benutzer erfaehrt damit dasselbe — nur in
            # MSMs Worten statt in denen des Anbieters.
            #
            # Der Wortlaut selbst geht **nicht** mit hinaus, sondern nur ins
            # Protokoll. Er wurde einmal mitgeschickt, unter der Zusage, er sei
            # „in `_kurzfassung` bereits von Schluesseln befreit". Die Zusage
            # hielt nicht: `redact_sensitive_text` trifft `sk-` nur mit 16
            # Folgezeichen aus `[A-Za-z0-9_-]`, und ein Anbieter nennt den
            # Schluessel maskiert (`sk-pr***…xyZ4`) — das `*` bricht die Klasse.
            # Und selbst mit dichterem Muster blieben Kontingentstand, Kontoname
            # und Fine-Tune-Bezeichnungen stehen; die traegt kein Muster aus,
            # weil sie wie gewoehnlicher Text aussehen. Ein Lauf gehoert einem
            # Benutzer mit `ai.chat.use`, nicht dem Betreiber — der Zugang, an
            # dem er haengt, gehoert aber dem Betreiber. Dieselbe Abwaegung ist
            # bei `_stimmzugang_pruefen` schon getroffen, und die dort ist die
            # strengere Probe: sie schweigt sogar gegenueber dem Betreiber.
            logger.warning(
                "AI-Lauf am Anbieter gescheitert run_id=%s code=%s grund=%s",
                run_id, code, redact_sensitive_text(exc.detail or "")[:200],
            )
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
            logger.exception("AI-Lauf fehlgeschlagen error=%s", type(exc).__name__)
            code, message_key = "AI_STREAM_FAILED", "ai.chat.errors.unavailable"
        if not abgerechnet:
            _abbruch_abrechnen()
        ai_run_broker.veroeffentlichen(
            run_id, "error", {"code": code, "message_key": message_key}
        )
        ai_stream._lauf_abschliessen(run_id, status="failed", stop_reason=code)

