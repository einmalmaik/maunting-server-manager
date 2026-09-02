"""Broker-Abonnement und sichere Voice-Ausgabe eines einzelnen AI-Runs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from services.ai_voice.contracts import (
    LAUF_TIMEOUT,
    ZUSTAND_BEREIT,
    ZUSTAND_DENKT,
    ZUSTAND_SPRICHT,
    voice_tool_frame,
)
from services.ai_voice.text import Belegfilter

logger = logging.getLogger(__name__)


async def tool_ereignis_senden(
    senden: Callable[[dict], Awaitable[None]], ereignis: str, daten: dict
) -> bool:
    """Sendet die vollständige, sichere Tool-Projektion an den Voice-Client."""

    frame = voice_tool_frame(ereignis, daten)
    if frame is None:
        return False
    await senden(frame)
    return True


class VoiceRunOutput:
    """Hält Broker-Transport, Belegfilter und TTS über schmale Callbacks zusammen."""

    def __init__(
        self,
        *,
        user_id: int,
        senden: Callable[[dict], Awaitable[None]],
        zustand_melden: Callable[[str], Awaitable[None]],
        ausgabe_aktiv: Callable[[], bool],
        stimme_oeffnen: Callable[[], Any],
        stimme_setzen: Callable[[Any | None], None],
        frage_vorlesen: Callable[[dict, Any], Awaitable[None]],
        vorschlag_merken: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._user_id = user_id
        self._senden = senden
        self._zustand_melden = zustand_melden
        self._ausgabe_aktiv = ausgabe_aktiv
        self._stimme_oeffnen = stimme_oeffnen
        self._stimme_setzen = stimme_setzen
        self._frage_vorlesen = frage_vorlesen
        self._vorschlag_merken = vorschlag_merken
        self._sprechpunkt = ""

    def checkpoint_verbrauchen(self) -> str | None:
        """Gibt nur bereits vorgelesenen Text als flüchtigen Folgekontext zurück."""

        checkpoint = self._sprechpunkt or None
        self._sprechpunkt = ""
        return checkpoint

    @staticmethod
    def eroeffnen_und_abonnieren(run_id: str):
        from services import ai_run_broker

        ai_run_broker.eroeffnen(run_id)
        return ai_run_broker.abonnieren(run_id)

    @staticmethod
    def abonnieren(run_id: str):
        from services import ai_run_broker

        return ai_run_broker.abonnieren(run_id)

    @staticmethod
    def abmelden(run_id: str, abo) -> None:
        if abo is None:
            return
        from services import ai_run_broker

        ai_run_broker.abmelden(run_id, abo[1])

    async def verfolgen(self, abo) -> None:
        """Übersetzt Broker-Ereignisse in JSON, TTS und Statusmeldungen."""

        if abo is None:
            await self._zustand_melden(ZUSTAND_BEREIT)
            return
        _abzug, warteschlange = abo
        belegfilter = Belegfilter()
        gesprochen = False
        async with self._stimme_oeffnen() as stimme:
            self._stimme_setzen(stimme)
            try:
                while True:
                    try:
                        ereignis, daten = await asyncio.wait_for(
                            warteschlange.get(), LAUF_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Sprachlauf ohne Ereignis user=%s", self._user_id)
                        break
                    if ereignis is None:
                        break
                    weiter, sprachanteil = await self._ereignis(
                        ereignis, daten, stimme, belegfilter
                    )
                    gesprochen = gesprochen or sprachanteil
                    if not weiter:
                        break
                rest, belege = belegfilter.ausklingen()
                for beleg in belege:
                    await self._senden(beleg)
                if rest.strip() and self._ausgabe_aktiv():
                    self._sprechpunkt += rest
                    await stimme.sagen(rest)
                    gesprochen = True
                if gesprochen and self._ausgabe_aktiv():
                    await self._zustand_melden(ZUSTAND_SPRICHT)
                if self._ausgabe_aktiv():
                    await stimme.ausklingen()
            finally:
                self._stimme_setzen(None)
        await self._zustand_melden(ZUSTAND_BEREIT)

    async def _ereignis(
        self,
        ereignis: str,
        daten: dict,
        stimme: Any,
        belegfilter: Belegfilter,
    ) -> tuple[bool, bool]:
        """Verarbeitet ein einzelnes Broker-Ereignis ohne Roh-Tooldaten."""

        if ereignis == "delta":
            text = str(daten.get("content") or "")
            sprechbar, belege = belegfilter.fuettern(text)
            for beleg in belege:
                await self._senden(beleg)
            gesprochen = False
            if sprechbar.strip() and self._ausgabe_aktiv():
                await self._zustand_melden(ZUSTAND_SPRICHT)
                self._sprechpunkt += sprechbar
                await stimme.sagen(sprechbar)
                gesprochen = True
            if text:
                await self._senden({"art": "antworttext", "text": text})
            return True, gesprochen

        if ereignis in {"tool_start", "werkzeug_gestartet"}:
            await tool_ereignis_senden(self._senden, ereignis, daten)
            return True, False

        if ereignis in {"tool", "tool_plan"}:
            rest, belege = belegfilter.ausklingen()
            for beleg in belege:
                await self._senden(beleg)
            gesprochen = False
            if rest.strip() and self._ausgabe_aktiv():
                await self._zustand_melden(ZUSTAND_SPRICHT)
                self._sprechpunkt += rest
                await stimme.sagen(rest)
                gesprochen = True
            await tool_ereignis_senden(self._senden, ereignis, daten)
            return True, gesprochen

        if ereignis == "question":
            await self._frage_vorlesen(daten, stimme)
            return False, True
        if ereignis == "proposal":
            await self._vorschlag_merken(daten)
            return True, False
        if ereignis == "action":
            return True, False
        if ereignis == "error":
            await self._senden({"art": "stoerung"})
            return False, False
        if ereignis == "done":
            return True, False
        if ereignis == "run":
            status = str(daten.get("status") or "")
            if status == "waiting_wake" and str(daten.get("stop_reason") or "") == "desktop_jobs":
                await self._zustand_melden(ZUSTAND_DENKT)
                return True, False
            return status == "running", False
        return True, False
