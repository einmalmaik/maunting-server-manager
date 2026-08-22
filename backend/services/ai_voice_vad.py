"""Wann hat der Mensch aufgehört zu reden?

Die einzige Fähigkeit, die der Wegfall von OpenAIs Realtime-API wirklich
gekostet hat. Dort hiess sie ``semantic_vad`` und war klug: sie hörte nicht auf
die Lautstärke, sondern auf den Satzbau, und wusste deshalb, dass nach „Ich
hätte gern …" noch etwas kommt. Hier wird gemessen statt verstanden — Lautstärke
und Nachlaufzeit.

**Warum das trotzdem reicht.** Eine falsche Trennung kostet hier keine Antwort,
sondern eine halbe: Luna bekommt „Ich hätte gern" als Frage und fragt zurück.
Bei der Realtime-API kostete dieselbe Fehleinschätzung ein abgebrochenes
Gespräch, weil die Sitzung mit dem Modell verwoben war. Der billigere Fehler
darf die einfachere Erkennung haben.

**Warum im Backend und nicht im Browser.** Im Browser wäre es näher am Mikrofon
und würde Bandbreite sparen — der Ton während der Stille müsste gar nicht erst
übertragen werden. Dagegen steht CLAUDE.md § 4: *„Das Frontend darf niemals
blind entscheiden."* Wann eine Äusserung beginnt und endet, entscheidet, was als
Frage an ein Modell mit Werkzeugen geht; ein manipulierter Browser könnte sonst
Tonstücke zu einer Äusserung zusammenfassen, die so nie gesprochen wurde. Der
zweite Grund ist prosaischer: hier ist es mit pytest prüfbar, dort mit einem
Mikrofon.

**Die Nachlaufzeit ist der einzige Regler, der sich anfühlt.** Zu kurz, und die
KI fällt einem ins Wort, sobald man Luft holt. Zu lang, und jede Antwort beginnt
mit einer Gedenksekunde. 700 Millisekunden sind gemessen an normaler Rede die
Grenze, ab der eine Pause als Satzende gehört wird — kürzer als die
Sprechpause zwischen zwei Sätzen, länger als das Zögern innerhalb eines.
"""

from __future__ import annotations

import array
import math
import sys
from dataclasses import dataclass


#: Abtastrate. Vereinbarung mit Browser, Gehör und Stimme — überall dieselbe.
ABTASTRATE = 24_000

#: Länge eines Messrahmens in Millisekunden. 20 ms ist der übliche Wert für
#: Sprachverarbeitung: kurz genug, um den Beginn eines Wortes nicht zu
#: verschlafen, lang genug, dass ein einzelner Knacks nicht wie Sprache aussieht.
RAHMEN_MS = 20

#: Wie lange es still sein muss, damit die Äusserung als beendet gilt.
#:
#: **Der Wert wächst mit der Länge der Äusserung** (`_stille_grenze`). Der
#: Anlass ist eine Meldung vom 18.08.2026: der Betreiber wurde beim Diktieren
#: eines längeren Auftrags mitten im Satz abgeschnitten, weil er kurz Luft
#: geholt hat. Sein Bild dafür: „ich erzähle was, trinke einen Schluck, das
#: dauert vielleicht 10–20 Sekunden".
#:
#: Eine feste Zahl kann beides nicht: klein genug, damit ein „Ja" sofort
#: durchgeht, und gross genug, damit eine Denkpause mitten im dritten Satz
#: nicht als Ende gilt. 0,7 s waren für das Erste richtig und für das Zweite
#: viel zu knapp.
#:
#: Deshalb ist das hier nur noch der **Ausgangswert** für kurze Äusserungen.
STILLE_SEKUNDEN = 0.7

#: Die Obergrenze, auf die die Stillegrenze bei langer Rede anwächst.
#:
#: Wer zwei Sätze am Stück gesprochen hat, ist erkennbar beim Diktieren und
#: nicht beim Zurufen — dort darf eine Atempause zwei Sekunden dauern, ohne
#: dass der Satz abgeschickt wird. Höher wäre es nicht mehr angenehm: nach dem
#: letzten Wort wartet der Mensch diese Zeit tatsächlich ab.
STILLE_SEKUNDEN_MAX = 2.0

#: Ab welcher gesprochenen Dauer die volle Stillegrenze gilt.
#:
#: Dazwischen wird linear interpoliert: ein 1-Sekunden-„Ja" wird nach 0,7 s
#: abgegeben, ein 6-Sekunden-Satz erst nach 2,0 s. Der Übergang ist gleitend,
#: damit es keine Schwelle gibt, an der sich das Verhalten sprunghaft ändert —
#: so etwas fühlt sich für den Sprechenden wie ein Fehler an.
STILLE_VOLL_AB_SEKUNDEN = 6.0

#: Wie lange am Stück laut sein muss, damit Rede beginnt. Drei Rahmen sind
#: 60 ms — ein Türklappen ist kürzer, die kürzeste gesprochene Silbe länger.
REDE_RAHMEN = 3

#: Wie viel Ton **vor** dem erkannten Beginn mitgenommen wird.
#:
#: Ohne diesen Vorlauf fehlt der Anlaut. Ein „Starte den Server" beginnt leise
#: mit dem „St", überschreitet die Schwelle erst beim „a", und das hörende
#: Modell bekäme „arte den Server" — es versteht dann irgendetwas, nur nicht
#: das. 300 ms decken jeden Anlaut ab.
VORLAUF_SEKUNDEN = 0.3

#: Die absolute Untergrenze, unterhalb derer nichts als Rede gilt — auch dann
#: nicht, wenn der Raum vollkommen still ist und der gemessene Grundpegel
#: entsprechend niedrig. Ohne sie würde in einem stillen Raum das Rauschen des
#: Mikrofons selbst zur Rede erklärt, weil es den Grundpegel um ein Vielfaches
#: übersteigt.
MINDESTPEGEL = 220.0

#: Um welchen Faktor der Grundpegel überschritten sein muss. Der Grundpegel
#: selbst wird laufend nachgeführt, damit ein brummender Lüfter nach wenigen
#: Sekunden nicht mehr als Rede zählt.
PEGELFAKTOR = 3.0

#: Wie träge der Grundpegel nachgeführt wird. Klein heisst träge: ein einzelner
#: lauter Rahmen hebt ihn kaum, ein dauerhaft lauter Raum nach ein paar
#: Sekunden schon.
NACHFUEHRUNG = 0.05

#: Wie lange eine Äusserung höchstens dauert, bevor sie auch ohne Pause
#: abgegeben wird. Die Schranke dahinter, falls jemand ohne Punkt und Komma
#: spricht oder das Mikrofon in einer lauten Umgebung steht.
#:
#: 30 s waren dafür zu knapp: der Betreiber diktiert längere Aufträge am
#: Stück, und die wurden mitten im Satz zerteilt — sichtbar wurde das nicht
#: einmal, denn `Aeusserung.abgeschnitten` wertet die Brücke nicht aus. Zwei
#: Minuten decken auch einen langen zusammenhängenden Auftrag ab und bleiben
#: eine wirksame Schranke gegen ein dauerlautes Mikrofon.
MAX_SEKUNDEN = 120.0

#: Wieviel **laute** Zeit eine Äusserung mindestens enthalten muss.
#:
#: Kürzeres ist ein Husten, ein Stuhlrücken, ein Klicken — und jeder davon würde
#: sonst eine Anfrage auslösen und Geld kosten.
#:
#: Gemessen wird ausdrücklich die laute Zeit und **nicht** die Länge des
#: aufgezeichneten Stücks. Die beiden gehen weit auseinander: um jede Äusserung
#: liegen `VORLAUF_SEKUNDEN` davor und `STILLE_SEKUNDEN` dahinter, zusammen eine
#: ganze Sekunde Polster. Ein Husten von 0,12 Sekunden ergab damit ein Stück von
#: 1,06 Sekunden — und kam durch jede Prüfung, die auf die Gesamtlänge sah.
MIN_SEKUNDEN = 0.35


@dataclass(frozen=True)
class Aeusserung:
    """Ein fertiges Stück gesprochene Rede, bereit zum Abhören."""

    pcm: bytes
    sekunden: float
    #: Ob die Äusserung wegen `MAX_SEKUNDEN` abgegeben wurde und nicht, weil
    #: jemand aufgehört hat zu reden.
    #:
    #: Die Brücke liest das Feld heute **nicht**, und das ist eine bekannte
    #: Lücke, keine Entscheidung: wer dreissig Sekunden am Stück redet, bekommt
    #: eine Antwort auf die erste Hälfte und erfährt nicht, dass die zweite nie
    #: ankam. Es zu ändern heisst, ein Ereignis dafür zu erfinden — und ein
    #: Ereignis ist eine Zeile im Backend, ein `case` im Browser und ein Satz in
    #: zwei Sprachdateien. Wer das tut, fängt hier an.
    abgeschnitten: bool = False


class Pausenerkennung:
    """Nimmt Tonrahmen entgegen und meldet fertige Äusserungen.

    Zustandsbehaftet und **nicht** nebenläufig benutzbar: eine Instanz je
    Sprachsitzung, gefüttert aus der einen Schleife, die auch den Ton empfängt.

    .. code-block:: python

        erkennung = Pausenerkennung()
        while True:
            rahmen = await browser.receive_bytes()
            if (aeusserung := erkennung.fuettern(rahmen)) is not None:
                await verarbeiten(aeusserung)
    """

    def __init__(
        self,
        *,
        abtastrate: int = ABTASTRATE,
        stille_sekunden: float = STILLE_SEKUNDEN,
        stille_sekunden_max: float = STILLE_SEKUNDEN_MAX,
        stille_voll_ab_sekunden: float = STILLE_VOLL_AB_SEKUNDEN,
        vorlauf_sekunden: float = VORLAUF_SEKUNDEN,
        max_sekunden: float = MAX_SEKUNDEN,
        min_sekunden: float = MIN_SEKUNDEN,
    ) -> None:
        self._abtastrate = abtastrate
        self._rahmen_bytes = abtastrate * RAHMEN_MS // 1000 * 2
        self._stille_rahmen = max(1, int(stille_sekunden * 1000 / RAHMEN_MS))
        # Die Obergrenze darf nie unter dem Ausgangswert liegen — sonst würde
        # längeres Sprechen die Pause *verkürzen*, also genau das Gegenteil.
        self._stille_rahmen_max = max(
            self._stille_rahmen, int(stille_sekunden_max * 1000 / RAHMEN_MS)
        )
        self._voll_ab_rahmen = max(1, int(stille_voll_ab_sekunden * 1000 / RAHMEN_MS))
        self._vorlauf_rahmen = max(0, int(vorlauf_sekunden * 1000 / RAHMEN_MS))
        self._max_bytes = int(max_sekunden * abtastrate * 2)
        self._min_laute_rahmen = max(1, int(min_sekunden * 1000 / RAHMEN_MS))

        self._rest = b""
        self._vorlauf: list[bytes] = []
        self._aufnahme: list[bytes] = []
        self._aufnahme_bytes = 0
        self._spricht = False
        self._laute_rahmen = 0
        self._laute_gesamt = 0
        self._stille_zaehler = 0
        #: Der Grundpegel des Raums. Startet bewusst hoch und sinkt in den ersten
        #: Sekunden auf den tatsächlichen Wert: andersherum — von null kommend —
        #: gälte das erste Rauschen als Rede, und die Sitzung begänne mit einer
        #: Äusserung, die niemand gesprochen hat.
        self._grundpegel = MINDESTPEGEL

    @property
    def spricht(self) -> bool:
        """Ob gerade jemand redet. Der Sprachmodus zeigt das als Zustand an."""
        return self._spricht

    @property
    def rede_nachgewiesen(self) -> bool:
        """Ob die laufende Äusserung schon genug **Rede** trägt.

        Dieselbe Messlatte wie beim Abgeben (`min_sekunden` laute Rahmen):
        was darunter bleibt, wird dort als Huster verworfen. Die Brücke nimmt
        das als Tor fürs Dazwischenreden — eine laufende Antwort wird erst
        abgewürgt, wenn die Störung auch als Äusserung durchginge.
        """
        return self._spricht and self._laute_gesamt >= self._min_laute_rahmen

    def fuettern(self, pcm: bytes) -> Aeusserung | None:
        """Nimmt einen Tonrahmen beliebiger Länge entgegen.

        Gibt eine `Aeusserung` zurück, sobald eine fertig ist, sonst ``None``.

        Die Rahmen vom Browser haben keine feste Länge — sie hängen an der
        Puffergrösse der Audio-API und daran, wie das Netz sie zerlegt hat.
        Hier werden sie deshalb auf gleich lange Messrahmen umgeschnitten;
        ``_rest`` trägt, was am Ende übrig bleibt, in den nächsten Aufruf.
        """
        if not pcm:
            return None
        daten = self._rest + pcm
        ergebnis: Aeusserung | None = None
        versatz = 0
        while versatz + self._rahmen_bytes <= len(daten):
            rahmen = daten[versatz : versatz + self._rahmen_bytes]
            versatz += self._rahmen_bytes
            fertig = self._rahmen_verarbeiten(rahmen)
            if fertig is not None and ergebnis is None:
                # Höchstens eine Äusserung je Aufruf. Zwei in einem Rahmen
                # kommen nur vor, wenn der Browser sehr grosse Blöcke schickt;
                # die zweite bleibt dann im Zustand und kommt beim nächsten Mal.
                ergebnis = fertig
        self._rest = daten[versatz:]
        return ergebnis

    def ausklingen(self) -> Aeusserung | None:
        """Was noch da ist, jetzt abgeben — für einen Aufrufer, der zumacht.

        Die Brücke ruft das **nicht**: wer auflegt, will keine Antwort mehr auf
        einen halben Satz, und eine, die nach dem Zumachen noch entstünde, ginge
        ohnehin ins Leere. Die Möglichkeit steht hier trotzdem, weil sie eine
        Zeile ist und der nächste Aufrufer — ein Diktierfeld, ein Testlauf über
        eine Aufnahme — sie braucht, ohne den Zustand von aussen anzufassen.
        """
        if not self._spricht:
            return None
        return self._abgeben(abgeschnitten=False)

    # ── innen ─────────────────────────────────────────────────────────────

    def _stille_grenze(self) -> int:
        """Wie viele stille Rahmen das Ende bedeuten — je nach bisheriger Länge.

        Ein „Ja" soll sofort durchgehen, ein diktierter Absatz eine Atempause
        vertragen. Eine feste Zahl kann nur eines von beidem. Deshalb wächst
        die Grenze linear mit der **laut gesprochenen** Zeit: bei kurzer
        Äusserung `STILLE_SEKUNDEN`, ab `STILLE_VOLL_AB_SEKUNDEN` der volle
        Wert `STILLE_SEKUNDEN_MAX`, dazwischen anteilig.

        Gemessen wird die laute Zeit und nicht die Gesamtlänge: Pausen sollen
        die Geduld nicht auch noch selbst verlängern, sonst wartet die
        Erkennung nach einer langen Pause noch länger und der Sprechende
        bekommt gar keine Antwort mehr.
        """
        anteil = min(1.0, self._laute_gesamt / self._voll_ab_rahmen)
        spanne = self._stille_rahmen_max - self._stille_rahmen
        return self._stille_rahmen + int(spanne * anteil)

    def _rahmen_verarbeiten(self, rahmen: bytes) -> Aeusserung | None:
        pegel = _effektivwert(rahmen)
        schwelle = max(MINDESTPEGEL, self._grundpegel * PEGELFAKTOR)
        laut = pegel >= schwelle

        if not laut:
            # Den Grundpegel **nur** in der Stille nachführen. Ihn während der
            # Rede mitzuziehen hiesse, die Schwelle mit der eigenen Stimme
            # anzuheben — wer lange spricht, würde dabei leiser als seine
            # eigene Schwelle und schnitte sich selbst ab.
            self._grundpegel += (pegel - self._grundpegel) * NACHFUEHRUNG

        if not self._spricht:
            self._vorlauf.append(rahmen)
            if len(self._vorlauf) > self._vorlauf_rahmen:
                self._vorlauf.pop(0)
            if laut:
                self._laute_rahmen += 1
                if self._laute_rahmen >= REDE_RAHMEN:
                    self._spricht = True
                    self._stille_zaehler = 0
                    self._laute_gesamt = self._laute_rahmen
                    # Der Vorlauf ist Teil der Äusserung und enthält die
                    # auslösenden Rahmen bereits — sie wurden oben angehängt.
                    self._aufnahme = list(self._vorlauf)
                    self._aufnahme_bytes = sum(len(teil) for teil in self._aufnahme)
                    self._vorlauf.clear()
            else:
                self._laute_rahmen = 0
            return None

        self._aufnahme.append(rahmen)
        self._aufnahme_bytes += len(rahmen)
        if laut:
            self._stille_zaehler = 0
            self._laute_gesamt += 1
        else:
            self._stille_zaehler += 1
            if self._stille_zaehler >= self._stille_grenze():
                return self._abgeben(abgeschnitten=False)
        if self._aufnahme_bytes >= self._max_bytes:
            return self._abgeben(abgeschnitten=True)
        return None

    def _abgeben(self, *, abgeschnitten: bool) -> Aeusserung | None:
        # Die Nachlaufstille abschneiden, aber nicht die ganze: ein paar Rahmen
        # bleiben stehen, weil ein Wort, das hart an seinem letzten Laut endet,
        # abgehackt klingt und vom hörenden Modell oft falsch verstanden wird.
        # Der Rest ist bezahlte Stille — jede Sekunde davon geht als Audio an
        # den Anbieter und wird in Tokens abgerechnet.
        nachlauf = max(0, self._stille_zaehler - REDE_RAHMEN)
        rahmen = self._aufnahme[: len(self._aufnahme) - nachlauf] if nachlauf else self._aufnahme
        pcm = b"".join(rahmen)
        laute_rahmen = self._laute_gesamt

        self._aufnahme = []
        self._aufnahme_bytes = 0
        self._spricht = False
        self._laute_rahmen = 0
        self._laute_gesamt = 0
        self._stille_zaehler = 0
        self._vorlauf.clear()

        if laute_rahmen < self._min_laute_rahmen:
            # Zu wenig **Rede** für eine Äusserung. Verworfen und nicht
            # gemeldet: ein Husten soll keine Anfrage auslösen.
            #
            # Gezählt werden die lauten Rahmen und nicht die Länge des Stücks.
            # Die Länge trägt Vorlauf und Nachlauf mit — zusammen rund eine
            # Sekunde —, und daran gemessen kam jeder Huster durch.
            return None
        return Aeusserung(
            pcm=pcm,
            sekunden=len(pcm) / (self._abtastrate * 2),
            abgeschnitten=abgeschnitten,
        )


def _effektivwert(rahmen: bytes) -> float:
    """Der Effektivwert (RMS) eines PCM16-Rahmens.

    Effektivwert und nicht Spitzenwert, aus demselben Grund wie bei der Blase in
    `audioWiedergabe.ts`: der Spitzenwert springt bei jedem Knacks auf Anschlag
    und macht ein zufallendes Fenster zu einem Satz. Der Effektivwert folgt der
    Lautstärke, wie ein Ohr sie hört.

    Von Hand und ohne `audioop`: das Modul ist mit Python 3.13 aus der
    Standardbibliothek entfernt worden. Eine Abhängigkeit dafür aufzunehmen
    wäre für zwei Zeilen Rechnung zu viel — und `numpy` steht hier ohnehin nicht.
    """
    if len(rahmen) < 2:
        return 0.0
    werte = array.array("h")
    werte.frombytes(rahmen[: len(rahmen) // 2 * 2])
    if sys.byteorder != "little":  # pragma: no cover - x86 und ARM sind little
        werte.byteswap()
    summe = 0
    for wert in werte:
        summe += wert * wert
    return math.sqrt(summe / len(werte))
