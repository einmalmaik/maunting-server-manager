"""Die Stimme: Text hinein, Ton heraus — und sonst nichts.

Dieses Modul **denkt nicht**. Es kennt keine Werkzeuge, keine Bestätigungen,
keinen Gesprächsverlauf und keinen Benutzer. Es bekommt Zeichen und liefert
Tonstücke. Das ist der ganze Unterschied zu dem, was bis zum 16.08.2026 an
dieser Stelle stand: OpenAIs Realtime-API war ein zweites Modell mit eigenem
Werkzeuglauf, eigener Autonomie und eigenem Gedächtnis neben dem des Chats —
zwei Wege, die dasselbe Panel bedienen durften, und jeder Befund musste zweimal
behoben werden.

Jetzt antwortet im Sprachmodus dasselbe Modell wie im getippten Chat, über
denselben `AiRun`. Davor sitzt das Gehör (`ai_stt`), dahinter diese
Stimme. Was hier fehlt, ist der Punkt.

**Warum die Verbindung früh aufgeht.** Ein Handschlag kostet rund 150
Millisekunden, und die will niemand hören, wenn das erste Wort schon da ist.
Also wird die Sitzung geöffnet, sobald der Lauf beginnt — nicht beim ersten
Zeichen. Sie überlappt dann mit der Denkzeit des Modells, die in jedem Fall
länger ist, und ist fertig, bevor sie gebraucht wird.

**Warum eine Sitzung je Antwort und nicht eine je Gespräch.** Die Gegenstelle
schliesst nach `inactivity_timeout` von selbst, und eine Verbindung über ein
ganzes Gespräch offenzuhalten hiesse, Lebenszeichen zu schicken, damit sie
nicht zumacht — Aufwand für einen Handschlag, der ohnehin in der Denkpause
verschwindet. Abgerechnet wird nach Zeichen, nicht nach Verbindung.

**Warum ganze Sätze und kein Zeichenstrom.** Die Gegenstelle puffert eingehenden
Text und erzeugt erst, wenn genug beisammen ist (`chunk_length_schedule`,
Vorgabe 120 Zeichen). Wer ihr Zeichen für Zeichen schickt, wartet also auf 120
Zeichen — oder senkt die Schwelle und bekommt zerhackte Betonung. Der dritte
Weg ist der richtige: hier zu Sätzen sammeln und jeden Satz mit ``flush``
sofort erzeugen lassen. Dann beginnt der Ton nach dem **ersten Satz** statt
nach 120 Zeichen, und jeder Satz wird als Satz betont.

Ausdrücklich **ohne** ``auto_mode``: der Parameter steht in der
Schnittstellenliste, aber in keiner erreichbaren Beschreibung (am 16.08.2026
gesucht). Was er tut, wäre geraten — und ``flush`` ist dokumentiert und tut
nachweislich dasselbe.

**Das Tonformat ist kein Zufall.** ``pcm_24000`` ist genau das, was die
Wiedergabe im Browser ohnehin erwartet (PCM16, 24 kHz, mono): dieselben
Binärrahmen wie zuvor, kein Dekodieren, kein Umrechnen, keine Änderung an
`audioWiedergabe.ts`. MP3 wäre kleiner und müsste im Browser dekodiert werden —
für ein Gespräch ist der Rechenweg die falsche Ersparnis. Höhere Abtastraten
verlangen zudem einen Bezahltarif.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
from typing import Awaitable, Callable
from urllib.parse import quote, urlencode, urlparse, urlunparse

try:
    import websockets
except ImportError:  # pragma: no cover - im Betrieb immer vorhanden
    # Weich importiert, und das ist keine Vorsicht, sondern eine Regel des
    # Hauses: ein harter Import im Modulkopf entscheidet über den Start des
    # **ganzen** Panels. Am 11.08.2026 stand es in einer Neustartschleife, weil
    # ein QR-Zeichner fehlte. Der Sprachmodus ist eine Zusatzfunktion, die einen
    # eigens eingerichteten Anbieterzugang braucht — ohne die Bibliothek fällt
    # er aus, und sonst nichts.
    websockets = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: Ob überhaupt vorgelesen werden kann. Der Router fragt das über `ai_tts`,
#: bevor er den Sprachmodus anbietet — dieselbe Antwort wie bei einem fehlenden
#: Zugang.
STIMME_MOEGLICH = websockets is not None

#: Warum nicht, falls nicht. Der Satz steht hier und nicht im Router, weil nur
#: dieses Modul den Grund kennt: es ist der weiche Import oben. Der Betreiber
#: bekommt ihn im Einstellungsdialog zu sehen und weiss dann, dass er eine
#: Bibliothek nachinstalliert und keinen Schlüssel sucht.
UNMOEGLICH_GRUND = "Die WebSocket-Bibliothek fehlt in dieser Installation."


# ── Vereinbarungen mit der Gegenstelle ────────────────────────────────────

#: Abtastrate des zurückkommenden Tons. Vereinbarung mit dem Browser **und**
#: mit ElevenLabs: `AUSGABEFORMAT` unten muss dazu passen, sonst klingt die
#: Stimme zu hoch oder zu tief, ohne dass irgendwo ein Fehler entsteht.
ABTASTRATE = 24_000

#: PCM16, 24 kHz, mono — dasselbe, was der Browser abspielt. Kein Bezahltarif
#: nötig; erst ``pcm_44100`` verlangt einen.
AUSGABEFORMAT = "pcm_24000"

#: Frist für den Verbindungsaufbau.
VERBINDUNGS_TIMEOUT = 20.0

#: Wie lange die Probe im Einstellungsdialog auf eine Antwort wartet. Kurz,
#: weil jemand auf einen Klick hin davorsitzt.
PROBE_TIMEOUT = 8.0

#: Wie lange die Gegenstelle eine stille Sitzung offenhält, in Sekunden. Der
#: Wert deckt die Denkpause zwischen zwei Sätzen einer Antwort ab — nicht die
#: Pause zwischen zwei Antworten, denn dazwischen gibt es keine Sitzung.
#:
#: Eine **Werkzeugrunde** kann länger dauern als das: während die KI arbeitet,
#: bekommt die Stimme keinen Text, und die Gegenstelle legt still auf. Das ist
#: kein Fehler der Antwort — der Rest gehört trotzdem gesprochen. Deshalb
#: verbindet `sagen()` in diesem Fall einmal transparent neu (`_neu_verbinden`),
#: statt den Wert hier hochzudrehen: die Frist gehört der Gegenstelle, und eine
#: Zahl, die jede denkbare Werkzeugrunde abdeckt, gibt es nicht.
INAKTIVITAET_SEKUNDEN = 20

#: Wie lange auf das Schlusszeichen der Gegenstelle gewartet wird, nachdem der
#: letzte Satz hinausging. Ohne Frist hinge das Auflegen an einer Gegenstelle,
#: die nicht mehr antwortet.
ABSCHLUSS_TIMEOUT = 20.0


# ── Grenzen ───────────────────────────────────────────────────────────────

#: Wie viele Zeichen eine einzelne Antwort höchstens vorliest.
#:
#: Eine Kostenbremse und keine Formfrage: abgerechnet wird je Zeichen, und ein
#: Modell, das sich verrennt und ein ganzes Log ausschreibt, verliest es sonst
#: vollständig. Viertausend Zeichen sind bei normalem Sprechtempo gut vier
#: Minuten am Stück — jenseits davon ist es kein Gesprächsbeitrag mehr.
#:
#: Der Text bricht dann einfach ab. Das ist unhöflich und trotzdem richtig: die
#: Alternative wäre eine Rechnung, die niemand erwartet hat, und im Panel steht
#: die vollständige Antwort ohnehin geschrieben.
MAX_ZEICHEN_JE_ANTWORT = 4_000

#: Ab wann ein Satzzeichen wirklich einen Satz beendet.
#:
#: „z. B.", „Nr. 5", „1.5 GB" — überall steht ein Punkt, und nirgends ist der
#: Satz zu Ende. Ein Mindestmass fängt diese Fälle ohne Abkürzungsliste ab: was
#: kürzer ist als das hier, wartet auf mehr Text. Falsch liegt es damit nur bei
#: sehr kurzen echten Sätzen („Ja.") — und die kosten dann einen Satz Verzug,
#: nicht den Ton.
MIN_STUECK_ZEICHEN = 24

#: Ab wann auch ohne Satzzeichen abgeschickt wird.
#:
#: Ein Modell, das einen langen Nebensatz baut, darf den Ton nicht anhalten.
#: Getrennt wird dann an der letzten Wortgrenze — nie mitten im Wort, sonst
#: spricht die Stimme die beiden Hälften als zwei Wörter aus.
MAX_STUECK_ZEICHEN = 240

#: Woran ein Satz endet. ``:`` und ``;`` sind bewusst dabei — sie tragen im
#: Deutschen eine Sprechpause, und je früher getrennt wird, desto früher
#: beginnt der Ton.
_SATZENDE = re.compile(r"[.!?…:;\n]")


def _naechstes_stueck(puffer: str, *, letzter: bool = False) -> tuple[str, str]:
    """Trennt vorne ein sprechbares Stück ab. Gibt (Stück, Rest) zurück.

    Ein leeres Stück heisst „noch nicht genug" und ist kein Fehler: der Aufrufer
    sammelt dann weiter. Genau dafür ist der Rückgabewert ein Paar und kein
    ``None`` — es gibt hier nichts Aussergewöhnliches zu behandeln, nur zwei
    normale Fälle.

    ``letzter=True`` beim Ausklingen: dann geht heraus, was da ist, auch ein
    halber Satz. Er ist ja der ganze Rest.
    """
    if letzter:
        return puffer.strip(), ""

    treffer = None
    for kandidat in _SATZENDE.finditer(puffer):
        if kandidat.end() >= MIN_STUECK_ZEICHEN:
            treffer = kandidat
            break
    if treffer is not None:
        return puffer[: treffer.end()].strip(), puffer[treffer.end() :]

    if len(puffer) >= MAX_STUECK_ZEICHEN:
        # Kein Satzzeichen in Sicht. An der letzten Wortgrenze trennen, damit
        # kein Wort zerfällt; findet sich keine, hart schneiden — ein einzelnes
        # Wort von 240 Zeichen ist kein Wort mehr.
        schnitt = puffer.rfind(" ", 0, MAX_STUECK_ZEICHEN)
        if schnitt <= 0:
            schnitt = MAX_STUECK_ZEICHEN
        return puffer[:schnitt].strip(), puffer[schnitt:]

    return "", puffer


# ── Die Adresse ───────────────────────────────────────────────────────────


def verbindungsadresse(base_url: str, voice_id: str, model_id: str) -> str:
    """Baut die WebSocket-Adresse aus der Basis-URL des Anbieters.

    Die Basis kommt aus `ai_provider_registry` und nicht aus einem Formular —
    sie kann also weder auf ein internes Netz noch auf einen umgeschriebenen
    Host zeigen. ``https`` wird zu ``wss``.

    ``voice_id`` steht in einem **Pfadsegment**. Sie ist deshalb zusätzlich
    kodiert, obwohl `schemas.ai_provider._stimme_lesen` sie schon auf
    ``[A-Za-z0-9_-]`` festnagelt: die beiden Prüfungen sichern verschiedene
    Wege, und die hier gilt auch für einen Wert, der auf anderem Weg in die
    Spalte gekommen ist. Zwei Zeilen für einen Pfad, der sonst woandershin
    zeigen könnte, sind ein guter Tausch.
    """
    teile = urlparse(base_url)
    schema = "wss" if teile.scheme == "https" else "ws"
    pfad = teile.path.rstrip("/")
    wirksames_modell = (model_id or "").strip() or "eleven_flash_v2_5"
    frage = urlencode(
        {
            "model_id": wirksames_modell,
            "output_format": AUSGABEFORMAT,
            "inactivity_timeout": INAKTIVITAET_SEKUNDEN,
        }
    )
    return urlunparse(
        (
            schema,
            teile.netloc,
            f"{pfad}/text-to-speech/{quote(voice_id, safe='')}/stream-input",
            "",
            frage,
            "",
        )
    )


def probe_fehlercode(fehler: BaseException) -> str:
    """Ein Fehlschlag als Code, den die Oberfläche schon kennt.

    Absichtlich auf die vorhandenen ``AI_PROVIDER_*``-Codes abgebildet statt auf
    neue: sie sind in beiden Sprachdateien übersetzt und sagen dasselbe. Ein
    eigener Satz Codes für die Stimme wäre eine zweite Wortwahl für dieselben
    drei Fälle — falscher Schlüssel, falsche Kennung, nicht erreichbar.

    Der Wortlaut des Anbieters geht **nicht** mit. Er kann Kontingentstände und
    Kontonamen enthalten; der Code sagt dem Betreiber, was zu tun ist.
    """
    antwort = getattr(fehler, "response", None)
    status = getattr(antwort, "status_code", None)
    if isinstance(status, int):
        if status in (401, 403):
            return "AI_PROVIDER_AUTH_FAILED"
        if status in (400, 404, 422):
            # Fast immer: die Stimm-Kennung oder das Sprachmodell gibt es in
            # diesem Konto nicht. Die Adresse baut MSM selbst, sie kann nicht
            # danebenliegen.
            return "AI_PROVIDER_REQUEST_REJECTED"
        if status == 429:
            return "AI_PROVIDER_RATE_LIMITED"
    if isinstance(fehler, (asyncio.TimeoutError, TimeoutError)):
        return "AI_PROVIDER_STREAM_TIMEOUT"
    return "AI_PROVIDER_UNAVAILABLE"


async def _verbinden(adresse: str, schluessel: str):
    if websockets is None:  # pragma: no cover - der Router prüft vorher
        raise RuntimeError("websockets fehlt; der Sprachmodus steht nicht zur Verfuegung")
    return await asyncio.wait_for(
        websockets.connect(
            adresse,
            # Roh, nicht als Bearer — siehe `ai_provider_registry`.
            additional_headers={"xi-api-key": schluessel},
            max_size=None,
        ),
        VERBINDUNGS_TIMEOUT,
    )


def _verbindung_zu(fehler: BaseException) -> bool:
    """Ob dieser Fehler nichts weiter sagt als: die Verbindung ist zu.

    Die Unterscheidung trägt die ganze Wiederverbindungslogik: eine zugegangene
    Verbindung ist der Alltagsfall nach einer langen Werkzeugrunde
    (`INAKTIVITAET_SEKUNDEN`) und wird transparent repariert. Alles andere —
    ein abgelehnter Schlüssel, ein Protokollfehler — bleibt ein Fehler und
    fällt beim Aufrufer auf.
    """
    if websockets is None:  # pragma: no cover - ohne Bibliothek keine Verbindung
        return False
    # Der Top-Level-Alias und nicht `websockets.exceptions`: das Unterpaket
    # existiert als Attribut erst, wenn es irgendwer eigens importiert hat —
    # `ConnectionClosed` dagegen ist der dokumentierte öffentliche Name.
    return isinstance(fehler, websockets.ConnectionClosed)


async def pruefen(adresse: str, schluessel: str) -> None:
    verbindung = await _verbinden(adresse, schluessel)
    try:
        await verbindung.send(json.dumps({"text": " ", "xi_api_key": schluessel}))
        # Auf die erste Antwort warten. Ohne das ginge eine Gegenstelle, die
        # erst nach dem Handschlag ablehnt, als Erfolg durch. Ein Ausbleiben
        # ist hier **kein** Fehlschlag: ein blosses Leerzeichen muss keinen Ton
        # ergeben, und eine schweigende Gegenstelle hat den Handschlag ja
        # angenommen. Abgelehnt hätte sie mit einem Schliessen, und das kommt
        # als Ausnahme aus `recv()`.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(verbindung.recv(), PROBE_TIMEOUT)
    finally:
        with contextlib.suppress(Exception):
            await verbindung.close()


# ── Die Sitzung ───────────────────────────────────────────────────────────


class Stimme:
    """Eine Vorlesesitzung — offen für die Dauer **einer** Antwort.

    Benutzt als asynchroner Kontext:

    .. code-block:: python

        async with Stimme(adresse=…, schluessel=…, senden=…) as stimme:
            async for stueck in antwort:
                await stimme.sagen(stueck)
            await stimme.ausklingen()

    ``senden`` bekommt fertige PCM16-Blöcke, sobald sie eintreffen — nicht am
    Ende. Ein Rückruf und keine Warteschlange, weil es genau einen Abnehmer gibt
    (den Browser) und eine Warteschlange dazwischen nur eine zweite Stelle wäre,
    an der Ton liegenbleiben kann.
    """

    def __init__(
        self,
        *,
        adresse: str,
        schluessel: str,
        senden: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._adresse = adresse
        self._schluessel = schluessel
        self._senden = senden
        self._verbindung = None
        self._empfang: asyncio.Task | None = None
        self._puffer = ""
        self._gesendet = 0
        self._abgeschnitten = False
        #: Ein Fehler aus der Empfangsschleife. Er wird **hier** abgelegt und
        #: nicht geworfen, wo er entsteht: die Schleife läuft nebenher, und eine
        #: Ausnahme in einer Nebenaufgabe sähe niemand. Beim nächsten `sagen()`
        #: fällt er auf.
        self._fehler: BaseException | None = None
        self._fertig = asyncio.Event()

    async def __aenter__(self) -> "Stimme":
        self._verbindung = await self._eroeffnen()
        self._empfang = asyncio.create_task(self._empfangen())
        return self

    async def _eroeffnen(self):
        verbindung = await _verbinden(self._adresse, self._schluessel)
        try:
            # Eröffnungsnachricht (BOS) mit voice_settings und xi_api_key
            await verbindung.send(
                json.dumps(
                    {
                        "text": " ",
                        "voice_settings": {
                            "stability": 0.5,
                            "similarity_boost": 0.8,
                        },
                        "xi_api_key": self._schluessel,
                    }
                )
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await verbindung.close()
            raise
        return verbindung

    async def _neu_verbinden(self) -> None:
        """Einmal transparent neu verbinden — die Gegenstelle hat still aufgelegt.

        Der Alltagsfall dahinter ist die Werkzeugrunde: solange die KI
        arbeitet, bekommt die Stimme keinen Text, und nach
        `INAKTIVITAET_SEKUNDEN` schliesst ElevenLabs die stille Sitzung von
        selbst. Das ist kein Fehler der Antwort — der Rest gehört gesprochen,
        nicht als Störung gemeldet. Der Zeichenzähler (`_gesendet`) läuft
        weiter: die Grenze gilt je Antwort, nicht je Verbindung.
        """
        logger.info("Stimme verbindet neu: die Gegenstelle hatte still aufgelegt")
        if self._empfang is not None:
            self._empfang.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._empfang
            self._empfang = None
        if self._verbindung is not None:
            with contextlib.suppress(Exception):
                await self._verbindung.close()
            self._verbindung = None
        self._fehler = None
        verbindung = await self._eroeffnen()
        # Das frische Schlusszeichen erst **nach** gelungener Verbindung: das
        # alte hat der Empfänger beim Abriss gesetzt, und scheitert die neue,
        # soll `ausklingen()` sofort zurückkehren statt auf ein Ereignis zu
        # warten, das nie jemand setzt. Gelingt sie, wartet es auf das Ende
        # **dieser** Sitzung.
        self._fertig = asyncio.Event()
        self._verbindung = verbindung
        self._empfang = asyncio.create_task(self._empfangen())

    async def __aexit__(self, *_ausnahme) -> None:
        await self.schliessen()

    async def sagen(self, text: str) -> None:
        """Nimmt ein Stück Antworttext entgegen und spricht, was fertig ist."""
        if not text or self._abgeschnitten:
            return
        self._pruefe_fehler()
        self._puffer += text
        while True:
            stueck, rest = _naechstes_stueck(self._puffer)
            if not stueck:
                break
            self._puffer = rest
            await self._stueck_senden(stueck)
            if self._abgeschnitten:
                self._puffer = ""
                break

    async def ausklingen(self) -> None:
        """Den Rest sprechen und das Ende ankündigen.

        Danach kommt noch Ton — das Ende des Textes ist nicht das Ende des
        Tons. Gewartet wird deshalb auf das Schlusszeichen der Gegenstelle und
        nicht auf das eigene letzte Wort.
        """
        if self._verbindung is None:
            return
        rest, _ = _naechstes_stueck(self._puffer, letzter=True)
        self._puffer = ""
        if rest and not self._abgeschnitten:
            with contextlib.suppress(Exception):
                await self._stueck_senden(rest)
        with contextlib.suppress(Exception):
            # Die leere Zeichenkette mit flush=True ist das vereinbarte „ich bin fertig".
            await self._verbindung.send(json.dumps({"text": "", "flush": True}))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._fertig.wait(), ABSCHLUSS_TIMEOUT)

    async def schliessen(self) -> None:
        """Sofort auflegen — auch mitten im Satz.

        Der Weg für den Fall, dass der Mensch dazwischenredet: was noch nicht
        erzeugt wurde, wird nicht mehr erzeugt, und was schon unterwegs war,
        verwirft der Browser. Beides ist gewollt — es ist die Antwort auf die
        vorige Frage.
        """
        if self._empfang is not None:
            self._empfang.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._empfang
            self._empfang = None
        if self._verbindung is not None:
            with contextlib.suppress(Exception):
                await self._verbindung.close()
            self._verbindung = None

    @property
    def abgeschnitten(self) -> bool:
        """Ob wegen `MAX_ZEICHEN_JE_ANTWORT` nicht alles vorgelesen wurde.

        Niemand fragt sie ab — dass gekürzt wurde, erfährt heute nur das
        Protokoll. Dieselbe Lücke wie bei `ai_voice_vad.Aeusserung.abgeschnitten`
        und aus demselben Grund offen: der Sprechende zu informieren braucht ein
        eigenes Ereignis. Auf dem Schirm steht der Text vollständig, nur gehört
        hat er ihn nicht bis zum Ende.
        """
        return self._abgeschnitten

    async def _stueck_senden(self, stueck: str) -> None:
        if self._verbindung is None:
            return
        rest = MAX_ZEICHEN_JE_ANTWORT - self._gesendet
        if rest <= 0:
            self._abgeschnitten = True
            return
        if len(stueck) > rest:
            stueck = stueck[:rest]
            self._abgeschnitten = True
        self._gesendet += len(stueck)
        # Das anhängende Leerzeichen verlangt das Protokoll ausdrücklich; ohne
        # es klebt der letzte an den nächsten Satz.
        # `try_trigger_generation` steuert die direkte Generierung; `flush: True`
        # darf erst beim finalen Ausklingen gesendet werden, um das 5-Kontexte-Limit
        # (Fehlercode 1008) von ElevenLabs nicht zu überschreiten.
        nutzlast = json.dumps({"text": f"{stueck} ", "try_trigger_generation": True})
        try:
            await self._verbindung.send(nutzlast)
        except Exception as fehler:
            if not _verbindung_zu(fehler):
                raise
            # Genau **ein** zweiter Versuch, kein Kreisen: die Verbindung ist
            # am eigenen `inactivity_timeout` gestorben, weil eine
            # Werkzeugrunde länger schwieg — der Satz selbst ist einwandfrei.
            # Scheitert auch die neue Verbindung, ist es eine echte Störung
            # und fällt wie bisher beim Aufrufer auf.
            await self._neu_verbinden()
            await self._verbindung.send(nutzlast)
        if self._abgeschnitten:
            logger.info(
                "Sprachantwort gekuerzt: Grenze von %d Zeichen erreicht",
                MAX_ZEICHEN_JE_ANTWORT,
            )

    def _pruefe_fehler(self) -> None:
        if self._fehler is None:
            return
        fehler = self._fehler
        self._fehler = None
        if _verbindung_zu(fehler):
            # Kein Fehler der Antwort: die Gegenstelle hat nach
            # `INAKTIVITAET_SEKUNDEN` Textstille von selbst aufgelegt —
            # typischerweise, weil eine Werkzeugrunde so lange dauerte. Das
            # nächste Stück verbindet neu (`_stueck_senden`); ihn hier zu
            # werfen hiesse, die restliche Antwort als „stoerung" wegzuwerfen.
            return
        raise fehler

    async def _empfangen(self) -> None:
        """Nimmt Tonstücke entgegen, solange die Gegenstelle welche schickt."""
        verbindung = self._verbindung
        if verbindung is None:  # pragma: no cover - nur ohne __aenter__
            return
        try:
            async for nachricht in verbindung:
                if isinstance(nachricht, bytes):
                    # Kommt nicht vor; die Gegenstelle antwortet in JSON. Ein
                    # Binärrahmen wäre ein Protokollwechsel, und ihn als Ton
                    # durchzureichen hiesse zu raten, wie er kodiert ist.
                    continue
                try:
                    ereignis = json.loads(nachricht)
                except (TypeError, ValueError):
                    continue
                if not isinstance(ereignis, dict):
                    continue
                if "error" in ereignis or ("message" in ereignis and not ereignis.get("audio")):
                    logger.warning("ElevenLabs TTS Rueckmeldung: %s", ereignis)
                ton = ereignis.get("audio")
                if isinstance(ton, str) and ton:
                    try:
                        roh = base64.b64decode(ton)
                    except (ValueError, TypeError):
                        continue
                    if roh:
                        await self._senden(roh)
                if ereignis.get("isFinal") is True:
                    self._fertig.set()
        except asyncio.CancelledError:
            raise
        except Exception as fehler:  # pragma: no cover - Netzabbruch
            self._fehler = fehler
            code = getattr(fehler, "code", None) or getattr(getattr(fehler, "rcvd", None), "code", None)
            grund = getattr(fehler, "reason", None) or getattr(getattr(fehler, "rcvd", None), "reason", None)
            logger.warning("Stimme abgebrochen: %s (Code: %s, Grund: %s)", type(fehler).__name__, code, grund)
        finally:
            # Auch im Fehlerfall setzen: `ausklingen()` wartet darauf, und eine
            # abgerissene Verbindung ist ein Ende wie jedes andere. Ohne das
            # liefe dort die volle Frist ab, für einen Ton, der nie kommt.
            self._fertig.set()
