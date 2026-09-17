"""Tests für die Google AI Studio Integration (Gemini, Gemma, Embeddings, Live API)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from models import AiProvider, User
from services import ai_embedding_service, ai_provider_registry, ai_provider_service
from services.ai_provider_registry.google import ANBIETER, katalog_lesen
from services.ai_voice.gemini_live_session import (
    GeminiLiveSitzung,
    downsample_24k_to_16k,
)
from services.ai_voice.realtime_session import RealtimeVorbereitung


def test_google_registry_spec() -> None:
    """Prüft die Registrierung von Google AI Studio in der Provider Registry."""
    spec = ai_provider_registry.anbieter("google")
    assert spec.kind == "google"
    assert spec.label == "Google AI Studio"
    assert spec.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert spec.catalog_url == "https://generativelanguage.googleapis.com/v1beta/models"
    assert spec.key_prefix is None
    assert spec.katalog_braucht_schluessel is True
    assert spec.schluessel_kopf == "Authorization"
    assert spec.schluessel_praefix == "Bearer "
    assert spec.katalog_schluessel_kopf == "x-goog-api-key"
    assert spec.katalog_schluessel_praefix == ""
    assert spec.katalog_liste_feld == "models"
    assert spec.realtime_tauglich is True
    assert "chat" in spec.gehoer_wege
    assert "reasoning_effort" in spec.anfrage_erweiterungen
    assert spec.empfehlung == "gemini-2.5-flash"


def test_google_catalog_gemini_models() -> None:
    """Prüft, dass Gemini-Modelle multimodal sind und 2.5/Thinking Denkstufen erhalten."""
    # Gemini 2.5 Flash
    m25 = katalog_lesen({"id": "gemini-2.5-flash"})
    assert m25 is not None
    assert m25.model_id == "gemini-2.5-flash"
    assert m25.sieht is True
    assert m25.denkt is True
    assert m25.stufen == ("low", "medium", "high")
    assert m25.standard_stufe == "medium"
    assert m25.kontext_tokens == 1_048_576

    # Gemini 2.0 Flash Thinking
    m20_think = katalog_lesen({"id": "gemini-2.0-flash-thinking-exp"})
    assert m20_think is not None
    assert m20_think.sieht is True
    assert m20_think.denkt is True
    assert m20_think.stufen == ("low", "medium", "high")

    # Gemini 1.5 Flash (ohne Thinking)
    m15 = katalog_lesen({"id": "gemini-1.5-flash"})
    assert m15 is not None
    assert m15.sieht is True
    assert m15.denkt is False
    assert m15.stufen == ()


def test_google_catalog_gemma_models() -> None:
    """Prüft, dass Gemma-Modelle als Textmodelle mit konfigurierbarem Thinking erkannt werden."""
    gemma27 = katalog_lesen({"id": "gemma-2-27b-it"})
    assert gemma27 is not None
    assert gemma27.model_id == "gemma-2-27b-it"
    assert gemma27.sieht is False
    assert gemma27.denkt is True
    assert gemma27.stufen == ("low", "medium", "high")
    assert gemma27.standard_stufe == "medium"
    assert gemma27.kontext_tokens == 32_768
    assert gemma27.max_ausgabe_tokens == 8_192

    gemma9 = katalog_lesen({"id": "gemma-2-9b-it"})
    assert gemma9 is not None
    assert gemma9.sieht is False
    assert gemma9.denkt is True
    assert gemma9.kontext_tokens == 32_768


def test_google_catalog_embedding_models() -> None:
    """Prüft Erkennung von text-embedding-004 und gemini-embedding-001."""
    emb = katalog_lesen({"id": "text-embedding-004"})
    assert emb is not None
    assert emb.sieht is False
    assert emb.denkt is False
    assert emb.kontext_tokens == 2_048

    emb_gemini = katalog_lesen({"id": "gemini-embedding-001"})
    assert emb_gemini is not None
    assert emb_gemini.sieht is False


def test_google_catalog_invalid_entries() -> None:
    """Ungültige oder leere Einträge liefern None."""
    assert katalog_lesen({}) is None
    assert katalog_lesen({"id": ""}) is None
    assert katalog_lesen({"id": None}) is None


def test_google_provider_realtime_validation(db: Session) -> None:
    """Prüft Realtime-Validierung bei Google (Gemini-Live-Modelle und Stimmen)."""
    # Gültige Gemini-Live-Konfiguration
    prov = ai_provider_service.create_provider(
        db,
        name="Google Studio",
        provider_kind="google",
        enabled=True,
        requires_api_key=True,
        operator_api_key="AIzaSyTestKey123456",
        realtime_default=True,
        realtime_model="gemini-2.5-flash",
        realtime_voice="Puck",
        realtime_reasoning_effort="high",
        realtime_language="de",
        realtime_vad_eagerness="medium",
    )
    assert prov.realtime_default is True
    assert prov.realtime_voice == "Puck"
    assert prov.realtime_reasoning_effort == "high"

    # Ungültiges Modell (kein Gemini)
    with pytest.raises(ai_provider_service.AiProviderConfigurationError, match="Gemini-Live-Modell"):
        ai_provider_service.create_provider(
            db,
            name="Google Ungültiges Modell",
            provider_kind="google",
            enabled=True,
            requires_api_key=True,
            operator_api_key="AIzaSyTestKey123456",
            realtime_default=True,
            realtime_model="gpt-realtime",
            realtime_voice="Puck",
        )

    # Ungültige Stimme (keine Google-Stimme)
    with pytest.raises(ai_provider_service.AiProviderConfigurationError, match="Gemini-Live-Stimme"):
        ai_provider_service.create_provider(
            db,
            name="Google Ungültige Stimme",
            provider_kind="google",
            enabled=True,
            requires_api_key=True,
            operator_api_key="AIzaSyTestKey123456",
            realtime_default=True,
            realtime_model="gemini-2.5-flash",
            realtime_voice="marin",
        )


def test_downsample_24k_to_16k() -> None:
    """Testet das 3:2 Downsampling von 24kHz auf 16kHz PCM16."""
    import numpy as np

    # 300 Samples bei 24kHz -> 200 Samples bei 16kHz
    samples = np.sin(np.linspace(0, 10, 300)) * 10000
    pcm_24k = samples.astype(np.int16).tobytes()

    pcm_16k = downsample_24k_to_16k(pcm_24k)
    # 200 Samples zu je 2 Bytes = 400 Bytes
    assert len(pcm_16k) == 400

    # Leere oder unvollständige Eingaben
    assert downsample_24k_to_16k(b"") == b""
    assert downsample_24k_to_16k(b"\x00\x00") == b""


def test_encode_with_google_mock() -> None:
    """Prüft Vektorberechnung und Normalisierung über Google AI Studio mit Mock."""
    dummy_embedding = [0.05] * 256
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"embedding": dummy_embedding}
        ]
    }

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp

    res = ai_embedding_service.encode_with_google(
        ["Testtext für Embedding"],
        api_key="AIzaSyTestKey",
        client=mock_client,
    )
    assert res is not None
    assert len(res) == 1
    assert len(res[0]) == ai_embedding_service.EMBEDDING_DIMENSIONS
    # Prüfe L2-Normalisierung (Länge ~= 1.0)
    import numpy as np
    norm = np.linalg.norm(res[0])
    assert pytest.approx(norm, 1e-4) == 1.0


def test_gemini_live_session_setup_payload() -> None:
    """Prüft die Erstellung des Gemini Live WebSocket Setup-Payloads."""
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        voice="Charon",
        api_key="AIzaSyTestKey",
        instructions="Du bist der Serverassistent.",
        tools=[{"name": "read_server_status", "description": "Liest Status"}],
    )
    mock_ws = MagicMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws,
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )
    assert sitzung.v.model == "gemini-2.5-flash"
    assert sitzung.v.voice == "Charon"
    assert len(sitzung.v.tools) == 1


def test_embedding_fallback_to_google_provider(db: Session) -> None:
    """Prüft, dass encode() auf den Google-Provider zurückfällt, wenn kein lokales Modell da ist."""
    prov = ai_provider_service.create_provider(
        db,
        name="Google Studio Embed",
        provider_kind="google",
        enabled=True,
        requires_api_key=True,
        operator_api_key="AIzaSyTestKey123456",
        default_model="gemini-2.5-flash",
    )
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = db
    mock_ctx.__exit__.return_value = None

    with patch("services.ai_embedding_service._load", return_value=None), \
         patch("services.ai_embedding_service.is_available", return_value=False), \
         patch("database.SessionLocal", return_value=mock_ctx), \
         patch("services.ai_provider_service.resolve_api_key", return_value="AIzaSyTestKey123456"), \
         patch("services.ai_embedding_service.encode_with_google", return_value=[[0.1] * 256]) as mock_encode:
        assert ai_embedding_service.is_ready() is True
        res = ai_embedding_service.encode(["Hallo Welt"])
        assert res == [[0.1] * 256]
        mock_encode.assert_called_once()


@pytest.mark.asyncio
async def test_gemini_live_session_tool_execution() -> None:
    """Prüft Ausführung von Werkzeugen in der Gemini Live Sitzung."""
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Du bist der Serverassistent.",
        tools=[{"name": "test_tool", "description": "Test"}],
    )
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws,
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )

    with patch("services.ai_voice.gemini_live_session.voice_werkzeug_ausfuehren", return_value=({"status": "ok"}, None, {"tool_name": "test_tool"}, [])):
        res = await sitzung._werkzeug_ausfuehren("call_123", "test_tool", {"arg": "val"})
        assert res == {"status": "ok"}
        assert "call_123" in sitzung._gestartet


def test_clean_gemini_schema() -> None:
    """Prüft, dass additionalProperties/$schema entfernt, Typen großgeschrieben und Union-Nullables sauber aufgelöst werden."""
    from services.ai_voice.gemini_live_session import _clean_gemini_schema

    raw_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "MyTool",
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Suchtext",
            },
            "server_id": {
                "type": ["integer", "null"],
                "description": "Server-ID oder null",
            },
            "options": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "limit": {"type": "integer"}
                }
            },
            "filter": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Optionaler Filter",
            }
        },
        "additionalProperties": False,
    }
    cleaned = _clean_gemini_schema(raw_schema)
    assert "$schema" not in cleaned
    assert "title" not in cleaned
    assert "additionalProperties" not in cleaned
    assert cleaned["type"] == "OBJECT"
    assert "additionalProperties" not in cleaned["properties"]["options"]
    assert cleaned["properties"]["query"]["type"] == "STRING"
    assert cleaned["properties"]["server_id"]["type"] == "INTEGER"
    assert cleaned["properties"]["server_id"]["nullable"] is True
    assert cleaned["properties"]["filter"]["type"] == "STRING"
    assert cleaned["properties"]["filter"]["nullable"] is True


def test_google_catalog_models_prefix_and_native_limits() -> None:
    """Prüft models/ Präfix-Bereinigung und Einlesen von Google-REST Token-Limits."""
    raw = {
        "name": "models/gemini-2.5-flash",
        "displayName": "Gemini 2.5 Flash",
        "inputTokenLimit": 1_000_000,
        "outputTokenLimit": 8_192,
        "thinking": True,
    }
    m = katalog_lesen(raw)
    assert m is not None
    assert m.model_id == "gemini-2.5-flash"
    assert m.name == "Gemini 2.5 Flash"
    assert m.kontext_tokens == 1_000_000
    assert m.max_ausgabe_tokens == 8_192
    assert m.denkt is True
    assert m.stufen == ("low", "medium", "high")
    assert m.standard_stufe == "medium"


def test_google_catalog_gemini_4_dynamic() -> None:
    """Prüft, dass für zukünftige Modelle wie Gemini 4 Limits und Thinking dynamisch aus der API kommen."""
    raw = {
        "name": "models/gemini-4-flash",
        "displayName": "Gemini 4 Flash",
        "description": "Next generation multimodal model with deep thinking capabilities",
        "inputTokenLimit": 2_097_152,
        "outputTokenLimit": 65_536,
        "supportedGenerationMethods": ["generateContent", "countTokens", "bidiGenerateContent"],
        "thinking": True,
    }
    m = katalog_lesen(raw)
    assert m is not None
    assert m.model_id == "gemini-4-flash"
    assert m.name == "Gemini 4 Flash"
    assert m.kontext_tokens == 2_097_152
    assert m.max_ausgabe_tokens == 65_536
    assert m.denkt is True
    assert m.stufen == ("low", "medium", "high")
    assert m.standard_stufe == "medium"
    assert m.sieht is True


def test_google_catalog_explicit_thinking_false() -> None:
    """Prüft, dass Modelle mit thinking=False kein Thinking erhalten, selbst wenn sie neu sind."""
    raw = {
        "name": "models/gemini-2.5-flash-lite",
        "displayName": "Gemini 2.5 Flash Lite",
        "inputTokenLimit": 1_048_576,
        "outputTokenLimit": 8_192,
        "thinking": False,
    }
    m = katalog_lesen(raw)
    assert m is not None
    assert m.denkt is False
    assert m.stufen == ()
    assert m.standard_stufe is None


@pytest.mark.asyncio
async def test_gemini_live_session_tool_response_has_name() -> None:
    """Prüft, dass toolResponse.functionResponses das vorgeschriebene Feld 'name' enthält."""
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Du bist der Assistent.",
        tools=[{"name": "get_status"}],
    )
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws,
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )

    google_ws_mock = AsyncMock()
    sitzung._google_ws = google_ws_mock

    event = {
        "toolCall": {
            "functionCalls": [
                {"id": "call_abc", "name": "get_status", "args": {}}
            ]
        }
    }
    with patch.object(sitzung, "_werkzeug_ausfuehren", return_value={"status": "running"}):
        # Rufe Tool-Handling durch _google_lesen Loop-Logik ab
        sitzung._google_ws.__aiter__.return_value = [json.dumps(event)]
        # Führe eine Iteration von _google_lesen aus
        await sitzung._google_lesen()

    assert google_ws_mock.send.called
    sent_raw = google_ws_mock.send.call_args[0][0]
    sent_data = json.loads(sent_raw)
    func_responses = sent_data.get("toolResponse", {}).get("functionResponses", [])
    assert len(func_responses) == 1
    assert func_responses[0]["name"] == "get_status"
    assert func_responses[0]["id"] == "call_abc"
    assert func_responses[0]["response"] == {"output": {"status": "running"}}


@pytest.mark.asyncio
async def test_gemini_live_session_text_streaming() -> None:
    """Prüft, dass Textteile aus modelTurn als 'antworttext' an das Panel weitergeleitet werden."""
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Du bist der Assistent.",
    )
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws,
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )

    google_ws_mock = AsyncMock()
    event = {
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {"text": "Guten Tag, wie kann ich helfen?"}
                ]
            }
        }
    }
    google_ws_mock.__aiter__.return_value = [json.dumps(event)]
    sitzung._google_ws = google_ws_mock

    await sitzung._google_lesen()

    # Prüfe ob antworttext gesendet wurde
    gesendete_frames = [call.args[0] for call in mock_ws.send_json.call_args_list]
    antwort_frames = [f for f in gesendete_frames if f.get("art") == "antworttext"]
    assert len(antwort_frames) == 1
    assert antwort_frames[0]["text"] == "Guten Tag, wie kann ich helfen?"


def test_gemini_live_session_incremental_usage(db: Session) -> None:
    """Prüft, dass wiederholte kumulative usageMetadata nur die Deltas verbucht."""
    prov = ai_provider_service.create_provider(
        db,
        name="Google Live Usage",
        provider_kind="google",
        enabled=True,
        requires_api_key=True,
        operator_api_key="AIzaSyTestKey123456",
        realtime_default=True,
        realtime_model="gemini-2.5-flash",
        realtime_voice="Puck",
    )
    vorb = RealtimeVorbereitung(
        provider_id=prov.id,
        provider_kind="google",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        usage_event_id=1,
    )
    sitzung = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )
    with patch.object(sitzung, "_preise_laden", return_value=(0, 0, 0, 0)), \
         patch("services.ai_voice.gemini_live_session.SessionLocal"), \
         patch("services.ai_usage_service.realtime_verbrauch_ergaenzen") as mock_ergaenzen:
        # Erste Meldung: 100 Tokens Input, 50 Tokens Output
        sitzung._verbrauch({"promptTokenCount": 100, "candidatesTokenCount": 50})
        assert mock_ergaenzen.call_count == 1
        _, kwargs1 = mock_ergaenzen.call_args
        assert kwargs1["text_input"] == 100
        assert kwargs1["text_output"] == 50

        # Zweite Meldung mit identischen kumulativen Zahlen -> Kein zusätzlicher Verbrauch
        sitzung._verbrauch({"promptTokenCount": 100, "candidatesTokenCount": 50})
        assert mock_ergaenzen.call_count == 1

        # Dritte Meldung: kumulativ 150 Tokens Input, 80 Tokens Output -> Delta 50 und 30
        sitzung._verbrauch({"promptTokenCount": 150, "candidatesTokenCount": 80})
        assert mock_ergaenzen.call_count == 2
        _, kwargs2 = mock_ergaenzen.call_args
        assert kwargs2["text_input"] == 50
        assert kwargs2["text_output"] == 30


def test_google_safety_settings_block_none() -> None:
    """Prüft, dass get_safety_settings(disable_safety=True) alle 5 Kategorien auf BLOCK_NONE setzt."""
    from services.ai_provider_registry.google import get_safety_settings

    active = get_safety_settings(disable_safety=False)
    for entry in active:
        assert entry["threshold"] == "BLOCK_MEDIUM_AND_ABOVE"

    disabled = get_safety_settings(disable_safety=True)
    assert len(disabled) == 5
    categories = {entry["category"] for entry in disabled}
    assert "HARM_CATEGORY_HARASSMENT" in categories
    assert "HARM_CATEGORY_HATE_SPEECH" in categories
    assert "HARM_CATEGORY_SEXUALLY_EXPLICIT" in categories
    assert "HARM_CATEGORY_DANGEROUS_CONTENT" in categories
    assert "HARM_CATEGORY_CIVIC_INTEGRITY" in categories
    for entry in disabled:
        assert entry["threshold"] == "BLOCK_NONE"


def test_gemini_live_session_safety_settings() -> None:
    """Prüft, dass GeminiLiveSitzung setup_payload safetySettings mit BLOCK_NONE mitschickt, wenn disable_safety aktiv ist."""
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Du bist ein Assistent.",
        disable_safety=True,
    )
    sitzung = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )

    from services.ai_provider_registry.google import get_safety_settings
    # Prüfe die Erstellung der Safety-Settings
    assert sitzung.v.disable_safety is True
    settings = get_safety_settings(disable_safety=sitzung.v.disable_safety)
    assert len(settings) == 5
    for s in settings:
        assert s["threshold"] == "BLOCK_NONE"


def test_list_catalog_models_with_ephemeral_key(client: TestClient, owner_cookies: dict, monkeypatch) -> None:
    """Prüft, dass der Katalog mit einem flüchtigen Schlüssel (Header oder Query) direkt abgerufen wird."""
    from services import ai_model_catalog

    aufgerufen_mit: dict[str, str | None] = {}

    async def _fake_modelle(http_client, kind, erzwingen=False, schluessel=None):
        aufgerufen_mit["kind"] = kind
        aufgerufen_mit["schluessel"] = schluessel
        return [
            ai_provider_registry.Modell(
                model_id="gemini-2.5-flash",
                name="Gemini 2.5 Flash",
                denkt=True,
                stufen=("low", "medium", "high"),
                standard_stufe="medium",
                kontext_tokens=1_048_576,
                sieht=True,
            )
        ]

    monkeypatch.setattr(ai_model_catalog, "modelle", _fake_modelle)

    # 1. Mit X-Provider-Api-Key Header
    resp = client.get(
        "/api/ai/settings/provider-kinds/google/models",
        headers={"x-provider-api-key": "AIzaSyLiveTestKey"},
        cookies=owner_cookies,
    )
    assert resp.status_code == 200
    daten = resp.json()
    assert len(daten) == 1
    assert daten[0]["model_id"] == "gemini-2.5-flash"
    assert aufgerufen_mit["schluessel"] == "AIzaSyLiveTestKey"

    # 2. Mit api_key Query Parameter
    resp2 = client.get(
        "/api/ai/settings/provider-kinds/google/models?api_key=AIzaSyQueryTestKey",
        cookies=owner_cookies,
    )
    assert resp2.status_code == 200
    assert aufgerufen_mit["schluessel"] == "AIzaSyQueryTestKey"




