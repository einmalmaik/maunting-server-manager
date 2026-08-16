"""Die Brücke: gesprochene Rede geht denselben Weg wie getippte.

Der Sprachmodus ist seit dem 16.08.2026 **kein eigenes Modell mehr**. Er ist der
gewöhnliche Chatlauf mit zwei Wandlern davor und dahinter:

.. code-block:: text

    Mikrofon → Pausenerkennung → Gehör → derselbe AiRun wie im Chat
                                              ↓
    Lautsprecher ←──── Stimme ←──── derselbe Antworttext

Was das kostet, ist eine Sprechpausenerkennung, die weniger klug ist als die
weggefallene (`ai_voice_vad`). Was es spart, ist ein zweiter Werkzeuglauf, eine
zweite Bestätigungsmechanik, ein zweites Gedächtnis, ein zweiter Werkzeugkatalog
und ein zweites Protokoll — rund 2.700 Zeilen, in denen jeder Befund zweimal
behoben werden musste und beim zweiten Mal regelmässig anders.

**Die Regeln sind deshalb nicht neu, sondern dieselben.** Autonomie entscheidet
`ai_autonomy_service`, Bestätigungspflicht `create_proposal`, Rechte
`_require_tool_permission`, Kosten `reserve_ai_usage`. Diese Datei entscheidet
nichts davon. Sie übersetzt nur zwischen „gesprochen" und „getippt", und die
Übersetzung besteht aus genau drei Eingriffen:

1. **Belege werden gezeigt statt vorgelesen.** Der Systemprompt verlangt vom
   Modell ohnehin, Logstellen als Codeblock zu zeigen und darunter zu deuten
   (`ai_prompt.BELEGE`). Im Gespräch wäre ein Codeblock vorgelesene
   Satzzeichen — also geht der Block auf den Schirm und die Deutung ans Ohr.
   Dasselbe Modell, dieselbe Anweisung, andere Ausgabe.
2. **Rückfragen werden gesprochen.** `ask_user` stellt im Chat eine Karte mit
   Knöpfen; hier werden Frage und Möglichkeiten vorgelesen. Beantwortet wird
   sie wie im Chat — mit der nächsten Nachricht, und die spricht der Mensch
   einfach.
3. **Bestätigungen werden gesprochen.** Ein Vorschlag, der auf ein Ja wartet,
   wird vorgelesen; ein gesprochenes Ja nimmt danach **denselben** Weg wie der
   Klick auf die Karte (`confirm_proposal` und `execute_proposal`). Es gibt
   keinen zweiten Bestätigungspfad — es gibt einen zweiten Auslöser für den
   ersten.

**Eine Äusserung ist eine Anfrage.** Das ist der eine Punkt, an dem sich für
den Betreiber etwas ändert: wo eine Sprachsitzung früher **eine** Buchung war,
ist jetzt jeder Zug eine. Ein Rollenlimit ``requests_per_minute`` von fünf
zerreisst damit ein Gespräch, das vorher durchlief. Ohne gesetztes Limit
(``None``, die Vorgabe) passiert nichts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from database import SessionLocal
from models import AiProvider, User
from services import (
    ai_run_broker,
    ai_stt_openrouter,
    ai_tts_elevenlabs,
    ai_voice_vad,
)
from services.ai_redaction import redact_sensitive_text

logger = logging.getLogger(__name__)


# ── Was der Browser zu sehen bekommt ──────────────────────────────────────
#
# Dieselbe kleine, geschlossene Liste wie zuvor — der Browser merkt vom
# Anbieterwechsel nichts. Binärrahmen sind Ton (PCM16, 24 kHz, mono),
# Textrahmen sind JSON und beschreiben, was gerade passiert.

ZUSTAND_BEREIT = "bereit"
ZUSTAND_HOERT = "hoert"
ZUSTAND_DENKT = "denkt"
ZUSTAND_SPRICHT = "spricht"


#: Wie lange eine Sprachsitzung höchstens läuft.
#:
#: Die Zahl ist keine Vorsicht, sondern die Lebensdauer des Access-Tokens. Ein
#: WebSocket prüft die Anmeldung **nur beim Handshake** (`dependencies.py`), und
#: eine Verbindung, die Stunden offen bleibt, umginge damit beides: den Ablauf
#: des Tokens und die jti-Sperrliste. Wer abgemeldet wird, spräche weiter.
MAX_SITZUNGSSEKUNDEN = 15 * 60

#: Wie groß ein einzelner Tonrahmen vom Browser höchstens sein darf. Bei 24 kHz
#: mono sind 128 KiB rund 2,7 Sekunden Ton — großzügig für Rahmen, die alle 20
#: bis 100 Millisekunden kommen sollen, und eng genug, dass niemand über diesen
#: Weg Speicher belegt.
MAX_TONRAHMEN_BYTES = 128 * 1024

#: Wie lang ein Textrahmen vom Browser höchstens sein darf. Der Browser schickt
#: hier nur winzige Steuerbefehle; alles Größere ist keine Steuerung mehr.
MAX_STEUERRAHMEN_ZEICHEN = 4_096

#: Wie lange auf einen Lauf gewartet wird, der nichts mehr meldet. Die Frist ist
#: grosszügig, weil ein Lauf mit mehreren Werkzeugrunden echte Minuten braucht —
#: sie fängt nur den Fall ab, dass die Gegenstelle gar nicht mehr antwortet.
LAUF_TIMEOUT = 300.0


# ── Belege: was gezeigt und nicht gesprochen wird ─────────────────────────

#: Ein Codeblock, wie ihn `ai_prompt.BELEGE` vom Modell verlangt.
_ZAUN = re.compile(r"^\s*```")

#: Wie viele Zeilen eine gezeigte Stelle höchstens hat. Der Prompt verlangt „ein
#: bis fünf"; das hier ist die Schranke dahinter, nicht die Erwartung.
MAX_BELEG_ZEILEN = 40

#: Wie lang eine einzelne gezeigte Zeile höchstens ist. Eine Logzeile mit
#: viertausend Zeichen ist keine Stelle mehr, sondern eine Datei.
MAX_BELEG_ZEICHEN = 2_000


@dataclass
class Belegfilter:
    """Trennt im laufenden Text das Gesprochene vom Gezeigten.

    Das Modell schreibt im Sprachmodus dasselbe wie im Chat: die Stelle als
    Codeblock, darunter die Deutung. Vorgelesen gehört nur die Deutung — ein
    Codeblock ist gesprochen eine Aneinanderreihung von Satzzeichen.

    Zustandsbehaftet, weil der Text stückweise ankommt und ein Zaun (```)
    zwischen zwei Stücken zerrissen sein kann. Gearbeitet wird deshalb
    zeilenweise: eine Zeile ist erst fertig, wenn ihr Zeilenumbruch da ist, und
    vorher lässt sich über sie nichts sagen.
    """

    _puffer: str = ""
    _im_block: bool = False
    _block: list[str] = field(default_factory=list)
    _quelle: str = ""

    def fuettern(self, text: str) -> tuple[str, list[dict]]:
        """Gibt (zu sprechender Text, fertige Belege) zurück."""
        self._puffer += text
        gesprochen: list[str] = []
        belege: list[dict] = []
        while "\n" in self._puffer:
            zeile, self._puffer = self._puffer.split("\n", 1)
            beleg = self._zeile(zeile, gesprochen)
            if beleg is not None:
                belege.append(beleg)
        return "".join(gesprochen), belege

    def ausklingen(self) -> tuple[str, list[dict]]:
        """Was noch im Puffer steht, jetzt herausgeben.

        Ein Codeblock ohne schliessenden Zaun ist hier kein Fehlerfall: das
        Modell wurde mitten im Satz abgeschnitten, und die Zeilen, die es schon
        geschrieben hat, sind so gültig wie die anderen.
        """
        gesprochen: list[str] = []
        belege: list[dict] = []
        if self._puffer:
            beleg = self._zeile(self._puffer, gesprochen)
            self._puffer = ""
            if beleg is not None:
                belege.append(beleg)
        if self._im_block:
            self._im_block = False
            fertig = self._beleg_bauen()
            if fertig is not None:
                belege.append(fertig)
        return "".join(gesprochen), belege

    def _zeile(self, zeile: str, gesprochen: list[str]) -> dict | None:
        if _ZAUN.match(zeile):
            if self._im_block:
                self._im_block = False
                return self._beleg_bauen()
            self._im_block = True
            self._block = []
            # Was hinter dem Zaun steht („```log", „```ini"), ist die Sprache
            # des Blocks — als Herkunftsangabe besser als nichts und ohne
            # eigenes Feld im Prompt zu haben.
            self._quelle = zeile.strip().lstrip("`").strip()
            return None
        if self._im_block:
            if len(self._block) < MAX_BELEG_ZEILEN:
                self._block.append(zeile[:MAX_BELEG_ZEICHEN])
            return None
        gesprochen.append(zeile + "\n")
        return None

    def _beleg_bauen(self) -> dict | None:
        zeilen = [zeile for zeile in self._block if zeile.strip()]
        self._block = []
        quelle, self._quelle = self._quelle, ""
        if not zeilen:
            return None
        return {"art": "beleg", "quelle": quelle, "zeilen": zeilen}


# ── Zustimmung und Ablehnung ──────────────────────────────────────────────
#
# Der heikelste Teil dieser Datei, und deshalb der engste.
#
# Ein gesprochenes „Ja" löst hier dasselbe aus wie ein Klick auf die Karte:
# `confirm_proposal` und danach `execute_proposal`. Es gibt für diesen Auslöser
# keine zweite Prüfung — Rechte, Sperre und Einmal-Token liegen unverändert
# dort, wo sie beim Klick liegen. Was hier entschieden wird, ist allein: **war
# das ein Ja?**
#
# Deshalb gilt eine Äusserung nur dann als Zustimmung, wenn sie **nichts
# anderes** enthält. „Ja" ist ein Ja. „Ja, aber schau vorher nochmal nach" ist
# keines — es ist eine neue Anweisung, und sie als Zustimmung zu lesen hiesse,
# einen Server zu löschen, weil das erste Wort passte. Die Prüfung ist eine
# Gleichheit gegen eine geschlossene Menge und ausdrücklich **keine** Suche
# nach einem enthaltenen Wort.

_ZUSTIMMUNG = frozenset({
    "ja", "jo", "jep", "jawohl", "jaja", "ja bitte", "ja gerne", "ja genau",
    "ja mach das", "ja mach", "ja tu das", "ja bestaetigt", "ja klar",
    "mach das", "mach es", "mach", "tu das", "los", "leg los", "mach weiter",
    "bestaetigt", "bestaetige", "bestaetigen", "einverstanden", "in ordnung",
    "passt", "okay", "ok", "okay mach das", "gerne", "genau", "korrekt",
    "yes", "yep", "yeah", "go", "do it", "confirm", "confirmed",
})

_ABLEHNUNG = frozenset({
    "nein", "ne", "nee", "noe", "nein danke", "lass es", "lass das",
    "nicht", "abbrechen", "abbruch", "stopp", "stop", "halt", "warte",
    "lieber nicht", "doch nicht", "vergiss es", "nein lass",
    "no", "nope", "cancel", "abort", "dont", "do not",
})

#: Was vor dem Vergleich wegfällt. Satzzeichen und Umlaute — „Ja!" und „Jä"
#: sind dasselbe Ja, und ein Transkript schreibt beides mal so, mal so.
_UMSCHRIFT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def _normalisieren(text: str) -> str:
    ohne = "".join(
        zeichen for zeichen in text.lower().translate(_UMSCHRIFT)
        if zeichen.isalnum() or zeichen.isspace()
    )
    return " ".join(ohne.split())


def ist_zustimmung(text: str) -> bool:
    """Ob diese Äusserung **nichts als** eine Zustimmung ist."""
    return _normalisieren(text) in _ZUSTIMMUNG


def ist_ablehnung(text: str) -> bool:
    """Ob diese Äusserung **nichts als** eine Ablehnung ist."""
    return _normalisieren(text) in _ABLEHNUNG


# ── Die Sitzung ───────────────────────────────────────────────────────────


@dataclass
class Lage:
    """Was am Ende im Protokoll steht. Zahlen, keine Inhalte."""

    rahmen_hin: int = 0
    rahmen_zurueck: int = 0
    aeusserungen: int = 0
    laeufe: int = 0
    abgelaufen: bool = False


class Sprachbruecke:
    """Eine Sprachsitzung von „verbunden" bis „aufgelegt"."""

    def __init__(
        self,
        browser: WebSocket,
        *,
        user_id: int,
        conversation_id: str,
        chat_provider_id: int,
        stimm_adresse: str,
        stimm_schluessel: str,
        http_client: httpx.AsyncClient,
        hoechstdauer: float = MAX_SITZUNGSSEKUNDEN,
    ) -> None:
        self._browser = browser
        self._user_id = user_id
        self._gespraech_id = conversation_id
        self._provider_id = chat_provider_id
        self._stimm_adresse = stimm_adresse
        self._stimm_schluessel = stimm_schluessel
        self._client = http_client
        self._hoechstdauer = hoechstdauer

        self._erkennung = ai_voice_vad.Pausenerkennung()
        self._lage = Lage()
        self._zustand = ""
        #: Der Lauf, der gerade antwortet. Solange einer läuft, wird eine neue
        #: Äusserung als Dazwischenreden behandelt und nicht als zweite Frage.
        self._laufende: asyncio.Task | None = None
        #: Die Stimme des laufenden Zuges. Zum Abwürgen beim Dazwischenreden.
        self._stimme: ai_tts_elevenlabs.Stimme | None = None
        #: Vorschläge, die auf ein gesprochenes Ja warten.
        self._offene_vorschlaege: list[str] = []

    async def fuehren(self) -> Lage:
        """Die Sitzung, bis der Browser geht oder die Zeit um ist."""
        await self._zustand_melden(ZUSTAND_BEREIT, erstmalig=True)
        ende = time.monotonic() + self._hoechstdauer
        try:
            while True:
                rest = ende - time.monotonic()
                if rest <= 0:
                    self._lage.abgelaufen = True
                    await self._senden({"art": "abgelaufen"})
                    break
                try:
                    nachricht = await asyncio.wait_for(
                        self._browser.receive(), timeout=rest
                    )
                except asyncio.TimeoutError:
                    continue
                if nachricht.get("type") == "websocket.disconnect":
                    break
                if not await self._rahmen(nachricht):
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await self._abwuergen()
        return self._lage

    # ── vom Browser ───────────────────────────────────────────────────────

    async def _rahmen(self, nachricht: dict) -> bool:
        """Verarbeitet einen Rahmen. ``False`` heisst: Schluss."""
        roh = nachricht.get("bytes")
        if roh is not None:
            if len(roh) > MAX_TONRAHMEN_BYTES:
                logger.info("Tonrahmen zu gross (%d Bytes), verworfen", len(roh))
                return True
            self._lage.rahmen_hin += 1
            await self._ton(roh)
            return True

        text = nachricht.get("text")
        if text is None:
            return True
        if len(text) > MAX_STEUERRAHMEN_ZEICHEN:
            return True
        try:
            befehl = json.loads(text)
        except (TypeError, ValueError):
            return True
        if isinstance(befehl, dict) and befehl.get("art") == "unterbrechen":
            await self._abwuergen()
            await self._zustand_melden(ZUSTAND_BEREIT)
        return True

    async def _ton(self, pcm: bytes) -> None:
        vorher = self._erkennung.spricht
        aeusserung = self._erkennung.fuettern(pcm)

        if not vorher and self._erkennung.spricht:
            # Der Mensch hat angefangen. Redet die KI gerade, ist alles, was
            # noch kommt, die Antwort auf die vorige Frage — und damit falsch.
            if self._laufende is not None and not self._laufende.done():
                await self._abwuergen()
            await self._zustand_melden(ZUSTAND_HOERT)

        if aeusserung is None:
            return
        self._lage.aeusserungen += 1
        await self._zustand_melden(ZUSTAND_DENKT)
        # Nebenher, damit die Tonpumpe weiterläuft: der Mensch kann während der
        # ganzen Antwort weiterreden, und seine Rahmen müssen währenddessen
        # gelesen werden. Genau das ging im alten Sprachmodus verloren — die
        # Schleife stand still, solange ein Werkzeug arbeitete.
        self._laufende = asyncio.create_task(self._zug(aeusserung))

    # ── ein Zug ───────────────────────────────────────────────────────────

    async def _zug(self, aeusserung: ai_voice_vad.Aeusserung) -> None:
        try:
            wortlaut = await self._abhoeren(aeusserung)
            if wortlaut is None:
                return
            await self._senden({"art": "gehoert", "text": wortlaut})

            if self._offene_vorschlaege:
                if await self._entscheidung(wortlaut):
                    return
            await self._antworten(wortlaut)
        except asyncio.CancelledError:
            raise
        except Exception as fehler:  # pragma: no cover - Netz und Anbieter
            logger.warning(
                "Sprachzug gescheitert user=%s error=%s",
                self._user_id, type(fehler).__name__,
            )
            await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)

    async def _abhoeren(self, aeusserung: ai_voice_vad.Aeusserung) -> str | None:
        from services.ai_provider_service import resolve_api_key

        zugang, schluessel = await asyncio.to_thread(self._zugang_holen, resolve_api_key)
        if zugang is None:
            await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        try:
            return await ai_stt_openrouter.hoeren(
                self._client, provider=zugang, api_key=schluessel, pcm=aeusserung.pcm
            )
        except ai_stt_openrouter.NichtsVerstanden:
            # Kein Fehler, sondern ein Alltagsfall: Husten, Räuspern, ein Wort
            # ins Leere. Stillschweigend zurück auf „bereit" — eine Meldung
            # dafür wäre lauter als das Ereignis.
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None

    def _zugang_holen(self, resolve_api_key) -> tuple[AiProvider | None, str | None]:
        """Zugang und Schlüssel je Zug frisch — die Sitzung hält keine offene DB."""
        with SessionLocal() as db:
            zugang = db.get(AiProvider, self._provider_id)
            if zugang is None or not zugang.enabled:
                return None, None
            schluessel = resolve_api_key(db, zugang, self._user_id)
            # Vom ORM lösen, damit das Objekt die Sitzung überlebt: gelesen
            # werden danach nur noch Felder, die schon geladen sind.
            db.expunge(zugang)
            return zugang, schluessel

    # ── Antworten ─────────────────────────────────────────────────────────

    async def _antworten(self, wortlaut: str) -> None:
        from services import ai_run_service
        from services.ai_stream_service import lauf_beginnen_nebenher

        run_id, fehler = await lauf_beginnen_nebenher(
            user_id=self._user_id,
            conversation_id=self._gespraech_id,
            provider_id=self._provider_id,
            request_id=uuid4(),
            content=wortlaut,
            # Nachdenken bleibt aus. Im Gespräch kostet jede Denkstufe Sekunden
            # vor dem ersten Wort, und ein Mensch, der wartet, hört nur Stille.
            # Wer Tiefe will, tippt — dort steht der Schalter.
            reasoning=False,
            # Der eine Unterschied im Prompt: `ai_prompt.NUR_GETIPPT` fällt weg,
            # `ai_prompt.GESPROCHEN` kommt dazu. Ein Schalter und kein zweiter
            # Prompt — sonst veralten zwei Texte gegeneinander, und zwar
            # lautlos.
            gesprochen=True,
        )
        if run_id is None:
            code = fehler[0] if fehler else "AI_PROVIDER_UNAVAILABLE"
            logger.info("Sprachlauf abgelehnt user=%s code=%s", self._user_id, code)
            await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return

        self._lage.laeufe += 1
        # Dieselbe Reihenfolge wie im Chat-Endpunkt, und sie ist hier alles:
        # Kanal auf, **abonnieren**, dann erst die Arbeit starten. Andersherum
        # wären die ersten Zeichen durch, bevor jemand zuhört — und im
        # Sprachmodus heisst das nicht „ein Stück Text fehlt im Verlauf",
        # sondern „der erste Satz wird nie gesprochen".
        #
        # Angestossen wird über `ai_run_service.lauf_starten` und nicht über
        # `segment_ausfuehren` von Hand: nur dort liegt der Platzhalter, der
        # zwei Segmente desselben Laufs auseinanderhält. Ein direkter Aufruf
        # wäre ein zweiter Schreiber auf demselben Zustand, sobald eine
        # Bestätigung den Lauf gleichzeitig weckt.
        ai_run_broker.eroeffnen(run_id)
        abo = ai_run_broker.abonnieren(run_id)
        ai_run_service.lauf_starten(run_id)
        try:
            await self._lauf_verfolgen(abo)
        finally:
            if abo is not None:
                ai_run_broker.abmelden(run_id, abo[1])

    async def _lauf_verfolgen(self, abo) -> None:
        if abo is None:
            await self._zustand_melden(ZUSTAND_BEREIT)
            return
        _abzug, warteschlange = abo
        filter_ = Belegfilter()
        gesprochen = False

        async with ai_tts_elevenlabs.Stimme(
            adresse=self._stimm_adresse,
            schluessel=self._stimm_schluessel,
            senden=self._ton_senden,
        ) as stimme:
            self._stimme = stimme
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
                    weiter = await self._ereignis(ereignis, daten, stimme, filter_)
                    if ereignis == "delta":
                        gesprochen = True
                    if not weiter:
                        break
                rest, belege = filter_.ausklingen()
                for beleg in belege:
                    await self._senden(beleg)
                if rest.strip():
                    await stimme.sagen(rest)
                    gesprochen = True
                if gesprochen:
                    await self._zustand_melden(ZUSTAND_SPRICHT)
                await stimme.ausklingen()
            finally:
                self._stimme = None
        await self._zustand_melden(ZUSTAND_BEREIT)

    async def _ereignis(
        self,
        ereignis: str,
        daten: dict,
        stimme: ai_tts_elevenlabs.Stimme,
        filter_: Belegfilter,
    ) -> bool:
        """Ein Ereignis des Laufs. ``False`` heisst: dieser Zug ist zu Ende."""
        if ereignis == "delta":
            text = str(daten.get("content") or "")
            sprechbar, belege = filter_.fuettern(text)
            for beleg in belege:
                await self._senden(beleg)
            if sprechbar.strip():
                if self._zustand != ZUSTAND_SPRICHT:
                    await self._zustand_melden(ZUSTAND_SPRICHT)
                await stimme.sagen(sprechbar)
            if text:
                await self._senden({"art": "antworttext", "text": text})
            return True

        if ereignis in ("tool", "tool_plan"):
            # Nur der Name auf den Schirm, nie die Argumente — dieselbe Regel
            # wie zuvor. Vorgelesen wird nichts davon: dass etwas passiert,
            # hört der Mensch daran, dass gerade nichts gesagt wird.
            name = self._werkzeugname(daten)
            if name:
                await self._senden({"art": "werkzeug", "name": name})
            return True

        if ereignis == "question":
            await self._frage_vorlesen(daten, stimme)
            return False

        if ereignis == "proposal":
            await self._vorschlag_merken(daten)
            return True

        if ereignis == "action":
            # Autonom ausgeführt. Es gibt nichts zu fragen — der Antworttext
            # sagt ohnehin, was geschehen ist.
            return True

        if ereignis == "error":
            await self._senden({"art": "stoerung"})
            return False

        if ereignis == "done":
            return True

        if ereignis == "run":
            return str(daten.get("status") or "") == "running"

        return True

    @staticmethod
    def _werkzeugname(daten: dict) -> str:
        name = daten.get("name")
        if isinstance(name, str) and name:
            return name
        aufrufe = daten.get("aufrufe")
        if isinstance(aufrufe, list) and aufrufe:
            erster = aufrufe[0]
            if isinstance(erster, dict) and isinstance(erster.get("name"), str):
                return erster["name"]
        return ""

    # ── Rückfrage und Bestätigung ─────────────────────────────────────────

    async def _frage_vorlesen(
        self, daten: dict, stimme: ai_tts_elevenlabs.Stimme
    ) -> None:
        """Eine `ask_user`-Karte als gesprochene Frage.

        Dieselbe Frage, dasselbe Werkzeug, dieselbe Nutzlast — nur gesprochen
        statt gedruckt. Beantwortet wird sie wie im Chat: mit der nächsten
        Nachricht. Der Mensch spricht sie einfach, statt auf einen Knopf zu
        tippen, und `lauf_beginnen` behandelt sie als Antwort auf die Rückfrage
        (es erbt dafür sogar die Schleifensignaturen des Vorgängers).
        """
        frage = str(daten.get("question") or "").strip()
        moeglichkeiten = daten.get("options")
        teile = [frage] if frage else []
        if isinstance(moeglichkeiten, list):
            beschriftungen = [
                str(eintrag["label"]).strip()
                for eintrag in moeglichkeiten
                if isinstance(eintrag, dict) and str(eintrag.get("label") or "").strip()
            ]
            if beschriftungen:
                # Als Aufzählung im Satz und nicht als Liste: „A, B oder C?".
                # Eine vorgelesene Nummerierung („Erstens …") klingt nach
                # Formular und lädt nicht zum Antworten ein.
                if len(beschriftungen) == 1:
                    teile.append(beschriftungen[0] + "?")
                else:
                    teile.append(
                        ", ".join(beschriftungen[:-1]) + " oder " + beschriftungen[-1] + "?"
                    )
        text = " ".join(teile).strip()
        if not text:
            return
        await self._zustand_melden(ZUSTAND_SPRICHT)
        await self._senden({"art": "antworttext", "text": text})
        await stimme.sagen(text)

    async def _vorschlag_merken(self, daten: dict) -> None:
        """Ein Vorschlag wartet auf ein Ja.

        Vorgelesen wird er **nicht** hier. Das Modell hat im selben Zug bereits
        gesagt, was es vorhat — der Prompt verlangt das —, und die Karte
        zusätzlich vorzulesen hiesse, dasselbe zweimal zu hören. Gemerkt wird
        nur, dass die nächste Äusserung eine Entscheidung sein kann.
        """
        kennung = daten.get("id")
        if isinstance(kennung, str) and kennung:
            self._offene_vorschlaege.append(kennung)
        # Die Karte geht trotzdem an den Browser: wer hinsieht, soll sie sehen,
        # und sie ist der Weg für alles, was per Sprache nicht bestätigt werden
        # darf (Löschen, Backups, Rechte).
        await self._senden({"art": "vorschlag", "vorschlag": daten})

    async def _entscheidung(self, wortlaut: str) -> bool:
        """Prüft, ob diese Äusserung über die offenen Vorschläge entscheidet.

        ``True`` heisst: erledigt, es beginnt kein neuer Lauf. ``False`` heisst:
        das war keine Entscheidung, sondern etwas Neues — dann wird der Wortlaut
        als gewöhnliche Nachricht behandelt, und `vorgaenger_abloesen` räumt die
        offenen Vorschläge weg wie im Chat auch.
        """
        offene = self._offene_vorschlaege
        if ist_zustimmung(wortlaut):
            self._offene_vorschlaege = []
            await self._zustand_melden(ZUSTAND_DENKT)
            erledigt = await asyncio.to_thread(self._ausfuehren, offene)
            if not erledigt:
                await self._senden({"art": "stoerung"})
                await self._zustand_melden(ZUSTAND_BEREIT)
            return True
        if ist_ablehnung(wortlaut):
            # Nichts an der Datenbank. Ein abgelehnter Vorschlag verhält sich
            # genau wie eine Karte, die niemand anklickt: er bleibt stehen, bis
            # er abläuft oder die nächste Nachricht ihn ablöst
            # (`vorgaenger_abloesen`). Einen eigenen Ablehnungsweg gibt es im
            # Chat nicht — hier einen zu erfinden hiesse, im Sprachmodus einen
            # Zustand herstellen zu können, den der Chat nicht kennt.
            self._offene_vorschlaege = []
            await self._zustand_melden(ZUSTAND_BEREIT)
            return True
        self._offene_vorschlaege = []
        return False

    def _ausfuehren(self, kennungen: list[str]) -> bool:
        """Bestätigen und ausführen — **derselbe** Weg wie der Klick auf die Karte.

        `confirm_proposal` prüft die Rechte erneut und erzeugt den Einmal-Token,
        `execute_proposal` prüft ein drittes Mal, nimmt den Server-Mutex und
        entwertet den Token atomar. Die gesprochene Zustimmung ersetzt genau
        einen Schritt — den Klick — und keinen einzigen der Schutzmechanismen.

        Was per Sprache nicht bestätigt werden darf, prallt hier nicht ab,
        sondern schon davor: solche Werkzeuge tragen ``requires_confirmation``
        und stehen gar nicht erst in der Werkzeugmenge des Sprachmodus.
        """
        from services import ai_action_errors, ai_proposal_service, ai_run_service

        erfolg = False
        with SessionLocal() as db:
            benutzer = db.get(User, self._user_id)
            if benutzer is None:
                return False
            for kennung in kennungen:
                try:
                    vorschlag = ai_proposal_service.owned_proposal(db, kennung, benutzer)
                    if vorschlag is None:
                        # Nicht seiner. `confirm_proposal` würde das gleich
                        # darauf ebenfalls feststellen und ablehnen — aber ein
                        # Aufruf, von dem hier schon feststeht, dass er
                        # scheitern muss, liest sich wie einer, der gelingen
                        # könnte. Die Kennung stammt aus einem Ereignis dieser
                        # Sitzung; steht sie trotzdem nicht in seinem Bestand,
                        # ist das kein Alltagsfall, sondern einer fürs
                        # Protokoll.
                        logger.info(
                            "Gesprochene Bestaetigung fuer fremden Vorschlag user=%s",
                            self._user_id,
                        )
                        continue
                    lauf_id = getattr(vorschlag, "run_id", None)
                    _, token = ai_proposal_service.confirm_proposal(
                        db, proposal_id=kennung, user=benutzer
                    )
                    ai_proposal_service.execute_proposal(
                        db, proposal_id=kennung, user=benutzer, confirmation_token=token
                    )
                    db.commit()
                    erfolg = True
                    if lauf_id:
                        with contextlib.suppress(Exception):
                            ai_run_service.lauf_fortsetzen(db, run_id=lauf_id)
                            db.commit()
                except ai_action_errors.AiActionStateError as fehler:
                    db.rollback()
                    logger.info(
                        "Gesprochene Bestaetigung abgewiesen user=%s code=%s",
                        self._user_id, fehler.args[0] if fehler.args else "?",
                    )
                except Exception:
                    db.rollback()
                    logger.warning(
                        "Gesprochene Bestaetigung gescheitert user=%s", self._user_id
                    )
        return erfolg

    # ── zum Browser ───────────────────────────────────────────────────────

    async def _ton_senden(self, pcm: bytes) -> None:
        if self._browser.client_state is not WebSocketState.CONNECTED:
            return
        with contextlib.suppress(Exception):
            await self._browser.send_bytes(pcm)
            self._lage.rahmen_zurueck += 1

    async def _senden(self, nutzlast: dict) -> None:
        if self._browser.client_state is not WebSocketState.CONNECTED:
            return
        with contextlib.suppress(Exception):
            await self._browser.send_text(json.dumps(nutzlast))

    async def _zustand_melden(self, zustand: str, *, erstmalig: bool = False) -> None:
        if zustand == self._zustand and not erstmalig:
            return
        self._zustand = zustand
        if erstmalig:
            await self._senden({"art": "bereit"})
            return
        await self._senden({"art": "zustand", "zustand": zustand})

    async def _abwuergen(self) -> None:
        """Den laufenden Zug abbrechen — der Mensch redet dazwischen."""
        stimme = self._stimme
        if stimme is not None:
            with contextlib.suppress(Exception):
                await stimme.schliessen()
        aufgabe = self._laufende
        self._laufende = None
        if aufgabe is not None and not aufgabe.done():
            aufgabe.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await aufgabe


def gespraechstext(rolle: str, inhalt: str) -> str:
    """Redigierter Text für das Protokoll. Nie roher Fremdtext im Log."""
    return f"{rolle}: {redact_sensitive_text(inhalt)[:200]}"
