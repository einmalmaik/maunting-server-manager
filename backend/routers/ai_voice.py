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

**Der Sprachmodus braucht zwei Zugänge, nicht einen.** Das Modell, das denkt,
ist dasselbe wie im getippten Chat (OpenRouter); dazu kommt eine Stimme
(ElevenLabs). Fehlt einer von beiden, gibt es keinen Sprachmodus — und der
Knopf erscheint erst gar nicht. Bis zum 16.08.2026 stand hier ein einziger
Zugang, weil OpenAIs Realtime-API beides in einem tat: sie dachte und sprach.
Sie tat damit auch alles doppelt, was der Chat schon konnte.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from dependencies import get_current_user_for_ws, require_global
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/voice", tags=["ai-voice"])


def _ws_origin_erlaubt(websocket: WebSocket) -> bool:
    """Derselbe Origin-Check wie beim Konsolen-WebSocket.

    Importiert statt nachgebaut: zwei Allowlists wären zwei Antworten auf
    dieselbe Frage, und die zweite veraltete.
    """
    from routers.servers import _ws_origin_allowed

    return _ws_origin_allowed(websocket.headers.get("origin"))


def _hoerender_zugang(db: Session) -> AiProvider | None:
    """Der Chatzugang, über den gesprochen werden kann.

    Verlangt wird mehr als ein Chatzugang: er muss ein **Transkriptmodell**
    hinterlegt haben. Ohne das gibt es kein Gehör, und ohne Gehör keinen
    Sprachmodus — eines zu raten hiesse, dem Betreiber ein Modell in Rechnung
    zu stellen, das er nie ausgewählt hat.

    Verlangt wird ausserdem, dass der **Anbieter** überhaupt zuhören kann
    (`gehoer_wege`). Das ist keine doppelte Prüfung neben dem Transkriptmodell:
    das Modell steht am Zugang und lässt sich überall eintragen, die Hörwege
    stehen am Anbieter. Ein ausgefülltes Feld an einem Anbieter ohne Gehör wäre
    sonst ein Sprachknopf, der beim ersten Satz abbricht.

    Gibt es mehrere, gilt der mit der kleinsten Kennung — stabil und
    nachvollziehbar, statt geraten.
    """
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


def _sprechender_zugang(db: Session) -> AiProvider | None:
    """Der Zugang, der vorliest — mit hinterlegter Stimme.

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


def sprachzugang(db: Session, user: User) -> tuple[AiProvider, AiProvider] | None:
    """Gehör und Stimme, oder gar nichts.

    Keine Auswahl durch den Benutzer, anders als beim Chatmodell. Der Grund ist
    nicht Bequemlichkeit: beide Zugänge sind Betreiberentscheidungen mit
    eigenem Schlüssel und eigener Rechnung, und es gibt keinen sinnvollen Fall,
    in dem ein Kunde unter zweien wählt.

    Ein Paar und kein einzelner Zugang, weil der Sprachmodus **beides** braucht.
    Die Rückgabe ist deshalb ganz oder gar nicht: ein eingerichtetes Gehör ohne
    Stimme ergäbe einen Knopf, der zuhört und schweigt.
    """
    hoeren = _hoerender_zugang(db)
    if hoeren is None:
        return None
    sprechen = _sprechender_zugang(db)
    if sprechen is None:
        return None
    return hoeren, sprechen


@router.get("/config")
def voice_config(
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
    zugaenge = sprachzugang(db, user)
    hoeren, sprechen = zugaenge if zugaenge else (None, None)
    return {
        "available": zugaenge is not None,
        # Das denkende Modell, nicht das hörende: danach fragt, wer wissen will,
        # wer da antwortet.
        "model": hoeren.default_model if hoeren else None,
        "sample_rate": ai_voice_vad.ABTASTRATE,
        "max_seconds": ai_voice_bridge.MAX_SITZUNGSSEKUNDEN,
        # Die Stimm-Kennung. Sie steht im Info-Dialog und ist ohne
        # eingerichteten Zugang ``null`` — es gibt hier nichts aufzulösen, weil
        # es keine Standardstimme gibt und geben soll.
        "voice": sprechen.default_voice if sprechen else None,
    }


@router.websocket("/ws")
async def voice_ws(websocket: WebSocket) -> None:
    """Die Sprachsitzung.

    Reihenfolge der Abweisungen: Origin, dann Anmeldung, dann Recht, dann
    Einrichtung. Sie ist von aussen nach innen sortiert — je weniger jemand
    nachweisen konnte, desto früher fliegt er raus, und desto weniger erfährt er
    über den Zustand des Panels.
    """
    if not _ws_origin_erlaubt(websocket):
        await websocket.close(code=1008)
        return

    db = SessionLocal()
    try:
        try:
            user = get_current_user_for_ws(websocket, db)
        except HTTPException:
            await websocket.close(code=1008)
            return

        if not has_global_permission(db, user, "ai.voice.use"):
            await websocket.close(code=1008)
            return

        zugaenge = sprachzugang(db, user)
        if zugaenge is None:
            # Nicht eingerichtet. Für den Benutzer ist das dasselbe wie „gibt es
            # nicht" — er hat den Knopf nur deshalb gesehen, weil der Betreiber
            # zwischen Seitenaufruf und Klick etwas entfernt hat.
            await websocket.close(code=1008)
            return
        hoeren, sprechen = zugaenge

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
            await websocket.close(code=1008)
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
    finally:
        # Die Sitzung der Anfrage gehört dem Request-Thread. Ab hier läuft eine
        # Verbindung über Minuten; sie darf keine offene Datenbanksitzung
        # mitschleppen.
        db.close()

    await websocket.accept()
    bruecke = ai_voice_bridge.Sprachbruecke(
        websocket,
        user_id=benutzer_id,
        conversation_id=gespraech,
        chat_provider_id=hoeren_id,
        stimm_kind=stimm_kind,
        stimm_adresse=stimm_adresse,
        stimm_schluessel=stimm_schluessel,
        http_client=websocket.app.state.ai_http_client,
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
