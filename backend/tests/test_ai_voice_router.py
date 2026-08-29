"""Wer am Sprach-WebSocket abprallt, und woran.

Die Reihenfolge der Abweisungen ist von aussen nach innen sortiert: Origin,
Anmeldung, Recht, eingerichtete Zugaenge. Je weniger jemand nachweisen konnte,
desto frueher fliegt er raus — und desto weniger erfaehrt er ueber den Zustand
des Panels. Alle vier enden gleich, als `close(1008)`, und das ist Absicht: ein
Abgewiesener soll nicht aus der Art der Absage schliessen koennen, an welcher
Stelle er gescheitert ist.

Der Sprachmodus braucht seit dem 16.08.2026 **zwei** Zugaenge und nicht einen.
Das Modell, das denkt, ist dasselbe wie im getippten Chat; dazu kommt eine
Stimme. Beide muessen vollstaendig eingerichtet sein — und „vollstaendig" heisst
hier mehr als „vorhanden": der Chatzugang braucht ein hoerendes Modell, der
Stimmzugang eine Stimm-Kennung. Fehlt eines von beiden, gibt es den Knopf nicht,
statt dass er beim Klick abbricht.

Genau das ist auch die Falle, die diese Datei stellt: ein halb eingerichteter
Sprachmodus sieht von aussen aus wie ein fertiger. Er faellt erst dem auf, der
spricht und keine Antwort hoert.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from models import AiProvider
from routers import ai_voice


ORIGIN = {"origin": "http://localhost:3000"}

#: Eine Stimm-Kennung, wie ElevenLabs sie vergibt. Zwanzig Zeichen aus
#: Buchstaben und Ziffern, gross- und kleinempfindlich.
STIMME = "21m00Tcm4TlvDq8ikWAM"


def _zugang(
    db,
    *,
    kind: str = "elevenlabs",
    key: bool = True,
    stimme: str | None = STIMME,
    hoeren: str | None = "google/gemini-2.5-flash",
) -> AiProvider:
    """Ein eingerichteter Anbieterzugang, so wie der Betreiber ihn anlegt.

    Die beiden Zusatzfelder sind je nur an einer Seite von Bedeutung und werden
    trotzdem immer gesetzt: `default_voice` am Stimmzugang, `transcription_model`
    am Chatzugang. Ein Test, der sie einzeln abwaehlen will, uebergibt ``None``.
    """
    from services.dis_client import DisClient

    stimmzugang = kind == "elevenlabs"
    zugang = AiProvider(
        name=f"Zugang {kind}",
        provider_kind=kind,
        default_model="eleven_flash_v2_5" if stimmzugang else "openai/gpt-5.6-luna",
        enabled=True,
        requires_api_key=True,
        default_voice=stimme if stimmzugang else None,
        transcription_model=None if stimmzugang else hoeren,
    )
    db.add(zugang)
    db.flush()
    if key:
        zugang.operator_api_key_encrypted = DisClient.encrypt(
            "sk-test-schluessel", aad=f"msm:ai:provider:{zugang.id}:operator-key"
        )
        zugang.operator_api_key_hint = "********ssel"
    db.commit()
    return zugang


def _beide(db) -> tuple[AiProvider, AiProvider]:
    """Ein vollstaendig eingerichteter Sprachmodus: Gehoer und Stimme."""
    return _zugang(db, kind="openrouter"), _zugang(db)


# ── Der Auskunftsendpunkt ─────────────────────────────────────────────────


def test_without_a_voice_access_there_is_no_voice_mode(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Ein Betreiber mit nur OpenRouter sieht die Funktion gar nicht.

    Kein ausgegrauter Knopf, kein Hinweis auf etwas, das er nicht bestellt hat —
    dieselbe Regel wie bei `web_search`, das ohne hinterlegten Suchschluessel
    nicht einmal im Werkzeugkatalog steht.
    """
    _zugang(db, kind="openrouter")

    antwort = client.get("/api/ai/voice/config", cookies=owner_cookies)

    assert antwort.status_code == 200
    assert antwort.json()["available"] is False
    assert antwort.json()["model"] is None


def test_without_a_chat_access_there_is_no_voice_mode_either(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Eine Stimme allein hat nichts vorzulesen.

    Die Gegenprobe zum Test darueber, und sie ist die leichter zu vergessende:
    wer ElevenLabs einrichtet, hat den Sprachmodus im Kopf und koennte meinen,
    damit sei es getan.
    """
    _zugang(db)

    assert client.get("/api/ai/voice/config", cookies=owner_cookies).json()["available"] is False


def test_with_both_accesses_the_config_names_the_thinking_model(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Genannt wird das denkende Modell, nicht das hoerende und nicht die Stimme.

    Danach fragt, wer wissen will, **wer da antwortet** — und das ist dasselbe
    Modell wie im getippten Chat. Das ist die eigentliche Aussage dieses
    Umbaus, und sie steht deshalb genau hier.
    """
    _beide(db)

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["available"] is True
    assert daten["model"] == "openai/gpt-5.6-luna"
    # Die Oberflaeche braucht beides: die Abtastrate fuer die Aufnahme und die
    # Hoechstdauer, damit sie ein planmaessiges Ende nicht fuer einen Absturz
    # haelt.
    assert daten["sample_rate"] == 24_000
    assert daten["max_seconds"] > 0


def test_an_access_without_a_key_does_not_count(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Ein Zugang ohne Schluessel ist kein Zugang, sondern eine halbe Eingabe."""
    _zugang(db, kind="openrouter")
    _zugang(db, key=False)

    assert client.get("/api/ai/voice/config", cookies=owner_cookies).json()["available"] is False


def test_a_chat_access_without_a_transcription_model_does_not_count(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Ohne hoerendes Modell gibt es kein Gehoer — und ohne Gehoer kein Gespraech.

    Ein Chatzugang ist damit **nicht** automatisch sprachfaehig, und das ist
    Absicht: es gibt bei OpenRouter keinen Transkriptions-Endpunkt, Audio geht
    als Inhaltsteil an ein hoerfaehiges Modell. Welches das sein soll, weiss nur
    der Betreiber — eines zu raten hiesse, ihm eine Rechnung fuer ein Modell zu
    stellen, das er nie ausgewaehlt hat.
    """
    _zugang(db, kind="openrouter", hoeren=None)
    _zugang(db)

    assert client.get("/api/ai/voice/config", cookies=owner_cookies).json()["available"] is False


def test_a_voice_access_without_a_voice_does_not_count(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Es gibt keine Standardstimme, und deshalb gibt es hier nichts aufzuloesen.

    Bis zum 16.08.2026 stand hier das Gegenteil: eine fehlende Stimme loeste auf
    ``alloy`` auf, weil alle acht demselben Modell gehoerten. Eine
    ElevenLabs-Kennung gehoert dem Konto des Betreibers — MSM kennt keine, und
    jede geratene stuende auf seiner Rechnung.
    """
    _zugang(db, kind="openrouter")
    _zugang(db, stimme=None)

    assert client.get("/api/ai/voice/config", cookies=owner_cookies).json()["available"] is False


def test_the_config_names_the_voice_the_operator_chose(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Die Oberflaeche nennt die Stimme im Info-Dialog, sie raet sie nicht.

    Der Dialog sagt dem Benutzer, was ihm hier gehoert und was dem Betreiber:
    Modell, Stimme und Schluessel sind dessen Entscheidung.
    """
    _beide(db)

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["voice"] == STIMME


def test_dictation_transcribes_pcm_without_sending_a_chat_message(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    provider = _zugang(db, kind="openrouter")
    seen = {}

    async def fake_transcription(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            grund=None,
            abschrift=SimpleNamespace(wortlaut="eingefügter Text"),
        )

    monkeypatch.setattr(ai_voice, "transkribieren", fake_transcription)
    response = client.post(
        f"/api/ai/voice/transcribe?provider_id={provider.id}",
        content=b"\x00\x00\x01\x00",
        headers={
            "X-CSRF-Token": csrf_token,
            "Content-Type": "application/octet-stream",
        },
        cookies=owner_cookies,
    )
    assert response.status_code == 200
    assert response.json() == {"text": "eingefügter Text"}
    assert seen["provider_id"] == provider.id
    assert seen["pcm"] == b"\x00\x00\x01\x00"


def test_dictation_rejects_non_pcm_payload_before_provider_call(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    provider = _zugang(db, kind="openrouter")
    called = False

    async def fake_transcription(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(ai_voice, "transkribieren", fake_transcription)
    response = client.post(
        f"/api/ai/voice/transcribe?provider_id={provider.id}",
        content=b"not pcm",
        headers={"X-CSRF-Token": csrf_token, "Content-Type": "text/plain"},
        cookies=owner_cookies,
    )
    assert response.status_code == 415
    assert called is False


def test_the_test_button_speaks_instead_of_chatting(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    """Ein Stimmzugang wird gesprochen geprueft, nicht getippt.

    Der Chattest schickt ein „ping" an `/chat/completions` — eine Adresse, die
    es bei ElevenLabs gar nicht gibt. Der Betreiber haette daraufhin an seiner
    Konfiguration gesucht, an der nichts war.
    """
    from services import ai_tts_elevenlabs

    _, stimme = _beide(db)
    gesprochen: list[str] = []

    async def probe(adresse: str, schluessel: str) -> None:
        gesprochen.append(adresse)

    monkeypatch.setattr(ai_tts_elevenlabs, "pruefen", probe)

    antwort = client.post(
        f"/api/ai/settings/providers/{stimme.id}/test",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["ok"] is True
    # Die Adresse ist die echte Sprachadresse, die Stimme steht im Pfad, und das
    # Tonformat ist das, was der Browser ohnehin abspielt.
    assert len(gesprochen) == 1
    assert gesprochen[0].startswith(
        f"wss://api.elevenlabs.io/v1/text-to-speech/{STIMME}/stream-input?"
    )
    assert "output_format=pcm_24000" in gesprochen[0]
    assert "model_id=eleven_flash_v2_5" in gesprochen[0]


def test_the_test_button_says_when_no_voice_is_configured(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    """Ohne Stimme wird gar nicht erst gefragt.

    Die Kennung steht im Pfad; ohne sie zeigte die Anfrage auf einen Endpunkt,
    den es nicht gibt, und der Betreiber laese „nicht erreichbar", wo „keine
    Stimme ausgewaehlt" gemeint ist. Genau die Art Fehlermeldung, die eine
    halbe Stunde Suche an der falschen Stelle kostet.
    """
    from services import ai_tts_elevenlabs

    stimme = _zugang(db, stimme=None)
    gefragt: list[str] = []

    async def probe(adresse: str, schluessel: str) -> None:
        gefragt.append(adresse)

    monkeypatch.setattr(ai_tts_elevenlabs, "pruefen", probe)

    antwort = client.post(
        f"/api/ai/settings/providers/{stimme.id}/test",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    ).json()

    assert antwort["ok"] is False
    assert antwort["code"] == "AI_PROVIDER_VOICE_MISSING"
    assert gefragt == []


def test_a_failed_probe_answers_in_a_code_and_not_in_the_providers_words(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    """Der Wortlaut kann Kontingentstaende und Kontonamen tragen."""
    from services import ai_tts_elevenlabs

    _, stimme = _beide(db)

    class Abgelehnt(Exception):
        response = type("A", (), {"status_code": 401})()

    async def probe(adresse: str, schluessel: str) -> None:
        raise Abgelehnt("Incorrect API key provided: sk_abc***. Account acct-geheim")

    monkeypatch.setattr(ai_tts_elevenlabs, "pruefen", probe)

    antwort = client.post(
        f"/api/ai/settings/providers/{stimme.id}/test",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    ).json()

    assert antwort["ok"] is False
    assert antwort["code"] == "AI_PROVIDER_AUTH_FAILED"
    assert antwort["detail"] is None


# ── Der WebSocket ─────────────────────────────────────────────────────────


def test_a_missing_origin_is_rejected(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Der Origin-Check ersetzt den CSRF-Schutz — WebSockets koennen keinen.

    Ohne ihn genuegte eine fremde Seite im selben Browser, um im Namen des
    angemeldeten Benutzers ein Gespraech mit dem Panel zu beginnen. Das Cookie
    schickt der Browser bei einem Upgrade naemlich von selbst mit.
    """
    _beide(db)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ai/voice/ws", cookies=owner_cookies) as ws:
            ws.receive_text()


def test_a_foreign_origin_is_rejected(
    client: TestClient, owner_cookies: dict, db
) -> None:
    _beide(db)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws",
            cookies=owner_cookies,
            headers={"origin": "https://boesartig.example.com"},
        ) as ws:
            ws.receive_text()


def test_without_a_session_cookie_nobody_speaks(client: TestClient, db) -> None:
    _beide(db)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ai/voice/ws", headers=ORIGIN) as ws:
            ws.receive_text()


def test_without_the_permission_nobody_speaks(
    client: TestClient, user_cookies: dict, db
) -> None:
    """`ai.voice.use` ist ein eigenes Recht und nicht `ai.chat.use`.

    Wer sprechen darf, bestaetigt Aenderungen per Stimme statt per Klick — und
    verbraucht dabei ein Vielfaches. Ein Betreiber muss das abwaehlen koennen,
    ohne den Chat mitzunehmen.
    """
    _beide(db)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws", cookies=user_cookies, headers=ORIGIN
        ) as ws:
            ws.receive_text()


def test_without_a_configured_access_the_socket_closes(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Auch mit allen Rechten: ohne Zugaenge gibt es nichts zu verbinden."""
    _zugang(db, kind="openrouter")
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws", cookies=owner_cookies, headers=ORIGIN
        ) as ws:
            ws.receive_text()


def test_without_the_websocket_library_there_is_no_voice_mode(
    db, owner_user, monkeypatch
) -> None:
    """Ein fehlendes Paket darf den Sprachmodus kosten — nie den Panelstart.

    `ai_tts_elevenlabs` importiert `websockets` weich, weil ein harter Import im
    Modulkopf ueber den Start des *ganzen* Panels entscheidet
    (`test_startup_dependencies.py`). Fehlt die Bibliothek, sieht der Benutzer
    dasselbe wie ohne eingerichteten Zugang: nichts.
    """
    from services import ai_tts_elevenlabs

    _beide(db)
    monkeypatch.setattr(ai_tts_elevenlabs, "STIMME_MOEGLICH", False)

    assert ai_voice.sprachzugang(db, owner_user) is None


# ── Die Auswahl der Zugaenge ──────────────────────────────────────────────


def test_the_two_accesses_are_told_apart(db, owner_user) -> None:
    """Gehoer und Stimme, jedes vom richtigen Anbieter.

    Vertauscht faellt es nicht sofort auf: beide Zeilen sehen gleich aus, beide
    haben einen Schluessel, beide sind aktiv. Auffallen wuerde es erst an einer
    Verbindung, die nie zustande kommt — `openrouter.ai` kennt kein
    `/text-to-speech`, und `api.elevenlabs.io` kein `/chat/completions`.
    """
    chat, stimme = _beide(db)

    gewaehlt = ai_voice.sprachzugang(db, owner_user)

    assert gewaehlt is not None
    hoeren, denken, sprechen = gewaehlt
    assert hoeren.id == chat.id
    assert denken.id == chat.id
    assert sprechen.id == stimme.id


def test_a_disabled_access_is_not_picked(db, owner_user) -> None:
    _, stimme = _beide(db)
    stimme.enabled = False
    db.commit()

    assert ai_voice.sprachzugang(db, owner_user) is None


def test_the_spoken_conversation_is_the_one_that_is_typed(
    db, owner_user
) -> None:
    """Dieselbe Unterhaltung, nur ein anderer Eingang.

    Eine frisch angelegte zweite waere der naheliegende Fehler — sie faellt
    nicht auf, denn gespeichert wuerde ja etwas; es stuende nur nirgends, wo
    jemand es liest.
    """
    from models import AiConversation
    from services import ai_chat_service

    vorhandene = ai_chat_service.get_or_create_primary_conversation(db, owner_user)
    db.commit()

    kennung = ai_voice._gespraech_holen(db, owner_user)

    assert kennung == vorhandene.id
    assert db.query(AiConversation).count() == 1


def test_der_handshake_reicht_herkunft_und_geraet_an_die_bruecke(
    client: TestClient, db, owner_user, monkeypatch
) -> None:
    """Der Produktionsweg vom Token bis zur Bruecke.

    Die Bruecke selbst ist anderswo geprueft (test_ai_voice_ws_auth), und
    genau das ist die Luecke, die dieser Test schliesst: bis zum 23.08.2026 las
    der Endpunkt die Geraetekennung gar nicht erst aus dem Handshake. Jeder
    Sprachlauf trug damit `familie=None`, und sein Auftrag an den Rechner war
    wieder fuer jedes gekoppelte Geraet abholbar — auf dem Weg, auf dem "schau
    auf meinen Bildschirm" ueberwiegend ankommt.

    Die Bruecke wird ersetzt, weil hier nur die Uebergabe zaehlt: eine echte
    Sitzung braeuchte eine Stimme am anderen Ende.
    """
    from dependencies import WS_BEARER_PROTOKOLL
    from services import ai_voice_bridge
    from services.auth_service import AuthService

    _beide(db)
    marke = AuthService.create_access_token({
        "sub": owner_user.username,
        "user_id": owner_user.id,
        "jti": "ws-familie",
        "geraet": "desktop",
        "familie": "fam-laptop",
    })

    angekommen: dict = {}

    class _StilleBruecke:
        def __init__(self, browser, **kwargs) -> None:
            angekommen.update(kwargs)

        async def fuehren(self):
            return ai_voice_bridge.Lage()

    monkeypatch.setattr(ai_voice_bridge, "Sprachbruecke", _StilleBruecke)

    with client.websocket_connect(
        "/api/ai/voice/ws",
        subprotocols=[WS_BEARER_PROTOKOLL, marke],
        headers=ORIGIN,
    ):
        pass

    assert angekommen["herkunft"] == "desktop"
    assert angekommen["familie"] == "fam-laptop"


def test_config_traegt_den_bearer_ws_marker(
    client: TestClient, owner_cookies: dict
) -> None:
    """Der Faehigkeitsmarker der Desktop-App.

    Ein gescheiterter WS-Handshake verraet dem Browser nichts; die App fragt
    dann diesen Endpunkt und unterscheidet am Marker "Panel zu alt" von "Netz
    weg". Er muss deshalb auch **ohne** eingerichtete Zugaenge da sein — die
    Frage stellt sich genau dann, wenn sonst nichts geht."""
    antwort = client.get("/api/ai/voice/config", cookies=owner_cookies)

    assert antwort.status_code == 200
    assert antwort.json()["bearer_ws"] is True


def test_die_gespeicherte_modellwahl_traegt_den_sprachmodus(
    db, owner_user
) -> None:
    """Ohne explizite Wahl gilt die im Konto gespeicherte (users.ai_provider_id).

    Das Overlay der Desktop-App schickt keine provider_id mit — es kennt die
    Providerliste nicht. Vor diesem Feld landete es damit auf dem erstbesten
    Chatzugang, der ein anderes (langsameres) Modell sein konnte als das im
    Panel gewaehlte. Eine explizit mitgeschickte Wahl sticht weiterhin."""
    from services.dis_client import DisClient

    erster = _zugang(db, kind="openrouter")
    # Von Hand statt über _zugang: der Name ist eindeutig (UNIQUE-Spalte).
    zweiter = AiProvider(
        name="Zweiter Chatzugang",
        provider_kind="openrouter",
        default_model="openai/gpt-5.6-luna",
        enabled=True,
        requires_api_key=True,
        transcription_model="google/gemini-2.5-flash",
    )
    db.add(zweiter)
    db.flush()
    zweiter.operator_api_key_encrypted = DisClient.encrypt(
        "sk-test-schluessel", aad=f"msm:ai:provider:{zweiter.id}:operator-key"
    )
    db.commit()
    _zugang(db)  # die Stimme

    # Ohne Wahl: die bisherige Reihenfolge — der erste Zugang.
    zugaenge = ai_voice.sprachzugang(db, owner_user)
    assert zugaenge is not None
    assert zugaenge[1].id == erster.id

    # Mit gespeicherter Wahl: genau dieser Zugang, fuer Hoeren und Denken.
    owner_user.ai_provider_id = zweiter.id
    db.commit()
    zugaenge = ai_voice.sprachzugang(db, owner_user)
    assert zugaenge is not None
    assert zugaenge[1].id == zweiter.id

    # Eine explizite Wahl des Clients sticht die gespeicherte.
    zugaenge = ai_voice.sprachzugang(db, owner_user, bevorzugter_provider_id=erster.id)
    assert zugaenge is not None
    assert zugaenge[1].id == erster.id
