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
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from models import AiProvider
from routers import ai_voice


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
