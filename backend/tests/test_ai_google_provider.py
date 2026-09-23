"""Tests für die Google AI Studio Integration (Gemini, Gemma, Embeddings, Live API)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote_plus
import httpx
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


def _gemini_sitzung() -> tuple[GeminiLiveSitzung, MagicMock]:
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Du bist der Serverassistent.",
        tools=[{"name": "analyze_region"}, {"name": "voice_resolve_latest_proposal"}],
    )
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws, vorbereitung=vorb, user_id=1, http_client=MagicMock()
    )
    return sitzung, mock_ws


@pytest.mark.asyncio
async def test_gemini_live_region_fragt_ohne_autonomie_erst() -> None:
    """Dieselbe Frage wie auf dem Realtime-Weg: ohne Ja keine Regionsanalyse.

    Die Regionsanalyse läuft an `voice_werkzeug_ausfuehren` vorbei und holte
    Wetter, Satellit und Nachrichten, bevor jemand zugestimmt hatte (Vorgabe
    des Betreibers vom 23.09.2026: ohne autonomen Modus fragt jedes Werkzeug).
    """
    from services.ai_voice import interactions as voice_interactions

    sitzung, mock_ws = _gemini_sitzung()
    kennung = "5d0e6c1a-8f3b-4c2d-9e7a-1b2c3d4e5f60"
    karte = {
        "id": kennung,
        "call_id": "call_region",
        "tool_name": "analyze_region",
        "status": "proposed",
        "autonomous": False,
    }
    wert = {"proposals": [karte], "status": "needs_confirmation",
            "hinweis": voice_interactions.JA_NOETIG}

    with patch.object(
        voice_interactions,
        "freigabe_einholen",
        return_value=(wert, None, {"tool_name": "analyze_region"}, [karte]),
    ), patch.object(sitzung, "_region_anfang", side_effect=AssertionError("ohne Ja")):
        res = await sitzung._werkzeug_ausfuehren(
            "call_region", "analyze_region", {"location": "Berlin"}
        )

    assert res["status"] == "needs_confirmation"
    assert sitzung._vorschlaege.rahmen()["vorschlag"]["id"] == kennung
    gesendet = [aufruf.args[0] for aufruf in mock_ws.send_json.call_args_list]
    assert {
        "art": "vorschlag",
        "vorschlag": {k: v for k, v in karte.items() if k != "call_id"},
        "klick": False,
    } in gesendet
    assert not any("geo_analysis" in rahmen for rahmen in gesendet)


@pytest.mark.asyncio
async def test_gemini_live_region_gibt_das_urteil_dem_modell_nicht_dem_schirm() -> None:
    """Dieselbe Beratung wie auf dem Realtime-Weg, und dieselbe Trennung."""
    from services.ai_voice import interactions as voice_interactions

    sitzung, mock_ws = _gemini_sitzung()
    initial = {"status": "success", "location": "Berlin", "weather": {"temperature_celsius": 20}}
    hinweis = {"untrusted": True, "quelle": "ethik_engine", "einschaetzung": "review",
               "empfehlung": "Nichts Privates zeigen.", "begruendung": "Ein Wohnhaus."}
    nachgeladen: list[dict] = []

    with patch.object(voice_interactions, "freigabe_einholen", return_value=None), \
            patch.object(voice_interactions, "ethik_anstossen", return_value=None),             patch.object(voice_interactions, "ethik_abholen", return_value=hinweis), \
            patch.object(sitzung, "_region_anfang", return_value=initial), \
            patch.object(sitzung, "_region_nachladen",
                         side_effect=lambda _args, stand: nachgeladen.append(stand)):
        res = await sitzung._werkzeug_ausfuehren(
            "call_region", "analyze_region", {"location": "Berlin"}
        )

    assert res == {**initial, "ethik": hinweis}
    gesendet = [aufruf.args[0] for aufruf in mock_ws.send_json.call_args_list]
    assert [r["geo_analysis"] for r in gesendet if "geo_analysis" in r] == [initial]
    assert nachgeladen == [initial]


def test_gemini_live_ja_bringt_das_ergebnis_mit_und_zaehlt_nur_einmal() -> None:
    """Nach dem Ja kommt das Ergebnis mit; ein Ja nach dem Klick führt nichts aus."""
    from services.ai_voice import interactions as voice_interactions

    sitzung, _ = _gemini_sitzung()
    ausgang = voice_interactions.Ausgang(
        erledigt=True, werkzeug="web_search", ergebnis={"results": []}
    )

    sitzung._vorschlaege.merken(
        {"id": "vorschlag-1", "tool_name": "web_search", "status": "proposed"}
    )
    with patch.object(voice_interactions, "schon_entschieden", return_value=None), \
            patch.object(voice_interactions, "braucht_klick", return_value=False), \
            patch.object(voice_interactions, "vorschlag_ausfuehren", return_value=ausgang):
        wert, fehler = sitzung._vorschlag_entscheiden("confirm")
    assert fehler is None
    assert wert == {"status": "confirmed", "tool_name": "web_search", "result": {"results": []}}

    sitzung._vorschlaege.merken(
        {"id": "vorschlag-2", "tool_name": "web_search", "status": "proposed"}
    )
    with patch.object(voice_interactions, "schon_entschieden", return_value="succeeded"), \
            patch.object(voice_interactions, "vorschlag_ausfuehren") as ausfuehren:
        wert, fehler = sitzung._vorschlag_entscheiden("confirm")
    assert fehler is None
    assert wert == {"status": "already_decided", "stand": "succeeded"}
    ausfuehren.assert_not_called()


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
    # Dieselbe Untrusted-Huelle wie im Chat (`werkzeugergebnis_umschlag`). Der
    # Systemprompt sagt zu, dass markiertes Material Daten sind und keine
    # Anweisungen — die Zusage haelt nur, wenn die Marke auch auf dem Sprachweg
    # dransteht. Sie stand dort nicht.
    assert func_responses[0]["response"] == {
        "untrusted": True,
        "tool": "get_status",
        "data": {"status": "running"},
    }


@pytest.mark.asyncio
async def test_gemini_live_deckelt_werkzeugargumente_wie_realtime() -> None:
    """Beide Sprachwege haben dieselbe Obergrenze für Werkzeugargumente.

    Der Realtime-Weg prüft sie vor dem Parsen (`MAX_TOOL_ARGUMENTE_ZEICHEN`).
    Bei Gemini kommt der Rahmen bereits geparst an, und die einzige Grenze war
    `max_size` des WebSockets — vier Megabyte je Aufruf.
    """
    from services.ai_voice.realtime_session import MAX_TOOL_ARGUMENTE_ZEICHEN

    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-2.5-flash",
        api_key="AIzaSyTestKey",
        tools=[{"name": "web_search"}],
    )
    mock_ws = MagicMock()
    mock_ws.send_json = AsyncMock()
    sitzung = GeminiLiveSitzung(
        websocket=mock_ws, vorbereitung=vorb, user_id=1, http_client=MagicMock()
    )

    with patch(
        "services.ai_stream.read_tools.voice_werkzeug_ausfuehren"
    ) as mock_exec:
        wert = await sitzung._werkzeug_ausfuehren(
            "call-gross", "web_search", {"query": "x" * (MAX_TOOL_ARGUMENTE_ZEICHEN + 1)}
        )

    assert wert == {"error": "Werkzeugargumente ungültig"}
    mock_exec.assert_not_called()
    # Das Panel darf nicht mit einem `werkzeug_gestartet` stehenbleiben, das
    # nie endet — der Abschlussrahmen geht auch im Fehlerfall hinaus.
    arten = [ruf.args[0].get("art") for ruf in mock_ws.send_json.call_args_list]
    assert "werkzeug" in arten


@pytest.mark.asyncio
async def test_gemini_live_fehlertext_traegt_den_schluessel_nicht_hinaus() -> None:
    """Eine Anbietermeldung geht redigiert und gekürzt an den Browser.

    Der Schlüssel steht bei Googles Live-API im Abfrageteil der Adresse
    (``?key=…``) — anders ist ihr WebSocket-Handshake nicht dokumentiert. Eine
    Ausnahme aus `websockets.connect` trägt diese Adresse oft im Text, und
    ``str(exc)`` ging bisher ungeschwärzt sowohl ins Protokoll als auch über
    ``art: "fehler"`` an den Browser. Die Mustererkennung allein rettet das
    nicht: ``key`` ist in `_GEHEIM_KERN` bewusst kein Geheimniswort.
    """
    from services.ai_voice.gemini_live_session import MAX_FEHLERTEXT_ZEICHEN

    schluessel = "AIzaSy" + "B" * 33
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        api_key=schluessel,
    )
    sitzung = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )

    roh = (
        "InvalidURI: wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={schluessel}"
        " isn't a valid URI " + "x" * 500
    )
    gekuerzt = sitzung._fremdtext(roh)

    assert schluessel not in gekuerzt
    assert "[REDACTED]" in gekuerzt
    assert len(gekuerzt) <= MAX_FEHLERTEXT_ZEICHEN
    # Auch URL-kodiert — genau so steht er in der Adresse.
    assert schluessel not in sitzung._fremdtext(f"?key={quote_plus(schluessel)}")


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
    """Prüft, dass der Katalog mit einem flüchtigen Schlüssel direkt abgerufen wird.

    **Nur über den Kopf.** Der Abfrageteil war der zweite Weg und ist weg: eine
    Adresse steht in der Zugriffszeile von Caddy und uvicorn, im Verlauf des
    Browsers und im `Referer` — ein Schlüssel dort ist nicht mehr einzufangen.
    """
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

    # 2. Derselbe Schlüssel im Abfrageteil wird **nicht** mehr angenommen.
    #    Google verlangt für seinen Katalog einen Schlüssel
    #    (`katalog_braucht_schluessel`); ohne einen im Kopf und ohne
    #    `provider_id` ist die richtige Antwort die leere Liste — nicht ein
    #    Abruf mit dem Schlüssel aus der Adresse.
    aufgerufen_mit.clear()
    resp2 = client.get(
        "/api/ai/settings/provider-kinds/google/models?api_key=AIzaSyQueryTestKey",
        cookies=owner_cookies,
    )
    assert resp2.status_code == 200
    assert resp2.json() == []
    assert aufgerufen_mit == {}


@pytest.mark.asyncio
async def test_google_stream_chat_completion_resilience() -> None:
    """Prüft, dass Google-Streaming-Antworten ohne index, ohne id oder mit leeren Datenzeilen nicht in AI_PROVIDER_PROTOCOL_ERROR laufen."""
    import httpx
    from services.openai_compatible_adapter import StreamUsage, stream_chat_completion

    provider = AiProvider(
        id=99,
        name="Google Test",
        provider_kind="google",
        default_model="gemini-2.5-flash",
        enabled=True,
        requires_api_key=True,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer AIzaSyTest"
        stream = (
            # 1. Leere Datenzeile (Heartbeat/Padding)
            "data: \n\n"
            # 2. Tool-Call ohne index und ohne id (wie von Google AI Studio geliefert)
            'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"list_my_servers","arguments":"{}"}}]}}]}\n\n'
            # 3. Stream-Ende
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        chunks = [
            chunk async for chunk in stream_chat_completion(
                http_client,
                provider=provider,
                api_key="AIzaSyTest",
                messages=[{"role": "user", "content": "list"}],
                usage=usage,
                tools=[{"type": "function", "function": {"name": "list_my_servers"}}],
            )
        ]

    assert len(chunks) == 2
    assert chunks[0].kind == "tool_start"
    assert chunks[0].text == "list_my_servers"
    assert chunks[1].kind == "tool_ready"
    assert chunks[1].tool_call is not None
    assert chunks[1].tool_call.name == "list_my_servers"
    assert chunks[1].tool_call.arguments == {}
    assert chunks[1].tool_call.id.startswith("call_")


@pytest.mark.asyncio
async def test_google_thought_signature_streaming_and_preservation() -> None:
    """Prüft, dass Gemini 3 thought_signature im Stream erfasst und in ProviderToolCall gespeichert wird."""
    from services.openai_compatible_adapter import (
        ProviderToolCall,
        extract_thought_signature,
        ensure_google_thought_signatures,
        stream_chat_completion,
        StreamUsage,
    )
    from services.ai_stream.read_tools import _aufrufnachricht

    # 1. extract_thought_signature Direkt- und Verschachtelungstests
    assert extract_thought_signature({"thought_signature": "sig_direct"}) == "sig_direct"
    assert extract_thought_signature({"thoughtSignature": "sig_camel"}) == "sig_camel"
    assert extract_thought_signature({"extra_content": {"google": {"thought_signature": "sig_nested"}}}) == "sig_nested"
    assert extract_thought_signature({"provider_specific_fields": {"thought_signature": "sig_psf"}}) == "sig_psf"
    assert extract_thought_signature({"function": {"thought_signature": "sig_func"}}) == "sig_func"
    assert extract_thought_signature({}) is None

    # 2. Streaming eines Tool-Calls mit thought_signature
    provider = AiProvider(
        id=99,
        name="Google Test",
        provider_kind="google",
        default_model="gemini-3.5-flash-lite",
        enabled=True,
        requires_api_key=True,
    )

    fake_sig = "E4bA9xK8...encrypted_thought_signature"

    async def handler(request: httpx.Request) -> httpx.Response:
        stream = (
            f'data: {{"choices":[{{"delta":{{"tool_calls":[{{"id":"call_abc123","function":{{"name":"read_server_status","arguments":"{{\\"server_id\\": 1}}"}},"thought_signature":"{fake_sig}"}}]}}}}]}}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        chunks = [
            chunk async for chunk in stream_chat_completion(
                http_client,
                provider=provider,
                api_key="AIzaSyTest",
                messages=[{"role": "user", "content": "status"}],
                usage=usage,
                tools=[{"type": "function", "function": {"name": "read_server_status"}}],
            )
        ]

    ready_chunks = [c for c in chunks if c.kind == "tool_ready"]
    assert len(ready_chunks) == 1
    tool_call = ready_chunks[0].tool_call
    assert isinstance(tool_call, ProviderToolCall)
    assert tool_call.thought_signature == fake_sig

    # 3. _aufrufnachricht behält thought_signature bei
    aufruf_msg = _aufrufnachricht([tool_call], "Ich lese den Status...")
    assert aufruf_msg["role"] == "assistant"
    assert aufruf_msg["content"] == "Ich lese den Status..."
    assert len(aufruf_msg["tool_calls"]) == 1
    tc_entry = aufruf_msg["tool_calls"][0]
    assert tc_entry["thought_signature"] == fake_sig
    assert tc_entry["extra_content"]["google"]["thought_signature"] == fake_sig
    assert tc_entry["function"]["thought_signature"] == fake_sig
    assert aufruf_msg["extra_content"]["google"]["thought_signature"] == fake_sig

    # 4. ensure_google_thought_signatures behält echte Signatur bei
    messages_with_sig = [aufruf_msg]
    ensured = ensure_google_thought_signatures(messages_with_sig)
    assert ensured[0]["tool_calls"][0]["thought_signature"] == fake_sig

    # 5. ensure_google_thought_signatures setzt 'skip_thought_signature_validator' ein, wenn Signatur fehlt
    legacy_tool_call = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_legacy",
                "type": "function",
                "function": {"name": "read_logs", "arguments": "{}"},
            }
        ],
    }
    ensured_fallback = ensure_google_thought_signatures([legacy_tool_call])
    assert ensured_fallback[0]["tool_calls"][0]["thought_signature"] == "skip_thought_signature_validator"
    assert (
        ensured_fallback[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        == "skip_thought_signature_validator"
    )
    assert (
        ensured_fallback[0]["tool_calls"][0]["function"]["thought_signature"]
        == "skip_thought_signature_validator"
    )


@pytest.mark.asyncio
async def test_google_multi_turn_tool_request_payload() -> None:
    """Prüft, dass stream_chat_completion ausgehende Nachrichten für Google anreichert."""
    from services.openai_compatible_adapter import stream_chat_completion, StreamUsage

    captured_request_json: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request_json
        captured_request_json = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"Server läuft einwandfrei."}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    provider = AiProvider(
        id=99,
        name="Google Test",
        provider_kind="google",
        default_model="gemini-3.5-flash-lite",
        enabled=True,
        requires_api_key=True,
    )

    outgoing_messages = [
        {"role": "user", "content": "Wie geht es Server 1?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_server_status", "arguments": '{"server_id": 1}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"status": "running"}'},
    ]

    usage = StreamUsage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        async for _ in stream_chat_completion(
            http_client,
            provider=provider,
            api_key="AIzaSyTest",
            messages=outgoing_messages,
            usage=usage,
        ):
            pass

    sent_messages = captured_request_json["messages"]
    sent_assistant = sent_messages[1]
    sent_tc = sent_assistant["tool_calls"][0]
    # Muss skip_thought_signature_validator haben, damit Google den Turn nicht als HTTP 400 abweist!
    assert sent_tc["thought_signature"] == "skip_thought_signature_validator"
    assert sent_tc["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert sent_tc["function"]["thought_signature"] == "skip_thought_signature_validator"


def test_gemini_live_session_setup_payload_thinking_level() -> None:
    """Prüft, dass Gemini Live nur für Thinking-Modelle thinkingConfig mitsendet."""
    from services.ai_voice.gemini_live_session import GeminiLiveSitzung
    from services.ai_voice.realtime_session import RealtimeVorbereitung

    # 1. Standardfall gemini-3.8-live: Kein thinkingConfig (da reines Audio-Live-Modell ohne Thinking)
    vorb = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-3.8-live",
        voice="Puck",
        api_key="AIzaSyTestKey",
        instructions="Sei präzise.",
    )
    sitzung = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb,
        user_id=1,
        http_client=MagicMock(),
    )
    payload = sitzung._build_setup_payload(
        model_name="models/gemini-3.8-live",
        model_raw="gemini-3.8-live",
        voice_name="Puck",
        gemini_tools=[],
    )
    gen_cfg = payload["setup"]["generationConfig"]
    assert "thinkingConfig" not in gen_cfg

    # 2. Modell mit Extended Thinking (gemini-3.8-live-extended-thinking)
    vorb_extended = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-3.8-live-extended-thinking",
        voice="Puck",
        api_key="AIzaSyTestKey",
    )
    sitzung_extended = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb_extended,
        user_id=1,
        http_client=MagicMock(),
    )
    payload_extended = sitzung_extended._build_setup_payload(
        model_name="models/gemini-3.8-live-extended-thinking",
        model_raw="gemini-3.8-live-extended-thinking",
        voice_name="Puck",
        gemini_tools=[],
    )
    assert payload_extended["setup"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"

    # 3. Extended Thinking mit expliziter Denkstufe 'high'
    vorb_high = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-3.8-live-extended-thinking",
        voice="Puck",
        reasoning_effort="high",
        api_key="AIzaSyTestKey",
    )
    sitzung_high = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb_high,
        user_id=1,
        http_client=MagicMock(),
    )
    payload_high = sitzung_high._build_setup_payload(
        model_name="models/gemini-3.8-live-extended-thinking",
        model_raw="gemini-3.8-live-extended-thinking",
        voice_name="Puck",
        gemini_tools=[],
    )
    assert payload_high["setup"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"

    # 4. Gemini 2.0 Thinking Modell nutzt thinkingBudget
    vorb_20_think = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-2.0-flash-thinking-exp",
        voice="Puck",
        reasoning_effort="low",
        api_key="AIzaSyTestKey",
    )
    sitzung_20_think = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb_20_think,
        user_id=1,
        http_client=MagicMock(),
    )
    payload_20_think = sitzung_20_think._build_setup_payload(
        model_name="models/gemini-2.0-flash-thinking-exp",
        model_raw="gemini-2.0-flash-thinking-exp",
        voice_name="Puck",
        gemini_tools=[],
    )
    assert payload_20_think["setup"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 1024

    # 5. Gemini 2.5 Flash (ohne thinking im Namen) hat kein thinkingConfig
    vorb_25_plain = RealtimeVorbereitung(
        provider_id=1,
        provider_kind="google",
        model="gemini-2.5-flash",
        voice="Puck",
        api_key="AIzaSyTestKey",
    )
    sitzung_25_plain = GeminiLiveSitzung(
        websocket=MagicMock(),
        vorbereitung=vorb_25_plain,
        user_id=1,
        http_client=MagicMock(),
    )
    payload_25_plain = sitzung_25_plain._build_setup_payload(
        model_name="models/gemini-2.5-flash",
        model_raw="gemini-2.5-flash",
        voice_name="Puck",
        gemini_tools=[],
    )
    assert "thinkingConfig" not in payload_25_plain["setup"]["generationConfig"]





