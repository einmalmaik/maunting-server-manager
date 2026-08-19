"""Die persistenten AI-Unterhaltungen eines Benutzers mit POST-SSE.

Es gibt bewusst keine Routen zum Auflisten, Anlegen oder Loeschen von
Unterhaltungen. Der Assistent hat genau einen Chat je Anlass, und die Anlaesse
sind fest (`models.ai_conversation.ARTEN`); geloescht wird der *Verlauf*, nicht
die Unterhaltung.

**Lesen kennt beide Fenster, Schreiben nur den Dauerchat.** Die Endpunkte, die
etwas veraendern — senden, leeren, eine Nachricht zuruecknehmen — arbeiten
weiterhin unbedingt auf ``primary``. Das ist keine Sparmassnahme, sondern der
Zweck des zweiten Fensters: eine getippte Nachricht loest ueber
`vorgaenger_abloesen` jeden offenen Lauf ihrer Unterhaltung ab, und in das
Guardian-Fenster zu schreiben hiesse, eine laufende Reparatur mit einem
Tastendruck abzubrechen. Wer eingreifen will, tut es ausdruecklich: die
Oberflaeche bricht den Auftrag ab und wechselt in den Dauerchat.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from dependencies import require_global, verify_csrf
from models import AiConversation, AiMessage, AiProvider, User
from models.ai_conversation import ARTEN
from schemas.ai_chat import (
    AiChatRequest,
    AiContextStatus,
    AiConversationDetail,
    AiConversationResponse,
    AiMessageEdit,
    AiMessageEditResponse,
    AiMessageResponse,
    AiQuestionPayload,
    AiRunResponse,
    AiSection,
    AiWorkerInfo,
)
from services import (
    ai_chat_service,
    ai_context_service,
    ai_context_window,
    ai_provider_service,
    ai_reasoning,
    ai_run_broker,
    ai_run_service,
)
from services.ai_redaction import redact_sensitive_text
# Das Fenster auf einen Lauf kommt vom Vermittler, die Arbeit von der Schleife.
# Frueher kam beides aus `ai_stream_service` — der Umzug macht die
# Aufgabenteilung, die dort im Modulkopf steht, hier an der Importzeile sichtbar.
from services.ai_run_broker import lauf_verfolgen, sse_event
from services.ai_stream_service import lauf_beginnen_nebenher


router = APIRouter(prefix="/api/ai/conversation", tags=["ai-chat"])

# Der sichtbare Verlauf. Aeltere Nachrichten bleiben gespeichert und fliessen
# ueber die Zusammenfassung weiter in den Kontext ein.
HISTORY_LIMIT = 200


def _fuer_chat(provider) -> bool:
    """Taugt dieser Zugang fuer den Chat?

    Ein Stimmzugang taugt es nicht — ElevenLabs spricht kein
    ``/chat/completions``, sondern nimmt Text und gibt Ton zurueck.
    Die Absage ist bewusst dasselbe 404 wie bei einem unbekannten Zugang und
    keine eigene Fehlermeldung: aus Sicht des Chats *gibt* es diesen Provider
    nicht. Er steht auch in keiner Auswahl (`/api/ai/providers/available`
    filtert ihn), es kann ihn hier also nur nennen, wer die Kennung errät oder
    eine alte Auswahl im Tab liegen hat.

    Die Bedingung selbst steht im Service — dieselbe, nach der die Auswahlliste
    filtert. Beides hier auszuschreiben hiesse, dieselbe Frage zweimal zu
    beantworten, und die zweite Antwort veraltet.
    """
    return ai_provider_service.fuer_chat(provider)


def _art(kind: str) -> str:
    """Prueft die Fensterangabe aus dem Query und gibt sie zurueck.

    Eine unbekannte Art ist ein 404 und keine stillschweigende Umdeutung auf
    ``primary``: wer ``?kind=guardain`` tippt, soll das erfahren und nicht den
    Dauerchat bekommen, den er fuer das Guardian-Fenster haelt.
    """
    if kind not in ARTEN:
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden")
    return kind


def _conversation_response(conversation) -> AiConversationResponse:
    return AiConversationResponse(
        id=conversation.id,
        kind=conversation.kind,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _run_response(db: Session, run) -> AiRunResponse:
    """Ein Lauf fuer die Oberflaeche — mitsamt dem Fenster, in dem er arbeitet.

    Die Art kommt ueber die **Unterhaltung** und nicht aus dem Laufrahmen: der
    Rahmen steht in `state_json` und wird bei jedem Endzustand mitsamt dem
    Arbeitsspeicher geleert (`ai_run_service.arbeitsspeicher_leeren`). Ein
    beendeter Reparaturlauf haette danach keine Art mehr — und die Glocke
    haengte ihn dem Dauerchat an, also ausgerechnet dort, wo er nicht hingehoert.
    """
    conversation = db.get(AiConversation, run.conversation_id)
    return AiRunResponse(
        id=run.id,
        status=run.status,
        stop_reason=run.stop_reason,
        message_id=run.message_id,
        live=ai_run_broker.laeuft(run.id),
        created_at=run.created_at,
        kind=getattr(conversation, "kind", "primary"),
        conversation_id=run.conversation_id,
        server_id=run.last_server_id,
    )


def _question(message: AiMessage) -> AiQuestionPayload | None:
    """Die gespeicherte Rueckfrage, falls diese Nachricht eine gestellt hat.

    Eine unlesbare Zeile wird uebergangen statt den ganzen Verlauf mitzureissen —
    im schlimmsten Fall fehlt eine Frage aus der Vergangenheit, der Chat bleibt
    benutzbar.
    """
    if not message.question_json:
        return None
    try:
        return AiQuestionPayload.model_validate_json(message.question_json)
    except ValueError:
        return None


def _sections(message: AiMessage) -> list[AiSection] | None:
    """Die gespeicherte Gliederung dieser Antwort, falls es eine gibt.

    Dieselbe Nachsicht wie bei `_question` daneben, aus demselben Grund: eine
    unlesbare Zeile kostet die Werkzeugchips einer alten Nachricht, nicht den
    ganzen Verlauf. Der Text steht ohnehin in `content` und wird angezeigt.
    """
    if not message.sections_json:
        return None
    try:
        roh = json.loads(message.sections_json)
    except ValueError:
        return None
    if not isinstance(roh, list):
        return None
    try:
        return [AiSection.model_validate(eintrag) for eintrag in roh]
    except ValueError:
        return None


def _message_response(message: AiMessage) -> AiMessageResponse:
    return AiMessageResponse(
        id=message.id,
        role=message.role,
        content=message.content,
        reasoning=message.reasoning,
        question=_question(message),
        sections=_sections(message),
        status=message.status,
        provider_id=message.provider_id,
        model=message.model,
        created_at=message.created_at,
    )


@router.get("", response_model=AiConversationDetail)
def get_conversation(
    kind: str = Query("primary"),
    before: str | None = Query(
        None,
        description=(
            "Kennung der aeltesten bereits geladenen Nachricht. Liefert die "
            "Seite davor."
        ),
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiConversationDetail:
    """Liefert eine Unterhaltung des Benutzers und legt sie beim ersten Aufruf an.

    ``kind`` waehlt das Fenster und ist ``primary``, wenn nichts dabeisteht —
    die vorhandene Oberflaeche fragt ohne Angabe und bekommt weiterhin den
    Dauerchat.

    ``before`` blaettert zurueck. Der Schnitt laeuft ueber ``(created_at, id)``
    und nicht ueber einen Zaehler: waehrend jemand blaettert, schreibt ein Lauf
    unter Umstaenden weiter, und ein Versatz haette dann Zeilen doppelt
    geliefert und andere ausgelassen. Der Index
    ``ix_ai_messages_conversation_created`` traegt genau diese Ordnung.
    """
    if kind == "worker":
        # Worker-Fenster gibt es viele je Benutzer — "das" Worker-Fenster
        # existiert nicht, und diese Fabrik legte sonst eines an. Gelesen
        # werden sie ueber ihre Kennung (`GET /conversation/worker/{id}`).
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden")
    conversation = ai_chat_service.get_or_create_conversation(db, user, _art(kind))
    db.commit()
    db.refresh(conversation)
    return _verlauf_seite(db, conversation, before)


def _verlauf_seite(
    db: Session, conversation: AiConversation, before: str | None
) -> AiConversationDetail:
    """Eine Seite des Verlaufs, rueckwaerts ab ``before`` — fuer alle Fenster.

    **Interne Zeilen bleiben draussen.** Vier Stellen schreiben eine
    Benutzernachricht, die kein Mensch getippt hat: die Zustellung der
    Worker-Meldungen, das Guardian-Briefing, der Wiederanlauf nach einem
    Neustart und der Pruefauftrag eines faelligen Auftrags. Der Betreiber las
    dort JSON-Nutzlasten und Anweisungen an die KI, adressiert an ihn selbst —
    "Meldung des Panels (nicht vom Benutzer geschrieben): deine
    Hintergrund-Auftraege haben berichtet…". Ein Worker arbeitet im
    Hintergrund; seine Zettel gehoeren nicht ins Gespraech.

    Gefiltert wird **nur hier**, auf dem Weg in den Browser. Der Kontext, der
    zum Anbieter geht (`ai_context_service`), bleibt vollstaendig: sonst
    bekaeme das Gehirn den Auftrag zu liefern und wuesste eine Runde spaeter
    nicht mehr, warum es geliefert hat.

    Der Filter sitzt vor der Seitengrenze und nicht danach. Andersherum zaehlte
    eine unsichtbare Zeile gegen ``HISTORY_LIMIT``, und eine Seite kaeme mit
    neunzehn statt zwanzig Nachrichten zurueck — oder, schlimmer, ``weitere``
    sagte "es geht weiter", obwohl die letzte sichtbare Zeile bereits gezeigt
    war.
    """
    query = db.query(AiMessage).filter(
        AiMessage.conversation_id == conversation.id,
        AiMessage.intern.is_(False),
    )
    if before is not None:
        anker = db.get(AiMessage, before)
        if anker is None or anker.conversation_id != conversation.id:
            raise HTTPException(status_code=404, detail="Nachricht nicht gefunden")
        # Gleicher Zeitstempel kommt vor — zwei Nachrichten derselben Runde
        # koennen in dieselbe Sekunde fallen. Die Kennung entscheidet dann,
        # damit die Seite weder haengt noch eine Zeile ueberspringt.
        query = query.filter(
            (AiMessage.created_at < anker.created_at)
            | (
                (AiMessage.created_at == anker.created_at)
                & (AiMessage.id < anker.id)
            )
        )

    # Eine mehr holen, als geliefert wird: das ist die ganze Auskunft darueber,
    # ob es weitergeht — ohne ein zweites COUNT ueber den halben Verlauf.
    zeilen = (
        query.order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .limit(HISTORY_LIMIT + 1)
        .all()
    )
    weitere = len(zeilen) > HISTORY_LIMIT
    messages = zeilen[:HISTORY_LIMIT]

    base = _conversation_response(conversation)
    return AiConversationDetail(
        **base.model_dump(),
        messages=[_message_response(message) for message in reversed(messages)],
        has_more=weitere,
    )


@router.get("/workers", response_model=list[AiWorkerInfo])
def list_workers(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> list[AiWorkerInfo]:
    """Die lebenden Hintergrund-Auftraege — die Worker-Leiste des Chats.

    Nur nicht beendete Laeufe: die Leiste "raeumt sich auf", indem
    Endzustaende beim naechsten Blick schlicht herausfallen
    (docs/agentic-framework.md, Frontend-Zeile). Geloescht wird dabei nichts —
    die Unterhaltung bleibt ueber `GET /conversation/worker/{id}` lesbar,
    solange die Aufbewahrung sie traegt (Audit-Regel).

    Kein eigenes Recht: es sind die eigenen Auftraege dieses Benutzers, und
    gestartet werden sie nur, wenn seine Rolle `ai.background.use` traegt.
    """
    from services import ai_worker_service

    eintraege: list[AiWorkerInfo] = []
    for lauf in ai_worker_service.aktive_worker(db, user_id=user.id):
        fenster = db.get(AiConversation, lauf.conversation_id)
        if fenster is None:
            continue
        eintraege.append(
            AiWorkerInfo(
                conversation_id=fenster.id,
                title=fenster.title,
                status=lauf.status,
                created_at=lauf.created_at,
            )
        )
    return eintraege


@router.get("/worker/{conversation_id}", response_model=AiConversationDetail)
def get_worker_conversation(
    conversation_id: str,
    before: str | None = Query(
        None,
        description=(
            "Kennung der aeltesten bereits geladenen Nachricht. Liefert die "
            "Seite davor."
        ),
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiConversationDetail:
    """Liest ein Worker-Fenster ueber seine Kennung — nur lesend.

    Es gibt bewusst keinen Schreibweg in ein Worker-Fenster: eine getippte
    Nachricht loeste ueber `vorgaenger_abloesen` den laufenden Auftrag ab
    (dieselbe Begruendung wie beim Guardian-Fenster im Modul-Docstring).
    Gesteuert wird im Gespraech — "Stopp den Auftrag" geht an das Gehirn,
    das `worker_cancel` ruft (docs/agentic-framework.md, §6).

    Ein fremdes oder andersartiges Fenster ist dasselbe 404 wie ein
    unbekanntes: aus Sicht dieses Benutzers gibt es diese Unterhaltung nicht.
    """
    fenster = db.get(AiConversation, conversation_id)
    if fenster is None or fenster.user_id != user.id or fenster.kind != "worker":
        raise HTTPException(status_code=404, detail="Unterhaltung nicht gefunden")
    return _verlauf_seite(db, fenster, before)


@router.post("/typing", status_code=status.HTTP_204_NO_CONTENT)
def typing_signal(
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> Response:
    """Das Tipp-Signal der Ruhe-Regel: "der Mensch schreibt gerade".

    Uebertragen wird ausschliesslich der Zeitpunkt — nie der Text und nicht
    einmal seine Laenge (Datenminimierung: das Eingabefeld kann Secrets
    enthalten). Die Meldestelle haelt je Benutzer nur den letzten Zeitstempel
    im Speicher (`tippen_melden`); solange er frisch ist, stellt sie keine
    Worker-Meldungen zu (docs/agentic-framework.md, §4).
    """
    from services import ai_meldestelle

    ai_meldestelle.tippen_melden(user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/context", response_model=AiContextStatus)
async def get_context_status(
    request: Request,
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiContextStatus:
    """Wie voll der Kontext ist, gemessen am Fenster des gewaehlten Modells.

    Der Provider steht im Query und nicht im Lauf, weil die Frage schon **vor**
    der ersten Nachricht beantwortet werden muss: die Oberflaeche zeigt den Ring
    von Anfang an, und beim Umschalten des Modells aendert sich die Antwort
    sofort, ohne dass jemand etwas gesendet haette.

    Die Belegung kommt aus ``geschaetzte_belegung`` und nicht aus einem echten
    Kontextaufbau. Ein Aufbau zoege Redaction, Memory-Auswahl und
    Skill-Verzeichnis ueber den ganzen Verlauf — bei jedem Blick auf den Ring,
    und der wird nach jeder Antwort neu geholt.
    """
    provider = db.get(AiProvider, provider_id)
    if provider is None or not provider.enabled or not _fuer_chat(provider):
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()

    fenster = await ai_context_window.ermitteln(
        request.app.state.ai_http_client, provider, db=db, user_id=user.id
    )
    context_chars = fenster.zeichen if fenster.bekannt else None
    grenzen = ai_context_service.teilbudgets(context_chars)
    belegt = ai_context_service.geschaetzte_belegung(db, conversation, grenzen)
    je_token = ai_context_window.ZEICHEN_JE_TOKEN
    return AiContextStatus(
        known=fenster.bekannt,
        window_tokens=fenster.fenster_tokens if fenster.bekannt else None,
        usable_tokens=fenster.nutzbar_tokens,
        used_tokens=belegt // je_token,
        compaction_percent=ai_context_window.schwelle_prozent(),
        summarized=bool(conversation.summary),
    )


@router.delete("/messages", status_code=status.HTTP_204_NO_CONTENT)
def clear_conversation_history(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> Response:
    """Leert den Verlauf. Die Unterhaltung und das Audit bleiben bestehen."""
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    ai_chat_service.clear_history(db, conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/messages/{message_id}", response_model=AiMessageEditResponse)
def edit_message(
    message_id: str,
    payload: AiMessageEdit,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> AiMessageEditResponse:
    """Nimmt eine eigene Nachricht zurueck, damit sie neu gestellt werden kann.

    Der Endpunkt sendet **nicht**. Er raeumt nur auf: die Nachricht und alles
    Spaetere verschwinden, danach schickt die Oberflaeche den neuen Text ueber
    den gewohnten Streamweg. Zwei Schritte statt einem, weil das Senden eine
    Kontingentreservierung, eine Anbieterwahl und einen Stream braucht — all
    das haette hier nichts verloren.

    Der neue Text wird trotzdem entgegengenommen und geprueft: eine Bearbeitung
    abzulehnen, *nachdem* der halbe Verlauf weg ist, waere die schlechtere
    Reihenfolge.
    """
    safe_content = redact_sensitive_text(payload.content).strip()
    if not safe_content:
        raise HTTPException(status_code=422, detail="Nachricht ist leer")

    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    message = ai_chat_service.owned_message(db, conversation, message_id)
    removed = ai_chat_service.truncate_from(db, conversation, message)
    db.commit()
    return AiMessageEditResponse(removed=removed)


async def _replay_message(message: AiMessage) -> AsyncIterator[str]:
    yield sse_event("message", {"message_id": message.id, "request_id": message.request_id})
    # Die Wiedergabe folgt der **gespeicherten Reihenfolge**, wenn es eine gibt.
    # Ein einzelnes `delta` mit dem ganzen Text waere fuer eine Antwort, die
    # zwischen ihren Absaetzen Werkzeuge gerufen hat, eine falsche Wiedergabe:
    # der Text stimmte, die Werkzeuge fehlten, und beim naechsten Neuladen
    # saehe dieselbe Nachricht wieder anders aus als eben.
    abschnitte = _sections(message)
    # Trägt die Gliederung die Gedanken, kommen sie an ihrer Stelle. Nur für
    # Nachrichten aus der Zeit davor gibt es sie ausschließlich flach — dann
    # vorweg, wie bisher. Beides zugleich wäre derselbe Text zweimal.
    if message.reasoning and not any(
        abschnitt.art == "denken" for abschnitt in (abschnitte or [])
    ):
        yield sse_event("reasoning", {"content": message.reasoning})
    if abschnitte:
        for abschnitt in abschnitte:
            if abschnitt.art == "text" and abschnitt.inhalt:
                yield sse_event("delta", {"content": abschnitt.inhalt})
            elif abschnitt.art == "denken" and abschnitt.inhalt:
                yield sse_event("reasoning", {"content": abschnitt.inhalt})
            elif abschnitt.art == "tool" and abschnitt.werkzeug:
                yield sse_event("tool", abschnitt.werkzeug)
    elif message.content:
        yield sse_event("delta", {"content": message.content})
    # Endete der Zug mit einer Rueckfrage, gehoert sie auch in die Wiedergabe —
    # sonst saehe der Benutzer nach einem erneuten Absenden derselben Anfrage
    # eine Antwort ohne die Frage, auf die er antworten soll.
    frage = _question(message)
    if frage is not None:
        yield sse_event("question", frage.model_dump())
    yield sse_event("done", {"message_id": message.id, "replayed": True})


async def _fehlerstrom(code: str, message_key: str) -> AsyncIterator[str]:
    """Ein Fehler, der vor dem Lauf auftrat — es gibt nichts zu verfolgen."""
    yield sse_event("error", {"code": code, "message_key": message_key})


@router.get("/run")
def get_active_run(
    kind: str | None = Query(
        "primary",
        description=(
            "Fenster, in dem gesucht wird. Ohne Angabe der Dauerchat; leer "
            "gelassen (kind=) ueber alle Fenster — das fragt die Glocke."
        ),
    ),
    conversation_id: str | None = Query(
        None,
        description=(
            "Kennung eines bestimmten Fensters — der Weg der Worker-Ansicht, "
            "denn kind=worker ist mehrdeutig. Hat Vorrang vor kind."
        ),
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> AiRunResponse | None:
    """Der Lauf, der gerade noch etwas vorhat — oder nichts.

    Die Oberflaeche fragt das beim Oeffnen: laeuft da noch etwas von vorhin?
    Damit haengt sie sich nach einem Seitenwechsel oder einem Neustart des
    Browsers wieder an, statt eine abgebrochene Antwort zu zeigen.

    **Die Fensterangabe ist hier keine Bequemlichkeit.** Ohne sie beantwortet
    der Endpunkt "laeuft irgendwo etwas fuer diesen Menschen?", und der Chat
    haengte sich danach an eine Guardian-Reparatur und zeichnete sie in das
    Fenster des Benutzers — genau das, was die Trennung beseitigt. Die Glocke
    darf die Frage weit stellen (``kind=``), muss dann aber ueber ``kind`` in
    der Antwort entscheiden, wohin sie zeigt.
    """
    if conversation_id is not None:
        run = ai_run_service.aktiver_lauf(
            db, user_id=user.id, conversation_id=conversation_id
        )
    else:
        run = ai_run_service.aktiver_lauf(
            db, user_id=user.id, kind=_art(kind) if kind else None
        )
    if run is None:
        return None
    return _run_response(db, run)


@router.post("/guardian/takeover")
def guardian_uebernehmen(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> dict:
    """Ein Mensch uebernimmt: die laufenden Reparaturen dieses Menschen enden.

    Der Knopf im Guardian-Fenster. Er ist der Grund, warum es dort kein
    Eingabefeld gibt: abbrechen soll man koennen, aber ausdruecklich und nicht
    durch einen Tastendruck — im Chat loest jede getippte Nachricht ueber
    `vorgaenger_abloesen` den offenen Lauf ab, und in einem Fenster, in dem seit
    vier Uhr eine Reparatur laeuft, waere ein Eingabefeld ein Knopf zum
    versehentlichen Abbrechen.

    **Beendet wird der Auftrag, nicht nur der Lauf.** Nur den Lauf abzubrechen
    hiesse, dass der Takt neunzig Sekunden spaeter den naechsten startet — der
    Mensch haette uebernommen und die KI arbeitete weiter.

    Kein eigenes Recht: es sind die eigenen Auftraege dieses Benutzers, und
    ``ai.chat.use`` hat er ohnehin, sonst gaebe es das Fenster fuer ihn nicht.
    """
    from services import ai_guardian_repair_service

    beendet = ai_guardian_repair_service.uebernehmen(db, user=user)
    # Erst der Auftrag, dann der Lauf. Andersherum faende der Takt zwischen
    # beiden Schritten einen Auftrag ohne laufenden Lauf und startete den
    # naechsten Anlauf — mitten in die Uebernahme hinein.
    fenster = ai_chat_service.get_or_create_conversation(db, user, "guardian")
    ai_run_service.vorgaenger_abloesen(db, conversation_id=fenster.id)
    db.commit()
    return {"aborted": beendet}


@router.get("/run/{run_id}/stream")
def attach_run(
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
) -> StreamingResponse:
    """Haengt sich an einen laufenden Lauf an — auch Minuten spaeter.

    Kein CSRF-Schutz noetig und keiner moeglich: das ist ein GET, der nichts
    veraendert. Die Zugehoerigkeit wird ueber ``eigener_lauf`` geprueft, ein
    fremder Lauf ist schlicht nicht zu finden.
    """
    run = ai_run_service.eigener_lauf(db, run_id, user)
    if run is None:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")
    return StreamingResponse(
        lauf_verfolgen(run.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/messages/stream")
async def stream_message(
    payload: AiChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> StreamingResponse:
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    provider = db.get(AiProvider, payload.provider_id)
    if provider is None or not provider.enabled or not _fuer_chat(provider):
        raise HTTPException(status_code=404, detail="Provider nicht gefunden")
    if provider.requires_api_key and not provider.operator_api_key_encrypted:
        raise HTTPException(status_code=409, detail="Fuer diesen Provider ist kein API-Key konfiguriert")

    existing = db.query(AiMessage).filter(AiMessage.request_id == str(payload.request_id)).first()
    if existing is not None:
        if existing.conversation_id != conversation.id or existing.provider_id != provider.id:
            raise HTTPException(status_code=409, detail="AI-Request-ID wurde widerspruechlich wiederverwendet")
        if existing.status == "complete":
            stream = _replay_message(existing)
        else:
            raise HTTPException(status_code=409, detail="AI-Anfrage ist bereits aktiv oder fehlgeschlagen")
    else:
        safe_content = redact_sensitive_text(payload.content).strip()
        if not safe_content:
            raise HTTPException(status_code=400, detail="Nachricht ist nach Sicherheitsfilter leer")
        # Die Denkvorgabe wird **hier** festgelegt und nicht erst beim Senden:
        # der Anlauf kennt keinen HTTP-Client, der Modellkatalog ist aber ein
        # HTTP-Abruf. Wichtiger noch — der geklemmte Wert gehoert in den Lauf,
        # damit eine Fortsetzung nach einer Bestaetigung dieselbe Tiefe
        # verwendet wie der erste Zug.
        denken, stufe = await ai_reasoning.vorgabe(
            request.app.state.ai_http_client,
            db,
            user=user,
            provider=provider,
            aktiv=payload.reasoning,
            wunsch=payload.reasoning_effort,
        )
        # Aus demselben Grund hier und nicht im Anlauf: das Kontextfenster steht
        # im Modellkatalog, und den zu befragen ist ein HTTP-Abruf. Es gehoert
        # ausserdem in den Lauf — eine Fortsetzung nach einer Bestaetigung muss
        # mit demselben Budget rechnen wie der erste Zug, auch wenn der
        # Betreiber zwischendurch das Modell wechselt.
        fenster = await ai_context_window.ermitteln(
            request.app.state.ai_http_client, provider, db=db, user_id=user.id
        )
        # Ab hier reisen nur noch **Kennungen** weiter. Der Anlauf legt sich
        # gleich eine eigene Sitzung in einem eigenen Thread an, und die des
        # Requests darf diese Grenze nicht ueberschreiten — sie gehoert dem
        # Request-Thread und wird von ihm geschlossen.
        user_id, conversation_id, provider_id = user.id, conversation.id, provider.id
        # Und sie darf auch keine offene Transaktion ueber die Grenze tragen.
        # Unter SQLite teilen sich beide Sitzungen eine Verbindung; der Commit
        # der einen schloesse die offene Arbeit der anderen mit ab. Frueher
        # endete `lauf_beginnen` selbst mit genau diesem Commit — es ist also
        # derselbe Zeitpunkt wie bisher, nur eine Zeile frueher.
        db.commit()
        run_id, fehler = await lauf_beginnen_nebenher(
            user_id=user_id,
            conversation_id=conversation_id,
            provider_id=provider_id,
            request_id=payload.request_id,
            content=safe_content,
            reasoning=denken,
            reasoning_effort=stufe,
            # Ein unbekanntes Fenster reist als `None` weiter und nicht als
            # Rueckfallzahl: nur so kann die Kompression den Unterschied
            # zwischen "kleines Fenster" und "kein Wissen" noch sehen.
            context_chars=fenster.zeichen if fenster.bekannt else None,
        )
        if run_id is None:
            code, message_key = fehler or ("AI_PREPARATION_FAILED", "ai.chat.errors.unavailable")
            stream = _fehlerstrom(code, message_key)
        else:
            # Reihenfolge ist hier alles: Kanal auf, **abonnieren**, dann erst
            # die Arbeit starten.
            #
            # Der Rumpf einer StreamingResponse laeuft erst an, wenn Starlette
            # ihn zu lesen beginnt — der Lauf arbeitet da laengst. Wuerde erst
            # dort abonniert, waeren die ersten Zeichen schon durch. Abonniert
            # wird deshalb hier, synchron, vor dem Start.
            ai_run_broker.eroeffnen(run_id)
            abo = ai_run_broker.abonnieren(run_id)
            ai_run_service.lauf_starten(run_id)
            stream = lauf_verfolgen(run_id, abo=abo)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
