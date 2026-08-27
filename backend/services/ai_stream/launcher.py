# -*- coding: utf-8 -*-
"""Start- und Nebenlaeufigkeits-Einstiegspunkte fuer AI-Runs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from database import SessionLocal, engine
from models import AiMessage, AiProvider, AiRun, User
import services.ai_stream as ai_stream
from services import (
    ai_attachment_service,
    ai_provider_service,
    ai_run_service,
)
from services.ai_chat_service import get_owned_conversation
from services.ai_context_service import build_provider_messages, estimate_reserved_tokens
from services.ai_guardian_service import briefing_nachricht
from services.ai_provider_service import estimate_cost_microunits
from services.ai_redaction import redact_sensitive_text
from services.ai_stream.context import _modell_fuer, _rolle_ableiten
from services.ai_usage_service import (
    AiQuotaExceeded,
    AiUsageConflict,
    reserve_ai_usage,
)
from services.dis_client import DisSidecarError

logger = logging.getLogger(__name__)


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
    context_chars: int | None = None,
    guardian_briefing_unterdruecken: bool = False,
    unbeaufsichtigt: bool = False,
    gesprochen: bool = False,
    rolle: str | None = None,
    herkunft: str = "panel",
    familie: str | None = None,
    intern: bool = False,
) -> tuple[AiRun | None, tuple[str, str] | None]:
    """Legt einen Lauf an: Benutzernachricht, Kontingent, Antwortnachricht.

    Bewusst **synchron im Request** und nicht im Hintergrund. Ein
    ueberschrittenes Kontingent, ein fehlender Schluessel oder eine doppelt
    gesendete Anfrage sind Dinge, die der Benutzer sofort erfahren soll — nicht
    Sekunden spaeter aus einem Ereignisstrom. Erst wenn all das durch ist,
    beginnt die eigentliche Arbeit, und ab da haengt sie an nichts mehr.

    ``unbeaufsichtigt`` sagt an, dass dies eine Heilung oder ein fällig
    gewordener Auftrag wird. Es wirkt ausschließlich auf das Skill-Verzeichnis
    (die Datennachricht hinter dem Systemprompt entfällt in einem Lauf ohne
    Zuschauer, siehe `ai_context_service._skill_index_block`): der Rahmen
    selbst (``zustand["guardian"]``, ``zustand["aufgabe"]``) entsteht erst
    **nach** dieser Funktion, der Kontext aber schon darin — ohne diesen
    Wert ließe sich der Unterschied hier nicht sehen. Ein eigener Wert und
    nicht an ``guardian_briefing_unterdruecken`` angehängt: das eine
    unterdrückt einen Bericht, das andere entscheidet über den Prompt, und ein
    Aufrufer, der nur das eine will, soll nicht stillschweigend das andere
    bekommen.

    ``rolle`` (voll/gehirn/worker) wird ohne Angabe aus Fensterart, Zugang und
    ``unbeaufsichtigt`` abgeleitet (`_rolle_ableiten`) und im Laufzustand
    eingefroren — jede Fortsetzung arbeitet unter derselben Rolle wie der
    erste Zug. Explizit setzt sie nur die Meldestelle: ihr Lieferlauf ist ein
    Gehirn-Zug im Dauerchat, obwohl niemand davor sitzt.

    ``familie`` ist das **Gerät**, von dem die Bitte kam (die Refresh-Familie
    der Sitzung). Es wird wie ``herkunft`` eingefroren, weil ein Lauf zwischen
    seinen Segmenten schläft: wacht er auf, um einen Auftrag an den Rechner zu
    legen, gibt es die Anfrage von damals längst nicht mehr. Ohne den Wert
    holt sich den Auftrag der Rechner, der zuerst fragt — bei mehreren
    gekoppelten Geräten also nicht zwingend der, an dem der Mensch sitzt.
    ``None`` heißt „nicht bekannt": dann bleibt der Auftrag für alle Geräte
    dieses Benutzers abholbar, so wie vor dieser Spalte.

    ``intern`` markiert die Benutzernachricht als **Maschinerie**: eine Zeile,
    die kein Mensch getippt hat und die er deshalb auch nicht lesen soll. Sie
    entsteht trotzdem und geht vollständig in den Kontext — nur der Weg in den
    Browser filtert sie (`routers/ai_chat.list_messages`). Vier Aufrufer
    setzen sie: die Zustellung der Worker-Meldungen, das Guardian-Briefing,
    der Wiederanlauf nach einem Neustart und die Notiz über eine abgebrochene
    Runde. Ohne sie las der Betreiber im eigenen Chat Anweisungen an die KI,
    adressiert an ihn selbst.
    """
    safe_content = redact_sensitive_text(content).strip()
    if not safe_content:
        return None, ("AI_MESSAGE_EMPTY", "ai.chat.errors.empty")
    if rolle is None:
        rolle = _rolle_ableiten(db, user, conversation, provider, unbeaufsichtigt)
    try:
        geerbte_signaturen = ai_run_service.vorgaenger_abloesen(
            db, conversation_id=conversation.id
        )

        benutzernachricht_id = str(uuid4())
        user_msg = AiMessage(
            id=benutzernachricht_id,
            conversation_id=conversation.id,
            role="user",
            content=safe_content,
            status="complete",
            intern=intern,
        )
        db.add(user_msg)
        ai_attachment_service.bind_to_message(
            db, conversation_id=conversation.id, user_id=user.id,
            message_id=benutzernachricht_id,
        )
        db.flush()
        # Das Thema laeuft weiter, auch wenn der Lauf wechselt: "und jetzt
        # starte ihn neu" nennt keinen Server, gemeint ist der aus der Frage
        # davor. Einmal ermittelt und zweimal gebraucht — fuer den Kontext
        # dieser Nachricht und als Startwert des neuen Laufs.
        #
        serverbezug = ai_run_service.letzter_serverbezug(
            db, conversation_id=conversation.id
        )
        provider_messages = build_provider_messages(
            db, conversation, query=safe_content, server_id=serverbezug,
            context_chars=context_chars, unbeaufsichtigt=unbeaufsichtigt,
            gesprochen=gesprochen, rolle=rolle, herkunft=herkunft,
        )
        # Was Guardian gemeldet hat, waehrend niemand da war. Nur wenn dieser
        # Lauf nicht selbst aus einer Heilung stammt — sonst berichtete die KI
        # sich selbst von dem Vorfall, an dem sie gerade arbeitet.
        #
        # Der Block wird **hier** angehaengt und nicht in
        # `build_provider_messages`: er gehoert zum Start eines Laufs, nicht zum
        # Kontext allgemein, und die Kennungen muessen in den Laufzustand. Eine
        # Provider-Nachricht mit einem Zusatzfeld waere der falsche Traeger — sie
        # geht so, wie sie ist, an den Anbieter.
        gebrieft: list[int] = []
        if not guardian_briefing_unterdruecken:
            from services.ai_guardian_service import briefing_nachricht

            briefing = briefing_nachricht(db, user)
            if briefing is not None:
                text, gebrieft = briefing
                provider_messages.append({"role": "user", "content": text})
        estimated_tokens = estimate_reserved_tokens(provider_messages)
        # Das Modell der Rolle — ein Worker bucht und beschriftet mit dem
        # Arbeitsmodell des Betreibers, nicht mit `default_model`.
        modell = _modell_fuer(provider, rolle)
        usage_event = ai_stream.reserve_ai_usage(
            db,
            user,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
            estimated_cost_microunits=estimate_cost_microunits(provider, estimated_tokens),
            server_id=None,
            provider_id=provider.id,
            model=modell,
        )
        message_id = str(uuid4())
        db.add(AiMessage(
            id=message_id,
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="streaming",
            provider_id=provider.id,
            model=modell,
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
        zustand["guardian_briefed"] = gebrieft
        # Die Rolle, eingefroren wie die Denkstufe: der Systemprompt in den
        # `provider_messages` ist bereits nach ihr geschnitten, und jede
        # Fortsetzung muss denselben Katalogschnitt sehen wie der erste Zug.
        zustand["rolle"] = rolle
        # Und die Herkunft, aus demselben Grund: der Katalog ist danach
        # geschnitten (`herkunft_schnitt`), und eine Fortsetzung darf nicht in
        # einer anderen Welt aufwachen als der erste Zug.
        zustand["herkunft"] = herkunft
        # Und daneben das Geraet: die Herkunft sagt "aus der App", die Familie
        # sagt "aus **dieser** App". Nur zusammen adressieren sie einen
        # Auftrag (`_desktop_behandeln`); die Familie allein entscheidet
        # nichts — weder ueber Rechte noch ueber den Katalog.
        zustand["familie"] = familie
        # Das Budget dieses Laufs, festgehalten fuer alle Fortsetzungen. Es hier
        # abzulegen statt es je Segment neu zu ermitteln ist dieselbe
        # Entscheidung wie bei `reasoning_effort`: was mitten in einer Aufgabe
        # gilt, darf sich nicht aendern, weil jemand zwischendurch das Modell
        # umgestellt hat.
        zustand["context_chars"] = context_chars
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
        print("IN LAUF_BEGINNEN AFTER COMMIT:", [(m.id, m.role, m.content[:20]) for m in db.query(AiMessage).all()])
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


def _anlauf_nebenlaeufigkeit() -> int:
    """Wieviele Laufbeginne gleichzeitig laufen duerfen.

    Dieselbe Regel wie bei `_werkzeug_nebenlaeufigkeit` und aus demselben Grund:
    auf **SQLite** teilen sich alle Sitzungen eine Verbindung, zwei
    Transaktionen darauf sind kein Nebenlauf, sondern ein Datenfehler. Auf
    **PostgreSQL** holt sich jeder Anlauf seine eigene Verbindung.

    Acht und nicht mehr, obwohl `pool_size=10` plus `max_overflow=20` mehr
    hergaebe: der Anlauf ist kurz (13 ms), aber er ist nicht das Einzige, was
    Verbindungen braucht. Bei Stufe 200 waren schon vorher 20 von 30
    gleichzeitig ausgeliehen. Eine unbegrenzte Breite haette daraus 200
    gleichzeitig wartende Anlaeufe gemacht — der Pool haette abgesagt, und zwar
    zuerst den gewoehnlichen Anfragen des Panels.

    **Der wichtigere Teil geht dabei nicht verloren.** Auch bei eins laeuft der
    Anlauf durch `asyncio.to_thread` und damit *neben* der Schleife. Die
    Gleichzeitigkeit ist der zweite Gewinn, nicht der erste.

    Nachgemessen, damit die Eins nicht als Vorsicht missverstanden wird: mit
    acht auf der SQLite-Datei des Benchmarks wurde bei Stufe 200 **alles**
    schlechter — Wanduhr 7,92 s statt 7,31 s, Blockade in Summe 3,87 s statt
    0,97 s, Pool 20 statt 6 Verbindungen. Acht Schreiber auf einer Datei sind
    keine acht Schreiber.
    """
    return 1 if str(engine.url).startswith("sqlite") else 8


@asynccontextmanager
async def _anlaufrecht(conversation_id: str) -> AsyncIterator[None]:
    """Haelt die Reihenfolge, die `lauf_beginnen` bisher geschenkt bekam.

    Solange der Anlauf synchron auf der Schleife lief, konnte es je Unterhaltung
    gar keine zwei geben — das erledigte das Nichtvorhandensein von
    Nebenlaeufigkeit. Im Thread ist das weg, und ausgerechnet hier haengt daran
    etwas Tragendes: `vorgaenger_abloesen` beendet die offenen Laeufe der
    Unterhaltung, und **danach** wird der neue angelegt. Liefen zwei Anlaeufe
    derselben Unterhaltung gleichzeitig, koennte jeder abloesen, bevor der
    andere angelegt hat — und am Ende schrieben zwei Laeufe in denselben Chat.
    Ein Benutzer hat genau eine Unterhaltung; zwei schnell hintereinander
    abgeschickte Nachrichten reichen also aus.

    Das Schloss haengt an der Unterhaltung und nicht am Prozess: zwei Benutzer
    stehen sich damit nicht im Weg, und genau darum geht es.

    **Erst das Schloss, dann die Schranke.** Andersherum haetten Wartende einer
    besetzten Unterhaltung Plaetze der Schranke belegt, ohne zu arbeiten.
    """
    # Globals on ai_stream package
    schleife = asyncio.get_running_loop()
    if schleife is not ai_stream._ANLAUF_SCHLEIFE:
        ai_stream._ANLAUF_SCHLEIFE = schleife
        ai_stream._ANLAUF_SCHRANKE = asyncio.Semaphore(ai_stream._anlauf_nebenlaeufigkeit())
        ai_stream._ANLAUF_SCHLOESSER.clear()
        ai_stream._ANLAUF_WARTENDE.clear()
    schloss = ai_stream._ANLAUF_SCHLOESSER.get(conversation_id)
    if schloss is None:
        schloss = asyncio.Lock()
        ai_stream._ANLAUF_SCHLOESSER[conversation_id] = schloss
    # Mitgezaehlt wird, damit das Schloss wieder verschwindet. Ohne das waechst
    # die Ablage mit jeder je begonnenen Unterhaltung und wird nie kleiner.
    ai_stream._ANLAUF_WARTENDE[conversation_id] = ai_stream._ANLAUF_WARTENDE.get(conversation_id, 0) + 1
    try:
        async with schloss:
            assert ai_stream._ANLAUF_SCHRANKE is not None
            async with ai_stream._ANLAUF_SCHRANKE:
                yield
    finally:
        rest = ai_stream._ANLAUF_WARTENDE.get(conversation_id, 1) - 1
        if rest > 0:
            ai_stream._ANLAUF_WARTENDE[conversation_id] = rest
        else:
            ai_stream._ANLAUF_WARTENDE.pop(conversation_id, None)
            ai_stream._ANLAUF_SCHLOESSER.pop(conversation_id, None)


def _anlauf_im_thread(
    *,
    user_id: int,
    conversation_id: str,
    provider_id: int,
    request_id: UUID,
    content: str,
    reasoning: bool,
    reasoning_effort: str | None,
    context_chars: int | None,
    guardian_briefing_unterdruecken: bool,
    gesprochen: bool = False,
    herkunft: str = "panel",
    familie: str | None = None,
) -> tuple[str | None, tuple[str, str] | None]:
    """Der Anlauf mit **eigener** Sitzung — das ist der ganze Zweck.

    Eine Sitzung ueber eine Threadgrenze zu reichen ist ein Fehler, kein
    Sparfleck: SQLAlchemy-Sitzungen sind nicht threadsicher, und die des
    Requests wird vom Request-Thread gleich danach geschlossen. Deshalb kommen
    hier nur Kennungen an, und die Objekte werden neu geholt.

    Das Neuholen ist zugleich die zweite Pruefung. Zwischen Rechtepruefung im
    Endpunkt und diesem Punkt liegt jetzt eine Wartezeit an der Schranke — in
    der ein Benutzer gesperrt oder ein Anbieter abgeschaltet worden sein kann.
    """
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            logger.info("Anlauf verworfen: Benutzer weg oder gesperrt user_id=%s", user_id)
            return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
        conversation = get_owned_conversation(db, conversation_id, user)
        if conversation is None:
            logger.info("Anlauf verworfen: Unterhaltung weg conversation_id=%s", conversation_id)
            return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
        provider = db.get(AiProvider, provider_id)
        if provider is None or not provider.enabled:
            logger.info("Anlauf verworfen: Anbieter weg provider_id=%s", provider_id)
            return None, ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
        run, fehler = ai_stream.lauf_beginnen(
            db,
            user=user,
            conversation=conversation,
            provider=provider,
            request_id=request_id,
            content=content,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            context_chars=context_chars,
            guardian_briefing_unterdruecken=guardian_briefing_unterdruecken,
            gesprochen=gesprochen,
            herkunft=herkunft,
            familie=familie,
        )
        # Nur die Kennung verlaesst den Thread. Ein ORM-Objekt aus einer gleich
        # geschlossenen Sitzung ist eine Falle: nach dem Commit sind seine
        # Felder abgelaufen, und der erste Zugriff danach wirft.
        return (run.id if run is not None else None), fehler


async def lauf_beginnen_nebenher(
    *,
    user_id: int,
    conversation_id: str,
    provider_id: int,
    request_id: UUID,
    content: str,
    reasoning: bool,
    reasoning_effort: str | None = None,
    context_chars: int | None = None,
    guardian_briefing_unterdruecken: bool = False,
    gesprochen: bool = False,
    herkunft: str = "panel",
    familie: str | None = None,
) -> tuple[str | None, tuple[str, str] | None]:
    """`lauf_beginnen`, aber **neben** der Ereignisschleife statt auf ihr.

    Gibt die Lauf-Kennung zurueck und nicht den Lauf — siehe `_anlauf_im_thread`.
    Der Endpunkt braucht ohnehin nur sie: mit ihr haengt sich der Browser an den
    Ereignisstrom, und das passiert weiterhin sofort, ohne Umweg ueber einen
    Hintergrundauftrag. Verschoben wird nur, **wo** gerechnet wird, nicht
    **wann** geantwortet wird. Ein ueberschrittenes Kontingent und ein
    Anfragekonflikt kommen deshalb weiterhin unmittelbar zurueck.
    """
    async with _anlaufrecht(conversation_id):
        return await asyncio.to_thread(
            ai_stream._anlauf_im_thread,
            user_id=user_id,
            conversation_id=conversation_id,
            provider_id=provider_id,
            request_id=request_id,
            content=content,
            reasoning=reasoning,
            reasoning_effort=reasoning_effort,
            context_chars=context_chars,
            guardian_briefing_unterdruecken=guardian_briefing_unterdruecken,
            gesprochen=gesprochen,
            herkunft=herkunft,
            familie=familie,
        )

