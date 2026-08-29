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
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import httpx
from fastapi import WebSocket
from starlette.websockets import WebSocketState

from database import SessionLocal
from models import AiProvider, User
from services import (
    ai_tts,
    ai_voice_vad,
)
from services.openai_compatible_adapter import StreamUsage
from services.ai_latency_metrics import metrics
from services.ai_voice import interactions as voice_interactions
from services.ai_voice.prefetch import VoicePrefetch
from services.ai_voice.run_output import VoiceRunOutput
from services.ai_voice import transcription as voice_transcription
from services.ai_voice.session import VoiceSession

logger = logging.getLogger(__name__)

# Reexports für bestehende Router, Tests und interne Aufrufer. Die konkrete
# Sitzungslogik liegt im Paket `services.ai_voice`, der öffentliche Importpfad
# bleibt unverändert.
from services.ai_voice.contracts import (
    LAUF_TIMEOUT,
    Lage,
    MAX_SITZUNGSSEKUNDEN,
    MAX_STEUERRAHMEN_ZEICHEN,
    MAX_TONRAHMEN_BYTES,
    ZUSTELL_TAKT_S,
    ZUSTAND_BEREIT,
    ZUSTAND_DENKT,
    ZUSTAND_HOERT,
    ZUSTAND_SPRICHT,
)
from services.ai_voice.text import (
    Belegfilter,
    _ist_gedanke_abgeschlossen,
    ist_ablehnung,
    ist_zustimmung,
)


# Die Kadenz enthält nur einen numerischen Faktor. Weder Abschriften noch
# PCM-Frames oder TTS-Schlüssel werden sitzungsübergreifend aufbewahrt.
_USER_KADENZ_CACHE: dict[int, float] = {}


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
        # Werden ausschließlich von der verpflichtenden Pipecat-Sitzung
        # gesetzt. Ohne sie kann die Bridge weiterhin isoliert getestet werden;
        # der produktive Weg setzt sie vor dem ersten Browserrahmen.
        self._pipecat_ton_ausgeben: Callable[[bytes], Awaitable[None]] | None = None
        self._pipecat_steuerung_ausgeben: Callable[[dict], Awaitable[None]] | None = None
        self._pipecat_ausgabe_unterbrechen: Callable[[], Awaitable[None]] | None = None
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
        self._prefetch = VoicePrefetch(
            user_id=user_id,
            herkunft=herkunft,
            familie=familie,
            senden=self._senden,
        )

        self._kadenz_faktor = _USER_KADENZ_CACHE.get(user_id, 1.0)
        self._erkennung = ai_voice_vad.Pausenerkennung(kadenz_faktor=self._kadenz_faktor)
        self._lage = Lage()
        self._zustand = ""
        #: Der Lauf, der gerade antwortet. Solange einer läuft, wird eine neue
        #: Äusserung als Dazwischenreden behandelt und nicht als zweite Frage.
        self._laufende: asyncio.Task | None = None
        #: Auch bei einer Sprachunterbrechung weiterlaufende Zuege. Lesen darf
        #: weiterlaufen; schreibende Werkzeuge bleiben weiter an Guardian,
        #: Berechtigungen und Freigaben gebunden. Die Menge wird nur fuer den
        #: ausdruecklichen Abbruch bzw. das Sitzungsende gebraucht.
        self._laeufe: set[asyncio.Task] = set()
        # Ausschließlich vom Server erzeugte IDs. Sie sind die Grenze für einen
        # expliziten Abbruch und stammen nie aus einem Browserrahmen.
        self._voice_run_ids: set[str] = set()
        #: Die Stimme des laufenden Zuges. Zum Abwürgen beim Dazwischenreden.
        self._stimme: ai_tts.Stimmsitzung | None = None
        #: Ein Barge-In schliesst nur den Audiokanal des betroffenen Zuges.
        #: Die Kennung statt eines globalen Schalters ist wichtig: waehrend
        #: der alte Run noch seine Read-Tools beendet, kann der Mensch bereits
        #: einen neuen Zug starten.
        self._unterdrueckte_laeufe: set[asyncio.Task] = set()
        self._run_output = VoiceRunOutput(
            user_id=user_id,
            senden=self._senden,
            zustand_melden=self._zustand_melden,
            ausgabe_aktiv=self._ausgabe_aktiv,
            stimme_oeffnen=self._stimme_oeffnen,
            stimme_setzen=self._stimme_setzen,
            frage_vorlesen=self._frage_vorlesen,
            vorschlag_merken=self._vorschlag_merken,
        )
        #: Vorschläge, die auf ein gesprochenes Ja warten.
        self._offene_vorschlaege: list[str] = []
        #: Turn-Merging & Kadenz-Lernen: unfertige Sätze bei Unterbrechung verschmelzen.
        self._letzte_eingabe: str | None = None
        self._letzte_eingabe_zeit: float = 0.0
        self._letzte_antwort_fertig: bool = True
        self._unterbrochen_fuer_merge: bool = False
        #: Der Zusteller: spricht Worker-Meldungen, sobald das Gespräch Ruhe
        #: hat. Lebt neben der Sitzungsschleife, weil die in
        #: `browser.receive()` blockiert und nur bei Browser-Rahmen aufwacht —
        #: eine Meldung käme sonst erst zu Wort, wenn der Mensch etwas sagt.
        self._zusteller: asyncio.Task | None = None

    def _kadenz_anpassen(self, *, erhoehen: bool) -> None:
        """Passt den gelernten Geduldsfaktor dynamisch an das Sprechtempo an."""
        if erhoehen:
            self._kadenz_faktor = min(2.5, self._kadenz_faktor + 0.15)
        else:
            self._kadenz_faktor = max(0.8, self._kadenz_faktor * 0.99)
        _USER_KADENZ_CACHE[self._user_id] = self._kadenz_faktor
        self._erkennung.kadenz_anpassen(self._kadenz_faktor)

    async def fuehren(self) -> Lage:
        """Delegiert die langlebige Socket-Schleife an die Sitzungsorchestrierung."""
        return await VoiceSession(self).fuehren()

    # ── vom Browser ───────────────────────────────────────────────────────

    async def _rahmen(self, nachricht: dict) -> bool:
        """Kompatibler Test-Hook für die extrahierte Rahmenverarbeitung."""
        return await VoiceSession(self).rahmen(nachricht)

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
                unterbrechung_beginn = time.perf_counter()
                await self._ausgabe_unterbrechen()
                metrics.record(
                    "ai_voice", "barge_in_output_stop",
                    (time.perf_counter() - unterbrechung_beginn) * 1000,
                )
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
        zug = asyncio.create_task(self._zug(aeusserung))
        self._laufende = zug
        self._laeufe.add(zug)
        def _zug_fertig(aufgabe: asyncio.Task) -> None:
            self._laeufe.discard(aufgabe)
            self._unterdrueckte_laeufe.discard(aufgabe)

        zug.add_done_callback(_zug_fertig)

    # ── ein Zug ───────────────────────────────────────────────────────────

    async def _zug(self, aeusserung: ai_voice_vad.Aeusserung) -> None:
        try:
            wortlaut = await self._abhoeren(aeusserung)
            if wortlaut is None:
                return

            jetzt = time.monotonic()
            ist_fortsetzung = (
                self._unterbrochen_fuer_merge
                or (
                    self._letzte_eingabe is not None
                    and (jetzt - self._letzte_eingabe_zeit < 5.0)
                    and not self._letzte_antwort_fertig
                )
            )
            self._unterbrochen_fuer_merge = False

            if ist_fortsetzung and self._letzte_eingabe:
                kombiniert = f"{self._letzte_eingabe} {wortlaut}".strip()
                # Gesprochene Inhalte sind privat. Für die Diagnose reicht die
                # Tatsache, dass die VAD zwei Züge zusammengeführt hat.
                logger.info("Turn-Merge user=%s", self._user_id)
                self._kadenz_anpassen(erhoehen=True)
                wortlaut = kombiniert

            self._letzte_eingabe = wortlaut
            self._letzte_eingabe_zeit = jetzt
            self._letzte_antwort_fertig = False

            # Abschriften bleiben innerhalb des laufenden Turns. Sie sind
            # weder Browser-Frames noch Diagnoseinhalt.
            await self._verarbeite_teil_transkript(wortlaut)

            if not _ist_gedanke_abgeschlossen(wortlaut):
                # Kurze Gnadenfrist für offene Gedanken, damit bei unmittelbarem
                # Weiterreden der Satz noch vor der LLM-Antwort verschmilzt.
                try:
                    await asyncio.sleep(0.8)
                except asyncio.CancelledError:
                    raise

            if self._offene_vorschlaege:
                if await self._entscheidung(wortlaut):
                    self._letzte_antwort_fertig = True
                    return
            await self._antworten(wortlaut)
            self._letzte_antwort_fertig = True
            if _ist_gedanke_abgeschlossen(wortlaut):
                self._kadenz_anpassen(erhoehen=False)
        except asyncio.CancelledError:
            raise
        except Exception as fehler:  # pragma: no cover - Netz und Anbieter
            # Der feste Fehlercode unterscheidet bekannte Ablehnungen. Details
            # eines Anbieterfehlers können jedoch Eingaben oder Schlüsselteile
            # spiegeln und gehören deshalb nicht in Voice-Diagnoselogs.
            kennung = getattr(fehler, "code", None)
            logger.warning(
                "Sprachzug gescheitert user=%s error=%s code=%s",
                self._user_id, type(fehler).__name__, kennung or "-",
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

        ergebnis = await voice_transcription.hoeren(
            client=self._client,
            user_id=self._user_id,
            provider_id=self._stt_provider_id,
            pcm=aeusserung.pcm,
            resolve_api_key=resolve_api_key,
        )
        if ergebnis.grund == "anbieter":
            await self._senden({"art": "stoerung"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        if ergebnis.grund == "unverstanden":
            # Kein Fehler, sondern ein Alltagsfall: Husten, Räuspern, ein Wort
            # ins Leere. Stillschweigend zurück auf „bereit" — eine Meldung
            # dafür wäre lauter als das Ereignis.
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        if ergebnis.grund == "kontingent":
            # Das Kontingent ist erschöpft. Die Äusserung fällt weg, aber der
            # Mensch erfährt es — mit `grund`, denn „warte eine Minute" ist
            # eine andere Auskunft als „etwas ist kaputt".
            await self._senden({"art": "stoerung", "grund": "kontingent"})
            await self._zustand_melden(ZUSTAND_BEREIT)
            return None
        return ergebnis.abschrift.wortlaut if ergebnis.abschrift is not None else None

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
        return voice_transcription.abschrift_verbuchen(
            user_id=self._user_id,
            zugang=zugang,
            messwerte=messwerte,
            wortlaut=wortlaut,
        )

    def _zugang_holen(self, resolve_api_key, provider_id: int) -> tuple[AiProvider | None, str | None]:
        """Zugang und Schlüssel je Zug frisch — die Sitzung hält keine offene DB."""
        return voice_transcription.zugang_holen(
            user_id=self._user_id,
            provider_id=provider_id,
            resolve_api_key=resolve_api_key,
        )

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
            voice_output_checkpoint=self._run_output.checkpoint_verbrauchen(),
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

        await asyncio.to_thread(self._prefetch_sitzung_an_lauf, run_id)
        self._voice_run_ids.add(run_id)

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
        abo = self._run_output.eroeffnen_und_abonnieren(run_id)
        ai_run_service.lauf_starten(run_id)
        try:
            await self._lauf_verfolgen(abo)
        finally:
            self._run_output.abmelden(run_id, abo)

    def _stimme_oeffnen(self):
        return self._stimmweg.Stimme(
            adresse=self._stimm_adresse,
            schluessel=self._stimm_schluessel,
            senden=self._ton_senden,
        )

    def _stimme_setzen(self, stimme: ai_tts.Stimmsitzung | None) -> None:
        self._stimme = stimme

    async def _lauf_verfolgen(self, abo) -> None:
        """Kompatibler Delegationspunkt für bestehende Voice-Tests."""
        await self._run_output.verfolgen(abo)

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
        return voice_interactions.vorschlag_ausfuehren(
            user_id=self._user_id, kennung=kennung
        )

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
        self._voice_run_ids.add(lauf_id)
        abo = self._run_output.abonnieren(lauf_id)
        try:
            await self._lauf_verfolgen(abo)
        finally:
            self._run_output.abmelden(lauf_id, abo)

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
            self._voice_run_ids.add(run.id)
            self._lage.laeufe += 1
            # `zustellung_anstossen` hat den Lauf ueber `anlauf` bereits
            # eroeffnet und sein Segment geplant — die Aufgabe laeuft aber
            # erst am naechsten Haltepunkt an, und bis hierher gibt es
            # keinen: das Abo kommt nie zu spaet.
            abo = self._run_output.abonnieren(run.id)
            try:
                await self._lauf_verfolgen(abo)
            finally:
                self._run_output.abmelden(run.id, abo)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Zustellung in der Sprachsitzung gescheitert user=%s", self._user_id)
            await self._zustand_melden(ZUSTAND_BEREIT)

    # ── zum Browser ───────────────────────────────────────────────────────

    async def _ton_senden(self, pcm: bytes) -> None:
        ausgeben = self._pipecat_ton_ausgeben
        if ausgeben is not None:
            await ausgeben(pcm)
            return
        await self._ton_senden_direkt(pcm)

    async def _ton_senden_direkt(self, pcm: bytes) -> None:
        """Schreibt nur der Pipecat-Ausgang tatsächlich auf den WebSocket."""
        if self._browser.client_state is not WebSocketState.CONNECTED:
            return
        with contextlib.suppress(Exception):
            await self._browser.send_bytes(pcm)
            self._lage.rahmen_zurueck += 1
            if self._lage.rahmen_zurueck == 1:
                logger.info("Erster Tonrahmen (TTS) an Browser gesendet (%d Bytes)", len(pcm))

    async def _senden(self, nutzlast: dict) -> None:
        ausgeben = self._pipecat_steuerung_ausgeben
        if ausgeben is not None:
            await ausgeben(nutzlast)
            return
        await self._senden_direkt(nutzlast)

    async def _senden_direkt(self, nutzlast: dict) -> None:
        """Serialisiert nur bereits bereinigte UI-Rahmen am Pipecat-Ausgang."""
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

    async def _verarbeite_teil_transkript(self, text_chunk: str) -> None:
        """Klassifiziert partielle Transkripte ohne einen zweiten Tool-Pfad."""
        await self._prefetch.verarbeite(text_chunk)

    def _prefetch_sitzung_an_lauf(self, run_id: str) -> None:
        self._prefetch.an_lauf_binden(run_id)

    async def _ausgabe_unterbrechen(self) -> None:
        """Stoppt sofort nur die Ausgabe; Tool-Runs laufen kontrolliert weiter."""
        if self._laufende is not None:
            self._unterdrueckte_laeufe.add(self._laufende)
        unterbrechen = self._pipecat_ausgabe_unterbrechen
        if unterbrechen is not None:
            await unterbrechen()
        stimme = self._stimme
        if stimme is not None:
            with contextlib.suppress(Exception):
                await stimme.schliessen()
            await self._senden({"art": "ausgabe_unterbrochen"})

    def _ausgabe_aktiv(self) -> bool:
        """Ob der aktuell lesende Zug noch an die Stimme senden darf."""
        aufgabe = asyncio.current_task()
        return aufgabe is None or aufgabe not in self._unterdrueckte_laeufe

    async def _abwuergen(self, *, runs_abbrechen: bool = False) -> None:
        """Räumt Voice-Ausgabe auf; nur ein expliziter Abort beendet AI-Runs."""
        self._prefetch.invalidieren()
        if runs_abbrechen and self._voice_run_ids:
            from services import ai_run_service

            await asyncio.to_thread(
                ai_run_service.eigene_laeufe_abbrechen,
                user_id=self._user_id,
                run_ids=set(self._voice_run_ids),
            )
            self._voice_run_ids.clear()
        if (self._laufende is not None and not self._laufende.done()) or (self._stimme is not None):
            self._unterbrochen_fuer_merge = True
        await self._ausgabe_unterbrechen()
        aufgaben = list(self._laeufe)
        if self._laufende is not None and self._laufende not in aufgaben:
            # Auch Test- und Sonderaufrufer duerfen den expliziten Abbruch
            # nicht umgehen, nur weil sie den Zug nicht ueber `_ton` angelegt
            # haben.
            aufgaben.append(self._laufende)
        self._laufende = None
        for aufgabe in aufgaben:
            if not aufgabe.done():
                aufgabe.cancel()
        for aufgabe in aufgaben:
            if not aufgabe.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await aufgabe
