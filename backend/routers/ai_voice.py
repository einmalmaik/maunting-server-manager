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
    ai_context_service,
    ai_provider_registry,
    ai_provider_service,
    ai_voice_session,
    ai_voice_tools,
    ai_voice_usage,
)
from services.permission_service import has_global_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai/voice", tags=["ai-voice"])

#: Die Stimme, mit der das Panel spricht. Bewusst eine Konstante und keine
#: Einstellung: es ist eine Stimme, keine Fachentscheidung, und jede
#: Einstellung, die niemand vermisst, ist eine, die gepflegt werden muss.
STIMME = "alloy"

#: Wieviele frühere Nachrichten die Sprachsitzung mitbekommt.
#:
#: Deutlich weniger als die 200 des Chats, und zwar aus einem Grund, der Geld
#: kostet: bei ``gpt-realtime-2.1`` schlägt Text mit 4 USD je Million Tokens zu
#: Buche und Ton mit 32. Ein voller Verlauf ginge einmal beim Sitzungsstart
#: hinaus und wäre danach in jeder Antwort mit im Kontext. Zwanzig Nachrichten
#: reichen, damit die KI weiss, worüber gerade gesprochen wurde.
VERLAUF_NACHRICHTEN = 20

#: Was im Sprachmodus anders ist als im getippten Chat.
#:
#: Kommt **hinter** den gewöhnlichen Systemprompt und ersetzt ihn nicht. Die
#: Regeln des Panels gelten unverändert; hier steht nur, was sich ändert, wenn
#: niemand mitliest.
#:
#: Der Prompt ist dabei nicht die Schranke — das ist er auch im Chat nicht. Die
#: Werkzeugmenge kommt aus `SPRACHE_LESEN`/`SPRACHE_HANDELN`, die
#: Bestätigungspflicht aus `create_proposal`, und ein Modell, das sich nicht
#: daran hält, prallt dort ab. Was hier steht, soll es nur nicht ohne Not in die
#: Irre laufen lassen.
SPRACH_ANWEISUNGEN = """
Du sprichst gerade. Der Mensch hört dich, er liest dich nicht.

Halte dich kurz. Zwei bis drei Sätze sind eine Antwort, eine Aufzählung mit
zwölf Punkten ist keine. Nenne Zahlen gerundet und in Worten, wo es geht — "gut
zwei Gigabyte" statt "2147483648 Bytes". Lies keine Pfade, Kennungen oder
Logzeilen vor, wenn du sie zusammenfassen kannst; wenn du eine nennen musst,
nenne genau die eine.

Du siehst denselben Verlauf wie im getippten Chat und schreibst hinein. Es ist
dieselbe Unterhaltung, nur ein anderer Eingang.

Wenn du etwas ändern sollst:

1. Leg den Vorschlag mit dem passenden `propose_`-Werkzeug an.
2. Das Ergebnis enthält ein Feld `vorlesen`. Lies es **wörtlich** vor und frag,
   ob du es tun sollst. Formuliere nicht um, kürze nicht, schmücke nicht aus —
   der Mensch stimmt dem zu, was du sagst, und es muss dasselbe sein wie das,
   was passiert.
3. Erst bei einem klaren Ja rufst du `bestaetige_vorschlag` auf. Ein Zögern,
   eine Rückfrage, ein "hm" oder ein "mach mal" mitten in einem anderen Satz
   sind kein Ja. Frag im Zweifel noch einmal.
4. Bei einem Nein tust du nichts und sagst, dass du nichts geändert hast.

Manches lässt sich per Sprache nicht bestätigen: Löschen, das Einspielen eines
Backups, Schlüssel und Rechte. Dort weist das Panel dich ab. Sag dann, dass du
es nicht per Sprache machen kannst und dass es im Panel auf der Karte
bestätigt werden muss — das ist keine Panne, sondern Absicht.

Es kann immer nur **ein** Vorschlag offen sein. Solange einer wartet, leg
keinen zweiten an.
""".strip()


def _ws_origin_erlaubt(websocket: WebSocket) -> bool:
    """Derselbe Origin-Check wie beim Konsolen-WebSocket.

    Importiert statt nachgebaut: zwei Allowlists wären zwei Antworten auf
    dieselbe Frage, und die zweite veraltete.
    """
    from routers.servers import _ws_origin_allowed

    return _ws_origin_allowed(websocket.headers.get("origin"))


def sprachzugang(db: Session, user: User) -> AiProvider | None:
    """Der Zugang, über den dieser Benutzer sprechen kann — oder keiner.

    Keine Auswahl durch den Benutzer, anders als im Chat. Der Grund ist nicht
    Bequemlichkeit: ein Sprachzugang ist eine Betreiberentscheidung mit einem
    eigenen Schlüssel und einer eigenen Rechnung, und es gibt keinen sinnvollen
    Fall, in dem ein Kunde unter zweien wählt. Gibt es mehrere, gilt der mit der
    kleinsten Kennung — stabil und nachvollziehbar, statt geraten.
    """
    if not ai_voice_session.SPRACHE_MOEGLICH:
        # Die WebSocket-Bibliothek fehlt in dieser Installation. Für den
        # Benutzer ist das dasselbe wie ein fehlender Zugang: die Funktion gibt
        # es nicht. Ein Knopf, der beim Klick abbricht, wäre die schlechtere
        # Auskunft.
        return None

    zugaenge = (
        db.query(AiProvider)
        .filter(AiProvider.enabled.is_(True))
        .order_by(AiProvider.id)
        .all()
    )
    for zugang in zugaenge:
        if not ai_provider_service.spricht(zugang, ai_provider_registry.REALTIME):
            continue
        if zugang.requires_api_key and not zugang.operator_api_key_encrypted:
            continue
        return zugang
    return None


@router.get("/config")
def voice_config(
    db: Session = Depends(get_db),
    user: User = Depends(require_global("ai.voice.use")),
) -> dict:
    """Ob der Sprachmodus für diesen Benutzer überhaupt zur Verfügung steht.

    Die Oberfläche fragt das, bevor sie einen Sprachknopf zeigt. Ohne
    eingerichteten Zugang gibt es keinen — kein ausgegrauter Knopf, kein Hinweis
    auf etwas, das der Betreiber nicht bestellt hat. Dieselbe Regel wie bei
    `web_search`, das ohne hinterlegten Schlüssel nicht einmal im
    Werkzeugkatalog steht.
    """
    zugang = sprachzugang(db, user)
    return {
        "available": zugang is not None,
        "model": zugang.default_model if zugang else None,
        "sample_rate": ai_voice_session.ABTASTRATE,
        "max_seconds": ai_voice_session.MAX_SITZUNGSSEKUNDEN,
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

        zugang = sprachzugang(db, user)
        if zugang is None:
            # Kein Sprachzugang eingerichtet. Für den Benutzer ist das dasselbe
            # wie „gibt es nicht" — er hat den Knopf nur deshalb gesehen, weil
            # der Betreiber den Zugang zwischen Seitenaufruf und Klick entfernt
            # hat.
            await websocket.close(code=1008)
            return

        # Der Schlüssel wird **vor** dem Upgrade geholt, und zwar im
        # Threadpool: `DisClient.decrypt` ist ein synchroner HTTP-Aufruf mit
        # 15 Sekunden Frist, und auf der Ereignisschleife stünde in dieser Zeit
        # der ganze Panelprozess.
        schluessel = await run_in_threadpool(
            ai_provider_service.resolve_api_key, db, zugang, user.id
        )
        if not schluessel:
            await websocket.close(code=1008)
            return

        vorbereitet = await run_in_threadpool(_vorbereiten, db, user)

        # Das Kontingent entscheidet **vor** dem Upgrade. Danach läuft eine
        # Verbindung über Minuten, und bei 32 USD je Million Eingabetokens ist
        # „wir sehen dann schon" keine Haltung.
        verbrauch = await run_in_threadpool(
            ai_voice_usage.oeffnen,
            db,
            user,
            zugang,
            geschaetzt=ai_voice_usage.schaetzung(
                vorbereitet["anweisungen"], vorbereitet["verlauf_zeichen"]
            ),
        )
        if verbrauch is None:
            await websocket.close(code=1008)
            return

        adresse = ai_voice_session.verbindungsadresse(
            ai_provider_service.base_url(zugang), zugang.default_model
        )
        konfiguration = ai_voice_session.sitzungskonfiguration(
            modell=zugang.default_model,
            anweisungen=vorbereitet["anweisungen"],
            stimme=STIMME,
            werkzeuge=vorbereitet["werkzeuge"],
        )
        bruecke = ai_voice_tools.Bruecke(user_id=user.id)
    finally:
        # Die Sitzung der Anfrage gehört dem Request-Thread. Ab hier läuft eine
        # Verbindung über Minuten; sie darf keine offene Datenbanksitzung
        # mitschleppen.
        db.close()

    await websocket.accept()
    gescheitert = False
    try:
        lage = await ai_voice_session.fuehren(
            websocket,
            adresse=adresse,
            schluessel=schluessel,
            konfiguration=konfiguration,
            verlauf=vorbereitet["verlauf"],
            werkzeuge=bruecke,
            kontingent=verbrauch,
        )
        logger.info(
            "Sprachsitzung beendet user=%s hin=%s zurueck=%s tokens=%s kontingent_aus=%s",
            user.id, lage.rahmen_hin, lage.rahmen_zurueck,
            verbrauch.verbraucht, lage.kontingent_aus,
        )
    except Exception as fehler:
        # Der Wortlaut bleibt im Protokoll. Nach aussen gibt es einen
        # Verbindungsabbruch und keine Auskunft über den Anbieter.
        gescheitert = True
        logger.warning(
            "Sprachsitzung abgebrochen user=%s error=%s", user.id, type(fehler).__name__
        )
    finally:
        # Die Reservierung wird **immer** geschlossen. Eine offene zählt
        # dauerhaft gegen das Kontingent des Benutzers, ohne je abzulaufen —
        # der Benutzer käme nach ein paar abgebrochenen Sitzungen an keine KI
        # mehr heran, und niemand fände den Grund.
        await run_in_threadpool(
            ai_voice_usage.abschliessen, verbrauch, gescheitert=gescheitert
        )

        from starlette.websockets import WebSocketState

        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()


def _vorbereiten(db: Session, user: User) -> dict:
    """Anweisungen und Verlauf — die Datenbankarbeit, gebündelt.

    Läuft am Stück im Threadpool, damit die Ereignisschleife nicht für jede
    einzelne Abfrage anhält. Und in **einer** Funktion, damit sichtbar bleibt,
    dass hier alles passiert, was eine Datenbank braucht: danach läuft die
    Sitzung ohne.
    """
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    db.commit()

    nachrichten = ai_context_service.build_provider_messages(db, conversation)

    # Der Systemprompt wird zu den Anweisungen der Sitzung, alles andere zum
    # Verlauf. Die Trennung ist die eigentliche Übersetzung zwischen den beiden
    # Protokollen: `chat/completions` kennt nur Nachrichten, `realtime` kennt
    # eine Sitzung **und** Nachrichten.
    anweisungen: list[str] = []
    verlauf: list[dict] = []
    for eintrag in nachrichten:
        rolle = str(eintrag.get("role") or "")
        inhalt = eintrag.get("content")
        if not isinstance(inhalt, str) or not inhalt.strip():
            continue
        if rolle == "system":
            anweisungen.append(inhalt)
            continue
        if rolle in ("user", "assistant"):
            verlauf.append(ai_voice_session.verlaufseintrag(rolle, inhalt))

    anweisungen.append(SPRACH_ANWEISUNGEN)

    gekuerzt = verlauf[-VERLAUF_NACHRICHTEN:]
    return {
        "anweisungen": "\n\n".join(anweisungen),
        "verlauf": gekuerzt,
        # Wie gross der Verlauf ist, den die Sitzung mitbekommt — die Grundlage
        # der Kontingentschätzung. Hier gezählt statt beim Aufrufer, weil hier
        # die Einträge noch offen liegen.
        "verlauf_zeichen": sum(
            len(teil.get("text") or "")
            for eintrag in gekuerzt
            for teil in eintrag["item"]["content"]
        ),
        # Der Katalog geht **einmal** in die Sitzungskonfiguration statt in jede
        # Runde. Im Chat macht er gemessene 94 Prozent des Prompts aus; hier
        # kostet er einmal — der einzige Posten, bei dem der Sprachweg billiger
        # ist als der getippte.
        "werkzeuge": ai_voice_tools.katalog(db, user),
    }
