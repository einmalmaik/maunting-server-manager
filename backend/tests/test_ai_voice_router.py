"""Wer am Sprach-WebSocket abprallt, und woran.

Die Reihenfolge der Abweisungen ist von aussen nach innen sortiert: Origin,
Anmeldung, Recht, eingerichteter Zugang. Je weniger jemand nachweisen konnte,
desto frueher fliegt er raus — und desto weniger erfaehrt er ueber den Zustand
des Panels. Alle vier enden gleich, als `close(1008)`, und das ist Absicht: ein
Abgewiesener soll nicht aus der Art der Absage schliessen koennen, an welcher
Stelle er gescheitert ist.

Der Sprachweg ist ausserdem der erste Ort, an dem ein zweiter Anbieter im Spiel
ist. Deshalb steht hier auch die Gegenprobe: ein reiner OpenRouter-Betrieb hat
keinen Sprachmodus, und zwar sichtbar — `/config` sagt es, bevor die Oberflaeche
einen Knopf zeichnet.

Der zweite Teil dieser Datei prueft nicht mehr, wer abprallt, sondern **was
durchgereicht wird**. Zwei Angaben entstehen naemlich hier und nirgends sonst:
die Stimme, mit der das Modell spricht, und die Kennung der Unterhaltung, in die
das Gesprochene geschrieben wird. Beide sind unsichtbar, solange sie stimmen —
eine falsche Stimme faellt niemandem auf, der das Panel zum ersten Mal hoert,
und eine fehlende Gespraechskennung merkt man erst daran, dass der getippte Chat
von dem Gespraech nichts weiss.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from models import AiConversation, AiProvider
from routers import ai_voice
from services import ai_chat_service, ai_voice_session, ai_voice_usage


ORIGIN = {"origin": "http://localhost:3000"}


def _zugang(db, *, kind: str = "openai_realtime", key: bool = True) -> AiProvider:
    """Ein eingerichteter Anbieterzugang, so wie der Betreiber ihn anlegt."""
    from services.dis_client import DisClient

    zugang = AiProvider(
        name=f"Zugang {kind}",
        provider_kind=kind,
        default_model="gpt-realtime-2.1" if kind == "openai_realtime" else "openai/gpt-5.6-luna",
        enabled=True,
        requires_api_key=True,
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


# ── Der Auskunftsendpunkt ─────────────────────────────────────────────────


def test_without_a_realtime_access_there_is_no_voice_mode(
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


def test_with_a_realtime_access_the_config_names_the_model(
    client: TestClient, owner_cookies: dict, db
) -> None:
    _zugang(db)

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["available"] is True
    assert daten["model"] == "gpt-realtime-2.1"
    # Die Oberflaeche braucht beides: die Abtastrate fuer die Aufnahme und die
    # Hoechstdauer, damit sie ein planmaessiges Ende nicht fuer einen Absturz
    # haelt.
    assert daten["sample_rate"] == 24_000
    assert daten["max_seconds"] > 0


def test_a_realtime_access_without_a_key_does_not_count(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Ein Zugang ohne Schluessel ist kein Zugang, sondern eine halbe Eingabe."""
    _zugang(db, key=False)

    assert client.get("/api/ai/voice/config", cookies=owner_cookies).json()["available"] is False


def test_the_config_names_the_voice_the_operator_chose(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Die Oberflaeche nennt die Stimme im Info-Dialog, sie raet sie nicht.

    Der Dialog sagt dem Benutzer, was ihm hier gehoert und was dem Betreiber:
    Modell, Stimme und Schluessel sind dessen Entscheidung. Eine Stimme, die
    dort anders steht als die, die er hoert, waere schlechter als gar keine
    Angabe.
    """
    zugang = _zugang(db)
    zugang.default_voice = "ballad"
    db.commit()

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["voice"] == "ballad"


def test_without_a_chosen_voice_the_config_answers_with_the_default(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Aufgeloest wird beim Lesen — geschrieben wird dabei nichts.

    Die zweite Zusage ist die wichtigere und die leichter zu verlierende: nach
    dieser Auskunft steht in der Spalte weiterhin ``NULL``. Wuerde die Vorgabe
    hier eingetragen (oder vom Formular beim naechsten Speichern
    zurueckgeschickt), waere sie fortan eine Wahl des Betreibers — und ein
    spaeterer Wechsel der `STANDARDSTIMME` erreichte diesen Zugang nie mehr.
    """
    zugang = _zugang(db)

    daten = client.get("/api/ai/voice/config", cookies=owner_cookies).json()

    assert daten["voice"] == ai_voice_session.STANDARDSTIMME
    db.refresh(zugang)
    assert zugang.default_voice is None


def test_the_test_button_speaks_instead_of_chatting(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    """Ein Sprachzugang wird gesprochen geprueft, nicht getippt.

    Der Chattest schickt ein „ping" an `/chat/completions`. Darauf antwortet
    OpenAI bei einem Realtime-Modell woertlich *„This is not a chat model and
    thus not supported in the v1/chat/completions endpoint"* — eine
    Fehlermeldung fuer einen voellig richtig eingerichteten Zugang. Der
    Betreiber haette daraufhin an seiner Konfiguration gesucht, an der nichts
    war.
    """
    from routers import ai_providers

    zugang = _zugang(db)
    gesprochen: list[str] = []

    async def probe(adresse: str, schluessel: str) -> None:
        gesprochen.append(adresse)

    monkeypatch.setattr(ai_providers.ai_voice_session, "pruefen", probe)

    antwort = client.post(
        f"/api/ai/settings/providers/{zugang.id}/test",
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token},
    )

    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True
    # Die Adresse ist die echte Sprachadresse und keine Chat-URL.
    assert gesprochen == ["wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"]


def test_a_failed_probe_answers_in_a_code_and_not_in_the_providers_words(
    client: TestClient, owner_cookies: dict, csrf_token: str, db, monkeypatch
) -> None:
    """Der Wortlaut kann Kontingentstaende und Kontonamen tragen."""
    from routers import ai_providers

    zugang = _zugang(db)

    class Abgelehnt(Exception):
        response = type("A", (), {"status_code": 401})()

    async def probe(adresse: str, schluessel: str) -> None:
        raise Abgelehnt("Incorrect API key provided: sk-abc***. Org org-geheim")

    monkeypatch.setattr(ai_providers.ai_voice_session, "pruefen", probe)

    antwort = client.post(
        f"/api/ai/settings/providers/{zugang.id}/test",
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
    _zugang(db)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ai/voice/ws", cookies=owner_cookies) as ws:
            ws.receive_text()


def test_a_foreign_origin_is_rejected(
    client: TestClient, owner_cookies: dict, db
) -> None:
    _zugang(db)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws",
            cookies=owner_cookies,
            headers={"origin": "https://boesartig.example.com"},
        ) as ws:
            ws.receive_text()


def test_without_a_session_cookie_nobody_speaks(client: TestClient, db) -> None:
    _zugang(db)
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
    _zugang(db)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws", cookies=user_cookies, headers=ORIGIN
        ) as ws:
            ws.receive_text()


def test_without_a_configured_access_the_socket_closes(
    client: TestClient, owner_cookies: dict, db
) -> None:
    """Auch mit allen Rechten: ohne Zugang gibt es nichts zu verbinden."""
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

    `ai_voice_session` importiert `websockets` weich, weil ein harter Import im
    Modulkopf ueber den Start des *ganzen* Panels entscheidet
    (`test_startup_dependencies.py`). Fehlt die Bibliothek, sieht der Benutzer
    dasselbe wie ohne eingerichteten Zugang: nichts. Ein Knopf, der beim Klick
    abbricht, waere die schlechtere Auskunft.
    """
    _zugang(db)
    monkeypatch.setattr(ai_voice.ai_voice_session, "SPRACHE_MOEGLICH", False)

    assert ai_voice.sprachzugang(db, owner_user) is None


def test_an_exhausted_quota_closes_before_the_upgrade(
    client: TestClient, owner_cookies: dict, owner_user, db, monkeypatch
) -> None:
    """Erst das Kontingent, dann das Upgrade — nicht andersherum.

    Danach laeuft eine Verbindung ueber Minuten, und bei 32 USD je Million
    Eingabetokens ist „wir sehen dann schon" keine Haltung. Der Test faelscht
    die Kontingententscheidung, weil hier die *Reihenfolge* geprueft wird und
    nicht die Kontingentrechnung — die steht in `test_ai_voice_usage.py`.
    """
    _zugang(db)
    monkeypatch.setattr(
        ai_voice.ai_voice_usage, "oeffnen", lambda *args, **kwargs: None
    )
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ai/voice/ws", cookies=owner_cookies, headers=ORIGIN
        ) as ws:
            ws.receive_text()


# ── Die Auswahl des Zugangs ───────────────────────────────────────────────


def test_only_a_realtime_provider_is_picked(db, owner_user) -> None:
    """Der Chat-Zugang darf nie zum Sprachzugang werden.

    Er zeigt auf `https://openrouter.ai/api/v1`, und dort gibt es kein
    `/realtime` — am 2026-08-15 mit einem 404 nachgewiesen. Ein Griff daneben
    endete also nicht in einer Fehlermeldung, sondern in einer Verbindung, die
    nie zustande kommt.
    """
    chat = _zugang(db, kind="openrouter")
    sprache = _zugang(db)

    gewaehlt = ai_voice.sprachzugang(db, owner_user)

    assert gewaehlt is not None
    assert gewaehlt.id == sprache.id
    assert gewaehlt.id != chat.id


def test_a_disabled_access_is_not_picked(db, owner_user) -> None:
    zugang = _zugang(db)
    zugang.enabled = False
    db.commit()

    assert ai_voice.sprachzugang(db, owner_user) is None


# ── Was der Router an die Sitzung weitergibt ──────────────────────────────


def _sitzung_abfangen(monkeypatch) -> dict:
    """Die Sprachsitzung durch eine Attrappe ersetzen, die nur mitschreibt.

    Ohne sie liefe hier eine echte WebSocket-Verbindung zu OpenAI auf — mit
    einem Testschluessel, der keiner ist. Geprueft werden soll ausserdem nicht,
    was die Gegenstelle tut, sondern **womit der Router sie aufruft**: das ist
    die Naht, an der die Stimme des Zugangs und die Kennung der Unterhaltung
    verlorengehen koennen, ohne dass irgendetwas kaputt aussieht.

    Kontingent und Abschlussbuchung sind aus demselben Grund gefaelscht wie in
    `test_an_exhausted_quota_closes_before_the_upgrade`: die Rechnung steht in
    `test_ai_voice_usage.py` und nicht hier.
    """
    gesehen: dict = {}

    async def statt_zu_sprechen(browser, **argumente):
        gesehen.update(argumente)
        # Ein Rahmen nach unten, damit der Test die Sitzung von einer
        # Abweisung unterscheiden kann: ohne ihn saehe ein abgewiesener
        # Verbindungsversuch genauso aus wie ein durchgelaufener.
        await browser.send_text('{"art": "bereit"}')
        return ai_voice_session.Lage()

    monkeypatch.setattr(ai_voice.ai_voice_session, "fuehren", statt_zu_sprechen)
    monkeypatch.setattr(
        ai_voice.ai_voice_usage,
        "oeffnen",
        lambda *args, **kwargs: ai_voice_usage.Sitzungsverbrauch(
            request_id=uuid4(), freiraum=None, reserviert=0
        ),
    )
    monkeypatch.setattr(ai_voice.ai_voice_usage, "abschliessen", lambda *a, **k: None)
    return gesehen


def _sprechen(client: TestClient, cookies: dict) -> None:
    """Eine Sitzung aufbauen und wieder gehen — die Attrappe tut den Rest."""
    with client.websocket_connect(
        "/api/ai/voice/ws", cookies=cookies, headers=ORIGIN
    ) as ws:
        ws.receive_text()


def test_the_session_speaks_with_the_voice_of_the_access(
    client: TestClient, owner_cookies: dict, db, monkeypatch
) -> None:
    """Die hinterlegte Stimme steht in der Sitzungskonfiguration — genau sie.

    Der Weg dorthin hat drei Stationen (Spalte, `_stimme`,
    `sitzungskonfiguration`), und jede einzelne ist fuer sich geprueft. Was
    keine von ihnen sieht, ist die Naht dazwischen: ein Router, der die
    Standardstimme statt der hinterlegten weiterreicht, faellt in keinem
    Einzeltest auf — nur dem Betreiber, der sein Panel mit einer fremden Stimme
    sprechen hoert.
    """
    zugang = _zugang(db)
    zugang.default_voice = "sage"
    db.commit()
    gesehen = _sitzung_abfangen(monkeypatch)

    _sprechen(client, owner_cookies)

    assert gesehen["konfiguration"]["session"]["audio"]["output"]["voice"] == "sage"


def test_without_a_chosen_voice_the_session_gets_the_default(
    client: TestClient, owner_cookies: dict, db, monkeypatch
) -> None:
    """``NULL`` wird hier aufgeloest und nicht weitergereicht.

    Ginge ``None`` als Stimme hinaus, wiese die Gegenstelle das
    ``session.update`` ab — und das Gespraech liefe danach weiter, nur ohne
    Anweisungen und ohne Werkzeuge. Also als beliebiger Assistent und nicht als
    dieses Panel.
    """
    _zugang(db)
    gesehen = _sitzung_abfangen(monkeypatch)

    _sprechen(client, owner_cookies)

    stimme = gesehen["konfiguration"]["session"]["audio"]["output"]["voice"]
    assert stimme == ai_voice_session.STANDARDSTIMME


def test_what_is_spoken_belongs_to_the_conversation_that_is_typed(
    client: TestClient, owner_cookies: dict, owner_user, db, monkeypatch
) -> None:
    """Dieselbe Unterhaltung, nur ein anderer Eingang.

    Der Modulkopf von `ai_voice_session` verspricht das seit dem ersten Tag;
    wahr wurde es erst, als die Kennung wirklich durchgereicht wurde. Geprueft
    wird deshalb beides: dass die Sitzung eine Kennung bekommt, und dass es die
    **vorhandene** ist. Eine frisch angelegte zweite Unterhaltung waere der
    naheliegende Fehler — sie faellt nicht auf, denn gespeichert wuerde ja
    etwas; es stuende nur nirgends, wo jemand es liest.
    """
    _zugang(db)
    vorhandene = ai_chat_service.get_or_create_primary_conversation(db, owner_user)
    db.commit()
    kennung = vorhandene.id
    gesehen = _sitzung_abfangen(monkeypatch)

    _sprechen(client, owner_cookies)

    assert gesehen["gespraech_id"] == kennung
    assert db.query(AiConversation).count() == 1
