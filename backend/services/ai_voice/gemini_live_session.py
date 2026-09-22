"""Google Gemini Multimodal Live API (BidiGenerateContent) Sitzungsführung.

Ermöglicht bidirektionales Echtzeit-Streaming von Audio und Werkzeugaufrufen
über die Gemini Multimodal Live WebSocket-Schnittstelle.

Audiofluss:
- Browser -> MSM: 24 kHz PCM16 über WebSocket (Binary Frames)
- MSM -> Google: 16 kHz PCM16 (3:2 Downsampling, base64 audio/pcm;rate=16000)
- Google -> MSM: 24 kHz PCM16 (base64 inlineData audio/pcm;rate=24000)
- MSM -> Browser: 24 kHz PCM16 (Binary Frames, direkt abspielbar im Web AudioContext)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

import httpx
import numpy as np
try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]
from sqlalchemy.orm import Session
from starlette.websockets import WebSocket, WebSocketDisconnect

from database import SessionLocal
from models import AiProvider, User
from services import ai_action_service, ai_meldestelle, ai_provider_service, ai_usage_service
from services.ai_stream.read_tools import (
    voice_werkzeug_ausfuehren,
    werkzeugergebnis_umschlag,
)
from services.ai_voice import interactions as voice_interactions
from services.ai_voice.contracts import Lage, MAX_SITZUNGSSEKUNDEN, voice_tool_frame
from services.ai_voice.realtime_session import (
    MAX_TOOL_ARGUMENTE_ZEICHEN,
    RealtimeVorbereitung,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_voice_debug import emit as voice_debug
from services.openai_compatible_adapter import ProviderToolCall


logger = logging.getLogger(__name__)

GEMINI_LIVE_WS_BASE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
GEMINI_TOOL_TIMEOUT_SECONDS = 12.0
#: Fremdtext in einer Fehlermeldung. Bewusst knapp, aus demselben Grund wie
#: `openai_compatible_adapter.MAX_PROVIDER_DETAIL_CHARS`: die Zeile soll die
#: Ursache benennen, nicht eine fremde Antwort in unsere Oberflaeche kopieren.
MAX_FEHLERTEXT_ZEICHEN = 200


class GeminiLiveSitzungsfehler(RuntimeError):
    """Fehler während der Gemini Live Sitzung."""


def _clean_gemini_schema(schema: Any) -> Any:
    """Bereinigt JSON Schema für Google Gemini Function Declarations.

    Google Gemini verwendet Protobuf-basierte Schemata. Unterstützt werden nur:
    type, format, description, nullable, enum, properties, required, items.
    Nicht unterstützt werden: additionalProperties, $schema, $id, title, default,
    minimum, maximum, minLength, maxLength etc.
    Zudem darf `type` kein Array (z. B. ["integer", "null"]) sein, sondern muss
    ein String-Enum sein; Nullable-Typen werden zu nullable=True umgewandelt.
    """
    if not isinstance(schema, dict):
        return schema

    # Falls anyOf / oneOf vorhanden ist, z. B. [{'type': 'string'}, {'type': 'null'}]
    for choice_key in ("anyOf", "oneOf"):
        if choice_key in schema and isinstance(schema[choice_key], list):
            choices = schema[choice_key]
            is_nullable = any(
                isinstance(c, dict) and (c.get("type") == "null" or "null" in c.get("type", []))
                for c in choices
            )
            real_choices = [
                c for c in choices
                if isinstance(c, dict) and c.get("type") != "null" and c.get("type") != ["null"]
            ]
            if real_choices:
                merged = dict(real_choices[0])
                for k, v in schema.items():
                    if k not in (choice_key, "type", "nullable"):
                        merged.setdefault(k, v)
                if is_nullable:
                    merged["nullable"] = True
                schema = merged
            break

    cleaned: dict[str, Any] = {}

    # 1. Type & Nullable behandeln
    raw_type = schema.get("type")
    is_nullable = bool(schema.get("nullable", False))

    if isinstance(raw_type, list):
        types = [t for t in raw_type if isinstance(t, str) and t.lower() != "null"]
        if any(isinstance(t, str) and t.lower() == "null" for t in raw_type):
            is_nullable = True
        cleaned["type"] = types[0].upper() if types else "STRING"
    elif isinstance(raw_type, str):
        if raw_type.lower() == "null":
            cleaned["type"] = "STRING"
            is_nullable = True
        else:
            cleaned["type"] = raw_type.upper()
    elif "properties" in schema:
        cleaned["type"] = "OBJECT"

    if is_nullable:
        cleaned["nullable"] = True

    # 2. Description & Format
    if "description" in schema and isinstance(schema["description"], str):
        cleaned["description"] = schema["description"]
    if "format" in schema and isinstance(schema["format"], str):
        cleaned["format"] = schema["format"]

    # 3. Enum
    if "enum" in schema and isinstance(schema["enum"], list):
        cleaned["enum"] = [str(x) for x in schema["enum"]]

    # 4. Properties (rekursiv)
    if "properties" in schema and isinstance(schema["properties"], dict):
        cleaned_props: dict[str, Any] = {}
        for prop_name, prop_val in schema["properties"].items():
            if isinstance(prop_val, dict):
                cleaned_props[str(prop_name)] = _clean_gemini_schema(prop_val)
            else:
                cleaned_props[str(prop_name)] = prop_val
        cleaned["properties"] = cleaned_props

    # 5. Required
    if "required" in schema and isinstance(schema["required"], list):
        cleaned["required"] = [str(x) for x in schema["required"]]

    # 6. Items (rekursiv für Arrays)
    if "items" in schema and isinstance(schema["items"], dict):
        cleaned["items"] = _clean_gemini_schema(schema["items"])

    return cleaned


def downsample_24k_to_16k(pcm: bytes) -> bytes:
    """Konvertiert 24 kHz 16-Bit Mono PCM zu 16 kHz 16-Bit Mono PCM (Verhältnis 3:2)."""
    if not pcm or len(pcm) < 6:
        return b""
    if len(pcm) % 2 != 0:
        pcm = pcm[:len(pcm) - (len(pcm) % 2)]
    samples = np.frombuffer(pcm, dtype=np.int16)
    n_in = len(samples)
    n_out = int(n_in * 2 / 3)
    if n_out <= 0:
        return b""
    orig_indices = np.arange(n_in)
    new_indices = np.linspace(0, n_in - 1, n_out)
    resampled = np.interp(new_indices, orig_indices, samples).astype(np.int16)
    return resampled.tobytes()


class GeminiLiveSitzung:
    """Führt eine Sprach- und Werkzeug-Sitzung über die Gemini Multimodal Live API."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        vorbereitung: RealtimeVorbereitung,
        user_id: int,
        http_client: httpx.AsyncClient,
        herkunft: str = "web",
        familie: str | None = None,
    ) -> None:
        self.websocket = websocket
        self.v = vorbereitung
        self.user_id = user_id
        self.http = http_client
        self.herkunft = herkunft
        self.familie = familie
        self.lage = Lage()

        self._google_ws: Any = None
        self._senden_lock = asyncio.Lock()
        self._tool_tasks: set[asyncio.Task] = set()
        self._region_tasks: set[asyncio.Task] = set()
        self._offener_vorschlag: str | None = None
        self._verbrauch_tokens = [0, 0, 0, 0]  # text_in, text_out, audio_in, audio_out
        self._verbrauch_kosten = 0
        self._last_prompt_tokens = 0
        self._last_candidates_tokens = 0
        self._gestartet: set[str] = set()
        self._beendet = False
        self._setup_fertig = asyncio.Event()

    def _fremdtext(self, text: object) -> str:
        """Fremdtext zu einer Zeile, die man dem Browser zeigen kann.

        Drei Wege brachten hier rohen Text nach draussen: die Fehlermeldung
        von Google (``error.message``), die Abbruchmeldung des WebSockets und
        ``str(exc)`` aus dem Sitzungsrumpf. Alle drei gingen unveraendert und
        ungekuerzt sowohl ins Protokoll als auch ueber ``art: "fehler"`` an den
        Browser.

        **Der Schluessel ist der Grund, warum das mehr als Unordnung war.** Die
        Live-API von Google nimmt ihn als Abfrageteil der Adresse (``?key=…``);
        anders ist ihr WebSocket-Handshake nicht dokumentiert. Eine Ausnahme
        aus `websockets.connect` traegt die Adresse aber oft im Text
        (``InvalidURI``, Weiterleitungen, manche Handshake-Fehler), und damit
        stand der Zugang des Betreibers in einer Meldung, die an den Browser
        ging. Die Mustererkennung in `ai_redaction` faengt das **nicht**:
        ``key`` allein ist dort kein Geheimniswort, sonst wuerde jedes
        ``server_key`` in einer Konfiguration unlesbar.

        Der eigene Schluessel wird deshalb woertlich gesucht und ersetzt — roh
        und URL-kodiert, denn genau so steht er in der Adresse. Das ist exakt
        und haengt an keinem Muster.

        Danach dieselbe Behandlung wie bei jedem Fremdtext: redigieren,
        einzeilig, hart kuerzen.
        """
        roh = str(text or "")
        schluessel = (self.v.api_key or "").strip()
        if schluessel:
            roh = roh.replace(schluessel, "[REDACTED]")
            roh = roh.replace(quote_plus(schluessel), "[REDACTED]")
        return " ".join(redact_sensitive_text(roh).split())[:MAX_FEHLERTEXT_ZEICHEN]

    async def _panel_senden(self, daten: dict) -> None:
        async with self._senden_lock:
            await self.websocket.send_json(daten)
            self.lage.rahmen_zurueck += 1

    async def _debug_senden(self, code: str, hint: str = "", **fields: object) -> None:
        voice_debug(code, hint=hint, **fields)
        try:
            await self._panel_senden({"art": "debug", "code": code, "hint": hint, **fields})
        except Exception:
            pass

    def _preise_laden(self) -> tuple[int, int, int, int]:
        with SessionLocal() as db:
            provider = db.get(AiProvider, self.v.provider_id)
            if provider is None:
                raise GeminiLiveSitzungsfehler("GEMINI_NOT_CONFIGURED")
            return tuple(
                int(getattr(provider, feld) or 0)
                for feld in ai_provider_service.REALTIME_PREISFELDER
            )  # type: ignore[return-value]

    def _verbrauch(self, usage: dict) -> None:
        prompt_tokens = usage.get("promptTokenCount", 0) or 0
        candidates_tokens = usage.get("candidatesTokenCount", 0) or 0
        ti = max(0, prompt_tokens - self._last_prompt_tokens)
        to = max(0, candidates_tokens - self._last_candidates_tokens)
        if ti == 0 and to == 0:
            return
        self._last_prompt_tokens = max(self._last_prompt_tokens, prompt_tokens)
        self._last_candidates_tokens = max(self._last_candidates_tokens, candidates_tokens)
        ai = 0
        ao = 0
        preise = self._preise_laden()
        deltas = (ti, to, ai, ao)
        for index, wert in enumerate(deltas):
            self._verbrauch_tokens[index] += wert
        gesamtkosten = sum(
            tokens * preis
            for tokens, preis in zip(self._verbrauch_tokens, preise, strict=True)
        ) // 1_000_000
        kosten = max(0, gesamtkosten - self._verbrauch_kosten)
        with SessionLocal() as db:
            ai_usage_service.realtime_verbrauch_ergaenzen(
                db,
                event_id=self.v.usage_event_id,
                text_input=ti,
                text_output=to,
                audio_input=ai,
                audio_output=ao,
                cost_microunits=kosten,
            )
            db.commit()
        self._verbrauch_kosten = gesamtkosten

    async def _tool_start(self, call_id: str, name: str) -> None:
        if call_id in self._gestartet:
            return
        self._gestartet.add(call_id)
        frame = voice_tool_frame("tool_start", {"name": name})
        if frame:
            await self._panel_senden(frame)

    async def _werkzeug_ausfuehren(
        self, call_id: str, name: str, argumente: dict
    ) -> dict[str, Any]:
        await self._tool_start(call_id, name)
        wert: dict[str, Any] = {}
        fehler: str | None = None
        anzeige: dict[str, Any] = {"tool_name": name}
        vorschlaege: list[dict] = []

        # **Derselbe Deckel wie auf dem Realtime-Weg** (dort
        # `MAX_TOOL_ARGUMENTE_ZEICHEN`, geprueft vor dem Parsen). Hier fehlte
        # er: Googles Rahmen kommt bereits geparst an, und die einzige Grenze
        # war `max_size` des WebSockets — vier Megabyte je Aufruf, die
        # ungebremst in einen Werkzeughandler und von dort in den Rueckkanal
        # gingen. Zwei Wege fuer dieselbe Sitzungsart duerfen nicht zwei
        # verschiedene Obergrenzen haben.
        #
        # Als erster Zweig der Kette und nicht als vorgezogener `return`: der
        # Abschluss unten schickt den `werkzeug`-Rahmen ans Panel. Wer hier
        # aussteigt, laesst das Panel mit einem `werkzeug_gestartet` stehen,
        # das nie endet.
        if len(json.dumps(argumente, default=str)) > MAX_TOOL_ARGUMENTE_ZEICHEN:
            fehler = "Werkzeugargumente ungültig"
            wert, anzeige = {"error": fehler}, {"tool_name": name, "failed": True}
        elif name == "voice_resolve_latest_proposal":
            wert, fehler = await asyncio.to_thread(
                self._vorschlag_entscheiden, argumente.get("decision")
            )
            anzeige = {"tool_name": name, **({"failed": True} if fehler else {})}
            await self._panel_senden({"art": "vorschlag", "vorschlag": None})
        elif name == "voice_set_region_view":
            tab = argumente.get("tab")
            source_id = argumente.get("source_id")
            scene_id = argumente.get("scene_id")
            if tab not in {"overview", "satellite", "news", "social", "traffic", "weather"}:
                wert, fehler, anzeige = {"error": "Ungültiger Regionalbereich"}, "Ungültiger Regionalbereich", {"tool_name": name, "failed": True}
            else:
                fokus = {"tab": tab, **({"source_id": source_id} if source_id else {}), **({"scene_id": scene_id} if scene_id else {})}
                await self._panel_senden({"art": "region_ui", "focus": fokus})
                wert, fehler, anzeige = {"ok": True}, None, {"tool_name": name}
        elif name == "voice_leave_region_view":
            await self._panel_senden({"art": "region_ui", "leave": True})
            wert, fehler, anzeige = {"ok": True}, None, {"tool_name": name}
        elif name == "analyze_region":
            try:
                wert = await asyncio.wait_for(
                    asyncio.to_thread(self._region_anfang, argumente),
                    timeout=GEMINI_TOOL_TIMEOUT_SECONDS,
                )
                fehler = None
                anzeige = {"tool_name": name, "geo_analysis": wert}
                if wert.get("status") == "success":
                    self._region_nachladen(argumente, wert)
            except TimeoutError:
                fehler = "Werkzeug hat nicht rechtzeitig geantwortet"
                wert = {"error": "TOOL_TIMEOUT"}
                anzeige = {"tool_name": name, "failed": True, "code": "REALTIME_TOOL_TIMEOUT"}
            except Exception:
                fehler = "Werkzeug konnte nicht ausgeführt werden"
                wert = {"error": fehler}
                anzeige = {"tool_name": name, "failed": True, "code": "REALTIME_TOOL_FAILED"}
        else:
            call = ProviderToolCall(id=call_id, name=name, arguments=argumente)
            try:
                wert, fehler, anzeige, vorschlaege = await asyncio.wait_for(
                    asyncio.to_thread(
                        voice_werkzeug_ausfuehren,
                        self.user_id,
                        call,
                        conversation_id=self.v.conversation_id,
                        herkunft=self.herkunft,
                        familie=self.familie,
                    ),
                    timeout=GEMINI_TOOL_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                fehler = "Werkzeug hat nicht rechtzeitig geantwortet"
                wert = {"error": "TOOL_TIMEOUT"}
                anzeige = {"tool_name": name, "failed": True, "code": "REALTIME_TOOL_TIMEOUT"}
            except Exception:
                fehler = "Werkzeug konnte nicht ausgeführt werden"
                wert = {"error": fehler}
                anzeige = {"tool_name": name, "failed": True, "code": "REALTIME_TOOL_FAILED"}

        frame = voice_tool_frame("tool", anzeige)
        if frame:
            await self._panel_senden(frame)
        if fehler:
            await self._debug_senden("REALTIME_TOOL_ERROR" if anzeige.get("failed") else "REALTIME_TOOL_OK", hint=name)

        for vorschlag in vorschlaege:
            kennung = vorschlag.get("id")
            if not isinstance(kennung, str) or not kennung:
                continue
            karte = {k: v for k, v in vorschlag.items() if k != "call_id"}
            if not bool(vorschlag.get("autonomous")) and vorschlag.get("status") == "proposed":
                self._offener_vorschlag = kennung
                await self._panel_senden({"art": "vorschlag", "vorschlag": karte})

        return wert

    def _vorschlag_entscheiden(self, decision: object) -> tuple[dict, str | None]:
        kennung = self._offener_vorschlag
        self._offener_vorschlag = None
        if decision not in {"confirm", "reject", "accept"} or kennung is None:
            fehler = "Kein passender Vorschlag in dieser Sprachsitzung"
            return {"error": fehler}, fehler
        if decision == "reject":
            return {"status": "rejected_by_user"}, None
        if voice_interactions.braucht_klick(user_id=self.user_id, kennung=kennung):
            return {"status": "needs_panel_confirmation",
                    "hinweis": voice_interactions.KLICK_NOETIG}, None
        erledigt, _ = voice_interactions.vorschlag_ausfuehren(
            user_id=self.user_id, kennung=kennung
        )
        if not erledigt:
            fehler = "Vorschlag konnte nicht bestätigt werden"
            return {"error": fehler}, fehler
        return {"status": "confirmed"}, None

    def _region_anfang(self, argumente: dict) -> dict:
        with SessionLocal() as db:
            user = db.get(User, self.user_id)
            if user is None:
                raise GeminiLiveSitzungsfehler("GEMINI_NOT_CONFIGURED")
            return ai_action_service.execute_realtime_region_initial(
                db, user=user, arguments=argumente
            )

    def _region_nachladen(self, argumente: dict, initial: dict) -> None:
        task = asyncio.create_task(self._region_nachladen_lassen(argumente, initial))
        self._region_tasks.add(task)
        task.add_done_callback(self._region_tasks.discard)

    async def _region_nachladen_lassen(self, argumente: dict, initial: dict) -> None:
        try:
            with SessionLocal() as db:
                user = db.get(User, self.user_id)
                if user is None:
                    return
                analysis = await asyncio.to_thread(
                    ai_action_service.execute_realtime_region_enrichment,
                    db,
                    user=user,
                    arguments=argumente,
                    initial=initial,
                    prefetch_session_id=self.v.conversation_id,
                )
            frame = voice_tool_frame("tool", {"tool_name": "analyze_region", "geo_analysis": analysis})
            if frame:
                await self._panel_senden(frame)
        except Exception:
            pass

    async def _client_lesen(self) -> None:
        while not self._beendet:
            try:
                nachricht = await self.websocket.receive()
            except WebSocketDisconnect:
                break
            except Exception:
                break

            self.lage.rahmen_hin += 1
            if "bytes" in nachricht and nachricht["bytes"]:
                raw_pcm = nachricht["bytes"]
                pcm_16k = downsample_24k_to_16k(raw_pcm)
                if pcm_16k and self._google_ws is not None:
                    chunk_b64 = base64.b64encode(pcm_16k).decode("ascii")
                    msg = {
                        "realtimeInput": {
                            "mediaChunks": [
                                {
                                    "mimeType": "audio/pcm;rate=16000",
                                    "data": chunk_b64,
                                }
                            ]
                        }
                    }
                    try:
                        await self._google_ws.send(json.dumps(msg))
                    except Exception:
                        break
            elif "text" in nachricht and nachricht["text"]:
                try:
                    daten = json.loads(nachricht["text"])
                    if daten.get("art") == "beenden":
                        break
                    elif daten.get("art") == "unterbrechen":
                        await self._panel_senden({"art": "zustand", "zustand": "hoert"})
                except Exception:
                    pass

    async def _google_lesen(self) -> None:
        assert self._google_ws is not None
        try:
            async for roh in self._google_ws:
                if not isinstance(roh, str):
                    continue
                try:
                    event = json.loads(roh)
                except json.JSONDecodeError:
                    continue

                # Fehlermeldung von Google (z. B. Modell nicht unterstützt, Quota oder ungültige Parameter)
                if "error" in event:
                    err = event["error"]
                    roh_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    err_msg = self._fremdtext(roh_msg)
                    logger.warning("Gemini Live Fehler vom Anbieter: %s", err_msg)
                    await self._debug_senden("GEMINI_LIVE_ERROR", hint=err_msg)
                    with contextlib.suppress(Exception):
                        await self._panel_senden({
                            "art": "fehler",
                            "code": "GEMINI_LIVE_ERROR",
                            "detail": err_msg,
                        })
                    break

                # Setup-Bestätigung
                if "setupComplete" in event:
                    self._setup_fertig.set()
                    await self._panel_senden({"art": "bereit"})
                    await self._panel_senden({"art": "zustand", "zustand": "hoert"})
                    await self._debug_senden("GEMINI_LIVE_SETUP_OK", hint=f"Verbunden mit {self.v.model}")
                    continue

                # Inhalte vom Modell
                server_content = event.get("serverContent")
                if server_content:
                    if server_content.get("interrupted"):
                        await self._panel_senden({"art": "zustand", "zustand": "hoert"})

                    model_turn = server_content.get("modelTurn")
                    if model_turn:
                        for part in model_turn.get("parts", []):
                            text = part.get("text")
                            if text:
                                await self._panel_senden({"art": "antworttext", "text": text})
                            inline_data = part.get("inlineData")
                            if inline_data and inline_data.get("data"):
                                try:
                                    audio_bytes = base64.b64decode(inline_data["data"])
                                    async with self._senden_lock:
                                        await self.websocket.send_bytes(audio_bytes)
                                        self.lage.rahmen_zurueck += 1
                                    await self._panel_senden({"art": "zustand", "zustand": "spricht"})
                                except Exception:
                                    pass

                    if server_content.get("turnComplete"):
                        self.lage.laeufe += 1
                        await self._panel_senden({"art": "zustand", "zustand": "hoert"})

                # Werkzeugaufrufe
                tool_call = event.get("toolCall")
                if tool_call:
                    await self._panel_senden({"art": "zustand", "zustand": "denkt"})
                    function_calls = tool_call.get("functionCalls", [])
                    responses = []
                    for fc in function_calls:
                        call_id = fc.get("id") or str(uuid4())
                        name = fc.get("name") or ""
                        args = fc.get("args") or {}
                        res = await self._werkzeug_ausfuehren(call_id, name, args)
                        # Dieselbe Huelle wie im Chat. Warum sie hier fehlte und
                        # warum gerade hier am wenigsten: siehe
                        # `werkzeugergebnis_umschlag`.
                        resp_obj = werkzeugergebnis_umschlag(name, res)
                        responses.append({"name": name, "response": resp_obj, "id": call_id})

                    tool_response = {
                        "toolResponse": {
                            "functionResponses": responses
                        }
                    }
                    try:
                        await self._google_ws.send(json.dumps(tool_response))
                    except Exception:
                        await self._debug_senden("GEMINI_TOOL_RESPONSE_FAILED", hint=name)
                        break

                # Nutzungsmetriken
                usage_meta = event.get("usageMetadata") or (server_content.get("usageMetadata") if server_content else None)
                if usage_meta:
                    try:
                        await asyncio.to_thread(self._verbrauch, usage_meta)
                    except Exception:
                        pass
        except Exception as exc:
            grund = self._fremdtext(exc)
            logger.warning("Gemini Live WebSocket Fehler im Lesestrom: %s", grund)
            await self._debug_senden("GEMINI_LIVE_CLOSED", hint=grund)
            with contextlib.suppress(Exception):
                await self._panel_senden({
                    "art": "fehler",
                    "code": "GEMINI_LIVE_CLOSED",
                    "detail": grund,
                })

    def _build_setup_payload(
        self,
        *,
        model_name: str,
        model_raw: str,
        voice_name: str,
        gemini_tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name,
                    }
                }
            },
        }

        model_lower = model_raw.lower()
        # Bei Google Multimodal Live:
        # - Modelle mit 'thinking' im Namen (z. B. gemini-3.8-live-extended-thinking) verlangen
        #   thinkingConfig mit thinkingLevel (LOW, MEDIUM, HIGH) bei Gemini 3+.
        # - Standard-Live-Modelle (z. B. gemini-3.8-live, gemini-2.5-flash) unterstützen KEIN thinkingLevel;
        #   wird es mitgesendet, bricht Google sofort mit Code 1007 ab ("Thinking level is not supported for this model").
        if "thinking" in model_lower:
            lvl = "LOW"
            if self.v.reasoning_effort:
                effort = str(self.v.reasoning_effort).strip().lower()
                if effort in ("high", "deep", "max"):
                    lvl = "HIGH"
                elif effort in ("medium", "med", "default"):
                    lvl = "MEDIUM"
                elif effort in ("low", "min", "minimal", "fast", "none", "off"):
                    lvl = "LOW"

            if "gemini-3" in model_lower or "gemini-4" in model_lower or "3." in model_lower:
                generation_config["thinkingConfig"] = {
                    "thinkingLevel": lvl,
                }
            else:
                effort_str = str(self.v.reasoning_effort or "").strip().lower()
                budget = 0 if effort_str in ("none", "off", "min", "minimal") else 1024
                generation_config["thinkingConfig"] = {
                    "thinkingBudget": budget,
                }

        setup_payload: dict[str, Any] = {
            "setup": {
                "model": model_name,
                "generationConfig": generation_config,
            }
        }
        if self.v.instructions and self.v.instructions.strip():
            setup_payload["setup"]["systemInstruction"] = {
                "parts": [{"text": self.v.instructions}]
            }
        if gemini_tools:
            setup_payload["setup"]["tools"] = [{"functionDeclarations": gemini_tools}]

        return setup_payload

    async def fuehren(self) -> Lage:
        if websockets is None:
            await self._debug_senden("GEMINI_NOT_AVAILABLE", hint="websockets Paket fehlt")
            raise GeminiLiveSitzungsfehler("GEMINI_NOT_AVAILABLE")

        ai_meldestelle.realtime_sitzung_start(self.user_id)
        url = f"{GEMINI_LIVE_WS_BASE}?key={quote_plus(self.v.api_key)}"
        model_raw = (self.v.model or "").strip()
        if not model_raw or "gemini" not in model_raw.lower():
            logger.info("Kein gültiges Gemini-Live-Modell angegeben ('%s'), nutze gemini-2.0-flash", model_raw)
            model_raw = "gemini-2.0-flash"
        model_name = model_raw if model_raw.startswith("models/") else f"models/{model_raw}"
        # Hier gehoert die Kleinschreibung hin und nicht nur in
        # `_build_setup_payload`: die Werkzeugschleife unten fragt dieselbe
        # Marke ab. Sie tat es bisher gegen einen Namen, den es in dieser
        # Funktion gar nicht gibt — ein `NameError`, der **vor** dem `try`
        # steht und damit jede Gemini-Live-Sitzung mit auch nur einem Werkzeug
        # gekillt hat, bevor die erste Verbindung stand.
        model_lower = model_raw.lower()
        voice_name = self.v.voice or "Puck"

        gemini_tools = []
        for t in self.v.tools:
            name = t.get("name")
            if not name:
                continue
            params = t.get("parameters")
            if not isinstance(params, dict):
                params = {"type": "OBJECT", "properties": {}}
            else:
                params = _clean_gemini_schema(params)
            fn_decl = {
                "name": name,
                "description": t.get("description", ""),
                "parameters": params,
            }
            if "thinking" in model_lower:
                fn_decl["behavior"] = "NON_BLOCKING"
            gemini_tools.append(fn_decl)

        setup_payload = self._build_setup_payload(
            model_name=model_name,
            model_raw=model_raw,
            voice_name=voice_name,
            gemini_tools=gemini_tools,
        )

        try:
            self._google_ws = await websockets.connect(
                url,
                max_size=4 * 1024 * 1024,
                open_timeout=15,
            )
            await self._google_ws.send(json.dumps(setup_payload))
            await self._debug_senden("GEMINI_LIVE_CONNECTED", hint=f"Handshake mit {model_name} läuft")

            async def _handshake_watchdog() -> None:
                try:
                    await asyncio.wait_for(self._setup_fertig.wait(), timeout=12.0)
                except asyncio.TimeoutError:
                    if not self._beendet and not self._setup_fertig.is_set():
                        logger.warning("Gemini Live Handshake Zeitüberschreitung für Modell %s", model_name)
                        await self._debug_senden("GEMINI_HANDSHAKE_TIMEOUT", hint=model_name)
                        with contextlib.suppress(Exception):
                            await self._panel_senden({
                                "art": "fehler",
                                "code": "GEMINI_HANDSHAKE_TIMEOUT",
                                "detail": f"Keine Antwort von Google für Modell '{model_name}'. Bitte wechsle in den AI-Provider-Einstellungen zu 'gemini-2.0-flash' oder 'gemini-3.8-live'.",
                            })

            watchdog_task = asyncio.create_task(_handshake_watchdog())
            client_task = asyncio.create_task(self._client_lesen())
            google_task = asyncio.create_task(self._google_lesen())

            done, pending = await asyncio.wait(
                {client_task, google_task},
                timeout=MAX_SITZUNGSSEKUNDEN,
                return_when=asyncio.FIRST_COMPLETED,
            )
            watchdog_task.cancel()
            self._beendet = True
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except asyncio.TimeoutError:
            self.lage.abgelaufen = True
            await self._panel_senden({"art": "abgelaufen"})
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            # **Kein `logger.exception`.** Der Rueckverfolgungstext enthaelt die
            # Aufrufzeile von `websockets.connect(url, …)` samt Adresse — und in
            # der Adresse steht der Schluessel. Die Art der Ausnahme und die
            # redigierte Meldung sagen dasselbe ueber die Ursache, ohne ihn.
            grund = self._fremdtext(exc)
            logger.warning(
                "Gemini Live Sitzung fehlgeschlagen art=%s grund=%s",
                type(exc).__name__,
                grund,
            )
            voice_debug("GEMINI_INTERNAL_ERROR", hint=type(exc).__name__)
            with contextlib.suppress(Exception):
                await self._panel_senden({"art": "fehler", "code": "GEMINI_INTERNAL_ERROR", "detail": grund})
        finally:
            self._beendet = True
            for task in self._tool_tasks:
                task.cancel()
            for task in self._region_tasks:
                task.cancel()
            if self._google_ws is not None:
                with contextlib.suppress(Exception):
                    await self._google_ws.close()
            await asyncio.to_thread(self._abschliessen)
            ai_meldestelle.realtime_sitzung_ende(self.user_id)

        return self.lage

    def _abschliessen(self) -> None:
        with SessionLocal() as db:
            ai_usage_service.realtime_sitzung_abschliessen(db, self.v.usage_event_id)
            db.commit()
