"""Der stabile, sichere Broker-zu-Voice-Vertrag."""

from __future__ import annotations

from services import ai_voice_bridge
from services.ai_voice.contracts import voice_tool_frame
from services.ai_voice.text import Belegfilter, ist_ablehnung, ist_zustimmung
from services.ai_voice.transcription import Abschrift
from services.openai_compatible_adapter import StreamUsage


def test_tool_plan_preserves_all_safe_display_fields_and_discards_arguments() -> None:
    geo = {"location": "Berlin", "coordinates": {"latitude": 52.52, "longitude": 13.405}}
    results = [{"title": "Amtliche Lage", "url": "https://example.invalid/lage"}]

    frame = voice_tool_frame("tool_plan", {
        "aufrufe": [
            {
                "call_id": "one",
                "tool_name": "analyze_region",
                "server_id": 7,
                "geo_analysis": geo,
                "arguments": {"location": "Berlin", "token": "must-not-leak"},
            },
            {
                "call_id": "two",
                "tool_name": "web_search",
                "web_results": results,
                "failed": False,
                "gruppe": "research",
                "skill_key": "incident-triage",
                "skill_name": "Incident Triage",
                "skill_status": "active",
                "skill_learned": True,
                "ergebnis": {"secret": "must-not-leak"},
            },
        ],
    })

    assert frame is not None
    assert frame["art"] == "werkzeug"
    assert frame["name"] == "analyze_region"
    assert frame["aufrufe"] == [
        {
            "call_id": "one",
            "tool_name": "analyze_region",
            "server_id": 7,
            "geo_analysis": geo,
        },
        {
            "call_id": "two",
            "tool_name": "web_search",
            "web_results": results,
            "failed": False,
            "gruppe": "research",
            "skill_key": "incident-triage",
            "skill_name": "Incident Triage",
            "skill_status": "active",
            "skill_learned": True,
        },
    ]
    assert "arguments" not in frame["aufrufe"][0]
    assert "ergebnis" not in frame["aufrufe"][1]
    # Die Top-Level-Projektion bleibt für die bestehende Voice-Ansicht nützlich.
    assert frame["geo_analysis"] == geo


def test_tool_result_keeps_web_results_for_voice_clients() -> None:
    results = [{"title": "Status", "snippet": "alles grün"}]
    frame = voice_tool_frame("tool", {
        "tool_name": "web_search",
        "web_results": results,
        "failed": True,
        "gruppe": "research",
    })

    assert frame == {
        "art": "werkzeug",
        "name": "web_search",
        "tool_name": "web_search",
        "web_results": results,
        "failed": True,
        "gruppe": "research",
    }


def test_tool_result_keeps_only_the_sanitized_camera_command() -> None:
    command = {"action": "zoom_in", "command_id": "camera-1"}
    frame = voice_tool_frame("tool", {
        "tool_name": "control_region_camera",
        "geo_camera": command,
        "arguments": {"action": "zoom_in", "private": "must-not-leak"},
    })

    assert frame == {
        "art": "werkzeug",
        "name": "control_region_camera",
        "tool_name": "control_region_camera",
        "geo_camera": command,
    }


def test_tool_start_stays_immediate_and_compatible() -> None:
    frame = voice_tool_frame("tool_start", {"tool_name": "calendar_read", "spekulativ": False})

    assert frame == {
        "art": "werkzeug_gestartet",
        "name": "calendar_read",
        "tool_name": "calendar_read",
        "spekulativ": True,
    }


def test_legacy_voice_facade_reexports_text_contract() -> None:
    assert ai_voice_bridge.Belegfilter is Belegfilter
    assert ai_voice_bridge.ist_zustimmung is ist_zustimmung
    assert ai_voice_bridge.ist_ablehnung is ist_ablehnung


def test_transcript_result_repr_never_contains_the_transcript() -> None:
    geheim = "private-spoken-content"
    result = Abschrift(wortlaut=geheim, messwerte=StreamUsage())

    assert geheim not in repr(result)
