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
from services.ai_stream.read_tools import voice_werkzeug_ausfuehren
from services.ai_voice import interactions as voice_interactions
from services.ai_voice.contracts import Lage, MAX_SITZUNGSSEKUNDEN, voice_tool_frame
from services.ai_voice.realtime_session import RealtimeVorbereitung
from services.ai_voice_debug import emit as voice_debug
from services.openai_compatible_adapter import ProviderToolCall


logger = logging.getLogger(__name__)

GEMINI_LIVE_WS_BASE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
GEMINI_TOOL_TIMEOUT_SECONDS = 12.0


class GeminiLiveSitzungsfehler(RuntimeError):
    """Fehler während der Gemini Live Sitzung."""


def _clean_gemini_schema(schema: Any) -> Any:
    """Bereinigt JSON Schema für Google Gemini Function Declarations.

    Google Gemini unterstützt keine `additionalProperties` oder `$schema` in Funktionsparametern.
    """
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("additionalProperties", "$schema", "$id", "title"):
            continue
        if isinstance(v, dict):
            cleaned[k] = _clean_gemini_schema(v)
        elif isinstance(v, list):
            cleaned[k] = [
                _clean_gemini_schema(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            cleaned[k] = v
    if "type" not in cleaned and "properties" in cleaned:
        cleaned["type"] = "object"
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

        if name == "voice_resolve_latest_proposal":
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

                # Setup-Bestätigung
                if "setupComplete" in event:
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
                        resp_obj = {"output": res} if isinstance(res, dict) else {"result": res}
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
            logger.warning("Gemini Live WebSocket Fehler im Lesestrom: %s", exc)
            await self._debug_senden("GEMINI_LIVE_CLOSED", hint=str(exc))

    async def fuehren(self) -> Lage:
        if websockets is None:
            await self._debug_senden("GEMINI_NOT_AVAILABLE", hint="websockets Paket fehlt")
            raise GeminiLiveSitzungsfehler("GEMINI_NOT_AVAILABLE")

        from urllib.parse import quote_plus

        ai_meldestelle.realtime_sitzung_start(self.user_id)
        url = f"{GEMINI_LIVE_WS_BASE}?key={quote_plus(self.v.api_key)}"

        model_raw = (self.v.model or "").strip()
        if not model_raw or "3.8" in model_raw or ("live" in model_raw.lower() and not any(v in model_raw.lower() for v in ("2.0", "2.5"))):
            logger.warning("Ungültiges Gemini-Live-Modell '%s', nutze gemini-2.5-flash", model_raw)
            model_raw = "gemini-2.5-flash"
        model_name = model_raw if model_raw.startswith("models/") else f"models/{model_raw}"
        voice_name = self.v.voice or "Puck"

        gemini_tools = []
        for t in self.v.tools:
            name = t.get("name")
            if not name:
                continue
            params = t.get("parameters")
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}}
            else:
                params = _clean_gemini_schema(params)
            fn_decl = {
                "name": name,
                "description": t.get("description", ""),
                "parameters": params,
            }
            gemini_tools.append(fn_decl)

        setup_payload: dict[str, Any] = {
            "setup": {
                "model": model_name,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": voice_name,
                            }
                        }
                    },
                },
            }
        }
        if self.v.instructions and self.v.instructions.strip():
            setup_payload["setup"]["systemInstruction"] = {
                "parts": [{"text": self.v.instructions}]
            }
        if gemini_tools:
            setup_payload["setup"]["tools"] = [{"functionDeclarations": gemini_tools}]
        if getattr(self.v, "disable_safety", False):
            from services.ai_provider_registry.google import get_safety_settings
            setup_payload["setup"]["safetySettings"] = get_safety_settings(disable_safety=True)

        try:
            self._google_ws = await websockets.connect(
                url,
                max_size=4 * 1024 * 1024,
                open_timeout=15,
            )
            await self._google_ws.send(json.dumps(setup_payload))
            await self._debug_senden("GEMINI_LIVE_CONNECTED", hint=f"Handshake mit {model_name} läuft")

            client_task = asyncio.create_task(self._client_lesen())
            google_task = asyncio.create_task(self._google_lesen())

            done, pending = await asyncio.wait(
                {client_task, google_task},
                timeout=MAX_SITZUNGSSEKUNDEN,
                return_when=asyncio.FIRST_COMPLETED,
            )
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
            logger.exception("Gemini Live Sitzung fehlgeschlagen: %s", exc)
            voice_debug("GEMINI_INTERNAL_ERROR", hint=type(exc).__name__)
            with contextlib.suppress(Exception):
                await self._panel_senden({"art": "fehler", "code": "GEMINI_INTERNAL_ERROR", "detail": str(exc)})
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
