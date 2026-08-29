"""Der Eingang zum Sprachmodus.

Ein WebSocket unter ``/api/…``, und das ist keine Geschmacksfrage: das Cookie
``__Secure-access_token`` gilt nur unterhalb von ``/api``. Ein Endpunkt daneben
bekäme es gar nicht erst, und die einzige Auth, die dann bliebe, wäre ein Token
im Query-String — verboten nach CLAUDE.md § 4, und zu Recht: er landet in
Zugriffslogs, Proxy-Logs und im Browserverlauf.

Die Auth-Kette ist dieselbe wie beim Konsolen-WebSocket
(`routers/servers.py::server_console_ws`) und ausdrücklich nicht neu erfunden:
Origin gegen die CORS-Allowlist statt CSRF, dann Cookie, dann Recht, Abweisung
jeweils als ``close(1008)``. Wer das kopiert, kopiert eine Entscheidung, die
schon einmal getroffen und begründet wurde.

Der Router wählt genau einen von zwei Wegen. Ein panelweit aktivierter
OpenAI-Realtime-Zugang hat Vorrang und verwendet WebRTC plus serverseitiges
Sideband. Ohne ihn bleibt der Legacy-Weg aus Transkription, normalem Chatlauf,
Pipecat und ElevenLabs unverändert. Ein Laufzeitfehler wechselt niemals still
zwischen diesen Wegen.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import (
    get_current_user_for_ws,
    require_global,
    verify_csrf,
    ws_session_familie,
    ws_session_herkunft,
    ws_subprotokoll,
)
from models import AiProvider, User
from services import (
    ai_chat_service,
    ai_provider_registry,
    ai_provider_service,
    ai_tts,
    ai_voice_bridge,
    ai_voice_vad,
)
from services.permission_service import has_global_permission
from services.ai_voice.pipecat_pipeline import pipecat_verfuegbar
from services.ai_voice.realtime_session import RealtimeSitzung, vorbereiten as realtime_vorbereiten
from services.ai_voice.transcription import hoeren as transkribieren

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/voice", tags=["ai-voice"])


async def _ws_ablehnen(websocket: WebSocket, *, grund: str) -> None:
    """Lehnt einen Sprach-Handshake ab, ohne Auth- oder Anbieterdaten zu leaken.

    Uvicorn kann vor ``accept()`` nur ``403`` melden. Die klassifizierte
    Serverdiagnose ist daher nötig, um eine fehlerhafte Desktop-Sitzung von
    einer fehlenden Anbieter-Konfiguration zu unterscheiden. Token, Origin,
    Providerdaten und Fehlermeldungen werden absichtlich nicht protokolliert.
    """
    logger.warning("Sprach-WebSocket vor Upgrade abgelehnt: grund=%s", grund)
    await websocket.close(code=1008)


def _ws_origin_erlaubt(websocket: WebSocket) -> bool:
    """Derselbe Origin-Check wie beim Konsolen-WebSocket.

    Importiert statt nachgebaut: zwei Allowlists wären zwei Antworten auf
    dieselbe Frage, und die zweite veraltete.
    """
    from routers.servers import _ws_origin_allowed

    return _ws_origin_allowed(websocket.headers.get("origin"))


def _hoerender_zugang(
    db: Session, bevorzugter_provider_id: int | None = None
) -> AiProvider | None:
    """Der Zugang, der Gesprochenes transkribiert (STT / Gehör).

    Verlangt wird mehr als ein Chatzugang: er muss ein **Transkriptmodell**
    hinterlegt haben. Ohne das gibt es kein Gehör, und ohne Gehör keinen
    Sprachmodus — eines zu raten hiesse, dem Betreiber ein Modell in Rechnung
    zu stellen, das er nie ausgewählt hat.

    Verlangt wird ausserdem, dass der **Anbieter** überhaupt zuhören kann
    (`gehoer_wege`).

    Wurde ein bevorzugter Provider (z. B. der im Chat gewählte) übergeben und
    besitzt er selbst ein Transkriptionsmodell, wird er bevorzugt. Andernfalls
    greift der erste aktivierte Provider mit Transkriptionsmodell.
    """
    if bevorzugter_provider_id:
        bevorzugt = db.get(AiProvider, bevorzugter_provider_id)
        if (
            bevorzugt
            and bevorzugt.enabled
            and ai_provider_service.spricht(bevorzugt, ai_provider_registry.CHAT)
            and ai_provider_registry.anbieter(bevorzugt.provider_kind).gehoer_wege
            and bool((bevorzugt.transcription_model or "").strip())
            and (not bevorzugt.requires_api_key or bool(bevorzugt.operator_api_key_encrypted))
        ):
            return bevorzugt

    for zugang in _zugaenge(db):
        if not ai_provider_service.spricht(zugang, ai_provider_registry.CHAT):
            continue
        if not ai_provider_registry.anbieter(zugang.provider_kind).gehoer_wege:
            continue
        if not (zugang.transcription_model or "").strip():
            continue
        if zugang.requires_api_key and not zugang.operator_api_key_encrypted:
            continue
        return zugang
    return None


def _denkender_zugang(
    db: Session, bevorzugter_provider_id: int | None = None
) -> AiProvider | None:
    """Der Chatzugang, der die Antwort generiert (LLM / Gehirn).

    Wurde ein bevorzugter Provider übergeben, wird dieser genommen. Andernfalls
    greift der erste aktivierte Chatzugang mit hinterlegtem Standardmodell.

    Zwei Fragen, bewusst getrennt gestellt: `fuer_chat()` beantwortet, ob der
    Zugang überhaupt einen Chatlauf tragen kann — dieselbe Antwort wie in der
    Providerliste und im Chat-Router. Die Schlüsselprüfung darunter ist die
    zweite Frage: ist er auch betriebsbereit? Sie bleibt hier und wandert nicht
    in `fuer_chat()`, denn die Providerliste zeigt einen Zugang ohne Schlüssel
    absichtlich an. Hier dagegen wird sofort losgesprochen, und ein Zugang ohne
    Schlüssel führte nur zu einer Stille, die niemand erklärt.
    """
    if bevorzugter_provider_id:
        bevorzugt = db.get(AiProvider, bevorzugter_provider_id)
        if (
            bevorzugt
            and bevorzugt.enabled
            and ai_provider_service.fuer_chat(bevorzugt)
            and (not bevorzugt.requires_api_key or bool(bevorzugt.operator_api_key_encrypted))
        ):
            return bevorzugt

    for zugang in _zugaenge(db):
        if not ai_provider_service.fuer_chat(zugang):
            continue
        if zugang.requires_api_key and not zugang.operator_api_key_encrypted:
            continue
        return zugang
    return None


def _sprechender_zugang(db: Session) -> AiProvider | None:
    """Der Zugang, der vorliest — mit hinterlegter Stimme (TTS).

    Ohne Stimm-Kennung gibt es keinen Sprachmodus. Eine zu raten wäre nicht
    bloss unhöflich, sondern falsch: die Stimmen gehören dem Konto des
    Betreibers, MSM kennt sie nicht, und jede geratene stünde auf seiner
    Rechnung.
    """
    for zugang in _zugaenge(db):
        if not ai_provider_service.spricht(zugang, ai_provider_registry.TTS):
            continue
        if not ai_tts.moeglich(zugang.provider_kind):
            # Der Sprachdienst dieses Anbieters läuft hier nicht — meist, weil
            # eine weich importierte Bibliothek fehlt. Für den Benutzer ist das
            # dasselbe wie ein fehlender Zugang: die Funktion gibt es nicht,
            # und ein Knopf, der beim Klick abbricht, wäre die schlechtere
            # Auskunft.
            #
            # Die Frage steht **je Zugang** und nicht einmal davor: der eine
            # Sprachdienst kann fehlen, während ein zweiter läuft.
            continue
        if not (zugang.default_voice or "").strip():
            continue
        if zugang.requires_api_key and not zugang.operator_api_key_encrypted:
            continue
        return zugang
    return None


def _zugaenge(db: Session) -> list[AiProvider]:
    return (
        db.query(AiProvider)
        .filter(AiProvider.enabled.is_(True))
        .order_by(AiProvider.id)
        .all()
    )


def sprachzugang(
    db: Session, user: User, bevorzugter_provider_id: int | None = None
) -> tuple[AiProvider, AiProvider, AiProvider] | None:
    """Gehör, Gehirn und Stimme, oder gar nichts.

    Löst alle drei Rollen unabhängig auf:
    1. hoeren: Provider für Speech-to-Text (STT)
    2. denken: Provider für Chat/LLM (Gehirn)
    3. sprechen: Provider für Text-to-Speech (TTS)

    Schickt der Client keine Wahl mit, gilt die im Konto gespeicherte
    (users.ai_provider_id): das Overlay der Desktop-App kennt die
    Providerliste nicht und soll trotzdem mit demselben Modell sprechen,
    das der Benutzer im Chat gewählt hat — nicht mit dem erstbesten.
    """
    if bevorzugter_provider_id is None:
        bevorzugter_provider_id = user.ai_provider_id
    hoeren = _hoerender_zugang(db, bevorzugter_provider_id)
    if hoeren is None:
        return None
    denken = _denkender_zugang(db, bevorzugter_provider_id)
    if denken is None:
        return None
    sprechen = _sprechender_zugang(db)
    if sprechen is None:
        return None
    return hoeren, denken, sprechen


@router.get("/config")
def voice_config(
    provider_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.voice.use")),
) -> dict:
    """Ob der Sprachmodus für diesen Benutzer überhaupt zur Verfügung steht.

    Die Oberfläche fragt das, bevor sie einen Sprachknopf zeigt. Ohne
    eingerichtete Zugänge gibt es keinen — kein ausgegrauter Knopf, kein Hinweis
    auf etwas, das der Betreiber nicht bestellt hat. Dieselbe Regel wie bei
    `web_search`, das ohne hinterlegten Schlüssel nicht einmal im
    Werkzeugkatalog steht.
    """
    realtime = ai_provider_service.realtime_zugang(db)
    zugaenge = None if realtime else sprachzugang(db, user, bevorzugter_provider_id=provider_id)
    hoeren, denken, sprechen = zugaenge if zugaenge else (None, None, None)
    diktat = _hoerender_zugang(db, provider_id or user.ai_provider_id)
    return {
        "available": realtime is not None or (zugaenge is not None and pipecat_verfuegbar()),
        "mode": "openai_realtime" if realtime else "legacy",
        # Das denkende Modell, nicht das hörende: danach fragt, wer wissen will,
        # wer da antwortet.
        "model": realtime.realtime_model if realtime else (denken.default_model if denken else None),
        "sample_rate": ai_voice_vad.ABTASTRATE,
        "max_seconds": ai_voice_bridge.MAX_SITZUNGSSEKUNDEN,
        # Die Stimm-Kennung. Sie steht im Info-Dialog und ist ohne
        # eingerichteten Zugang ``null`` — es gibt hier nichts aufzulösen, weil
        # es keine Standardstimme gibt und geben soll.
        "voice": realtime.realtime_voice if realtime else (sprechen.default_voice if sprechen else None),
        "language": realtime.realtime_language if realtime else "auto",
        "reasoning_effort": realtime.realtime_reasoning_effort if realtime else None,
        "dictation_available": bool(
            diktat and has_global_permission(db, user, "ai.chat.use")
        ),
        # Fähigkeitsmarker für die Desktop-App: dieses Backend nimmt das
        # Bearer-Token als WebSocket-Subprotokoll an. Ein gescheiterter
        # WS-Handshake verrät dem Browser nichts — die App fragt dann hier
        # nach und kann „Panel zu alt" von „Netz weg" unterscheiden. Auf
        # älteren Ständen fehlt das Feld, und genau das ist die Auskunft.
        "bearer_ws": True,
    }


@router.post("/transcribe")
async def transcribe_voice_input(
    request: Request,
    provider_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.chat.use")),
    _: None = Depends(verify_csrf),
) -> dict:
    """Transkribiert begrenztes PCM für das Chat-Eingabefeld, ohne zu senden."""
    if not has_global_permission(db, user, "ai.voice.use"):
        raise HTTPException(status_code=403, detail="Nicht erlaubt")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/octet-stream":
        raise HTTPException(status_code=415, detail="Audio muss als PCM übertragen werden")
    zugang = _hoerender_zugang(db, provider_id)
    if zugang is None or zugang.id != provider_id:
        raise HTTPException(status_code=400, detail="Kein Transkriptionsmodell eingerichtet")
    max_bytes = ai_voice_vad.ABTASTRATE * 2 * 180
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="Audioaufnahme ist zu lang")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Ungültige Audioaufnahme") from exc
    pcm_puffer = bytearray()
    async for teil in request.stream():
        if len(pcm_puffer) + len(teil) > max_bytes:
            raise HTTPException(status_code=413, detail="Audioaufnahme ist zu lang")
        pcm_puffer.extend(teil)
    pcm = bytes(pcm_puffer)
    if not pcm or len(pcm) % 2:
        raise HTTPException(status_code=400, detail="Ungültige Audioaufnahme")
    ergebnis = await transkribieren(
        client=request.app.state.ai_http_client,
        user_id=user.id,
        provider_id=provider_id,
        pcm=pcm,
        resolve_api_key=ai_provider_service.resolve_api_key,
    )
    if ergebnis.grund == "kontingent":
        raise HTTPException(status_code=429, detail="KI-Kontingent ist ausgeschöpft")
    if ergebnis.abschrift is None:
        raise HTTPException(status_code=502, detail="Sprache konnte nicht erkannt werden")
    return {"text": ergebnis.abschrift.wortlaut}


@router.websocket("/ws")
async def voice_ws(websocket: WebSocket, provider_id: int | None = None) -> None:
    """Die Sprachsitzung.

    Reihenfolge der Abweisungen: Origin, dann Anmeldung, dann Recht, dann
    Einrichtung. Sie ist von aussen nach innen sortiert — je weniger jemand
    nachweisen konnte, desto früher fliegt er raus, und desto weniger erfährt er
    über den Zustand des Panels.
    """
    if not _ws_origin_erlaubt(websocket):
        await _ws_ablehnen(websocket, grund="origin")
        return

    db = SessionLocal()
    try:
        try:
            user = get_current_user_for_ws(websocket, db)
        except HTTPException:
            await _ws_ablehnen(websocket, grund="auth")
            return

        if not has_global_permission(db, user, "ai.voice.use"):
            await _ws_ablehnen(websocket, grund="permission")
            return

        realtime = ai_provider_service.realtime_zugang(db)
        if realtime is not None:
            herkunft = ws_session_herkunft(websocket)
            familie = ws_session_familie(websocket)
            try:
                realtime_daten = await run_in_threadpool(
                    realtime_vorbereiten,
                    db,
                    provider=realtime,
                    user=user,
                    herkunft=herkunft,
                )
            except Exception as exc:
                # Kein Anbieterfehler, Schlüssel oder Quotendetail verlässt
                # den noch nicht aufgebauten authentifizierten Kanal.
                db.rollback()
                logger.warning(
                    "Realtime-Sprachvorbereitung fehlgeschlagen: error=%s",
                    type(exc).__name__,
                )
                await _ws_ablehnen(websocket, grund="realtime_setup")
                return
            benutzer_id = user.id
        else:
            realtime_daten = None

        # Pipecat ist ausschließlich der Legacy-Voice-Rand. Realtime darf weder
        # von seiner Installation noch von ElevenLabs oder STT abhängen.
        if realtime is None and not pipecat_verfuegbar():
            await _ws_ablehnen(websocket, grund="legacy_runtime")
            return

        # Ab hier gilt nur noch der Legacy-Vertrag. Fehlt dort ein Zugang,
        # bleibt das Panel erreichbar, aber der Socket wird abgewiesen.
        zugaenge = None if realtime else sprachzugang(db, user, bevorzugter_provider_id=provider_id)
        if realtime is None and zugaenge is None:
            # Nicht eingerichtet. Für den Benutzer ist das dasselbe wie „gibt es
            # nicht" — er hat den Knopf nur deshalb gesehen, weil der Betreiber
            # zwischen Seitenaufruf und Klick etwas entfernt hat.
            await _ws_ablehnen(websocket, grund="legacy_configuration")
            return
        if realtime is None:
            assert zugaenge is not None
            hoeren, denken, sprechen = zugaenge

        # Der Schlüssel wird **vor** dem Upgrade geholt, und zwar im
        # Threadpool: `DisClient.decrypt` ist ein synchroner HTTP-Aufruf mit
        # 15 Sekunden Frist, und auf der Ereignisschleife stünde in dieser Zeit
        # der ganze Panelprozess.
        #
        # Nur der für die Stimme: den des Chatzugangs holt die Brücke sich je
        # Zug selbst, weil ein Lauf ohnehin eine eigene Datenbanksitzung
        # aufmacht und ein über Minuten gehaltener Schlüssel nichts gewinnt.
            stimm_schluessel = await run_in_threadpool(
                ai_provider_service.resolve_api_key, db, sprechen, user.id
            )
            if not stimm_schluessel:
                await _ws_ablehnen(websocket, grund="tts_key")
                return

            gespraech = await run_in_threadpool(_gespraech_holen, db, user)
            stimm_kind = sprechen.provider_kind
            stimm_adresse = ai_tts.stimmweg(stimm_kind).verbindungsadresse(
                ai_provider_service.base_url(sprechen),
                sprechen.default_voice or "",
                sprechen.default_model,
            )
            benutzer_id = user.id
            hoeren_id = hoeren.id
            denken_id = denken.id
        # Aus demselben Token wie die Identitaet — eine gekoppelte App traegt
        # `geraet="desktop"` im Anspruch, und ihre Sprachlaeufe bekommen damit
        # dieselbe Werkzeugmenge wie ihr getippter Chat.
            herkunft = ws_session_herkunft(websocket)
        # Und **welcher** Rechner spricht. Der Sprachmodus ist der Hauptweg der
        # App — „schau auf meinen Bildschirm" kommt überwiegend gesprochen an,
        # nicht getippt. Ohne diesen Wert trug jeder Sprachlauf `familie=None`,
        # und sein Desktop-Auftrag war wieder für jedes gekoppelte Gerät
        # abholbar (`desktop_job_service.naechster`).
            familie = ws_session_familie(websocket)
    finally:
        # Die Sitzung der Anfrage gehört dem Request-Thread. Ab hier läuft eine
        # Verbindung über Minuten; sie darf keine offene Datenbanksitzung
        # mitschleppen.
        db.close()

    # Hat der Client sein Token als Subprotokoll angeboten, muss die Antwort
    # es spiegeln — ein Browser-WebSocket bricht sonst ab, obwohl der Server
    # laengst angenommen hat. Cookie-Clients bekommen das unveraenderte `None`.
    await websocket.accept(subprotocol=ws_subprotokoll(websocket))
    if realtime_daten is not None:
        sitzung = RealtimeSitzung(
            websocket,
            vorbereitung=realtime_daten,
            user_id=benutzer_id,
            http_client=websocket.app.state.ai_http_client,
            herkunft=herkunft,
            familie=familie,
        )
        try:
            await sitzung.fuehren()
        finally:
            from starlette.websockets import WebSocketState

            if websocket.client_state is WebSocketState.CONNECTED:
                await websocket.close()
        return

    bruecke = ai_voice_bridge.Sprachbruecke(
        websocket,
        user_id=benutzer_id,
        conversation_id=gespraech,
        chat_provider_id=denken_id,
        stt_provider_id=hoeren_id,
        stimm_kind=stimm_kind,
        stimm_adresse=stimm_adresse,
        stimm_schluessel=stimm_schluessel,
        http_client=websocket.app.state.ai_http_client,
        herkunft=herkunft,
        familie=familie,
    )
    try:
        lage = await bruecke.fuehren()
        logger.info(
            "Sprachsitzung beendet user=%s hin=%s zurueck=%s aeusserungen=%s laeufe=%s",
            benutzer_id, lage.rahmen_hin, lage.rahmen_zurueck,
            lage.aeusserungen, lage.laeufe,
        )
    except Exception as fehler:
        # Der Wortlaut bleibt im Protokoll. Nach aussen gibt es einen
        # Verbindungsabbruch und keine Auskunft über den Anbieter.
        logger.warning(
            "Sprachsitzung abgebrochen user=%s error=%s",
            benutzer_id, type(fehler).__name__,
        )
    finally:
        from starlette.websockets import WebSocketState

        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()


def _gespraech_holen(db: Session, user: User) -> str:
    """Die Unterhaltung, in die gesprochen wird — dieselbe wie beim Tippen.

    Nur die Kennung, nicht das Objekt: die Sitzung der Anfrage wird gleich
    geschlossen, und ein danach gehaltenes ORM-Objekt wäre abgelaufen. Die
    Brücke öffnet für jeden Zug ihre eigene, kurzlebige.
    """
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()
    return conversation.id
