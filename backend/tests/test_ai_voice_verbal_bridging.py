"""Tests für die generische Zero-Latency Voice & Parallel Tool Execution Engine.

Prüft:
1. Sofortiges spekulatives UI-Event-Dispatching (werkzeug_gestartet mit spekulativ=True).
2. Generisches Verbal Bridging (Sprechen der einleitenden Überbrückungsphrase vor/während Werkzeugausführung).
3. Nahtlose Weiterführung, wenn der Modellstrom nach dem Werkzeug die Fakten liefert.
4. Korrekte Zuordnung der Überbrückungsphrasen je Werkzeug und Gruppe.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from services import ai_run_broker, ai_tts_elevenlabs, ai_voice_bridge
from services.ai_voice_bridge import _verbal_bridge_phrase


class _Attrappe(ai_voice_bridge.Sprachbruecke):
    """Eine Brücke ohne echten WebSocket für isolierte Tests."""

    def __init__(self, user_id: int = 1) -> None:
        super().__init__(
            browser=None,  # type: ignore[arg-type]
            user_id=user_id,
            conversation_id="test-conv",
            chat_provider_id=1,
            stimm_kind="elevenlabs",
            stimm_adresse="wss://example.invalid/",
            stimm_schluessel="test-key",
            http_client=None,  # type: ignore[arg-type]
        )
        self.ereignisse: list[dict] = []

    async def _senden(self, nutzlast: dict) -> None:
        self.ereignisse.append(nutzlast)

    def zustaende(self) -> list[str]:
        return [
            ereignis["zustand"]
            for ereignis in self.ereignisse
            if ereignis.get("art") == "zustand"
        ]


class _StummeStimme:
    """Stimm-Attrappe zur Überprüfung aller an TTS übergebenen Sätze."""

    letzte: "_StummeStimme | None" = None

    def __init__(self, **_unbenutzt) -> None:
        self.saetze: list[str] = []
        _StummeStimme.letzte = self

    async def __aenter__(self) -> "_StummeStimme":
        return self

    async def __aexit__(self, *_ausnahme) -> None:
        return None

    async def sagen(self, text: str) -> None:
        self.saetze.append(text)

    async def ausklingen(self) -> None:
        return None

    async def schliessen(self) -> None:
        return None


def test_verbal_bridge_phrases_fuer_verschiedene_werkzeuge() -> None:
    """Prüft, dass für alle Werkzeuggruppen präzise und natürliche Sätze erzeugt werden."""
    # Kalender
    phrase_cal = _verbal_bridge_phrase("calendar_read")
    assert "kalender" in phrase_cal.lower()

    # E-Mail
    phrase_mail = _verbal_bridge_phrase("email_search")
    assert "e-mail" in phrase_mail.lower() or "mail" in phrase_mail.lower()

    # Geo / Satellit
    phrase_geo = _verbal_bridge_phrase("analyze_region")
    assert "analyse" in phrase_geo.lower() or "satellit" in phrase_geo.lower()

    # Server-Status & Metriken
    phrase_server = _verbal_bridge_phrase("read_server_status")
    assert "server" in phrase_server.lower()

    # Server-Logs
    phrase_logs = _verbal_bridge_phrase("read_server_logs")
    assert "log" in phrase_logs.lower()

    # Websuche
    phrase_web = _verbal_bridge_phrase("web_search")
    assert "web" in phrase_web.lower()

    # Gedächtnis
    phrase_mem = _verbal_bridge_phrase("search_memory")
    assert "gedächtnis" in phrase_mem.lower() or "gedaechtnis" in phrase_mem.lower()

    # Unbekanntes Werkzeug -> generischer Fallback
    phrase_generic = _verbal_bridge_phrase("unknown_custom_tool")
    assert len(phrase_generic) > 5


@pytest.mark.asyncio
async def test_tool_start_loest_sofort_verbal_bridging_und_spekulatives_ui_event_aus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wenn ein tool_start-Event eintrifft und die KI noch nichts gesagt hat,

    wird sofort die verbale Überbrückung an TTS geschickt und das spekulative UI-Event gesendet.
    """
    monkeypatch.setattr(ai_tts_elevenlabs, "Stimme", _StummeStimme)
    bruecke = _Attrappe()

    lauf_id = str(uuid4())
    ai_run_broker.eroeffnen(lauf_id)
    abo = ai_run_broker.abonnieren(lauf_id)

    aufgabe = asyncio.create_task(bruecke._lauf_verfolgen(abo))

    # Spekulativer Werkzeugstart trifft ein
    ai_run_broker.veroeffentlichen(
        lauf_id, "tool_start", {"tool_name": "calendar_read", "spekulativ": True}
    )
    # Nach Ausführung liefert die Folgerunde Fakten
    ai_run_broker.veroeffentlichen(
        lauf_id, "delta", {"content": "Du hast heute um 15 Uhr einen Termin."}
    )
    ai_run_broker.veroeffentlichen(
        lauf_id, "run", {"run_id": lauf_id, "status": "completed"}
    )
    ai_run_broker.beenden(lauf_id)

    await aufgabe
    ai_run_broker.abmelden(lauf_id, abo[1])

    stimme = _StummeStimme.letzte
    assert stimme is not None
    # 1. Die verbale Überbrückungsphrase wurde sofort gesprochen
    assert any("kalender" in s.lower() for s in stimme.saetze)
    # 2. Die finale Antwort wurde nahtlos gesprochen
    assert any("15 uhr" in s.lower() for s in stimme.saetze)

    # 3. Spekulatives UI-Event wurde an den WebSocket gesendet
    events = [e for e in bruecke.ereignisse if e.get("art") == "werkzeug_gestartet"]
    assert len(events) == 1
    assert events[0]["name"] == "calendar_read"
    assert events[0]["spekulativ"] is True


@pytest.mark.asyncio
async def test_wenn_ki_bereits_text_generiert_hat_wird_keine_doppelte_phrase_erzeugt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hat das Modell vor dem Werkzeugaufruf bereits Text gestreamt (z. B. 'Moment, ich gucke mal nach.'),

    wird dieser Text gesprochen und keine zusätzliche Fallback-Phrase vorangestellt.
    """
    monkeypatch.setattr(ai_tts_elevenlabs, "Stimme", _StummeStimme)
    bruecke = _Attrappe()

    lauf_id = str(uuid4())
    ai_run_broker.eroeffnen(lauf_id)
    abo = ai_run_broker.abonnieren(lauf_id)

    aufgabe = asyncio.create_task(bruecke._lauf_verfolgen(abo))

    # Modell streamt eigenen einleitenden Satz
    ai_run_broker.veroeffentlichen(
        lauf_id, "delta", {"content": "Ich schaue direkt in deine Server.\n"}
    )
    # Danach tool_start
    ai_run_broker.veroeffentlichen(
        lauf_id, "tool_start", {"tool_name": "read_server_status", "spekulativ": True}
    )
    # Nach Werkzeugabschluss
    ai_run_broker.veroeffentlichen(
        lauf_id, "delta", {"content": "Alle Systeme laufen stabil."}
    )
    ai_run_broker.veroeffentlichen(
        lauf_id, "run", {"run_id": lauf_id, "status": "completed"}
    )
    ai_run_broker.beenden(lauf_id)

    await aufgabe
    ai_run_broker.abmelden(lauf_id, abo[1])

    stimme = _StummeStimme.letzte
    assert stimme is not None
    assert any("ich schaue direkt" in s.lower() for s in stimme.saetze)
    assert any("alle systeme" in s.lower() for s in stimme.saetze)
    # Kein doppelter Standard-Fallback
    assert not any("ich frage die server-metriken" in s.lower() for s in stimme.saetze)

    # UI-Event ging trotzdem sofort raus
    assert any(e.get("art") == "werkzeug_gestartet" for e in bruecke.ereignisse)


@pytest.mark.asyncio
async def test_analyze_region_voice_bridge_events_and_geo_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stellt sicher, dass werkzeug_gestartet und werkzeug Events mit vollständiger geo_analysis Payload übertragen werden."""
    monkeypatch.setattr(ai_tts_elevenlabs, "Stimme", _StummeStimme)
    bruecke = _Attrappe()

    lauf_id = str(uuid4())
    ai_run_broker.eroeffnen(lauf_id)
    abo = ai_run_broker.abonnieren(lauf_id)

    aufgabe = asyncio.create_task(bruecke._lauf_verfolgen(abo))

    mock_geo = {
        "status": "success",
        "location": "London, UK",
        "coordinates": {"latitude": 51.5074, "longitude": -0.1278},
        "weather": {"temperature_celsius": 20},
    }

    # Spekulativer Start mit name oder tool_name und geo_analysis
    ai_run_broker.veroeffentlichen(
        lauf_id,
        "tool_start",
        {"name": "analyze_region", "spekulativ": True, "geo_analysis": mock_geo},
    )
    # Vollständiger Werkzeugaufruf mit aufrufe-Liste
    ai_run_broker.veroeffentlichen(
        lauf_id,
        "tool",
        {
            "aufrufe": [
                {"tool_name": "analyze_region", "arguments": {"location": "London"}, "geo_analysis": mock_geo}
            ]
        },
    )
    ai_run_broker.veroeffentlichen(
        lauf_id, "delta", {"content": "Hier ist die Satellitenanalyse für London."}
    )
    ai_run_broker.veroeffentlichen(
        lauf_id, "run", {"run_id": lauf_id, "status": "completed"}
    )
    ai_run_broker.beenden(lauf_id)

    await aufgabe
    ai_run_broker.abmelden(lauf_id, abo[1])

    # Prüfen, dass werkzeug_gestartet mit geo_analysis gesendet wurde
    start_events = [e for e in bruecke.ereignisse if e.get("art") == "werkzeug_gestartet"]
    assert len(start_events) == 1
    assert start_events[0]["name"] == "analyze_region"
    assert start_events[0]["geo_analysis"] == mock_geo

    # Prüfen, dass werkzeug mit geo_analysis gesendet wurde
    tool_events = [e for e in bruecke.ereignisse if e.get("art") == "werkzeug"]
    assert len(tool_events) == 1
    assert tool_events[0]["name"] == "analyze_region"
    assert tool_events[0]["geo_analysis"] == mock_geo
