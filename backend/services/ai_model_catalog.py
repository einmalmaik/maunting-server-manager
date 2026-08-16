"""Der Modellkatalog eines Anbieters — Denkfähigkeiten und Kontextfenster.

Dies ist die Antwort auf zwei Fragen, die eine handgepflegte Liste sonst
beantworten müsste: *welches Modell kann wie tief nachdenken?* und *wieviel
Text fasst es auf einmal?* OpenRouter führt beides je Modell selbst und gibt es
ohne Schlüssel heraus, also pflegt MSM nichts.

Gemessen am 2026-08-11 über alle 402 Einträge des Katalogs:

* 272 Modelle können nachdenken.
* Davon nennen **127** eine Stufenliste — und zwar in **20 verschiedenen
  Zusammenstellungen**, von ``['high']`` über ``['medium','low']`` bis
  ``['max','xhigh','high','medium','low','none']``.
* **145** nennen keine, können also nur an oder aus.
* **82** können Nachdenken gar nicht abschalten (``mandatory``).
* Nur **10** rechnen in Token statt in Stufen.

Die zweite Zahl ist der Grund, warum die Auswahl aus diesem Katalog kommen muss
und nicht aus einer Konstante im Programm: eine feste Liste von Stufen wäre bei
der Mehrheit der Modelle falsch. Sie zeigte Stufen an, die es nicht gibt, und
verschwiege welche, die es gibt.

Dasselbe gilt für das **Zwischenspeichern des Prompts**, und aus demselben
Grund. Gemessen am 2026-08-12 über alle 406 Einträge:

* **240** Modelle führen einen Lesepreis (``pricing.input_cache_read``), können
  also überhaupt zwischenspeichern; 166 führen keinen.
* Davon nennen **71** zusätzlich einen Schreibpreis
  (``pricing.input_cache_write``) — und genau diese verlangen eine ausdrückliche
  Marke in der Anfrage. Es sind die Familien, die OpenRouter auch namentlich als
  „explizit“ führt: Anthropic (28), Google (17), Alibaba Qwen (13), OpenAI ab
  GPT-5.6 (6).
* Die übrigen **174** speichern von selbst zwischen (OpenAI bis GPT-5.5, Grok,
  DeepSeek, Moonshot, Mistral, Z.AI). Für sie ist nichts zu tun — eine Marke
  wäre dort bestenfalls wirkungslos.

Beide Preisfelder sind im Katalog durchweg Zeichenketten und nie ``"0"``: ihr
Vorhandensein ist die Aussage, nicht ihr Wert. Deshalb liest
``_modell_aus_openrouter`` sie als Ja/Nein und rechnet nicht mit ihnen — MSM
stellt keine Preise dar, es entscheidet nur, ob eine Marke mitgeht.

Für das Kontextfenster gilt dasselbe Argument, nur mit anderen Zahlen: derselbe
Katalog führt Fenster von 4.096 bis 1.000.000 Token nebeneinander. Eine feste
Zahl wäre für fast jedes Modell falsch — und anders als bei den Stufen fiele es
niemandem auf, denn ein zu klein angenommenes Fenster schlägt nicht fehl, es
lässt den Chat nur früher vergessen.

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging

import httpx

from services.ai_provider_registry import Anbieter, anbieter


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


@dataclass(frozen=True)
class Modell:
    """Ein Modell des Anbieters, so wie MSM es braucht.

    ``stufen`` ist leer, wenn das Modell keine kennt. Das ist **nicht**
    dasselbe wie „kann nicht nachdenken“ — die Mehrheit der denkenden Modelle
    landet genau hier und wird nur an- oder ausgeschaltet.

    ``zwingend`` heißt, dass Nachdenken nicht abschaltbar ist. Für diese
    Modelle darf die Oberfläche kein „aus“ anbieten, sonst verspricht sie
    etwas, das der Anbieter ablehnt.

    ``kontext_tokens`` ist das Kontextfenster — wieviel Text das Modell auf
    einmal lesen kann. Es steht aus demselben Grund hier wie die Denkstufen:
    die Werte gehen von 4.000 bis 1.000.000 und ändern sich mit jedem neuen
    Modell. Eine Zahl im Programm wäre bei fast jedem Modell falsch, und die
    Folge sähe man nicht — der Chat vergäße nur früher als nötig.

    ``max_ausgabe_tokens`` ist, was das Modell antworten darf. Der Platz dafür
    geht vom Fenster ab; wer ihn nicht abzieht, schickt eine Anfrage, die
    hineinpasst, und bekommt trotzdem eine Absage.

    Beides darf ``None`` sein. Der Auto Router führt gar kein Fenster, manche
    Modelle keine Ausgabegrenze.

    ``cache_marke_noetig`` heißt: dieses Modell speichert den Prompt nur dann
    zwischen, wenn die Anfrage es ausdrücklich verlangt. ``False`` deckt **zwei**
    Fälle ab, die für den Sendepfad dasselbe bedeuten — das Modell speichert von
    selbst zwischen, oder es kann es gar nicht. Beide Male ist nichts zu tun,
    und beide Male wäre eine Marke falsch: dort wirkungslos, hier eine Bitte um
    etwas, das nicht angeboten wird.
    """

    model_id: str
    name: str
    denkt: bool
    stufen: tuple[str, ...] = ()
    standard_stufe: str | None = None
    zwingend: bool = False
    kontext_tokens: int | None = None
    max_ausgabe_tokens: int | None = None
    cache_marke_noetig: bool = False


@dataclass
class _Eintrag:
    modelle: list[Modell] = field(default_factory=list)
    geholt_am: datetime | None = None
    #: Wann der letzte Abruf gescheitert ist. Getrennt von ``geholt_am``, weil
    #: beides gleichzeitig gelten kann und Verschiedenes bedeutet: ein alter,
    #: aber brauchbarer Stand plus ein frischer Fehlschlag.
    fehler_am: datetime | None = None


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


def laufzeit_setzen(http: httpx.AsyncClient | None) -> None:
    """Den Client hinterlegen, mit dem im Hintergrund abgerufen wird.

    Ohne ihn gibt es keine Hintergrundauffrischung — und dann verhaelt sich das
    Modul wieder genau wie frueher: der Aufrufer wartet auf den Abruf. Das ist
    Absicht und keine Notloesung. Ein Abruf, der niemandem gehoert, waere
    schlimmer als ein Aufrufer, der wartet.
    """
    global _HTTP
    _HTTP = http


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

    Braucht bewusst **keine** Datenbank und fragt nicht, welche Anbieter
    eingerichtet sind: die Anbieterliste steht im Programm, ein Abruf je Anbieter
    kostet einmal wenige hundert Kilobyte, und ein Start, der auf das Schema
    wartet, waere von der Reihenfolge im ``lifespan`` abhaengig.

    Genau deshalb bleiben schluesselpflichtige Kataloge hier aussen vor: der
    Schluessel steht in der Datenbank, und die wird hier nicht gefragt. Ein
    Versuch ohne ihn endete in einem 401, wuerde als Fehlversuch vermerkt, und
    die Ruhefrist verzoegerte anschliessend den ersten echten Abruf um eine
    Minute — fuer einen Fehler, den niemand gemacht hat.
    """
    for kind in _LESER:
        if anbieter(kind).katalog_braucht_schluessel:
            continue
        _auffrischen_anstossen(kind)


def _antwortet_ohne_abruf(eintrag: _Eintrag, jetzt: datetime) -> bool:
    """Beantwortet dieser Eintrag die Frage, ohne den Anbieter zu fragen?

    Zwei Gruende sprechen gegen einen erneuten Abruf, und sie stehen hier
    zusammen, weil dieselbe Entscheidung zweimal faellt — einmal vor dem
    Schloss und einmal darin:

    * Der Stand ist frisch genug (``geholt_am`` innerhalb ``CACHE_TTL``).
    * Der letzte Versuch ist gerade erst gescheitert (``fehler_am`` innerhalb
      ``FEHLER_RUHE``). Dann ist der alte Stand — notfalls die leere Liste —
      die richtige Antwort, denn ein zweiter Versuch im selben Atemzug kostet
      nur dieselbe Wartezeit noch einmal und liefert dasselbe Nichts.
    """
    if eintrag.geholt_am is not None and jetzt - eintrag.geholt_am < CACHE_TTL:
        return True
    return eintrag.fehler_am is not None and jetzt - eintrag.fehler_am < FEHLER_RUHE


def _positive_zahl(wert: object) -> int | None:
    """Eine Tokenzahl aus fremden Daten, oder ``None``.

    ``bool`` wird ausdruecklich abgewiesen: in Python ist ``True`` eine 1, und
    ein Fenster von einem Token waere schlimmer als gar keine Angabe — es
    schluege nicht fehl, sondern kuerzte den Kontext auf nichts.
    """
    if isinstance(wert, bool) or not isinstance(wert, int):
        return None
    return wert if wert > 0 else None


def _fenster_aus_openrouter(rohdaten: dict) -> tuple[int | None, int | None]:
    """Kontextfenster und Ausgabegrenze eines Katalogeintrags.

    OpenRouter nennt das Fenster **zweimal**: einmal oben als groesstes Fenster
    ueber alle Anbieter dieses Modells, und einmal in ``top_provider`` fuer den
    Anbieter, zu dem im Standardfall geroutet wird. Vorrang hat ``top_provider``
    — das ist das Fenster, das man tatsaechlich bekommt. Der obere Wert kann
    hoeher liegen, und danach zu rechnen hiesse, eine Anfrage zu bauen, die beim
    tatsaechlichen Anbieter nicht mehr hineinpasst.

    ``None`` ist ein regulaeres Ergebnis und kein Fehler: der Auto Router fuehrt
    ``top_provider.context_length: null`` ohne oberen Wert, weil er erst zur
    Laufzeit entscheidet, wohin er geht. Ein solcher Eintrag bleibt gueltig — er
    faellt spaeter nur auf das Rueckfallfenster zurueck.
    """
    top = rohdaten.get("top_provider")
    top = top if isinstance(top, dict) else {}
    kontext = _positive_zahl(top.get("context_length"))
    if kontext is None:
        kontext = _positive_zahl(rohdaten.get("context_length"))
    return kontext, _positive_zahl(top.get("max_completion_tokens"))


def _cache_marke_noetig(rohdaten: dict) -> bool:
    """Verlangt dieses Modell eine ausdrueckliche Cache-Marke?

    Der Katalog sagt es nicht mit einem Schalter, sondern mit einem **Preis**:
    wer einen Schreibpreis fuehrt, rechnet das Anlegen des Zwischenspeichers
    gesondert ab — und rechnet es nur ab, wenn man es verlangt. Wer nur einen
    Lesepreis fuehrt, speichert von selbst zwischen; das Anlegen ist dort
    kostenlos und deshalb nicht aufgefuehrt.

    Die Ableitung ist gemessen und nicht geraten: die 71 Modelle mit
    Schreibpreis sind genau die Familien, die OpenRouter in seiner Doku als
    „explizit“ auffuehrt (Anthropic, Google, Alibaba Qwen, OpenAI ab GPT-5.6).
    Deckungsgleich, ohne Ausreisser in beide Richtungen.

    Geprueft wird nur auf **Vorhandensein**, nicht auf den Wert. Der Katalog
    fuehrt beide Felder durchweg als Zeichenkette und nie als ``"0"``; eine
    Umrechnung in eine Zahl waere eine Genauigkeit, die hier niemand braucht,
    und ein ``float()`` ueber Fremddaten ein Fehlerfall mehr.
    """
    preise = rohdaten.get("pricing")
    if not isinstance(preise, dict):
        return False
    schreibpreis = preise.get("input_cache_write")
    return isinstance(schreibpreis, str) and bool(schreibpreis.strip())


def _modell_aus_openrouter(rohdaten: dict) -> Modell | None:
    """Liest einen Katalogeintrag von OpenRouter.

    Gibt ``None`` zurück, wenn der Eintrag keine brauchbare Kennung hat. Ein
    einzelner kaputter Eintrag darf den ganzen Katalog nicht verwerfen — bei 400
    Einträgen von einem fremden Dienst ist mit genau so etwas zu rechnen.
    """
    model_id = rohdaten.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None

    kontext, ausgabe = _fenster_aus_openrouter(rohdaten)
    cache_marke = _cache_marke_noetig(rohdaten)
    reasoning = rohdaten.get("reasoning")
    if not isinstance(reasoning, dict):
        # Kein Denk-Objekt heißt: dieses Modell denkt nicht. Der Katalog führt
        # das Feld bei allen 272 denkenden Modellen; sein Fehlen ist eine
        # Aussage und keine Lücke.
        return Modell(
            model_id=model_id,
            name=str(rohdaten.get("name") or model_id),
            denkt=False,
            kontext_tokens=kontext,
            max_ausgabe_tokens=ausgabe,
            cache_marke_noetig=cache_marke,
        )

    rohe_stufen = reasoning.get("supported_efforts")
    stufen = (
        tuple(item for item in rohe_stufen if isinstance(item, str) and item)
        if isinstance(rohe_stufen, list)
        else ()
    )
    standard = reasoning.get("default_effort")
    return Modell(
        model_id=model_id,
        name=str(rohdaten.get("name") or model_id),
        denkt=True,
        stufen=stufen,
        standard_stufe=standard if isinstance(standard, str) and standard else None,
        # ``default_enabled`` liefert der Anbieter mit, MSM uebernimmt es
        # bewusst **nicht**: der Sendepfad nennt ``enabled`` immer ausdruecklich
        # (siehe openai_compatible_adapter), fragt also nie nach der
        # Voreinstellung des Modells. Ein gefuelltes, aber nie gelesenes Feld
        # verspricht eine Faehigkeit, die es nicht gibt — und der naechste Leser
        # haelt es fuer eine benutzte Quelle.
        zwingend=bool(reasoning.get("mandatory")),
        kontext_tokens=kontext,
        max_ausgabe_tokens=ausgabe,
        cache_marke_noetig=cache_marke,
    )


def _modell_aus_elevenlabs(rohdaten: dict) -> Modell | None:
    """Liest ein Sprachmodell von ElevenLabs — und weiss dabei fast nichts.

    Das ist der Unterschied zu `_modell_aus_openrouter` und keine
    Nachlässigkeit: ein Sprachmodell hat kein Kontextfenster, keine Denkstufen
    und keinen Tokenpreis. Es hat einen Namen, eine Kennung und eine Antwort auf
    die Frage, ob es überhaupt vorlesen kann. Alles Weitere, was `Modell` bieten
    würde, wäre hier eine erfundene Null.

    ``kontext_tokens=None`` heisst überall im Code „unbekannt" und nie „klein"
    (`ai_context_window.ermitteln`) — hier heisst es zusätzlich „gibt es nicht".
    Beides führt zum selben Verhalten, und das ist der Grund, warum kein drittes
    Feld nötig ist.

    ``can_do_text_to_speech`` ist die eigentliche Arbeit dieser Funktion. Der
    Katalog führt auch Modelle zur Stimmumwandlung; eines davon in der Auswahl
    für den Sprachmodus wäre ein Eintrag, der beim ersten Satz scheitert. Fehlt
    das Feld, wird der Eintrag **nicht** übernommen: ein unbekanntes Modell in
    einer Auswahl ist ein Versprechen, das MSM nicht halten kann.
    """
    model_id = rohdaten.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return None
    if rohdaten.get("can_do_text_to_speech") is not True:
        return None
    name = rohdaten.get("name")
    return Modell(
        model_id=model_id,
        name=name if isinstance(name, str) and name else model_id,
        denkt=False,
    )


#: Je Anbieter ein Leser. Ein zweiter Anbieter ist eine Zeile hier und ein
#: Eintrag in `ai_provider_registry` — kein Umbau.
_LESER = {
    "openrouter": _modell_aus_openrouter,
    "elevenlabs": _modell_aus_elevenlabs,
}


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

    leser = _LESER[spec.kind]
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
    ausgewertet. Der Speicher bleibt dabei nach ``kind`` geschluesselt und nicht
    nach Zugang: welche Modelle es *gibt*, haengt nicht daran, wer fragt. Zwei
    Betreiberschluessel beim selben Anbieter liefern dieselbe Liste, und sie
    zweimal zu halten hiesse, denselben Katalog zweimal zu holen.

    ``erzwingen=True`` umgeht die Frist — das ist der Knopf „Modelle neu laden“
    in den Provider-Einstellungen. Er ist nötig, weil der häufigste Fall nicht
    „unbekanntes Modell“ ist, sondern „der Katalog ist ein paar Stunden alt“.
    Er umgeht **auch** die Ruhefrist nach einem Fehlversuch: wer den Knopf
    drueckt, hat gerade beim Anbieter nachgesehen und will jetzt eine Antwort,
    keine gespeicherte Absage. Und er wartet als einziger Weg noch auf den
    Abruf — das ist genau das, was der Knopf verspricht.
    """
    return await _besorgen(
        client, kind, erzwingen=erzwingen, sofort=True, schluessel=schluessel
    )


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
    jetzt = datetime.now(timezone.utc)

    eintrag = _cache.get(kind)
    if not erzwingen and eintrag is not None and _antwortet_ohne_abruf(eintrag, jetzt):
        return eintrag.modelle

    if not erzwingen and sofort and _auffrischen_anstossen(kind, schluessel):
        # Ab hier holt jemand anders den frischen Stand. Also nicht warten.
        if eintrag is not None and eintrag.modelle:
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
        return bestand.modelle if bestand is not None else []

    async with _schloss(kind):
        # Zweite Prüfung im Schloss: während des Wartens kann ein anderer
        # Aufruf den Katalog bereits geholt haben — oder erfolglos versucht
        # haben, ihn zu holen. Ohne sie holen ihn beim Start alle gleichzeitig
        # wartenden Anfragen nacheinander erneut, und bei einem haengenden
        # Anbieter wartet jede von ihnen ihre eigene volle ``ABRUF_TIMEOUT``.
        eintrag = _cache.get(kind)
        if (
            not erzwingen
            and eintrag is not None
            and _antwortet_ohne_abruf(eintrag, datetime.now(timezone.utc))
        ):
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
            gescheitert = eintrag if eintrag is not None else _Eintrag()
            gescheitert.fehler_am = datetime.now(timezone.utc)
            _cache[kind] = gescheitert
            return gescheitert.modelle
        # Erfolg setzt den Eintrag neu auf und loescht damit auch einen
        # frueheren ``fehler_am`` — ein geglueckter Abruf ist die Antwort auf
        # alles, was vorher schiefging.
        _cache[kind] = _Eintrag(modelle=frisch, geholt_am=datetime.now(timezone.utc))
        return frisch


async def finde(
    client: httpx.AsyncClient,
    kind: str,
    model_id: str,
    *,
    schluessel: str | None = None,
) -> Modell | None:
    """Ein einzelnes Modell, oder ``None``, wenn der Katalog es nicht führt."""
    gesucht = (model_id or "").strip()
    if not gesucht:
        return None
    for modell in await modelle(client, kind, schluessel=schluessel):
        if modell.model_id == gesucht:
            return modell
    return None


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
    global _HTTP
    _HTTP = None
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
    global _HTTP
    for aufgabe in _auffrischungen.values():
        aufgabe.cancel()
    _auffrischungen.clear()
    _locks.clear()
    _cache.clear()
    _HTTP = None
