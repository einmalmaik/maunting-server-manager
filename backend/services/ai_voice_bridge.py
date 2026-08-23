"""Die Brücke: gesprochene Rede geht denselben Weg wie getippte.

Der Sprachmodus ist seit dem 16.08.2026 **kein eigenes Modell mehr**. Er ist der
gewöhnliche Chatlauf mit zwei Wandlern davor und dahinter:

.. code-block:: text

    Mikrofon → Pausenerkennung → Gehör → derselbe AiRun wie im Chat
                                              ↓
    Lautsprecher ←──── Stimme ←──── derselbe Antworttext

Was das kostet, ist eine Sprechpausenerkennung (`ai_voice_vad`), die weniger
klug ist als das weggefallene ``semantic_vad``. Was es spart, ist ein zweiter Werkzeuglauf, eine
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
sind es jetzt **zwei je Zug** — eine für die Abschrift
(`_abschrift_verbuchen`) und eine für den Lauf. Ein Rollenlimit
``requests_per_minute`` von fünf zerreisst damit ein Gespräch, das vorher
durchlief. Ohne gesetztes Limit (``None``, die Vorgabe) passiert nichts.
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

from config import settings
from database import SessionLocal
from models import AiConversation, AiProvider, AiRun, User
from services import (
    ai_run_broker,
    ai_stt,
    ai_tts,
    ai_voice_vad,
)
# Kein neues Paket: `ai_stt` oben importiert den Adapter bereits hart.
from services.openai_compatible_adapter import StreamUsage

logger = logging.getLogger(__name__)


# ── Was der Browser zu sehen bekommt ──────────────────────────────────────
#
# Die **Rahmenformate** sind unverändert, und darauf kommt es an: Binärrahmen
# sind Ton (PCM16, 24 kHz, mono), Textrahmen sind JSON und beschreiben, was
# gerade passiert. Die Liste der Ereignisse hat sich mit dem Umbau dagegen
# geändert — `kontingent` ist weg, weil es keine eigene Sprachkontingentrechnung
# mehr gibt, und `vorschlag` ist dazugekommen. Wer hier eines hinzufügt, muss in
# `useSprachsitzung.ts` einen `case` dafür anlegen; unbekannte fallen dort
# stillschweigend durch.

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
#:
#: Deshalb **abgeleitet** und nicht abgeschrieben: `access_token_expire_minutes`
#: ist über ``MSM_ACCESS_TOKEN_EXPIRE_MINUTES`` einstellbar. Hier stand die 15
#: ein zweites Mal fest im Code — wer sie auf fünf senkt, um Tokens schneller
#: rotieren zu lassen, hätte eine Sprachsitzung bekommen, die zehn Minuten
#: länger spricht als der Token gilt. Genau der Fall, den der Absatz darüber
#: ausschliessen soll.
MAX_SITZUNGSSEKUNDEN = settings.access_token_expire_minutes * 60

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

#: Wie oft der Zusteller nach offenen Meldungen sieht. Ein Poll und kein Abo,
#: mit Absicht: Meldestelle wie Broker sind ohnehin prozesslokal (Doku §10),
#: und die eine gezählte Abfrage alle paar Sekunden je **offener Sprachsitzung**
#: ist billiger als ein Pub/Sub über die Threadgrenze — `melden()` läuft mal
#: auf der Schleife, mal im Threadpool, und genau diese Weiche hat bei
#: `_broker_melden` schon einmal einen geschlossenen Loop getroffen. Kürzer
#: als der 60-s-Chat-Takt, damit die Stimme die Zustellung gewinnt, solange
#: die Sitzung offen ist — der Chat bleibt der Kanal, der immer trägt.
ZUSTELL_TAKT_S = 3.0


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
        if not self._im_block and not self._puffer.lstrip().startswith("`"):
            treffer = re.search(r"([.!?…:;])\s+", self._puffer)
            if treffer is not None and treffer.end() >= 10:
                satz = self._puffer[: treffer.end()]
                self._puffer = self._puffer[treffer.end() :]
                gesprochen.append(satz)
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
        stt_provider_id: int | None = None,
        stimm_kind: str,
        stimm_adresse: str,
        stimm_schluessel: str,
        http_client: httpx.AsyncClient,
        hoechstdauer: float = MAX_SITZUNGSSEKUNDEN,
        herkunft: str = "panel",
        familie: str | None = None,
    ) -> None:
        self._browser = browser
        self._user_id = user_id
        self._gespraech_id = conversation_id
        self._chat_provider_id = chat_provider_id
        self._stt_provider_id = stt_provider_id if stt_provider_id is not None else chat_provider_id
        #: Wer vorliest. Einmal nachgeschlagen und nicht je Zug: welcher
        #: Sprachdienst gilt, steht für die Dauer der Sitzung fest, und ein
        #: Wechsel mitten im Gespräch wäre keine Funktion, sondern ein Fehler.
        #: Die Brücke nennt ihn nirgends beim Namen — sie kennt nur `ai_tts`.
        self._stimmweg = ai_tts.stimmweg(stimm_kind)
        self._stimm_adresse = stimm_adresse
        self._stimm_schluessel = stimm_schluessel
        self._client = http_client
        self._hoechstdauer = hoechstdauer
        #: Panel oder Desktop — entscheidet die Werkzeugmenge der Laeufe
        #: (`ai_tool_registry.herkunft_schnitt`), nie Rechte. Kommt aus dem
        #: Handshake-Token (`dependencies.ws_session_herkunft`) und steht fuer
        #: die ganze Sitzung fest, wie alles andere an dieser Verbindung.
        self._herkunft = herkunft
        #: **Welcher** Rechner spricht — die Refresh-Familie der Sitzung, aus
        #: demselben Handshake-Token (`dependencies.ws_session_familie`). Die
        #: Herkunft sagt „App oder Browser", diese Kennung sagt „welche App".
        #: Ohne sie trug jeder gesprochene Lauf `familie=None`, und ein „schau
        #: auf meinen Bildschirm" landete bei irgendeinem gekoppelten Gerät —
        #: obwohl feststeht, in welches Mikrofon gesprochen wurde. ``None`` für
        #: den Browser und für Token von vor dem Anspruch.
        self._familie = familie

        self._erkennung = ai_voice_vad.Pausenerkennung()
        self._lage = Lage()
        self._zustand = ""
        #: Der Lauf, der gerade antwortet. Solange einer läuft, wird eine neue
        #: Äusserung als Dazwischenreden behandelt und nicht als zweite Frage.
        self._laufende: asyncio.Task | None = None
        #: Die Stimme des laufenden Zuges. Zum Abwürgen beim Dazwischenreden.
        self._stimme: ai_tts.Stimmsitzung | None = None
        #: Vorschläge, die auf ein gesprochenes Ja warten.
        self._offene_vorschlaege: list[str] = []
        #: Der Zusteller: spricht Worker-Meldungen, sobald das Gespräch Ruhe
        #: hat. Lebt neben der Sitzungsschleife, weil die in
        #: `browser.receive()` blockiert und nur bei Browser-Rahmen aufwacht —
        #: eine Meldung käme sonst erst zu Wort, wenn der Mensch etwas sagt.
        self._zusteller: asyncio.Task | None = None

    async def fuehren(self) -> Lage:
        """Die Sitzung, bis der Browser geht oder die Zeit um ist."""
        await self._zustand_melden(ZUSTAND_BEREIT, erstmalig=True)
        self._zusteller = asyncio.create_task(self._meldungen_zustellen())
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
            # Erst der Zusteller, dann der laufende Zug: der Zusteller wartet
            # womöglich gerade auf eine Lieferung in `self._laufende`, und
            # andersherum spräche er nach dem Abwürgen munter weiter.
            zusteller = self._zusteller
            self._zusteller = None
            if zusteller is not None:
                zusteller.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await zusteller
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
        lauf_laeuft = self._laufende is not None and not self._laufende.done()

        if lauf_laeuft:
            # Die KI arbeitet oder spricht gerade. Nicht jede Regung im Raum
            # darf sie abwürgen: eine Tür, ein Huster, eine fremde Stimme im
            # Hintergrund lösten die Rede-Kante genauso aus wie der Mensch —
            # die Antwort war weg, und das Geräusch selbst wurde kurz darauf
            # als Störung verworfen. Abgewürgt wird deshalb erst, wenn die
            # laufende Äusserung dieselbe Messlatte reisst, an der auch der
            # Huster-Filter misst: wer wirklich dazwischenredet, kommt durch —
            # nur um die Mindestrede später. `_abwuergen` leert `_laufende`,
            # darum feuert dieses Tor je Äusserung höchstens einmal.
            if self._erkennung.rede_nachgewiesen:
                await self._abwuergen()
                await self._zustand_melden(ZUSTAND_HOERT)
        elif not vorher and self._erkennung.spricht:
            # Ohne laufende Antwort gibt es nichts zu schützen — „hört zu"
            # kommt sofort mit der Rede-Kante, damit die Blase mitgeht.
            await self._zustand_melden(ZUSTAND_HOERT)
        elif (
            vorher
            and not self._erkennung.spricht
            and aeusserung is None
            and self._zustand == ZUSTAND_HOERT
        ):
            # Als Störgeräusch verworfen. Ohne diese Meldung bliebe die
            # Anzeige auf „hört zu" stehen — kein Zug dreht sie weiter, und
            # der Zusteller wartet auf „bereit".
            await self._zustand_melden(ZUSTAND_BEREIT)

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
            # Der Klassenname allein war eine Sackgasse: „AiProviderRequestError"
            # steht gleichermassen fuer einen abgelaufenen Schluessel, ein
            # falsch geschriebenes Modell und einen Anbieter, der gerade nicht
            # mag. Genau dafuer traegt dieser Fehler `code` und `detail`, und
            # beide sind ausdruecklich fuer die Ausgabe gebaut: der Code ist
            # eine feste Kennung, das Detail die **redigierte**, einzeilige und
            # gekuerzte Meldung des Anbieters (`_error_detail`). Sie hier
            # wegzuwerfen hiess, den Betreiber mit dem Wort „Fehler" allein zu
            # lassen.
            kennung = getattr(fehler, "code", None)
            einzelheit = getattr(fehler, "detail", None)
            logger.warning(
                "Sprachzug gescheitert user=%s error=%s code=%s detail=%s",
                self._user_id, type(fehler).__name__, kennung or "-", einzelheit or "-",
            )
            # Ein erschoepftes Kontingent ist keine Panne, sondern eine Grenze
            # — und sie trifft den Sprachmodus haerter als den Chat, weil jeder
            # Zug **zwei** Buchungen kostet (Abschrift + Lauf, siehe
            # Modulkopf). `grund` laesst den Browser das unterscheiden: „warte
            # eine Minute" ist eine andere Auskunft als „etwas ist kaputt".
            from services.ai_usage_service import AiQuotaExceeded

            if isinstance(fehler, AiQuotaExceeded):
                await self._senden({"art": "stoerung", "grund": "kontingent"})
            else:
                await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)

    async def _abhoeren(self, aeusserung: ai_voice_vad.Aeusserung) -> str | None:
        from services.ai_provider_service import resolve_api_key

        zugang, schluessel = await asyncio.to_thread(
            self._zugang_holen, resolve_api_key, self._stt_provider_id
        )
        if zugang is None:
            await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        messwerte = StreamUsage()
        try:
            wortlaut = await ai_stt.hoeren(
                self._client, provider=zugang, api_key=schluessel, pcm=aeusserung.pcm,
                usage=messwerte,
            )
        except ai_stt.NichtsVerstanden:
            # Kein Fehler, sondern ein Alltagsfall: Husten, Räuspern, ein Wort
            # ins Leere. Stillschweigend zurück auf „bereit" — eine Meldung
            # dafür wäre lauter als das Ereignis.
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        if not await asyncio.to_thread(self._abschrift_verbuchen, zugang, messwerte, wortlaut):
            # Das Kontingent ist erschöpft. Die Äusserung fällt weg, aber der
            # Mensch erfährt es — mit `grund`, denn „warte eine Minute" ist
            # eine andere Auskunft als „etwas ist kaputt".
            await self._senden({"art": "stoerung", "grund": "kontingent"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        return wortlaut

    def _abschrift_verbuchen(
        self, zugang: AiProvider, messwerte: StreamUsage, wortlaut: str
    ) -> bool:
        """Bucht das Zuhören als eigenen Verbrauch — **nach** der Abschrift.

        Die Reihenfolge ist eine Entscheidung und keine Nachlässigkeit: eine
        Reservierung **vor** dem Hören würfe die Äusserung weg, bevor irgendwer
        weiss, was gesagt wurde — der Sprechende bekäme nicht einmal die
        Auskunft, dass sein Kontingent erschöpft ist. Deshalb wird erst gehört
        und dann gebucht; die Buchung zählt trotzdem voll gegen Tages-, Wochen-
        und Monatsgrenzen, nur um eine Äusserung versetzt.

        ``False`` heisst ausschliesslich: das Kontingent ist erschöpft, dieser
        Zug endet hier. Jeder **andere** Buchungsfehler lässt das Gespräch
        weiterlaufen — die Anbieterkosten sind längst entstanden, und den
        Sprechenden für einen Buchhaltungsfehler zu bestrafen zöge die falsche
        Konsequenz. Er landet im Protokoll statt auf dem Ohr.
        """
        from services import ai_usage_service
        from services.ai_provider_service import estimate_cost_microunits

        geschaetzt = messwerte.total_tokens
        if geschaetzt is None:
            # Der Anbieter schweigt: dieselbe Näherung wie überall sonst,
            # Zeichen durch vier. Der Tonanteil bleibt dabei ungezählt — MSM
            # erfindet keine Zahl für etwas, das der Anbieter nicht meldet.
            geschaetzt = max(1, len(wortlaut) // 4)
        geschaetzt = min(geschaetzt, ai_usage_service.TOKEN_LIMIT_MAX)
        with SessionLocal() as db:
            benutzer = db.get(User, self._user_id)
            if benutzer is None:
                # Kein Kontingentfall: das Konto ist mitten in der Sitzung
                # verschwunden. `False` hiesse „warte eine Minute" — eine
                # falsche Auskunft. Wie jeder andere Buchungsfehler: ins
                # Protokoll, das Gespraech laeuft weiter; beendet wird die
                # Sitzung ohnehin an der naechsten Stelle, die den Benutzer
                # wirklich braucht (der Lauf selbst).
                logger.warning(
                    "Abschrift nicht verbucht user=%s: Benutzer existiert nicht mehr",
                    self._user_id,
                )
                return True
            try:
                ereignis = ai_usage_service.reserve_ai_usage(
                    db,
                    benutzer,
                    request_id=uuid4(),
                    estimated_tokens=geschaetzt,
                    estimated_cost_microunits=estimate_cost_microunits(zugang, geschaetzt),
                    provider_id=zugang.id,
                    model=zugang.transcription_model,
                )
                # Dieselbe Abrechnung wie im Chat und in der Verdichtung: was
                # der Anbieter meldet, sticht die Schätzung.
                tokens, kosten, herkunft = ai_usage_service.abrechnung(
                    messwerte,
                    reserved_tokens=ereignis.reserved_tokens,
                    estimated_actual_tokens=geschaetzt,
                    token_price_micro_usd_per_million=(
                        zugang.token_price_micro_usd_per_million
                    ),
                )
                ai_usage_service.complete_ai_usage(
                    db, ereignis,
                    actual_tokens=tokens,
                    actual_cost_microunits=kosten,
                    aufschluesselung=messwerte,
                    cost_source=herkunft,
                )
                db.commit()
            except ai_usage_service.AiQuotaExceeded as grenze:
                db.rollback()
                logger.info(
                    "Abschrift ohne Kontingent user=%s grund=%s",
                    self._user_id, grenze.reason,
                )
                return False
            except Exception:
                db.rollback()
                logger.warning(
                    "Abschrift nicht verbucht user=%s", self._user_id, exc_info=True
                )
        return True

    def _zugang_holen(self, resolve_api_key, provider_id: int) -> tuple[AiProvider | None, str | None]:
        """Zugang und Schlüssel je Zug frisch — die Sitzung hält keine offene DB."""
        with SessionLocal() as db:
            zugang = db.get(AiProvider, provider_id)
            if zugang is None or not zugang.enabled:
                return None, None
            schluessel = resolve_api_key(db, zugang, self._user_id)
            # Vom ORM lösen, damit das Objekt die Sitzung überlebt: gelesen
            # werden danach nur noch Felder, die schon geladen sind.
            db.expunge(zugang)
            return zugang, schluessel

    # ── Antworten ─────────────────────────────────────────────────────────

    async def _denkwahl(self) -> tuple[bool, str | None]:
        """„Nicht nachdenken" — in der Mundart des Modells, nicht als Wunsch.

        Hier stand hart ``reasoning=False`` ohne Stufe. Bei Modellen mit
        Denkzwang setzt das nichts durch: der Anbieter nimmt seine Vorgabe
        (meist medium), und der Mensch hört Sekunden Stille vor dem ersten
        Wort — genau das, was der Kommentar hier auszuschließen versprach.
        `ai_reasoning.aus_fuer` kennt die Ausnahme und schickt bei Denkzwang
        die **flachste** Stufe hinaus, wie Verdichtung, Mail und Diktat es
        längst tun.

        Je Zug frisch aufgelöst, wie `_abhoeren` seinen Zugang: der Katalog
        antwortet aus dem Zwischenspeicher, das kostet nichts. Ein Cache über
        die Sitzung stand hier kurz — und hätte einen schweigenden Katalog
        (Anbieter eine Minute down) als „dieses Modell denkt nicht" für bis
        zu einer Stunde festgeschrieben.
        """
        from services import ai_reasoning
        from services.ai_provider_service import resolve_api_key

        zugang, schluessel = await asyncio.to_thread(
            self._zugang_holen, resolve_api_key, self._chat_provider_id
        )
        if zugang is None:
            return (False, None)
        try:
            return await ai_reasoning.aus_fuer(self._client, zugang, api_key=schluessel)
        except Exception:
            # Der Katalog ist eine Zusatzauskunft. Scheitert er, geht der
            # blosse Schalter hinaus wie vor diesem Fix — ein Denkzwang-Modell
            # denkt dann in seiner Vorgabe, aber das Gespraech laeuft. Ein
            # Katalogfehler darf nie Stille sein.
            return (False, None)

    async def _antworten(self, wortlaut: str) -> None:
        from services import ai_run_service
        from services.ai_stream_service import lauf_beginnen_nebenher

        # Nachdenken bleibt aus — durchgesetzt statt gewünscht (`_denkwahl`).
        # Im Gespräch kostet jede Denkstufe Sekunden vor dem ersten Wort, und
        # ein Mensch, der wartet, hört nur Stille. Wer Tiefe will, tippt.
        denken, denkstufe = await self._denkwahl()
        run_id, fehler = await lauf_beginnen_nebenher(
            user_id=self._user_id,
            conversation_id=self._gespraech_id,
            provider_id=self._chat_provider_id,
            request_id=uuid4(),
            content=wortlaut,
            reasoning=denken,
            reasoning_effort=denkstufe,
            # Der eine Unterschied im Prompt: `ai_prompt.NUR_GETIPPT` fällt weg,
            # `ai_prompt.GESPROCHEN` kommt dazu. Ein Schalter und kein zweiter
            # Prompt — sonst veralten zwei Texte gegeneinander, und zwar
            # lautlos.
            gesprochen=True,
            # Seit dem 21.08.2026 kommt die App auch hier an: ihr Token liegt
            # als Subprotokoll im Handshake, und die Herkunft daraus gilt fuer
            # jeden Lauf dieser Sitzung. Ein Browser bleibt "panel".
            herkunft=self._herkunft,
            # Und **welcher** Rechner. Der Sprachweg ist der Hauptweg der App:
            # „schau auf meinen Bildschirm" und „nimm mal die Maus" kommen
            # überwiegend gesprochen an. Ohne diese Zeile trug der Lauf
            # `familie=None`, und sein Auftrag war wieder für jedes gekoppelte
            # Gerät abholbar (`desktop_job_service.naechster`).
            familie=self._familie,
        )
        if run_id is None:
            code = fehler[0] if fehler else "AI_PROVIDER_UNAVAILABLE"
            logger.info("Sprachlauf abgelehnt user=%s code=%s", self._user_id, code)
            # Ein erschoepftes Kontingent kommt hier als **Rueckgabewert** an,
            # nicht als Ausnahme: `lauf_beginnen_nebenher` faengt
            # `AiQuotaExceeded` selbst und liefert `(None, ("AI_QUOTA_…", …))`.
            # Genau diese zweite Buchung des Zugs (die erste ist die Abschrift)
            # trifft ein `requests_per_minute`-Limit zuerst — ohne den `grund`
            # hoerte der Sprechende „etwas ist kaputt", wo „warte eine Minute"
            # die Auskunft ist.
            if code.startswith("AI_QUOTA_"):
                await self._senden({"art": "stoerung", "grund": "kontingent"})
            else:
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

        async with self._stimmweg.Stimme(
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
        stimme: ai_tts.Stimmsitzung,
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
        self, daten: dict, stimme: ai_tts.Stimmsitzung
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
        # Die Karte geht trotzdem an den Browser, aber ohne Knopf: sie sagt,
        # *was* gleich passiert — welcher Server, welche Aktion —, und das ist
        # gesprochen schwer zu behalten. Entschieden wird ausschliesslich per
        # Stimme; ein zweiter Weg neben dem gesprochenen Ja waere ein zweiter
        # Zustand, den der Sprachmodus dann pflegen muesste.
        await self._senden({"art": "vorschlag", "vorschlag": daten})

    async def _entscheidung(self, wortlaut: str) -> bool:
        """Prüft, ob diese Äusserung über die offenen Vorschläge entscheidet.

        ``True`` heisst: erledigt, es beginnt kein neuer Lauf. ``False`` heisst:
        das war keine Entscheidung, sondern etwas Neues — dann wird der Wortlaut
        als gewöhnliche Nachricht behandelt.

        Weggeräumt wird dabei nur **diese Liste hier**, also das Wissen der
        Brücke, welche Kennungen ein gesprochenes Ja gerade meinen könnte. Der
        Vorschlag selbst bleibt in der Datenbank ausführbar, bis seine Frist
        abläuft — genau wie eine Karte im Chat, die niemand anklickt.
        `vorgaenger_abloesen` beendet den alten **Lauf** und sagt dazu
        ausdrücklich, dass der Vorschlag davon unberührt bleibt.
        """
        offene = self._offene_vorschlaege
        if ist_zustimmung(wortlaut):
            self._offene_vorschlaege = []
            # Ein Ja meint **einen** Vorschlag, nicht alle. Der Browser zeigt
            # nur die zuletzt geschickte Karte (`useSprachsitzung.ts` hält
            # genau einen `vorschlag`) — ein Ja auf alle offenen anzuwenden
            # hiesse, Dinge auszuführen, die der Mensch nie gesehen hat.
            # Die übrigen verhalten sich wie beim Nein: die Kennung wird
            # vergessen, der Vorschlag bleibt in der Datenbank ausführbar, bis
            # seine Frist abläuft. Und es wird angesagt, damit niemand glaubt,
            # alles sei bestätigt worden.
            letzter, verworfene = offene[-1], offene[:-1]
            if verworfene:
                await self._senden({
                    "art": "antworttext",
                    "text": (
                        f"Bestätigt wurde nur der zuletzt gezeigte Vorschlag; "
                        f"{len(verworfene)} weitere wurden verworfen."
                    ),
                })
            await self._zustand_melden(ZUSTAND_DENKT)
            erledigt, lauf_id = await asyncio.to_thread(self._ausfuehren, letzter)
            if not erledigt:
                await self._senden({"art": "stoerung"})
                await self._zustand_melden(ZUSTAND_BEREIT)
                return True
            await self._fortsetzung_verfolgen(lauf_id)
            return True
        if ist_ablehnung(wortlaut):
            # Nichts an der Datenbank. Ein abgelehnter Vorschlag verhält sich
            # genau wie eine Karte, die niemand anklickt: er bleibt ausführbar,
            # bis seine Frist abläuft. Vergessen wird nur die Kennung hier.
            # Einen eigenen Ablehnungsweg gibt es im Chat nicht — hier einen zu
            # erfinden hiesse, im Sprachmodus einen Zustand herstellen zu
            # können, den der Chat nicht kennt.
            self._offene_vorschlaege = []
            await self._zustand_melden(ZUSTAND_BEREIT)
            return True
        self._offene_vorschlaege = []
        return False

    def _ausfuehren(self, kennung: str) -> tuple[bool, str | None]:
        """Bestätigen und ausführen — **derselbe** Weg wie der Klick auf die Karte.

        `confirm_proposal` prüft die Rechte erneut und erzeugt den Einmal-Token,
        `execute_proposal` prüft ein drittes Mal, nimmt den Server-Mutex und
        entwertet den Token atomar. Die gesprochene Zustimmung ersetzt genau
        einen Schritt — den Klick — und keinen einzigen der Schutzmechanismen.

        Es gibt **keine** Werkzeugmenge, die der Sprachmodus sich vorbehält.
        Er nimmt denselben Katalog wie der Chat, `ALWAYS_CONFIRM_TOOLS`
        eingeschlossen; die frühere Sperre ist am 16.08.2026 auf ausdrückliche
        Anweisung des Betreibers gefallen. Ob ein Vorschlag überhaupt bestätigt
        werden muss, entscheidet unverändert `create_proposal` über
        ``immer_bestaetigen`` — hier wird nur der Klick durch ein Wort ersetzt.

        Zurück kommt neben dem Erfolg der **geweckte Lauf**: `lauf_fortsetzen`
        hat ihn wieder auf „running" gestellt, und der Aufrufer muss sich
        anhängen (`_fortsetzung_verfolgen`), sonst bleibt das Ergebnis stumm.
        ``None`` heisst: es gibt nichts zu verfolgen — der Vorschlag hing an
        keinem Lauf, oder die Fortsetzung kam nicht zustande.
        """
        from services import ai_action_errors, ai_proposal_service, ai_run_service

        with SessionLocal() as db:
            benutzer = db.get(User, self._user_id)
            if benutzer is None:
                return False, None
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
                    return False, None
                lauf_id = getattr(vorschlag, "run_id", None)
                _, token = ai_proposal_service.confirm_proposal(
                    db, proposal_id=kennung, user=benutzer
                )
                ai_proposal_service.execute_proposal(
                    db, proposal_id=kennung, user=benutzer, confirmation_token=token
                )
                db.commit()
                fortgesetzt: str | None = None
                if lauf_id:
                    with contextlib.suppress(Exception):
                        if ai_run_service.lauf_fortsetzen(db, run_id=lauf_id):
                            fortgesetzt = lauf_id
                        db.commit()
                if fortgesetzt:
                    # Ein geweckter **Worker** wird nicht verfolgt: die Stimme
                    # spricht ausschliesslich Gehirn-Ausgaben (Doku §12). Das
                    # Wecken selbst ist richtig und bleibt — sein Ergebnis
                    # kommt als Meldung, und die spricht der Zusteller.
                    lauf = db.get(AiRun, fortgesetzt)
                    fenster = (
                        db.get(AiConversation, lauf.conversation_id)
                        if lauf is not None else None
                    )
                    if fenster is not None and fenster.kind == "worker":
                        fortgesetzt = None
                return True, fortgesetzt
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
        return False, None

    async def _fortsetzung_verfolgen(self, lauf_id: str | None) -> None:
        """Nach dem gesprochenen Ja dem geweckten Lauf zuhören.

        Derselbe Weg wie in `_antworten`, und er fehlte hier: die Bestätigung
        weckte den Lauf, aber niemand hörte ihm zu — das Ergebnis blieb stumm,
        und der Zustand hing für den Rest der Sitzung auf „denkt". Nur
        `eroeffnen` entfällt: das hat `lauf_fortsetzen` schon getan, bevor es
        das Segment plante.
        """
        if lauf_id is None:
            # Nichts zu verfolgen — aber der Zustand muss zurück, sonst zeigt
            # der Browser „denkt" für etwas, das längst erledigt ist.
            await self._zustand_melden(ZUSTAND_BEREIT)
            return
        abo = ai_run_broker.abonnieren(lauf_id)
        try:
            await self._lauf_verfolgen(abo)
        finally:
            if abo is not None:
                ai_run_broker.abmelden(lauf_id, abo[1])

    # ── Der Zusteller: Worker-Meldungen als gesprochener Zwischenruf ───────

    def _ruhe(self) -> bool:
        """Darf die Stimme von sich aus sprechen? Vier Bedingungen, alle vier.

        Der VAD-Zustand „bereit" ersetzt im Sprachmodus die Chat-Ruhe der
        Meldestelle (docs/agentic-framework.md, §4). `erkennung.spricht`
        schliesst die Mikroluecke zwischen VAD-Flanke und Zustandsmeldung,
        die Vorschlagsliste haelt den Zusteller aus dem Fenster zwischen
        Vorschlag und gesprochenem Ja heraus — ein Zwischenruf dort, und das
        naechste „Ja" bestaetigte etwas anderes, als der Mensch meint.
        """
        return (
            self._zustand == ZUSTAND_BEREIT
            and not self._erkennung.spricht
            and (self._laufende is None or self._laufende.done())
            and not self._offene_vorschlaege
        )

    def _meldungen_offen(self) -> bool:
        from services import ai_meldestelle

        with SessionLocal() as db:
            benutzer = db.get(User, self._user_id)
            if benutzer is None or not benutzer.is_active:
                return False
            return bool(ai_meldestelle.offene_meldungen(db, user_id=self._user_id))

    async def _meldungen_zustellen(self) -> None:
        """Die Schleife des Zustellers: nachsehen, Ruhe abwarten, liefern.

        Die Lieferung selbst liegt in `self._laufende` — demselben Feld wie
        ein gewoehnlicher Zug. Nur dieses Feld cancelt `_abwuergen`, und nur
        so bricht Dazwischenreden auch eine laufende Zustellansage ab (§4:
        Barge-in bricht Zwischenmeldungen und Abschlussansagen).
        """
        while True:
            await asyncio.sleep(ZUSTELL_TAKT_S)
            if not self._ruhe():
                continue
            try:
                if not await asyncio.to_thread(self._meldungen_offen):
                    continue
            except Exception:
                continue
            # Zwischen `to_thread` und hier kann der Mensch angefangen haben
            # zu sprechen — noch einmal fragen, dann ohne await starten:
            # zwischen dieser Pruefung und `create_task` liegt kein
            # Haltepunkt, an dem sich die Lage aendern koennte.
            if not self._ruhe():
                continue
            aufgabe = asyncio.create_task(self._meldung_liefern())
            self._laufende = aufgabe
            try:
                await aufgabe
            except asyncio.CancelledError:
                # Zwei Auslöser, zwei Richtungen: hat Barge-in die
                # **Lieferung** gecancelt, lebt der Zusteller weiter und
                # wartet auf die nächste Ruhe. Wurde der **Zusteller selbst**
                # gecancelt (Sitzungsende), muss der Abbruch hinaus — ihn zu
                # schlucken machte die Schleife zum Zombie, und `fuehren()`
                # hinge beim Aufräumen für immer an `await zusteller`.
                if aufgabe.cancelled() and not asyncio.current_task().cancelling():
                    continue
                raise
            except Exception:
                # `_meldung_liefern` fängt seine Fehler selbst; das hier ist
                # nur die Rückversicherung, dass die Schleife weiterlebt.
                pass

    async def _meldung_liefern(self) -> None:
        """Ein Lieferzug: die Meldestelle baut den Gehirn-Lauf, die Bruecke spricht ihn.

        Gesprochen wird nicht der rohe Meldungstext, sondern die Lieferung des
        Gehirns — dieselbe Nachricht, die auch im Chat steht (kein zweiter
        Wortlaut, keine Phrase). ``ruhe_noetig=False``, weil die Chat-Ruhe
        eine offene Sprachsitzung faelschlich blockierte; das Ruhe-Praedikat
        dieser Sitzung ist `_ruhe()` und wurde vom Zusteller geprueft.
        """
        from services import ai_meldestelle

        await self._zustand_melden(ZUSTAND_DENKT)
        try:
            with SessionLocal() as db:
                benutzer = db.get(User, self._user_id)
                if benutzer is None:
                    await self._zustand_melden(ZUSTAND_BEREIT)
                    return
                run = await ai_meldestelle.zustellung_anstossen(
                    db, user=benutzer, ruhe_noetig=False
                )
            if run is None:
                await self._zustand_melden(ZUSTAND_BEREIT)
                return
            self._lage.laeufe += 1
            # `zustellung_anstossen` hat den Lauf ueber `anlauf` bereits
            # eroeffnet und sein Segment geplant — die Aufgabe laeuft aber
            # erst am naechsten Haltepunkt an, und bis hierher gibt es
            # keinen: das Abo kommt nie zu spaet.
            abo = ai_run_broker.abonnieren(run.id)
            try:
                await self._lauf_verfolgen(abo)
            finally:
                if abo is not None:
                    ai_run_broker.abmelden(run.id, abo[1])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Zustellung in der Sprachsitzung gescheitert user=%s", self._user_id)
            await self._zustand_melden(ZUSTAND_BEREIT)

    # ── zum Browser ───────────────────────────────────────────────────────

    async def _ton_senden(self, pcm: bytes) -> None:
        if self._browser.client_state is not WebSocketState.CONNECTED:
            return
        with contextlib.suppress(Exception):
            await self._browser.send_bytes(pcm)
            self._lage.rahmen_zurueck += 1
            if self._lage.rahmen_zurueck == 1:
                logger.info("Erster Tonrahmen (TTS) an Browser gesendet (%d Bytes)", len(pcm))

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
