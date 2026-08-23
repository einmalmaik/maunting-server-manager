"""Wann ein Modellkatalog geholt wird und wie lange er gilt.

Was in einem Katalogeintrag steht, weiß diese Datei **nicht** — das weiß der
Anbieter, der ihn geschrieben hat, und deshalb steht es in seiner Datei unter
`ai_provider_registry`. Hier steht nur das, was für alle Anbieter gleich ist:
holen, auf Größe prüfen, die Liste auspacken, jeden Eintrag durch
``katalog_leser(kind)`` schicken, das Ergebnis behalten und Fristen darauf
setzen. Die Trennung hat einen praktischen Zweck — ein neuer Anbieter ändert an
dieser Datei keine einzige Zeile.

Warum es den Katalog überhaupt gibt: er beantwortet zwei Fragen, die sonst eine
handgepflegte Liste im Programm beantworten müsste — *welches Modell kann wie
tief nachdenken?* und *wieviel Text fasst es auf einmal?* Eine feste Antwort im
Code wäre bei der Mehrheit der Modelle falsch (die Zahlen dazu stehen in
`ai_provider_registry.openrouter`), und beim Kontextfenster fiele es nicht
einmal auf: ein zu klein angenommenes Fenster schlägt nicht fehl, es lässt den
Chat nur früher vergessen.

Was ein Anbieter **nicht** herausgibt, bleibt unbekannt und wird hier nicht
ersetzt. ``kontext_tokens=None`` heißt überall „unbekannt" und nie „klein".

Mit **einer** Ausnahme, und sie ersetzt nichts, sie fragt woanders nach: führt
ein Anbieter ``faehigkeiten_aus``, dann beschreibt der Katalog eines *anderen*
Anbieters dieselben Modelle, und was der eigene verschweigt, wird von dort
nachgetragen (`_mit_faehigkeiten`). Der Anlass ist OpenAI — sein ``/v1/models``
kennt weder Kontextfenster noch Denkstufen, OpenRouters Katalog kennt beides
für dieselben Modelle. Das ist keine Tabelle im Programm, sondern weiterhin ein
Katalog; es ist nur nicht derselbe. Der eigene Katalog behält immer recht, und
fällt der fremde aus, bleibt es bei „unbekannt".

**Ein Ausfall des Katalogs hält nichts an.** Der zuletzt geholte Stand bleibt
gültig, auch über die Frist hinaus — ein veralteter Katalog ist unbrauchbarer
als ein frischer, aber unendlich viel brauchbarer als gar keiner. Nur beim
allerersten Abruf gibt es nichts zu retten; dann meldet die Oberfläche das.

Ein Ausfall wird ausserdem **vermerkt**. Nach einem Fehlversuch wird eine
Minute lang gar nicht erst gefragt, sonst zahlte jeder einzelne Aufruf erneut
die volle ``ABRUF_TIMEOUT`` — und da das Schloss ueber dem Abruf gehalten wird,
warteten alle gleichzeitigen Anfragen hintereinander mit. Ein nicht
erreichbarer Anbieter haette den Chat dann nicht nur ausgebremst, sondern
gestaut: die Providerliste fragt den Katalog je Provider, das Absenden einer
Nachricht noch einmal.

**Niemand wartet auf diesen Abruf, wenn es etwas auszuliefern gibt.** Das ist
die zweite Lehre. Sie kam aus der Suche nach einer langsamen Chatantwort, und
dabei ist wichtig, was sie **nicht** war: gemessen am 2026-08-13 antwortet
OpenRouter auf diesen Abruf in 0,08 bis 0,17 Sekunden (653 KB, 409 Modelle).
Der Katalog war also nicht der Grund fuer die beobachtete Wartezeit, und dieser
Absatz behauptet das auch nicht.

Was er behauptet, ist schlichter: der Abruf hatte im Sendepfad nichts zu
suchen. Lief die Frist ab, wartete der Absender einer Chatnachricht auf einen
HTTP-Abruf zu einem fremden Dienst — obwohl ein vollkommen brauchbarer Stand
danebenlag. Die Frist trennt naemlich nicht „brauchbar“ von „unbrauchbar“,
sondern nur „frisch“ von „nicht mehr frisch“; ein Kontextfenster schrumpft
ueber Nacht nicht, und die Denkstufen eines Modells aendern sich auch nicht.
Solange OpenRouter schnell ist, kostet das kaum etwas. An dem Tag, an dem es
langsam ist, kostet es ``ABRUF_TIMEOUT`` — und zwar jeden Chat gleichzeitig,
denn das Schloss lag frueher ueber allen Anbietern zusammen.

Deshalb gilt jetzt: ein vorhandener Stand geht **sofort** hinaus, die
Auffrischung laeuft nebenher. Nur wenn es ueberhaupt nichts gibt, wartet noch
jemand — und auch dann hoechstens ``ERSTE_WARTE``, denn beide Stellen im
Sendepfad kommen mit einer leeren Antwort zurecht (``ai_context_window`` sagt
dann „Fenster unbekannt“, ``ai_reasoning`` bleibt beim reinen An/Aus ohne
Stufe). Eine Sekunde spaeter falsch zu liegen ist besser als eine Minute lang
richtig zu schweigen.

Damit der Fall „ueberhaupt nichts da“ im Sendepfad gar nicht erst eintritt,
waermt ``vorwaermen_anstossen()`` den Katalog beim Start der Anwendung vor.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import logging

import httpx

from services.ai_provider_registry import ANBIETER, Anbieter, Modell, anbieter, katalog_leser


logger = logging.getLogger(__name__)

#: Wie lange ein geholter Katalog als frisch gilt. Modelle kommen täglich dazu,
#: aber keines verschwindet innerhalb von Stunden — sechs Stunden halten die
#: Liste aktuell genug und die Zahl der Abrufe klein.
CACHE_TTL = timedelta(hours=6)
ABRUF_TIMEOUT = 30.0
#: Wie lange nach einem gescheiterten Abruf gar nicht erst wieder gefragt wird.
#: Ohne diese Frist kostet ein haengender Anbieter **jeden** Aufruf die volle
#: ``ABRUF_TIMEOUT``, und weil das Schloss ueber dem Abruf gehalten wird, warten
#: alle gleichzeitigen Anfragen zusaetzlich hintereinander. Ein einziges
#: GET /api/ai/providers fragt den Katalog je aktiviertem Provider, das Absenden
#: einer Chatnachricht ueber ``ai_reasoning.vorgabe`` noch einmal — aus 30
#: Sekunden werden so Minuten. Eine Minute Ruhe ist kurz genug, dass ein wieder
#: erreichbarer Anbieter praktisch sofort wirkt, und lang genug, dass ein
#: Ausfall einmal bezahlt wird statt bei jeder Anfrage neu.
FEHLER_RUHE = timedelta(seconds=60)
#: Schutz gegen eine Antwort, die kein Katalog mehr ist. Der echte Katalog liegt
#: bei rund 660 KB; alles jenseits dieser Grenze wird nicht erst geparst.
MAX_KATALOG_BYTES = 8 * 1024 * 1024
#: Wie lange ein Aufrufer hoechstens auf den **allerersten** Katalog wartet.
#: Danach bekommt er die leere Liste und der Abruf laeuft im Hintergrund weiter;
#: der naechste Aufrufer findet ihn dann fertig vor. Nur hier ist Warten
#: ueberhaupt noch noetig, denn nur hier gibt es nichts auszuliefern — und drei
#: Sekunden sind die Grenze, ab der ein Chat sich aufgehaengt anfuehlt.
ERSTE_WARTE = 3.0


@dataclass
class _Eintrag:
    modelle: list[Modell] = field(default_factory=list)
    geholt_am: datetime | None = None
    #: Wann der letzte Abruf gescheitert ist. Getrennt von ``geholt_am``, weil
    #: beides gleichzeitig gelten kann und Verschiedenes bedeutet: ein alter,
    #: aber brauchbarer Stand plus ein frischer Fehlschlag.
    fehler_am: datetime | None = None
    #: Der Fehlschlag war „kein Schluessel zu beschaffen" und **kein** Ausfall
    #: des Anbieters. Der Unterschied entscheidet, wen die Ruhefrist bindet: den
    #: Anbieter kann niemand herbeireden, einen Schluessel schon — wer selbst
    #: einen mitbringt, hat den Grund des Fehlschlags in der Hand und darf nicht
    #: auf ihn warten muessen.
    schluessel_fehlte: bool = False
    #: Wessen Liste das ist, als Abdruck. Nur gesetzt, wo der Katalog am
    #: Schluessel haengt (`Anbieter.katalog_braucht_schluessel`); dort ist die
    #: Antwort naemlich nicht „was es gibt", sondern „was **dieser Zugang**
    #: sehen darf", und ein Stand des einen ist fuer den anderen falsch.
    #:
    #: An einem gescheiterten Versuch steht er ebenso — dort beantwortet er die
    #: Frage „wessen Versuch scheiterte?" und entscheidet damit, wen die
    #: Ruhefrist bindet. Ohne ihn band sie bei diesen Anbietern niemanden.
    fingerabdruck: str | None = None


_cache: dict[str, _Eintrag] = {}
#: Ein Schloss **je Anbieter**, nicht eines fuer alle. Vorher teilten sich alle
#: Anbieter eines: ein haengender Anbieter staute damit auch die Abrufe der
#: uebrigen, obwohl die nichts miteinander zu tun haben. Nebenbei loest das eine
#: zweite Sache — ein ``asyncio.Lock`` bindet sich an die Ereignisschleife, in
#: der er zuerst benutzt wird, und die Testsuite baut je Test eine neue. Ein
#: Woerterbuch laesst sich leeren, ein Modulobjekt nicht.
_locks: dict[str, asyncio.Lock] = {}
#: Laufende Auffrischungen, je Anbieter hoechstens eine. Der Verweis liegt hier,
#: damit die Aufgabe nicht mitten im Abruf vom Aufraeumer eingesammelt wird —
#: ``create_task`` allein haelt nichts fest.
_auffrischungen: dict[str, asyncio.Task] = {}
#: Der langlebige HTTP-Client der Anwendung. Eine Hintergrundauffrischung darf
#: **nicht** den Client des Aufrufers benutzen: der gehoert einer Anfrage und ist
#: geschlossen, sobald sie beantwortet ist. Gesetzt wird er im ``lifespan``,
#: genau wie bei ``ai_run_service.laufzeit_setzen``.
_HTTP: httpx.AsyncClient | None = None
#: Woher ein Katalogschluessel kommt, wenn der Aufrufer keinen mitbringt. Eine
#: eingehaengte Funktion und kein Import, aus demselben Grund wie ``_HTTP``: der
#: Schluessel steht in der Datenbank und ist mit dem DIS-Sidecar verschluesselt,
#: und diese Datei soll von beidem nichts wissen. Eingehaengt wird im
#: ``lifespan`` (siehe `ai_provider_service.katalogschluessel`); ohne den
#: Aufruf — Testsuite, Skripte — verhaelt sich alles wie vorher.
_SCHLUESSELQUELLE: Callable[[str], str | None] | None = None


def laufzeit_setzen(http: httpx.AsyncClient | None) -> None:
    """Den Client hinterlegen, mit dem im Hintergrund abgerufen wird.

    Ohne ihn gibt es keine Hintergrundauffrischung — und dann verhaelt sich das
    Modul wieder genau wie frueher: der Aufrufer wartet auf den Abruf. Das ist
    Absicht und keine Notloesung. Ein Abruf, der niemandem gehoert, waere
    schlimmer als ein Aufrufer, der wartet.
    """
    global _HTTP
    _HTTP = http


def schluesselquelle_setzen(quelle: Callable[[str], str | None] | None) -> None:
    """Hinterlegen, wie sich ein Katalogschluessel beschaffen laesst.

    Ohne diesen Aufruf bleibt ein schluesselpflichtiger Katalog auf den
    Aufrufer angewiesen — und das ist genau die Luecke, die es zu schliessen
    gilt: den Schluessel hat nur die Provider-Einstellungsseite zur Hand. Alle
    anderen Leser des Katalogs (``ai_reasoning.vorgabe``,
    ``ai_context_window.ermitteln``, die Providerliste im Chat) haben ihn nicht
    und koennten ihn nur mit einer **zusaetzlichen** Entschluesselung je Anfrage
    bekommen. Bei fuenf Lesestellen waeren das fuenf DIS-Aufrufe fuer eine
    Angabe, die sich sechs Stunden lang nicht aendert.

    Die Funktion wird deshalb genau dort gerufen, wo ohnehin abgerufen wird:
    einmal je faelligem Katalogabruf, im Hintergrund, unter dem Schloss und in
    einem eigenen Thread. ``DisClient.decrypt`` ist ein synchroner HTTP-Aufruf
    mit 15 Sekunden Frist; auf der Ereignisschleife stuende in dieser Zeit der
    ganze Panelprozess.
    """
    global _SCHLUESSELQUELLE
    _SCHLUESSELQUELLE = quelle


async def _schluessel_besorgen(kind: str) -> str | None:
    """Der hinterlegte Weg zum Schluessel, oder ``None`` — nie ein Fehler.

    Hier scheitern zu duerfen ist Absicht. Ein nicht erreichbares DIS-Sidecar,
    ein noch nicht eingerichteter Anbieter, eine Datenbank, die beim Start noch
    nicht antwortet: alles drei heisst „gerade kein Schluessel", und keines
    davon rechtfertigt einen Fehler in einer Auffrischung, auf die niemand
    wartet.
    """
    quelle = _SCHLUESSELQUELLE
    if quelle is None:
        return None
    try:
        return await asyncio.to_thread(quelle, kind)
    except Exception as exc:
        logger.warning(
            "Katalogschluessel fuer %s nicht zu ermitteln error=%s",
            kind,
            type(exc).__name__,
        )
        return None


def _schloss(kind: str) -> asyncio.Lock:
    schloss = _locks.get(kind)
    if schloss is None:
        schloss = _locks[kind] = asyncio.Lock()
    return schloss


def _auffrischen_anstossen(kind: str, schluessel: str | None = None) -> bool:
    """Eine Auffrischung im Hintergrund anstossen; laeuft schon eine, nichts tun.

    Der Rueckgabewert ist die eigentliche Aussage: ``True`` heisst „um den
    frischen Stand kuemmert sich jemand“. Nur dann darf der Aufrufer den alten
    ausliefern. Ist es ``False`` — kein Client hinterlegt, keine laufende
    Schleife —, bleibt es beim alten Verhalten und der Aufrufer holt selbst.
    Sonst lieferte MSM in der Testsuite und in jedem Skript ohne Anwendung
    ewig denselben veralteten Stand aus, ohne ihn je zu erneuern.

    ``schluessel`` reist mit in die Hintergrundaufgabe. Ohne ihn bekaeme ein
    schluesselpflichtiger Katalog dort ein 401 — und zwar unsichtbar, weil
    niemand auf diese Aufgabe wartet.
    """
    laufend = _auffrischungen.get(kind)
    if laufend is not None and not laufend.done():
        return True
    client = _HTTP
    if client is None:
        return False
    try:
        schleife = asyncio.get_running_loop()
    except RuntimeError:
        return False
    aufgabe = schleife.create_task(_auffrischen(client, kind, schluessel))
    _auffrischungen[kind] = aufgabe
    # Ohne diesen Rueckruf meldet asyncio beim Aufraeumen "Task exception was
    # never retrieved" — und der Verweis bliebe fuer immer stehen.
    aufgabe.add_done_callback(lambda fertig: _auffrischung_abschliessen(kind, fertig))
    return True


def _auffrischung_abschliessen(kind: str, aufgabe: asyncio.Task) -> None:
    if _auffrischungen.get(kind) is aufgabe:
        _auffrischungen.pop(kind, None)
    if aufgabe.cancelled():
        return
    fehler = aufgabe.exception()
    if fehler is not None:
        logger.warning(
            "Auffrischung des Modellkatalogs %s abgebrochen error=%s",
            kind,
            type(fehler).__name__,
        )


async def _auffrischen(
    client: httpx.AsyncClient, kind: str, schluessel: str | None = None
) -> None:
    """Der Abruf im Hintergrund.

    Ausdruecklich **ohne** ``erzwingen``: die Ruhefrist nach einem Fehlversuch
    gilt auch hier. Sonst liefe bei einem nicht erreichbaren Anbieter mit jeder
    abgeschickten Nachricht ein neuer Abruf ueber die volle ``ABRUF_TIMEOUT``
    an — unsichtbar, aber deswegen nicht harmlos.
    """
    await _besorgen(client, kind, erzwingen=False, sofort=False, schluessel=schluessel)


def vorwaermen_anstossen() -> None:
    """Den Katalog beim Start der Anwendung holen, bevor ihn jemand braucht.

    Damit trifft die erste Chatnachricht nach einem Neustart einen gefuellten
    Speicher statt eines leeren. Das ist der einzige Fall, in dem ueberhaupt noch
    jemand auf den Katalog warten muesste, und dieser Aufruf raeumt ihn aus dem
    Weg.

    Fragt selbst **keine** Datenbank und nicht, welche Anbieter eingerichtet
    sind: die Anbieterliste steht im Programm, ein Abruf je Anbieter kostet
    einmal wenige hundert Kilobyte, und ein Start, der auf das Schema wartet,
    waere von der Reihenfolge im ``lifespan`` abhaengig.

    Bis zum 17.08.2026 blieben schluesselpflichtige Kataloge hier aussen vor,
    und die Begruendung stand an dieser Stelle: der Schluessel steht in der
    Datenbank, ein Versuch ohne ihn endete in einem 401, das als Fehlversuch
    vermerkt wurde — und die Ruhefrist verzoegerte anschliessend den ersten
    echten Abruf um eine Minute, fuer einen Fehler, den niemand gemacht hatte.
    Praktisch hiess das: an einem OpenAI-Zugang war der Katalog nach jedem
    Neustart leer, bis jemand die Provider-Einstellungen oeffnete.

    Beides ist erledigt, und zwar an zwei Stellen. Den Schluessel besorgt sich
    der Abruf jetzt selbst (`schluesselquelle_setzen`), und findet er keinen,
    wird gar nicht erst abgerufen — ein 401 kann so nicht mehr entstehen. Und
    der Vermerk, den ein solcher Fehlversuch hinterlaesst, haelt fest, dass es
    am Schluessel lag (``_Eintrag.schluessel_fehlte``): wer selbst einen
    mitbringt, wartet die Ruhefrist nicht ab, denn er stellt nicht denselben
    Versuch noch einmal. Ohne diese zweite Haelfte waere die Frist genau der
    Fehler geblieben, den dieser Absatz beschreibt — nur eine Schicht tiefer.

    Die Anbieter stehen deshalb alle gleich hier, ohne Unterscheidung — mit
    einer Ausnahme, und die ist keine Politik, sondern eine Tatsache: ein
    Anbieter **ohne Katalogadresse** hat nichts zum Vorwaermen. Ihn trotzdem
    anzustossen kostete eine Aufgabe je Start, die sofort mit der leeren Liste
    zurueckkaeme.

    Der Aufruf bleibt trotzdem frei von Datenbankwissen: die Frage nach dem
    Schluessel faellt nicht hier, sondern in der Hintergrundaufgabe, also erst
    nachdem der ``lifespan`` weitergelaufen ist.
    """
    for kind, spec in ANBIETER.items():
        if spec.catalog_url is None:
            continue
        _auffrischen_anstossen(kind)


def _fingerabdruck(schluessel: str) -> str:
    """Ein Wiedererkennungszeichen fuer einen Schluessel — nicht der Schluessel.

    Gespeichert wird nur dieser Abdruck, und er beantwortet genau die eine
    Frage, die hier gestellt wird: „ist das derselbe wie vorhin?". Zurueckrechnen
    laesst er sich nicht, und damit haelt der Zwischenspeicher kein Geheimnis
    fest, obwohl er Schluessel auseinanderhaelt.
    """
    return hashlib.sha256(schluessel.encode("utf-8")).hexdigest()


def _gehoert_dem_frager(eintrag: _Eintrag, spec: Anbieter, schluessel: str | None) -> bool:
    """Ist dieser Stand die Antwort auf **diese** Frage?

    Bei einem offen liegenden Katalog immer: OpenRouters Liste ist fuer jeden
    dieselbe, und wer fragt, aendert daran nichts.

    Haengt der Katalog dagegen am Schluessel, ist die Liste eine Auskunft ueber
    ein Konto — bei OpenAI stehen die Feinabstimmungen der eigenen Organisation
    darin, und deren Kennungen tragen den Firmennamen, den der Betreiber ihnen
    gegeben hat. Ein Stand aus Zugang A ist dann fuer Zugang B nicht bloss
    ungenau, sondern zweimal falsch: er zeigt dort Modelle, die es fuer B nicht
    gibt, und verschweigt die, die nur B hat.

    Dass ``katalog_braucht_schluessel`` diese Frage mitbeantwortet, ist kein
    Kurzschluss, sondern die vorsichtige Richtung: ein Katalog, der einen
    Schluessel verlangt, antwortet ihm auch. Waere er ausnahmsweise doch fuer
    alle gleich, kostet die Annahme einen zusaetzlichen Abruf je Zugang — die
    umgekehrte Annahme kostet die Trennung zweier Konten. Ein eigenes Feld
    bekommt der Fall, wenn es ihn gibt.
    """
    if not spec.katalog_braucht_schluessel:
        return True
    return schluessel is not None and eintrag.fingerabdruck == _fingerabdruck(schluessel)


def _antwortet_ohne_abruf(
    eintrag: _Eintrag, jetzt: datetime, *, schluessel_da: bool
) -> bool:
    """Beantwortet dieser Eintrag die Frage, ohne den Anbieter zu fragen?

    Zwei Gruende sprechen gegen einen erneuten Abruf, und sie stehen hier
    zusammen, weil dieselbe Entscheidung zweimal faellt — einmal vor dem
    Schloss und einmal darin:

    * Der Stand ist frisch genug (``geholt_am`` innerhalb ``CACHE_TTL``).
    * Der letzte Versuch ist gerade erst gescheitert (``fehler_am`` innerhalb
      ``FEHLER_RUHE``). Dann ist der alte Stand — notfalls die leere Liste —
      die richtige Antwort, denn ein zweiter Versuch im selben Atemzug kostet
      nur dieselbe Wartezeit noch einmal und liefert dasselbe Nichts.

    Der zweite Grund kennt eine Ausnahme, und sie ist der Sinn von
    ``schluessel_da``: war der Fehlschlag „kein Schluessel zu beschaffen", dann
    liefert ein Aufrufer, der einen mitbringt, nicht denselben Versuch noch
    einmal — er liefert einen anderen. Ihn trotzdem eine Minute lang mit der
    leeren Liste abzuspeisen hiesse, ihm ein Nichtwissen als Katalog auszugeben:
    die Oberflaeche zeigt dann „dieser Anbieter kennt keine Modelle", waehrend
    der Schluessel daneben gespeichert ist.
    """
    if eintrag.geholt_am is not None and jetzt - eintrag.geholt_am < CACHE_TTL:
        return True
    if eintrag.fehler_am is None:
        return False
    if eintrag.schluessel_fehlte and schluessel_da:
        return False
    return jetzt - eintrag.fehler_am < FEHLER_RUHE


async def _hole(
    client: httpx.AsyncClient, spec: Anbieter, schluessel: str | None = None
) -> list[Modell]:
    # Der Schluessel geht nur mit, wenn der Anbieter ihn fuer den **Katalog**
    # verlangt. Ihn vorsorglich immer mitzuschicken waere ein Geheimnis an einer
    # Adresse, die es nicht braucht — OpenRouter gibt seine Liste offen heraus.
    kopf = (
        {spec.schluessel_kopf: f"{spec.schluessel_praefix}{schluessel}"}
        if spec.katalog_braucht_schluessel and schluessel
        else None
    )
    antwort = await client.get(spec.catalog_url, timeout=ABRUF_TIMEOUT, headers=kopf)
    antwort.raise_for_status()
    if len(antwort.content) > MAX_KATALOG_BYTES:
        raise ValueError("Katalog ist unerwartet groß")

    nutzlast = antwort.json()
    if spec.katalog_liste_feld is None:
        # Die Antwort **ist** die Liste. Kein Sonderfall, nur eine andere
        # Hausordnung — ElevenLabs macht es so.
        rohliste = nutzlast
    else:
        rohliste = (
            nutzlast.get(spec.katalog_liste_feld) if isinstance(nutzlast, dict) else None
        )
    if not isinstance(rohliste, list):
        erwartet = spec.katalog_liste_feld or "eine Liste"
        raise ValueError(f"Katalog hat kein {erwartet}-Feld")

    leser = katalog_leser(spec.kind)
    modelle = [
        modell
        for eintrag in rohliste
        if isinstance(eintrag, dict) and (modell := leser(eintrag)) is not None
    ]
    if not modelle:
        # Ein leerer Katalog ist kein gültiges Ergebnis, sondern eine Antwort,
        # die wir nicht verstanden haben. Sie darf einen brauchbaren alten
        # Stand nicht überschreiben.
        raise ValueError("Katalog enthält kein einziges Modell")
    return sorted(modelle, key=lambda item: item.model_id)


async def modelle(
    client: httpx.AsyncClient,
    kind: str,
    *,
    erzwingen: bool = False,
    schluessel: str | None = None,
) -> list[Modell]:
    """Die Modelle eines Anbieters, gecacht.

    ``schluessel`` wird nur von Anbietern mit ``katalog_braucht_schluessel``
    ausgewertet — und bei genau diesen entscheidet er auch, **wessen** Liste
    zurueckkommt. Hier stand einmal das Gegenteil („welche Modelle es gibt,
    haengt nicht daran, wer fragt"), und fuer OpenRouters offene Liste stimmt es
    weiterhin. Fuer einen Katalog hinter einem Schluessel stimmt es nicht: er
    antwortet mit dem, was dieses Konto sehen darf, samt seiner eigenen
    Feinabstimmungen — und deren Kennungen tragen den Firmennamen, den der
    Betreiber ihnen gegeben hat.

    Der Speicher bleibt trotzdem nach ``kind`` geschluesselt; getrennt wird
    nicht durch einen zweiten Platz, sondern durch einen Abdruck **im** Eintrag
    (`_gehoert_dem_frager`). Ein Stand, der einem anderen Zugang gehoert, wird
    dann nicht ausgegeben, sondern neu geholt. Zwei Zugaenge desselben Anbieters
    kosten so einen Abruf mehr; die Kennungen des einen im Auswahlfeld des
    anderen kosten mehr als das.

    ``erzwingen=True`` umgeht die Frist — das ist der Knopf „Modelle neu laden“
    in den Provider-Einstellungen. Er ist nötig, weil der häufigste Fall nicht
    „unbekanntes Modell“ ist, sondern „der Katalog ist ein paar Stunden alt“.
    Er umgeht **auch** die Ruhefrist nach einem Fehlversuch: wer den Knopf
    drueckt, hat gerade beim Anbieter nachgesehen und will jetzt eine Antwort,
    keine gespeicherte Absage. Und er wartet als einziger Weg noch auf den
    Abruf — das ist genau das, was der Knopf verspricht.
    """
    eigene = await _besorgen(
        client, kind, erzwingen=erzwingen, sofort=True, schluessel=schluessel
    )
    return await _mit_faehigkeiten(client, anbieter(kind), eigene)


def _anreichern(eigen: Modell, fremd: Modell | None) -> Modell:
    """Was der eigene Katalog nicht sagt, sagt der fremde — sonst nichts.

    Die Richtung ist die ganze Regel: **jede** Angabe des eigenen Katalogs
    bleibt stehen, nachgetragen wird nur, wo sie fehlt. Der Anbieter, dessen
    Modell es ist, hat immer recht.

    Bei den beiden Wahrheitswerten sieht das nach einer Ausnahme aus (``or``
    statt „vorhanden?"), ist aber dieselbe Regel: ein ``bool`` kann nicht
    „unbekannt" sagen, und ein Anbieter mit ``faehigkeiten_aus`` laesst sie
    deshalb bewusst auf dem Standardwert stehen — bei OpenAI steht dort
    ``denkt=False`` und meint „von hier aus unbekannt".

    ``cache_marke_noetig`` wandert ausdruecklich **nicht** mit. Es ist kein
    Merkmal des Modells, sondern eine Aussage ueber die Abrechnung des
    Anbieters: OpenRouter setzt es, wenn sein Katalog ``input_cache_write``
    fuehrt, und die Marke, die daraufhin mitginge, ist ``cache_control`` — eine
    OpenRouter-Erweiterung, auf die OpenAI mit einem 400 antwortet. Uebernommen
    wuerde hier also die Rechnung eines Vermittlers als Eigenschaft eines
    fremden Modells.

    ``sieht`` wandert dagegen mit, und zwar nach der Fenster-Regel und nicht
    nach der ``or``-Regel: es ist ein ``bool | None``, kann „unbekannt" also
    selbst sagen. Ob ein Modell Bilder liest, haengt am Modell und nicht am
    Weg dorthin — anders als die Cache-Marke, die eine Aussage ueber die
    Abrechnung eines Vermittlers ist.

    ``name`` bleibt ebenfalls der eigene. Im fremden Katalog steht der Name des
    Listeneintrags dort (``OpenAI: GPT-5.5``); in der Modellauswahl eines
    OpenAI-Zugangs waere das die Beschriftung eines Vermittlers, den der
    Betreiber gerade nicht benutzt.
    """
    if fremd is None:
        return eigen
    return replace(
        eigen,
        denkt=eigen.denkt or fremd.denkt,
        zwingend=eigen.zwingend or fremd.zwingend,
        stufen=eigen.stufen or fremd.stufen,
        standard_stufe=eigen.standard_stufe or fremd.standard_stufe,
        kontext_tokens=(
            eigen.kontext_tokens
            if eigen.kontext_tokens is not None
            else fremd.kontext_tokens
        ),
        max_ausgabe_tokens=(
            eigen.max_ausgabe_tokens
            if eigen.max_ausgabe_tokens is not None
            else fremd.max_ausgabe_tokens
        ),
        sieht=eigen.sieht if eigen.sieht is not None else fremd.sieht,
    )


async def _mit_faehigkeiten(
    client: httpx.AsyncClient, spec: Anbieter, eigene: list[Modell]
) -> list[Modell]:
    """Den Katalog eines Anbieters um das ergaenzen, was er selbst nicht sagt.

    Gilt nur fuer Anbieter mit ``faehigkeiten_aus`` — alle anderen gehen
    unveraendert durch, und das ist der Normalfall.

    Der fremde Katalog wird ueber `_besorgen` geholt und **nicht** ueber
    `modelle`. Das ist die Zeile, die eine Endlosschleife ausschliesst: `modelle`
    ist genau `_besorgen` plus diese Funktion, ein Aufruf von hier waere also
    ein Kreis, sobald zwei Anbieter aufeinander zeigen. Ueber `_besorgen` gibt
    es genau einen Sprung und keinen zweiten — ohne Zaehler, ohne Merkliste,
    einfach weil es keinen Weg zurueck gibt.

    Ergaenzt wird bei **jedem** Lesen und nicht einmal beim Abruf. Gespeichert
    bleibt damit, was der Anbieter gesagt hat, und die Ergaenzung ist nie aelter
    als die beiden Kataloge, aus denen sie stammt. Der Preis ist ein Woerterbuch
    ueber die fremde Liste je Aufruf; sie liegt bereits im Speicher, und der
    Aufruf, der sie holen muesste, ist der einzige teure — genau ihn vermeidet
    die Frist darunter.

    Faellt der fremde Katalog aus, bleibt es beim eigenen Wissen. Er ist eine
    Ergaenzung und keine Bedingung; ein Anbieter, den MSM direkt anspricht, darf
    nicht daran haengen, dass ein anderer erreichbar ist.
    """
    if not eigene:
        return eigene
    nach_kennung = await _fremdkatalog(client, spec)
    if not nach_kennung:
        return eigene
    return [
        _anreichern(
            modell, nach_kennung.get(f"{spec.faehigkeiten_praefix}{modell.model_id}")
        )
        for modell in eigene
    ]


async def _fremdkatalog(
    client: httpx.AsyncClient, spec: Anbieter
) -> dict[str, Modell]:
    """Der Katalog, aus dem dieser Anbieter seine Faehigkeiten borgt.

    Nach Kennung geschluesselt, leer wenn es nichts zu borgen gibt oder der
    fremde Katalog gerade ausfaellt. **Nie eine Ausnahme**: er ist eine
    Ergaenzung und keine Bedingung — ein Anbieter, den MSM direkt anspricht,
    darf nicht daran haengen, dass ein anderer erreichbar ist.

    Eigene Funktion, seit es zwei Leser gibt: `_mit_faehigkeiten` reichert damit
    eine **Liste** an, `finde` ein **einzelnes** Modell bei einem Anbieter, der
    gar keine Liste hat. Zweimal derselbe Abruf mit denselben drei
    Fehlerfaellen, einmal geschrieben.
    """
    if spec.faehigkeiten_aus is None:
        return {}
    if spec.faehigkeiten_aus not in ANBIETER:
        logger.warning(
            "Anbieter %s verweist fuer Faehigkeiten auf %s — den gibt es nicht",
            spec.kind,
            spec.faehigkeiten_aus,
        )
        return {}
    try:
        fremde = await _besorgen(
            client, spec.faehigkeiten_aus, erzwingen=False, sofort=True
        )
    except Exception as exc:
        logger.warning(
            "Faehigkeiten fuer %s nicht aus %s nachzutragen error=%s",
            spec.kind,
            spec.faehigkeiten_aus,
            type(exc).__name__,
        )
        return {}
    return {modell.model_id: modell for modell in fremde}


async def _besorgen(
    client: httpx.AsyncClient,
    kind: str,
    *,
    erzwingen: bool,
    sofort: bool,
    schluessel: str | None = None,
) -> list[Modell]:
    """Der gemeinsame Weg fuer Vordergrund und Hintergrund.

    ``sofort`` unterscheidet die beiden. Im Vordergrund (``True``) zaehlt, dass
    niemand wartet: ein vorhandener Stand geht hinaus, notfalls ein abgelaufener.
    Im Hintergrund (``False``) zaehlt das Gegenteil — dort *soll* wirklich
    abgerufen werden, sonst faende die Auffrischung nur den alten Stand vor, den
    sie gerade ersetzen soll, und taete nichts.
    """
    spec = anbieter(kind)

    if spec.catalog_url is None:
        # **Kein Katalog ist kein Fehlschlag.** Der Unterschied ist der ganze
        # Grund fuer diesen frueh gesetzten Ausstieg: ein Fehlschlag wuerde
        # vermerkt, brächte eine Ruhefrist mit sich und liesse die Oberflaeche
        # eine Stoerung melden. Hier gibt es aber nichts zu holen und nichts,
        # was spaeter besser waere — bei Azure heisst ein Modell so, wie der
        # Betreiber sein Deployment genannt hat, und eine Liste dafuer fuehrt
        # der Anbieter nicht (`basis.Anbieter.catalog_url`).
        #
        # Vor dem Schloss und vor jedem Zwischenspeicherzugriff, weil weder das
        # eine noch das andere hier etwas beitraegt: die Antwort ist immer
        # dieselbe und kostet nichts.
        return []

    def bedient(eintrag: _Eintrag, schluessel: str | None) -> bool:
        """Antwortet dieser Stand **diesem** Frager, ohne den Anbieter zu fragen?

        Drei Fragen in einer, weil sie an drei Stellen gemeinsam gestellt werden
        und einzeln keine Entscheidung ergeben: Ist erzwungen worden? Gehoert
        der Stand dem, der fragt? Und ist er frisch genug beziehungsweise die
        Ruhefrist noch offen?
        """
        return (
            not erzwingen
            and _gehoert_dem_frager(eintrag, spec, schluessel)
            and _antwortet_ohne_abruf(
                eintrag,
                datetime.now(timezone.utc),
                schluessel_da=bool(schluessel),
            )
        )

    eintrag = _cache.get(kind)
    if eintrag is not None and bedient(eintrag, schluessel):
        return eintrag.modelle

    if not erzwingen and sofort and _auffrischen_anstossen(kind, schluessel):
        # Ab hier holt jemand anders den frischen Stand. Also nicht warten —
        # aber nur mit einem Stand antworten, der auch diesem Frager gehoert.
        # Ein abgelaufener eigener Stand ist eine brauchbare Antwort, der Stand
        # eines fremden Kontos ist keine.
        if (
            eintrag is not None
            and eintrag.modelle
            and _gehoert_dem_frager(eintrag, spec, schluessel)
        ):
            return eintrag.modelle
        # Ausser es gibt noch gar nichts — dann ist Warten die einzige Chance
        # auf eine brauchbare Antwort. Aber nur kurz, und ohne den Abruf
        # mitzureissen: ``shield`` sorgt dafuer, dass er weiterlaeuft, wenn die
        # Geduld hier abgelaufen ist. Der naechste Aufrufer erbt ihn fertig.
        try:
            await asyncio.wait_for(
                asyncio.shield(_auffrischungen[kind]), ERSTE_WARTE
            )
        except Exception:
            # Zeit abgelaufen, Aufgabe verschwunden oder gescheitert — alles
            # drei enden gleich: mit dem, was da ist. Und da ist notfalls nichts.
            pass
        bestand = _cache.get(kind)
        if bestand is None or not _gehoert_dem_frager(bestand, spec, schluessel):
            # Die laufende Auffrischung holte den Katalog eines anderen Zugangs.
            # „Noch unbekannt" ist hier die einzige ehrliche Antwort; der
            # naechste Aufruf geht ins Schloss und holt den eigenen.
            return []
        return bestand.modelle

    async with _schloss(kind):
        # Zweite Prüfung im Schloss: während des Wartens kann ein anderer
        # Aufruf den Katalog bereits geholt haben — oder erfolglos versucht
        # haben, ihn zu holen. Ohne sie holen ihn beim Start alle gleichzeitig
        # wartenden Anfragen nacheinander erneut, und bei einem haengenden
        # Anbieter wartet jede von ihnen ihre eigene volle ``ABRUF_TIMEOUT``.
        eintrag = _cache.get(kind)
        if eintrag is not None and bedient(eintrag, schluessel):
            return eintrag.modelle

        if spec.katalog_braucht_schluessel and not schluessel:
            # Erst hier gefragt und nicht frueher: die Frage kostet eine
            # Datenbanksitzung und eine Entschluesselung, und beides waere
            # verschwendet, solange ein gueltiger Stand danebenliegt. An dieser
            # Stelle steht fest, dass wirklich abgerufen wird.
            schluessel = await _schluessel_besorgen(kind)
            if not schluessel:
                # Kein Schluessel, also **kein Abruf**. Frueher lief er
                # trotzdem, endete in einem 401 und wurde als Ausfall des
                # Anbieters vermerkt — ein Fehlschlag, den niemand verursacht
                # hatte und der sich anschliessend eine Minute lang selbst am
                # Leben hielt.
                #
                # Vermerkt wird er weiterhin, aber mit anderer Bedeutung:
                # „gerade nicht zu beschaffen, in einer Minute wieder fragen".
                # Ohne den Vermerk liefe bei einem stillstehenden DIS-Sidecar
                # jeder Aufruf erneut in dessen volle Frist, und zwar unter
                # diesem Schloss.
                #
                # ``schluessel_fehlte`` haelt fest, dass die Frist diesen Grund
                # hat und keinen anderen. Wer selbst einen Schluessel mitbringt,
                # loest sie damit auf — sonst bekaeme die Einstellungsseite eine
                # Minute lang die leere Liste zurueck, obwohl der Schluessel
                # neben ihr gespeichert ist, und zeigte „kennt keine Modelle".
                logger.info(
                    "Modellkatalog %s nicht abgerufen grund=kein_schluessel", kind
                )
                ohne = eintrag if eintrag is not None else _Eintrag()
                ohne.fehler_am = datetime.now(timezone.utc)
                ohne.schluessel_fehlte = True
                _cache[kind] = ohne
                return ohne.modelle

            # Mit dem Schluessel in der Hand ist erst jetzt zu erkennen, ob der
            # Stand daneben ueberhaupt zu ihm gehoert. Bei einem offenen Katalog
            # war das schon oben klar; bei einem kontogebundenen ist es genau die
            # Frage, die den Abruf spart oder noetig macht.
            if eintrag is not None and bedient(eintrag, schluessel):
                return eintrag.modelle
        try:
            frisch = await _hole(client, spec, schluessel)
        except Exception as exc:
            logger.warning(
                "Modellkatalog %s nicht abrufbar error=%s", kind, type(exc).__name__
            )
            # Der alte Stand überlebt den Fehlversuch — ohne aufgefrischtes
            # ``geholt_am``, damit nach der Ruhefrist wirklich wieder abgerufen
            # wird. Der Fehlschlag selbst wird jedoch vermerkt: ohne diesen
            # Vermerk liefe der naechste Aufruf sofort wieder in dieselbe
            # 30-Sekunden-Wartezeit, und zwar unter demselben Schloss.
            # Weiterfuehren darf den alten Stand nur, wem er auch gehoert. Bei
            # einem kontogebundenen Katalog liegt hier sonst die Liste eines
            # **anderen** Kontos, und der Fehlschlag machte sie zweimal falsch:
            # ``gescheitert.modelle`` ginge als Antwort an den Falschen zurueck,
            # und der Abdruck darunter schriebe sie ihm anschliessend zu. Fuer
            # ihn ist „noch nichts" die einzige ehrliche Antwort; der Preis ist
            # der verworfene Stand des anderen Zugangs, und ein Abruf mehr ist
            # billiger als eine fremde Modellliste.
            eigener = eintrag is not None and _gehoert_dem_frager(
                eintrag, spec, schluessel
            )
            gescheitert = eintrag if eigener else _Eintrag()
            gescheitert.fehler_am = datetime.now(timezone.utc)
            # Am Schluessel lag es diesmal nicht — er war da, der Anbieter
            # nicht. Ein stehengebliebenes ``True`` von vorhin liesse den
            # naechsten Aufrufer die Ruhefrist ueberspringen und in denselben
            # Ausfall laufen, gegen den sie ihn schuetzen soll.
            gescheitert.schluessel_fehlte = False
            # Und der Abdruck geht auch beim Fehlschlag mit. Ohne ihn galt die
            # Ruhefrist bei einem kontogebundenen Katalog fuer **niemanden**:
            # `_gehoert_dem_frager` verlangt einen passenden Abdruck, ein
            # gescheiterter Eintrag hatte keinen, und damit fiel jeder Aufruf
            # mit Schluessel durch die Frist hindurch in denselben Ausfall —
            # bei OpenAI also jede Chatnachricht, jede mit ihrer vollen
            # ``ABRUF_TIMEOUT`` und einer Entschluesselung obendrauf.
            #
            # Der Abdruck des **gescheiterten** Versuchs ist dabei genau die
            # richtige Grenze: derselbe Zugang wartet, ein anderer nicht. Ein
            # abgelehnter Schluessel ist die Sache seines Kontos, und ein Konto
            # soll nicht das andere aussperren.
            gescheitert.fingerabdruck = (
                _fingerabdruck(schluessel)
                if spec.katalog_braucht_schluessel and schluessel
                else None
            )
            _cache[kind] = gescheitert
            return gescheitert.modelle
        # Erfolg setzt den Eintrag neu auf und loescht damit auch einen
        # frueheren ``fehler_am`` — ein geglueckter Abruf ist die Antwort auf
        # alles, was vorher schiefging. Der Abdruck geht mit: er sagt, wem diese
        # Liste gehoert, und bei einem offenen Katalog gehoert sie allen.
        _cache[kind] = _Eintrag(
            modelle=frisch,
            geholt_am=datetime.now(timezone.utc),
            fingerabdruck=(
                _fingerabdruck(schluessel)
                if spec.katalog_braucht_schluessel and schluessel
                else None
            ),
        )
        return frisch


async def finde(
    client: httpx.AsyncClient,
    kind: str,
    model_id: str,
    *,
    schluessel: str | None = None,
) -> Modell | None:
    """Ein einzelnes Modell, oder ``None``, wenn der Katalog es nicht führt.

    Geht über `modelle` und damit über dieselbe Ergänzung — die Frage „was kann
    dieses eine Modell?" ist genau die, für die `_mit_faehigkeiten` da ist.

    **Der zweite Weg gilt nur für Anbieter ohne eigenen Katalog** (Azure). Dort
    gibt es keine Liste, die sich anreichern liesse, und ohne diesen Nachschlag
    bliebe jedes Modell unbekannt — der Chat rechnete dann mit
    `ai_context_window.RUECKFALL_NUTZBAR_TOKENS`, also mit 6.000 Token an einem
    Modell mit 200.000. Gefragt wird derselbe fremde Katalog wie sonst, nur
    eben nach genau dieser einen Kennung.

    Getroffen wird damit die Konvention, die Microsoft in seinen eigenen
    Beispielen verwendet: das Deployment heisst wie das Modell. Heisst es
    ``prod-chat``, findet der fremde Katalog nichts, und die Antwort ist wieder
    ``None`` — unbekannt, nie geraten.

    Die Bedingung „kein eigener Katalog" ist **tragend** und keine Optimierung.
    Bei einem Anbieter mit Katalog wäre dieser Zweig genau das, was
    `ai_provider_registry.openai` ausschliesst: MSM behauptete die Existenz
    eines Modells, das der Anbieter nicht führt, weil ein Vermittler es kennt.
    Ein Tippfehler im Modellnamen sähe dann aus wie ein gültiges Modell mit
    fremden Fähigkeiten.
    """
    gesucht = (model_id or "").strip()
    if not gesucht:
        return None
    for modell in await modelle(client, kind, schluessel=schluessel):
        if modell.model_id == gesucht:
            return modell

    spec = anbieter(kind)
    if spec.catalog_url is not None:
        return None
    fremd = (await _fremdkatalog(client, spec)).get(
        f"{spec.faehigkeiten_praefix}{gesucht}"
    )
    if fremd is None:
        return None
    # Die **eigene** Kennung behalten und nur die Fähigkeiten borgen: der Name,
    # unter dem MSM dieses Modell anspricht, ist der Deployment-Name des
    # Betreibers. Der Weg durch `_anreichern` ist derselbe wie bei einer
    # angereicherten Liste — damit gelten dort auch dieselben Ausnahmen
    # (``cache_marke_noetig`` und ``name`` wandern nicht mit).
    return _anreichern(Modell(model_id=gesucht, name=gesucht, denkt=False), fremd)


async def fuer_provider(
    client: httpx.AsyncClient,
    db,
    provider,
    *,
    user_id: int,
) -> Modell | None:
    """Das eingestellte Modell eines Providers — mit Schlüssel, wenn nötig.

    **Die eine Stelle, die weiß, dass manche Kataloge einen Schlüssel wollen.**
    Ohne sie musste jeder Aufrufer daran denken, und wer es vergaß, bekam kein
    Fehlerbild, sondern ein stilles ``None``: der Katalog antwortet mit 401,
    `modelle` faengt das ab, und uebrig bleibt "dieses Modell kenne ich nicht".

    Genau das ist zweimal passiert. Bei den Denkstufen fehlten daraufhin im
    Panel alle Stufen (behoben am 18.08.2026 in `routers/ai_providers.py`), und
    beim Kontextfenster rechnete MSM statt mit 829.800 nutzbaren Token mit dem
    Rueckfall von 6.000 — bei einem Modell mit 1.050.000 Token Fenster. Der
    Betreiber sah "Das Kontextfenster dieses Modells ist nicht bekannt" und
    einen Chat, der viel zu frueh zusammengefasst wurde.

    Der Schluesselabruf laeuft im Threadpool: `resolve_api_key` geht ueber den
    DIS-Sidecar und macht dort ein synchrones ``httpx.post``. In der
    Ereignisschleife waere das eine blockierende Runde fuer alle.

    Schlaegt der Abruf fehl, wird **ohne** Schluessel gefragt statt gar nicht:
    bei Anbietern mit offener Liste (OpenRouter) ist das die richtige Antwort,
    und bei den anderen ist ein stilles ``None`` immer noch besser als ein
    Fehler, der den Chat anhaelt.
    """
    from starlette.concurrency import run_in_threadpool

    from services import ai_provider_registry
    from services.ai_provider_service import resolve_api_key

    kind = provider.provider_kind
    modell_id = provider.default_model or ""
    if not modell_id:
        return None

    schluessel: str | None = None
    if ai_provider_registry.anbieter(kind).katalog_braucht_schluessel:
        try:
            schluessel = await run_in_threadpool(
                resolve_api_key, db, provider, user_id
            )
        except Exception as exc:
            logger.warning(
                "Katalogschluessel nicht ladbar kind=%s error=%s",
                kind, type(exc).__name__,
            )

    return await finde(client, kind, modell_id, schluessel=schluessel)


async def aufraeumen() -> None:
    """Laufende Auffrischungen beenden, bevor die Anwendung schliesst.

    Muss **vor** dem Schliessen des HTTP-Clients laufen. Eine Auffrischung haelt
    denselben Client wie der Sendepfad; wird der unter ihr weggezogen, endet sie
    in einem Fehler auf einem geschlossenen Client. Das haelt nichts auf — es
    hinterlaesst nur eine Fehlermeldung beim Herunterfahren, die aussieht, als
    sei etwas kaputt.

    Abbrechen allein genuegt nicht: ``cancel()`` bittet nur darum. Erst das
    Abwarten stellt sicher, dass die Aufgabe wirklich fertig ist, wenn diese
    Funktion zurueckkehrt.
    """
    global _HTTP, _SCHLUESSELQUELLE
    _HTTP = None
    # Und die Schluesselquelle mit. Sie greift auf Datenbank und DIS-Sidecar zu;
    # beide gehen beim Herunterfahren, und ein spaeter Zugriff darauf endete in
    # einer Fehlermeldung, die nach einem Defekt aussieht.
    _SCHLUESSELQUELLE = None
    laufende = [a for a in _auffrischungen.values() if not a.done()]
    for aufgabe in laufende:
        aufgabe.cancel()
    if laufende:
        await asyncio.gather(*laufende, return_exceptions=True)
    _auffrischungen.clear()


def cache_leeren_fuer_tests() -> None:
    """Setzt den gesamten Modulzustand zurueck.

    Auch Schloesser und laufende Aufgaben, nicht nur der Speicher: beide haengen
    an einer Ereignisschleife, und die Testsuite baut je Test eine neue. Ein
    Schloss aus dem vorigen Test waere im naechsten unbrauchbar, eine Aufgabe
    aus einer geschlossenen Schleife nie mehr fertig.
    """
    global _HTTP, _SCHLUESSELQUELLE
    for aufgabe in _auffrischungen.values():
        aufgabe.cancel()
    _auffrischungen.clear()
    _locks.clear()
    _cache.clear()
    _HTTP = None
    _SCHLUESSELQUELLE = None
