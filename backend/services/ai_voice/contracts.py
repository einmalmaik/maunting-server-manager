"""Stabile, sichere Projektion zwischen Run-Broker und Voice-WebSocket."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from config import settings


ZUSTAND_BEREIT = "bereit"
ZUSTAND_HOERT = "hoert"
ZUSTAND_DENKT = "denkt"
ZUSTAND_SPRICHT = "spricht"

MAX_SITZUNGSSEKUNDEN = settings.access_token_expire_minutes * 60
MAX_TONRAHMEN_BYTES = 128 * 1024
MAX_STEUERRAHMEN_ZEICHEN = 4_096
LAUF_TIMEOUT = 300.0
ZUSTELL_TAKT_S = 3.0


@dataclass
class Lage:
    """Nicht-inhaltliche Sitzungsmetriken für das Router-Protokoll."""

    rahmen_hin: int = 0
    rahmen_zurueck: int = 0
    aeusserungen: int = 0
    laeufe: int = 0
    abgelaufen: bool = False

BrokerEreignis = Literal[
    "message", "delta", "reasoning", "tool_start", "tool_plan", "tool",
    "question", "proposal", "action", "run", "done", "error",
]


class ToolAufruf(TypedDict):
    """Bereits serverseitig bereinigte Anzeige eines geplanten Tool-Aufrufs."""

    call_id: NotRequired[str]
    tool_name: NotRequired[str]
    name: NotRequired[str]
    server_id: NotRequired[int | None]
    geo_analysis: NotRequired[dict]
    web_results: NotRequired[list[dict]]
    failed: NotRequired[bool]
    gruppe: NotRequired[str]
    skill_key: NotRequired[str]
    skill_name: NotRequired[str]
    skill_status: NotRequired[str]
    skill_learned: NotRequired[bool]


class VoiceToolFrame(TypedDict):
    """Additiver Voice-WebSocket-Frame für sichere Tool-Anzeigeinformationen."""

    art: Literal["werkzeug", "werkzeug_gestartet"]
    name: NotRequired[str]
    tool_name: NotRequired[str]
    spekulativ: NotRequired[bool]
    aufrufe: NotRequired[list[ToolAufruf]]
    geo_analysis: NotRequired[dict]
    web_results: NotRequired[list[dict]]
    failed: NotRequired[bool]
    gruppe: NotRequired[str]
    skill_key: NotRequired[str]
    skill_name: NotRequired[str]
    skill_status: NotRequired[str]
    skill_learned: NotRequired[bool]


_TOOL_FIELDS = (
    "geo_analysis", "web_results", "failed", "gruppe", "skill_key",
    "skill_name", "skill_status", "skill_learned",
)


def werkzeug_name(daten: dict) -> str:
    """Liest den kompatiblen Anzeigenamen, ohne Modellargumente zu übernehmen."""

    name = daten.get("name") or daten.get("tool_name")
    if isinstance(name, str) and name:
        return name
    aufrufe = daten.get("aufrufe")
    if isinstance(aufrufe, list):
        for aufruf in aufrufe:
            if isinstance(aufruf, dict):
                name = aufruf.get("tool_name") or aufruf.get("name")
                if isinstance(name, str) and name:
                    return name
    return ""


def voice_tool_frame(ereignis: str, daten: dict) -> VoiceToolFrame | None:
    """Projiziert ausschließlich den Broker-Anzeigevertrag auf Voice.

    Der Broker liefert hier bereits keine Tool-Argumente oder Rohresultate. Die
    explizite Whitelist verhindert, dass spätere interne Felder versehentlich
    über den WebSocket zu einem Browser gelangen.
    """

    if ereignis not in {"tool_start", "werkzeug_gestartet", "tool_plan", "tool"}:
        return None

    art: Literal["werkzeug", "werkzeug_gestartet"] = (
        "werkzeug_gestartet" if ereignis in {"tool_start", "werkzeug_gestartet"} else "werkzeug"
    )
    frame: VoiceToolFrame = {"art": art}
    name = werkzeug_name(daten)
    if name:
        # Beide Namen bleiben für ältere Desktop-Clients und den bestehenden Hook.
        frame["name"] = name
        frame["tool_name"] = name
    if art == "werkzeug_gestartet":
        frame["spekulativ"] = True

    aufrufe = daten.get("aufrufe")
    if isinstance(aufrufe, list):
        sichere_aufrufe: list[ToolAufruf] = []
        for aufruf in aufrufe:
            if not isinstance(aufruf, dict):
                continue
            sicher: ToolAufruf = {}
            for feld in ("call_id", "tool_name", "name", "server_id", *_TOOL_FIELDS):
                if feld in aufruf:
                    sicher[feld] = aufruf[feld]
            sichere_aufrufe.append(sicher)
        frame["aufrufe"] = sichere_aufrufe

    for feld in _TOOL_FIELDS:
        if feld in daten:
            frame[feld] = daten[feld]
    # Bei tool_plan liegen Zusatzdaten gegebenenfalls nur am einzelnen Aufruf.
    if frame.get("aufrufe"):
        erster = frame["aufrufe"][0]
        for feld in _TOOL_FIELDS:
            if feld not in frame and feld in erster:
                frame[feld] = erster[feld]
    return frame
