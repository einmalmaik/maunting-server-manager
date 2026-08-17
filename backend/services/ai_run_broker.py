"""Verteilt die Ereignisse eines Laufs an alle, die gerade zusehen.

Der Lauf arbeitet im Hintergrund; wer zusieht, ist seine Sache. Dieser Vermittler
ist die Trennlinie zwischen beidem: der Lauf **veroeffentlicht**, ein Client
**abonniert**, und keiner von beiden kennt den anderen.

Der entscheidende Teil ist der **Abzug** (``Abzug``): der vollstaendige Stand
eines Laufs zu dem Zeitpunkt, an dem sich jemand anhaengt. Ohne ihn haette ein
Client, der spaeter dazukommt, nur den Rest der Antwort — die ersten Absaetze
waeren fuer ihn nie passiert.

Warum der Abzug hier gehalten wird und nicht aus der Datenbank kommt: die
Datenbank hinkt zwangslaeufig hinterher (sie wird nicht bei jedem einzelnen
Zeichen geschrieben). Ein Abzug aus der Datenbank plus ein danach eroeffnetes
Abonnement haette ein Loch genau zwischen beidem. Hier entstehen Abzug und
Abonnement in *einem* Ausdruck ohne ``await`` dazwischen — es gibt kein Loch.

Das Vorbild ist die Server-Konsole (``console_stream_service``): auch dort
ueberlebt der Inhalt die Verbindung, und beim Wiederverbinden wird nachgeliefert.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
import logging


logger = logging.getLogger(__name__)

# Wie viele Laeufe gleichzeitig im Speicher gehalten werden. Ein Kanal traegt
# den Text *einer* Antwort — ein paar Kilobyte. Die Grenze ist keine
# Speicherfrage, sondern eine Reissleine gegen unbegrenztes Wachstum in einem
# Prozess, der monatelang laeuft.
MAX_KANAELE = 256
# Wie viele Ereignisse ein langsamer Zuhoerer aufstauen darf, bevor er
# uebergangen wird. Ein haengender Client darf den Lauf nicht ausbremsen —
# fuer ihn ist der Abzug beim Wiederanhaengen die Rettung, nicht der Rueckstau.
MAX_RUECKSTAU = 512


def text_abschnitt(inhalt: str) -> dict:
    return {"art": "text", "inhalt": inhalt}


def werkzeug_abschnitt(werkzeug: dict) -> dict:
    return {"art": "tool", "werkzeug": werkzeug}


def denk_abschnitt(inhalt: str) -> dict:
    return {"art": "denken", "inhalt": inhalt}


@dataclass
class Abzug:
    """Der vollstaendige Stand eines Laufs — alles, was ein Zuschauer braucht.

    **Die Abschnitte sind die Ordnung.** Hier standen einmal ``inhalt`` (eine
    Zeichenkette) und ``werkzeuge`` (eine Liste) nebeneinander — zwei Toepfe
    ohne jede Beziehung zueinander. Solange die KI erst alle Werkzeuge rief und
    danach redete, fiel das nicht auf: die Oberflaeche haengte die Werkzeuge
    einfach vor die Antwortblase, und das stimmte zufaellig.

    Sobald die KI **waehrend** der Arbeit spricht — "ich sehe mir erst den
    Status an", Werkzeug, "der laeuft, jetzt die Logs", Werkzeug — ist die
    Reihenfolge zwischen beiden die eigentliche Information. Zwei getrennte
    Felder koennen sie nicht tragen, und sie ist auch nicht rekonstruierbar:
    beim Wiederanhaengen nach einem Seitenwechsel waere jede Anordnung geraten.

    ``inhalt`` gibt es weiterhin, aber als **Ableitung** und nicht als zweiten
    Speicher: der reine Text ist das, was in die Nachricht und zum Anbieter
    geht, die Abschnitte sind das, was der Browser zeichnet.

    **Der Denktext gehört genauso dazu.** Er stand hier zuletzt als flaches
    Feld ``denken`` neben den Abschnitten — derselbe Fehler ein zweites Mal,
    nur eine Zeile tiefer. Die Oberfläche konnte ihn deshalb nur als *einen*
    Kasten über allem zeichnen, und die Gedanken der dritten Runde landeten
    über dem Text der ersten. Jetzt ist er eine dritte Art in derselben Liste,
    und ``denken`` ist die Ableitung daraus.
    """

    run_id: str
    status: str = "running"
    message_id: str | None = None
    abschnitte: list[dict] = field(default_factory=list)
    frage: dict | None = None
    vorschlaege: list[dict] = field(default_factory=list)
    stop_reason: str | None = None

    @property
    def inhalt(self) -> str:
        """Der reine Text des Laufs, Abschnitt fuer Abschnitt.

        **Mit Leerzeile zwischen den Abschnitten.** Frueher stand hier
        ``"".join(...)``, und das war an einer Stelle richtig und an einer
        anderen falsch: *innerhalb* eines Abschnitts sind die Stuecke
        Token-Bruchstuecke und gehoeren nahtlos aneinander, *zwischen* zwei
        Abschnitten liegt aber ein Werkzeugaufruf. Der Prompt-Block ``MITREDEN``
        verlangt vor jedem Werkzeug einen Satz, also endet ein Abschnitt mit
        einem Satzende und der naechste beginnt mit einem Grossbuchstaben.

        Ohne Trenner kam dabei heraus, was ein Betreiber in einer Berichtsmail
        vorfand: „…damit die Mail nur bestaetigte Informationen enthaelt.Ich
        pruefe jetzt den Status…“. Im Chat fiel es nicht auf, weil der die
        Abschnitte einzeln zeichnet — nur wer ``inhalt`` weiterverwendet, sah es.

        ``rstrip`` je Abschnitt, damit aus einem bereits vorhandenen Zeilenumbruch
        keine dritte Leerzeile wird. Fuehrende Leerzeichen bleiben: sie koennen
        zu einem Codeblock gehoeren.
        """
        stuecke = (
            str(abschnitt.get("inhalt") or "").rstrip()
            for abschnitt in self.abschnitte
            if abschnitt.get("art") == "text"
        )
        return "\n\n".join(stueck for stueck in stuecke if stueck)

    @property
    def denken(self) -> str:
        """Der gesamte Denktext des Laufs, Abschnitt für Abschnitt.

        **Ohne Trenner**, anders als ``inhalt`` daneben. Das ist Absicht und
        keine Nachlässigkeit: hier stand früher ein einziges Feld, an das jedes
        Bruchstück mit ``+=`` angehängt wurde. Genau diese Zeichenkette geht in
        ``AiMessage.reasoning`` und von dort in die Berichtsmail — ein Trenner
        an den Rundengrenzen wäre eine stille Änderung an gespeichertem Text.
        Die Gliederung, die es vorher nicht gab, steckt in den Abschnitten.
        """
        return "".join(
            str(abschnitt.get("inhalt") or "")
            for abschnitt in self.abschnitte
            if abschnitt.get("art") == "denken"
        )

    @property
    def werkzeuge(self) -> list[dict]:
        return [
            abschnitt["werkzeug"]
            for abschnitt in self.abschnitte
            if abschnitt.get("art") == "tool"
        ]

    def text_anhaengen(self, stueck: str) -> None:
        """Haengt an den letzten Textabschnitt an — oder faengt einen neuen an.

        Genau hier entsteht das Wechselspiel: laeuft gerade Text, waechst er
        weiter; kam zwischendurch ein Werkzeug, beginnt danach ein **neuer**
        Textabschnitt. Ohne das waere die Antwort wieder ein Block, und die
        Werkzeuge lagen irgendwo daneben.
        """
        if self.abschnitte and self.abschnitte[-1].get("art") == "text":
            self.abschnitte[-1]["inhalt"] += stueck
            return
        self.abschnitte.append(text_abschnitt(stueck))

    def denken_anhaengen(self, stueck: str) -> None:
        """Dasselbe für den Denktext: anhängen, solange gedacht wird.

        Kam dazwischen ein Werkzeug oder ein Satz, beginnt danach ein **neuer**
        Denkabschnitt — und genau der ist die Runde, an deren Stelle die
        Oberfläche ihn zeichnet.
        """
        if self.abschnitte and self.abschnitte[-1].get("art") == "denken":
            self.abschnitte[-1]["inhalt"] += stueck
            return
        self.abschnitte.append(denk_abschnitt(stueck))

    def kopie(self) -> "Abzug":
        """Ein Standbild — **nicht** der weiterlaufende Abzug selbst.

        Wichtig genug fuer eigenen Code: gaebe ``abonnieren`` das lebende Objekt
        heraus, waechst es zwischen Abonnement und Serialisierung weiter. Der
        Client bekaeme denselben Text zweimal — einmal im Abzug und einmal als
        Ereignis aus der Warteschlange.

        Die Abschnitte werden **tief** kopiert, anders als die Listen frueher.
        Eine flache Kopie teilte sich die Woerterbuecher mit dem lebenden Abzug,
        und ``text_anhaengen`` schreibt in genau diese hinein — das Standbild
        haette sich nachtraeglich noch veraendert.
        """
        return Abzug(
            run_id=self.run_id,
            status=self.status,
            message_id=self.message_id,
            abschnitte=[dict(abschnitt) for abschnitt in self.abschnitte],
            frage=self.frage,
            vorschlaege=list(self.vorschlaege),
            stop_reason=self.stop_reason,
        )

    def als_ereignis(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "message_id": self.message_id,
            "sections": [dict(abschnitt) for abschnitt in self.abschnitte],
            # Abgeleitet und mitgeschickt, nicht doppelt gefuehrt. Der Browser
            # braucht den reinen Text an Stellen, an denen die Gliederung nichts
            # beitraegt — die Pruefung "kam ueberhaupt eine Antwort?" etwa.
            "content": self.inhalt,
            "reasoning": self.denken,
            "question": self.frage,
            "proposals": list(self.vorschlaege),
            "stop_reason": self.stop_reason,
        }


@dataclass
class _Kanal:
    abzug: Abzug
    zuhoerer: set[asyncio.Queue] = field(default_factory=set)
    # Ein beendeter Kanal bleibt liegen, damit ein Client, der eine Sekunde zu
    # spaet kommt, noch den Schlussstand bekommt statt ins Leere zu greifen.
    beendet: bool = False


_KANAELE: "OrderedDict[str, _Kanal]" = OrderedDict()
#: Laeufe, deren **laufender** Kanal der Kanalgrenze geopfert wurde. Ihr Abzug
#: ist ab da unvollstaendig: der Lauf legt beim naechsten `veroeffentlichen`
#: einen leeren Kanal an, und `abschnitte()` saehe nur den Rest seit dem
#: Verwerfen. `_finalize_stream` schriebe diese Restliste als `sections_json`,
#: und die Oberflaeche zeichnete nach dem Neuladen aus ihr statt aus `content`
#: — der Anfang der Antwort waere weg, obwohl er daneben steht. Die Marke
#: laesst `abschnitte()` fuer solche Laeufe leer antworten; leer heisst am
#: Speicherort "nimm den content". Ein neues Segment desselben Laufs beginnt
#: bei null und hebt die Marke wieder auf.
_ABZUG_UNVOLLSTAENDIG: set[str] = set()


def _kanal(run_id: str) -> _Kanal:
    kanal = _KANAELE.get(run_id)
    if kanal is None:
        kanal = _Kanal(abzug=Abzug(run_id=run_id))
        _KANAELE[run_id] = kanal
        _aufraeumen()
    _KANAELE.move_to_end(run_id)
    return kanal


def _aufraeumen() -> None:
    """Wirft die aeltesten beendeten Kanaele weg, wenn es zu viele werden."""
    while len(_KANAELE) > MAX_KANAELE:
        for run_id, kanal in list(_KANAELE.items()):
            if kanal.beendet and not kanal.zuhoerer:
                del _KANAELE[run_id]
                break
        else:
            # Nur noch laufende oder beobachtete Kanaele: dann ist der aelteste
            # dran. Lieber ein Lauf ohne Live-Bild als ein Prozess ohne Speicher —
            # der Verlauf steht ohnehin in der Datenbank.
            aeltester, _ = next(iter(_KANAELE.items()))
            logger.warning(
                "AI-Kanalgrenze erreicht, aeltester laufender Kanal verworfen run_id=%s",
                aeltester,
            )
            # Erst wecken, dann wegwerfen. Ohne das `beenden` bekamen hier
            # angehängte Zuhörer nie ihr `(None, None)`: `lauf_verfolgen`
            # stand in `await warteschlange.get()` ohne Frist, der weiterlaufende
            # Lauf legte sich über `_kanal()` einen **neuen** Kanal an, und auch
            # das abschließende `beenden(run_id)` traf nur diesen. Die
            # SSE-Verbindung blieb dann für immer offen, im Browser fiel
            # `setStreaming(false)` nie, und die Eingabe blieb gesperrt — der
            # Kommentar darüber verspricht „ohne Live-Bild“, nicht „hängend“.
            beenden(aeltester)
            del _KANAELE[aeltester]
            _ABZUG_UNVOLLSTAENDIG.add(aeltester)


def eroeffnen(run_id: str) -> None:
    """Legt den Kanal an, **bevor** der Lauf zu arbeiten beginnt.

    Ohne das gaebe es ein Wettrennen: der Streamendpunkt abonniert, waehrend der
    Lauf noch nichts veroeffentlicht hat — dann existiert kein Kanal, und der
    Client bekaeme faelschlich "laeuft hier nicht" zu hoeren, obwohl der Lauf
    gerade erst anlaeuft.
    """
    _kanal(run_id)


def veroeffentlichen(run_id: str, ereignis: str, daten: dict) -> None:
    """Schreibt ein Ereignis in den Abzug und an alle Zuhoerer.

    Wird ausschliesslich aus dem Lauf heraus gerufen, also auf der Ereignis-
    schleife der Anwendung. Deshalb reicht eine gewoehnliche ``asyncio.Queue``
    ohne Schloss: es gibt keinen zweiten Schreiber.
    """
    kanal = _kanal(run_id)
    abzug = kanal.abzug

    # Den Abzug fortschreiben. Genau diese Buchfuehrung macht das spaetere
    # Anhaengen vollstaendig statt bruchstueckhaft.
    if ereignis == "message":
        abzug.message_id = daten.get("message_id")
    elif ereignis == "delta":
        abzug.text_anhaengen(str(daten.get("content") or ""))
    elif ereignis == "reasoning":
        abzug.denken_anhaengen(str(daten.get("content") or ""))
    elif ereignis == "tool":
        abzug.abschnitte.append(werkzeug_abschnitt(daten))
    elif ereignis == "question":
        abzug.frage = daten
    elif ereignis in {"proposal", "action"}:
        abzug.vorschlaege = [
            vorhandener for vorhandener in abzug.vorschlaege
            if vorhandener.get("id") != daten.get("id")
        ] + [daten]
    elif ereignis == "run":
        abzug.status = str(daten.get("status") or abzug.status)
        abzug.stop_reason = daten.get("stop_reason")

    # Ein neues Segment schreibt eine neue Nachricht — der bisherige Text gehoert
    # zur vorherigen und darf nicht in die neue hineinlaufen.
    # Der Denktext braucht hier keine eigene Zeile mehr: er steht in derselben
    # Liste und geht mit ihr.
    if ereignis == "segment":
        abzug.abschnitte = []
        abzug.frage = None
        # Ein neues Segment ist eine neue Nachricht: ab hier ist der Abzug
        # wieder vollstaendig, auch wenn ein frueherer Kanal geopfert wurde.
        _ABZUG_UNVOLLSTAENDIG.discard(run_id)

    for warteschlange in list(kanal.zuhoerer):
        if warteschlange.qsize() >= MAX_RUECKSTAU:
            # Der Zuhoerer kommt nicht mit. Ihn hier zu bedienen hiesse, den Lauf
            # an sein Tempo zu binden.
            continue
        warteschlange.put_nowait((ereignis, daten))


def abonnieren(run_id: str) -> tuple[Abzug, asyncio.Queue] | None:
    """Haengt einen Zuschauer an. Gibt Abzug **und** Abonnement zusammen zurueck.

    Zusammen, weil getrennt ein Loch entstuende: zwischen "Stand holen" und
    "ab jetzt zuhoeren" duerfen keine Ereignisse verlorengehen. Hier liegt kein
    ``await`` dazwischen, also kann die Ereignisschleife nicht dazwischenfunken.

    ``None`` heisst: dieser Lauf laeuft in diesem Prozess nicht (mehr). Der
    Aufrufer muss dann aus der Datenbank antworten.
    """
    kanal = _KANAELE.get(run_id)
    if kanal is None:
        return None
    warteschlange: asyncio.Queue = asyncio.Queue()
    kanal.zuhoerer.add(warteschlange)
    _KANAELE.move_to_end(run_id)
    return kanal.abzug.kopie(), warteschlange


def abmelden(run_id: str, warteschlange: asyncio.Queue) -> None:
    kanal = _KANAELE.get(run_id)
    if kanal is not None:
        kanal.zuhoerer.discard(warteschlange)


def beenden(run_id: str) -> None:
    """Meldet: hier kommt nichts mehr. Weckt alle Zuhoerer ein letztes Mal."""
    # Die Unvollstaendigkeits-Marke endet mit dem Lauf, sonst wuechse die Menge
    # mit jeder Opferung um eine Kennung, die nie wieder gebraucht wird. Beim
    # Opfern selbst ist die Reihenfolge in `_aufraeumen` entscheidend: erst
    # dieses `beenden`, dann die Marke — sie ueberlebt diesen Aufruf also.
    _ABZUG_UNVOLLSTAENDIG.discard(run_id)
    kanal = _KANAELE.get(run_id)
    if kanal is None:
        return
    kanal.beendet = True
    for warteschlange in list(kanal.zuhoerer):
        warteschlange.put_nowait((None, None))


def laeuft(run_id: str) -> bool:
    kanal = _KANAELE.get(run_id)
    return kanal is not None and not kanal.beendet


def abschnitte(run_id: str) -> list[dict]:
    """Die Gliederung des laufenden Segments — fuer das Festhalten am Ende.

    Der Lauf koennte sie auch selbst mitschreiben, aber dann gaebe es sie
    zweimal: einmal hier, wo sie ohnehin fuer die Zuschauer entsteht, und einmal
    dort. Zwei Listen, die dasselbe meinen, laufen frueher oder spaeter
    auseinander — und die Reihenfolge ist genau das, was hier nicht schiefgehen
    darf.
    """
    kanal = _KANAELE.get(run_id)
    if kanal is None:
        return []
    if run_id in _ABZUG_UNVOLLSTAENDIG:
        # Der laufende Kanal wurde der Kanalgrenze geopfert; was hier steht,
        # ist nur der Rest seit dem Verwerfen. Lieber keine Gliederung als
        # eine, die den Anfang der Antwort verschluckt — leer heisst beim
        # Speichern "nimm den content".
        return []
    return [dict(abschnitt) for abschnitt in kanal.abzug.abschnitte]


def neues_segment(run_id: str) -> None:
    """Setzt Text und Frage zurueck, weil eine neue Nachricht beginnt."""
    veroeffentlichen(run_id, "segment", {"run_id": run_id})


def zuruecksetzen_fuer_tests() -> None:
    _ABZUG_UNVOLLSTAENDIG.clear()
    _KANAELE.clear()
