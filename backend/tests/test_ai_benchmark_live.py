"""Was der Benutzer wartet — gemessen statt geschaetzt.

Die uebrigen Tests fragen *ob* etwas funktioniert. Diese Datei fragt **wie
lange es dauert, bis der Mensch davor etwas sieht** — und wo die Zeit
hingegangen ist. Das ist eine andere Frage, und sie braucht andere Zahlen.

Gemessen wird an ``segment_ausfuehren``, also am echten Lauf mit echtem
Anbieter, echten Werkzeugen und echter Datenbank. Nicht gemessen wird der
Transportweg (SSE ueber HTTP zum Browser); der liegt hinter dem Vermittler und
kostet Millisekunden, waehrend hier Sekunden gesucht werden.

**Die vier Zahlen, auf die es ankommt**

``ttft_text``
    Wann erscheint das **erste sichtbare Zeichen** der Antwort. Das ist die
    Zahl, ueber die sich der Betreiber beschwert hat: "dann steht da erstmal
    20, 30, 40 Sekunden 'Antwort wird erstellt'". Alles andere ist Diagnose;
    das hier ist das Symptom.

``stille_max``
    Die laengste Pause zwischen zwei Dingen, die der Benutzer zu sehen bekommt.
    Eine Antwort, die in 40 Sekunden fertig ist und dabei nie laenger als zwei
    Sekunden schweigt, fuehlt sich schnell an. Eine, die nach 8 Sekunden fertig
    ist und dazwischen 7 Sekunden nichts sagt, fuehlt sich kaputt an. Der
    Betreiber hat nach "gefuehlt Echtzeit" gefragt, nicht nach einem kleineren
    Gesamtwert — deshalb steht diese Zahl gleichberechtigt neben ``gesamt``.

``loop_block_max``
    Wie lange die Ereignisschleife am Stueck nicht zur Verfuegung stand. Das
    ist **die Zeit, in der das ganze Panel steht** — jede andere Anfrage jedes
    anderen Benutzers wartet genauso lange. Der beobachtete Fall ("ich habe ein
    Backup von jedem Server erstellen lassen, danach hat die Seite nicht mehr
    geladen, erst nachdem der Auftrag fertig war") ist genau diese Zahl. Sie
    gehoert in einen Benchmark, weil sie sonst niemandem auffaellt: der
    messende Lauf selbst merkt nichts davon.

``anbieterzeit`` gegen ``msm_zeit``
    Die Aufteilung, ohne die jede Optimierung raten muesste. ``anbieterzeit``
    ist die Summe der Zeit *innerhalb* von ``stream_chat_completion`` — daran
    laesst sich nur ueber weniger/kleinere Anfragen etwas aendern. ``msm_zeit``
    ist der Rest: Vorbereitung, Werkzeuge, Datenbank, Kompression. Das ist der
    Teil, der uns gehoert.

**Die Phasen: wo die Zeit hingeht, nicht nur dass sie hingeht.**

Die vier Zahlen oben beschreiben das Symptom. Sie sagen nicht, welche
Aenderung hilft — und zwei davon sind Sammelposten, in denen sich sehr
verschiedene Ursachen verstecken. ``anbieterzeit`` und ``msm_zeit`` sind
deshalb zusaetzlich in sieben Stationen zerlegt, die zusammen wieder die
Gesamtzeit ergeben:

    Anlauf → Ruest → [ Netz → Prefill → Dekod → Zwisch ]* → Nacharb

``Netz`` ist Verbindung und Warteschlange bis zu den Antwortkopfzeilen,
``Prefill`` das Lesen der Eingabe durch das Modell, ``Dekod`` das Schreiben
der Antwort, ``Zwisch`` alles zwischen zwei Runden (Werkzeuge, Schwaerzung,
Datenbank). Die Unterscheidung ist nicht akademisch: eine kleinere Anfrage
senkt ``Prefill`` und sonst nichts, weniger Runden senken ``Netz`` und
``Zwisch``, eine kuerzere Antwort senkt ``Dekod``. Wer nur die Summe sieht,
waehlt den Hebel per Vermutung.

``Prefill`` steht dabei neben ``katalog`` in der Rueckflusszeile: der
Werkzeugkatalog geht als ``tools`` ueber dieselbe Leitung wie die
Nachrichten, zaehlt in ``anfrage`` aber nicht mit. Beide Zahlen zusammen
beantworten die Frage, ob ein geschrumpfter Katalog ueberhaupt Sekunden
brächte oder nur Tokens spart.

``nb`` schliesslich ist die Werkzeugsumme geteilt durch die belegte
Wanduhrzeit — **1,0 heisst, es lief nie mehr als ein Werkzeug gleichzeitig**.
Zusammen mit ``Rd/Wz`` (Runden gegen Werkzeugaufrufe) trennt sie zwei Fehler,
die sich gleich anfuehlen: ein Dienst, der nebeneinander koennte und es nicht
tut, gegen ein Modell, das je Runde nur ein Werkzeug bestellt. Beim zweiten
ist Nebenlaeufigkeit im Ausfuehren wirkungslos.

**``ttfs`` — was der Sprachmodus wartet.** ``ttft_text`` ist die Zahl des
Chats: dort erscheint jedes Zeichen sofort. Die Stimme braucht einen
abgeschlossenen Satz, und ``ai_voice_bridge.Belegfilter`` gibt Codebloecke
gar nicht heraus. ``ttfs`` misst mit **demselben** Filter, wann das erste Wort
an die Stimme gehen koennte. Nicht gemessen sind die zwei Posten davor und
danach, die es nur im Sprachmodus gibt: die Stillepause der Spracherkennung
(``ai_voice_vad.STILLE_SEKUNDEN``, 0,7 s) und der Verbindungsaufbau zur
Sprachausgabe. Sie stehen hier, damit niemand ``ttfs`` fuer die ganze
Wartezeit des Sprechers haelt.

**Ein Szenario hat ein Gedächtnis, die übrigen nicht.** Vor jeder Messung wird
der Verlauf geleert, und das muss so bleiben — sonst misst das zwölfte Szenario
den Kontext der elf davor mit. Der Preis dafür war, dass **jede** gemessene
Anfrage aus genau drei Nachrichten bestand (Systemprompt, Lageblock, Frage) und
ein Werkzeugkontext nie entstand: alles, was mit Verlauf und Werkzeugrückfluss
zu tun hat, war hier per Konstruktion unsichtbar. Eine Änderung an der Größe
dieses Rückflusses hätte einen Nullbefund geliefert, und ein Nullbefund ist kein
Freispruch.

``kontext_folge`` ist die Ausnahme: drei Fragen nacheinander in **derselben**
Unterhaltung. Jede Frage ist ein eigener Lauf und steht als ``kontext_folge``,
``kontext_folge#2``, ``kontext_folge#3`` in Tabelle und Protokoll. Messbar
werden damit der wachsende Verlauf, der Präfix zweier aufeinander folgender
Anfragen **und der Werkzeugrückfluss**.

Beim Rückfluss ist die Stelle enger, als die drei Fragen vermuten lassen: er
wird **einmal je Durchgang** gemessen, in der ersten Runde von Frage 2.
``_recent_tool_results`` nimmt nur die Zeilen des jüngsten Laufs, der überhaupt
Zeilen beigesteuert hat (``rows[0].run_id``) — für Frage 2 ist das der Lauf von
Frage 1 mit ihren Blueprints. Was Frage 3 sieht, hängt daran, was Frage 2 getan
hat: rief sie selbst ein Werkzeug, rückt der jüngste Lauf nach und Frage 3 sieht
nur dessen kleines Ergebnis; rief sie keines, sieht Frage 3 denselben Block ein
zweites Mal. Beides ist keine zweite unabhängige Messung. Frage 3 trägt
Verlaufslänge und Präfix bei, nicht den Rückfluss.

**Warum Blueprints und nicht die Doku.** ``_recent_tool_results`` nimmt Doku-
und Skillwerkzeuge ausdrücklich aus (``tool_name.notin_(SKILL_TOOLS |
DOCS_TOOLS)``) — ein Szenario, das auf die Doku zielt, füllt den Rückfluss mit
null Zeichen. Nachgeprüft: zwei Dokuergebnisse mit 3.000 und 37.000 Zeichen
ergeben ``None``. ``read_blueprint`` steht in keiner der beiden Mengen, liest
echte Repo-Daten ohne Docker und ohne Node und liefert je Aufruf gemessene
2.567 bis 5.824 Zeichen. Die sieben Blueprints der ersten Frage ergeben zusammen
26.031 Zeichen und laufen damit sicher in den Deckel von 16.000
(``MAX_TOOL_RESULT_CONTEXT_CHARS``), sichtbar an der Marke ``[...gekuerzt]``.
Wer das Szenario auf die Doku zurückdreht, misst hier wieder nichts.

Dazu zwei Zahlen, die es vorher nicht gab: der Verbrauch **je Runde**
statt nur als Summe (eine Summe beantwortet nicht, welche Runde den
Zwischenspeicher traf) und der gemeinsame **Präfix** zweier aufeinander
folgender Anfragen in Zeichen — deterministisch, während die gemeldete
Cache-Quote bei drei Wiederholungen zwischen 0 % und 100 % springt.

**Sichtbar ist nicht dasselbe wie veroeffentlicht.** Der Lauf schreibt seine
Ereignisse mit ``ai_run_broker.veroeffentlichen`` in eine Warteschlange — das
kostet nichts und gelingt auch, waehrend die Schleife blockiert ist. Beim
Browser ankommen kann das Ereignis aber erst, wenn die Schleife wieder laeuft.
Deshalb fuehrt der Benchmark zwei Zeitachsen: wann etwas *entstand* und wann es
frueheste *zu sehen* war. Die Differenz ist kein Messfehler, sondern der
gesuchte Befund.

**Ausfuehren**

    cd backend
    MSM_BENCH_AI_KEY=sk-or-v1-... \\
    MSM_BENCH_AI_MODEL=openai/gpt-5.6-luna \\
    MSM_BENCH_LABEL=baseline \\
    python -m pytest tests/test_ai_benchmark_live.py -o addopts="" -q -s

``-o addopts=""`` ist Pflicht und keine Geschmacksfrage. Die ``pytest.ini``
setzt ``-n auto --dist loadfile --timeout=120`` — beides waere hier falsch:
``-n auto`` verteilt auf acht Prozesse, dann messen acht Laeufe gegeneinander
und jede Zahl ist Rauschen; ``--timeout=120`` bricht die laengeren Szenarien
mitten in der Messung ab. Die Zeitgrenze uebernimmt ``MSM_BENCH_TIMEOUT``, und
zwar je Einzelmessung statt fuer den ganzen Durchlauf.

Ohne ``MSM_BENCH_AI_KEY`` wird uebersprungen. Die Datei kostet Tokens und
braucht Netz; sie gehoert nicht in die normale Suite.

Das Ergebnis landet als JSON unter ``backend/logs/ai-benchmark/`` (gitignoriert)
und wird beim naechsten Lauf mit anderem ``MSM_BENCH_LABEL`` vergleichbar.

**Die zweite Messung: Gleichzeitigkeit.**

    cd backend
    MSM_BENCH_PARALLEL=1,10,50,200,1000 \\
    python -m pytest tests/test_ai_benchmark_live.py -o addopts="" -q -s

Sie braucht **keinen** Schluessel und kein Netz und laeuft unabhaengig von der
Messung oben: der Anbieter ist eine Attrappe. Das ist keine Sparsamkeit,
sondern die Voraussetzung — mit echtem Anbieter waere die Zahl seine
Warteschlange und nicht unsere Grenze. Gefragt wird: wieviele Laeufe kommen je
Stufe durch, woran scheitern die uebrigen, wie lange steht dabei die
Ereignisschleife, und **welche Grenze greift zuerst**. Einzelheiten stehen am
Anfang des Abschnitts "Gleichzeitigkeit" weiter unten.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiConversation, AiProvider, Server, User
from services import (
    ai_chat_service,
    ai_context_service,
    ai_context_window,
    ai_memory_service,
    ai_reasoning,
    ai_run_broker,
    ai_skill_service,
    ai_stream_service,
)
from services.ai_provider_service import _operator_aad
from services.ai_voice_bridge import Belegfilter
from services.dis_client import DisClient


BENCH_KEY = os.environ.get("MSM_BENCH_AI_KEY", "").strip()
BENCH_MODEL = os.environ.get("MSM_BENCH_AI_MODEL", "openai/gpt-5.6-luna").strip()
BENCH_BASE_URL = os.environ.get(
    "MSM_BENCH_AI_BASE_URL", "https://openrouter.ai/api/v1"
).strip()
BENCH_LABEL = os.environ.get("MSM_BENCH_LABEL", "baseline").strip()
#: Wie oft jedes Szenario laeuft. Eine einzelne Messung gegen ein
#: Sprachmodell ist Rauschen — dasselbe Szenario schwankt zwischen zwei
#: Laeufen deutlich. Der Median ueber drei ist stabil genug, um eine
#: Verbesserung von einer Laune zu unterscheiden, und billig genug, dass der
#: ganze Durchlauf in Minuten bleibt.
BENCH_WIEDERHOLUNGEN = int(os.environ.get("MSM_BENCH_RUNS", "3"))
#: Reissleine je Einzelmessung. Ein haengender Anbieter soll den Durchlauf
#: nicht aufhalten — er soll als Ausfall in der Tabelle stehen.
BENCH_TIMEOUT = float(os.environ.get("MSM_BENCH_TIMEOUT", "180"))
#: Kuenstliche Dauer je Werkzeugaufruf, in Sekunden. Voreingestellt aus.
#:
#: **Warum das ueberhaupt noetig ist.** In der Testumgebung sind alle Werkzeuge
#: sofort fertig: die Datenbank liegt in SQLite, es gibt keinen Node, keinen
#: Docker, keine SSH-Verbindung. Der erste Baseline-Lauf mass deshalb 0,3
#: Sekunden Werkzeugzeit ueber saemtliche Szenarien — und die Frage, die der
#: Betreiber gestellt hat ("ich habe von jedem Server ein Backup erstellen
#: lassen, danach lud die Seite nicht mehr"), war damit **nicht beantwortbar**.
#: Ein Benchmark, der die interessante Groesse nicht erzeugen kann, misst sie
#: auch nicht.
#:
#: Mit einer gesetzten Dauer schlaeft der Werkzeugaufruf **synchron**, genau wie
#: ein echter Aufruf an einen Node es tut. Der Waechter beantwortet dann die
#: eigentliche Frage: steht das Panel waehrenddessen? Das ist kein
#: nachgestelltes Ergebnis — geschlafen wird an der Stelle, an der echte Arbeit
#: passiert, und gemessen wird, was der Prozess daraus macht.
BENCH_TOOL_DELAY = float(os.environ.get("MSM_BENCH_TOOL_DELAY", "0"))
#: Nur diese Szenarien fahren, kommagetrennt. Leer heisst alle.
#:
#: Aus einem konkreten Anlass: `chat_trivial` schwankte in einem Vergleichslauf
#: zwischen 4,6 und 11,1 Sekunden — bei **identischer** Tokenzahl. Ob das an
#: einer Aenderung liegt oder am Anbieter, beantwortet nur eine Messung mit mehr
#: Wiederholungen, und dafuer den ganzen Satz zu fahren kostet zwoelfmal soviel
#: wie noetig.
BENCH_NUR = {
    name.strip() for name in os.environ.get("MSM_BENCH_ONLY", "").split(",")
    if name.strip()
}

#: Ab wann ein ueberzogener ``asyncio.sleep`` als Blockade gilt. Der Takt des
#: Waechters ist 20 ms; alles unter 50 ms ist gewoehnliches Rauschen des
#: Betriebssystems und kein blockierter Aufruf.
WAECHTER_TAKT = 0.02
WAECHTER_SCHWELLE = 0.05

#: Wieviele Laeufe gleichzeitig, kommagetrennt: ``MSM_BENCH_PARALLEL=1,10,50,200``.
#: Leer heisst: die Gleichzeitigkeitsmessung wird uebersprungen.
#:
#: **Eine eigene Variable und nicht ``MSM_BENCH_AI_KEY``**, weil es eine andere
#: Messung ist. Der Szenariendurchlauf oben fragt den Anbieter etwas und misst,
#: was der Mensch davor wartet — dafuer braucht er ein echtes Modell. Diese
#: Messung fragt, **wo MSM selbst kippt**, wenn viele Laeufe gleichzeitig
#: arbeiten. Ein echter Anbieter waere dafuer nicht nur teuer (tausend Antworten
#: kosten Geld), er waere schaedlich: die Zahl, die dabei herauskaeme, waere
#: seine Warteschlange und sein Kontingent, nicht unsere Grenze.
BENCH_PARALLEL_STUFEN = [
    int(stufe.strip())
    for stufe in os.environ.get("MSM_BENCH_PARALLEL", "").split(",")
    if stufe.strip().isdigit() and int(stufe.strip()) > 0
]
#: Wie lange der gefaelschte Anbieter je Runde braucht, in Sekunden.
#:
#: Ohne Wartezeit waere die Messung wertlos: ein Anbieter, der sofort antwortet,
#: laesst die Laeufe hintereinander durchlaufen statt nebeneinander, und
#: "gleichzeitig" waere nur ein Wort in der Ueberschrift. Mit einer Wartezeit
#: haengen alle Laeufe zur selben Zeit im ``await`` — genau der Zustand, den der
#: Betreiber fuerchtet.
BENCH_PARALLEL_LATENZ = float(os.environ.get("MSM_BENCH_PARALLEL_LATENZ", "0.30"))
#: Reissleine je Einzellauf der Gleichzeitigkeitsmessung. Deutlich groesser als
#: die Latenz mal Runden: ein Lauf, der wartet, weil tausend andere die Schleife
#: belegen, ist kein Ausfall, sondern der gesuchte Befund. Erst wenn er gar nicht
#: mehr drankommt, ist er einer.
BENCH_PARALLEL_TIMEOUT = float(os.environ.get("MSM_BENCH_PARALLEL_TIMEOUT", "300"))

NUR_MIT_SCHLUESSEL = pytest.mark.skipif(
    not BENCH_KEY,
    reason="MSM_BENCH_AI_KEY nicht gesetzt — Benchmark uebersprungen",
)
NUR_MIT_STUFEN = pytest.mark.skipif(
    not BENCH_PARALLEL_STUFEN,
    reason="MSM_BENCH_PARALLEL nicht gesetzt — Lastmessung uebersprungen",
)

# Zweites Schloss neben den beiden Schluesselpruefungen. Der Schluessel
# entscheidet, ob dieser Lauf etwas messen kann; die Marke entscheidet, ob er im
# Normallauf ueberhaupt gesammelt wird. Ein Schloss allein hat nicht gehalten:
# eine Nachbardatei las den Schluessel beim Import selbst aus dem
# Heimatverzeichnis und gab damit bei einem schlichten `python -m pytest` Geld
# aus. Ob ein Lauf kostet, darf nicht davon abhaengen, welche Dateien auf dem
# Rechner liegen. Der oben dokumentierte Aufruf mit `-o addopts=""` hebt beide
# Filter ohnehin auf.
pytestmark = pytest.mark.live


# ── Der Waechter ─────────────────────────────────────────────────────────


async def _waechter(
    blockaden: list[tuple[float, float]],
    beobachter: Callable[[], None] | None = None,
) -> None:
    """Merkt sich, wann die Ereignisschleife nicht zur Verfuegung stand.

    Eine Koroutine, die 20 ms schlafen will und erst nach 3 Sekunden wieder
    drankommt, hat 3 Sekunden lang nicht existiert — und genauso ging es in
    dieser Zeit jeder Anfrage jedes anderen Benutzers. Das ist der ganze Trick:
    der Waechter misst nichts am Lauf, er misst die **Verfuegbarkeit des
    Prozesses**, und das ist die Groesse, die der Betreiber als "die Seite laedt
    nicht mehr" erlebt hat.

    Aufgezeichnet wird das Fenster, nicht nur die Dauer. Damit laesst sich
    spaeter fuer jedes Ereignis ausrechnen, wann es fruehestens sichtbar war.

    ``beobachter`` ist ein zweiter Blick im selben Takt — die Lastmessung
    braucht den Hoechststand des Verbindungspools, und der ist nur zu sehen,
    solange er hoch ist. Als Parameter und nicht als eigene Schleife: eine
    zweite Aufgabe, die im selben Takt schlaeft, waere ein zweiter Messfuehler
    fuer dieselbe Groesse und wuerde bei jeder Blockade eine andere Zahl
    liefern als dieser hier.
    """
    letzte = perf_counter()
    while True:
        await asyncio.sleep(WAECHTER_TAKT)
        if beobachter is not None:
            beobachter()
        jetzt = perf_counter()
        verzug = (jetzt - letzte) - WAECHTER_TAKT
        if verzug >= WAECHTER_SCHWELLE:
            # Beginn der Blockade ist der Zeitpunkt, an dem der Schlaf haette
            # enden sollen — vorher war die Schleife nachweislich frei.
            blockaden.append((letzte + WAECHTER_TAKT, jetzt))
        letzte = jetzt


#: Wann die Kopfzeilen der zuletzt gestellten Anbieteranfrage eintrafen.
#:
#: **Warum ein Modulwert und kein Rueckgabewert.** Die Zeit bis zu den
#: Kopfzeilen entsteht in `httpx`, drei Schichten unter dem gemessenen Code —
#: `stream_chat_completion` bekommt sie nie zu sehen und kann sie folglich auch
#: nicht durchreichen. Der Ereignishaken von `httpx` ist die einzige Stelle, an
#: der dieser Zeitpunkt existiert. Ein Wert je Prozess reicht, weil die Messung
#: die Szenarien nacheinander faehrt und ein Lauf seine Runden ebenfalls
#: nacheinander stellt: zwei Anfragen an `/chat/completions` sind hier nie
#: gleichzeitig offen.
_KOPFZEIT: dict[str, float] = {}


async def _kopfzeit_mitschreiben(response: httpx.Response) -> None:
    """Haelt fest, wann der Anbieter geantwortet hat — vor dem ersten Zeichen.

    Der Haken laeuft bei `stream=True`, sobald die Kopfzeilen da sind und bevor
    der Rumpf gelesen wird. Genau dazwischen liegt die Grenze, um die es geht:
    davor Netz und Warteschlange des Anbieters, danach das Modell.

    Gefiltert auf `/chat/completions`, weil derselbe Client auch den
    Modellkatalog holt. Ohne den Filter truege die naechste Runde die Kopfzeit
    eines Katalogabrufs und meldete ein negatives Prefill.
    """
    if response.request.url.path.endswith("/chat/completions"):
        _KOPFZEIT["letzte"] = perf_counter()


def _sichtbar_ab(zeitpunkt: float, blockaden: list[tuple[float, float]]) -> float:
    """Wann ein zu ``zeitpunkt`` erzeugtes Ereignis fruehestens beim Browser ist.

    Faellt der Zeitpunkt in eine Blockade, ist das deren Ende: vorher kommt der
    SSE-Schreiber nicht dran. Sonst ist es der Zeitpunkt selbst.
    """
    for beginn, ende in blockaden:
        if beginn <= zeitpunkt < ende:
            return ende
    return zeitpunkt


# ── Was eine Messung ist ─────────────────────────────────────────────────


def _gemeinsamer_praefix(links: list[dict], rechts: list[dict]) -> int:
    """Wieviele Zeichen zwei Anfragen sich von vorne her teilen.

    Der Zwischenspeicher des Anbieters greift auf dem **Präfix**: er bricht an
    der ersten Abweichung ab und alles dahinter ist frisch bezahlte Eingabe.
    Genau das macht diese Zahl zur Kennzahl neben der Cache-Quote — sie steht
    im Code fest, während die gemeldete Quote bei drei Wiederholungen zwischen
    0 % und 100 % springt, ohne dass sich an der Anfrage etwas geändert hätte.

    Verglichen wird **nachrichtenweise** und nicht Zeichen für Zeichen. Eine
    geänderte Nachricht ändert ohnehin alles ab ihrem Anfang, und der Rest des
    Unterschieds liegt innerhalb einer einzelnen Nachricht — unter dem Rauschen
    der Tokenisierung. Dafür kostet diese Fassung nichts, und das ist hier
    Bedingung: sie läuft mitten in der gemessenen Strecke.

    Gezählt wird mit ``message_character_count`` und nicht mit einer eigenen
    Formel: das ist die Währung, in der das Kontextbudget rechnet, und zwei
    Zählweisen für dieselbe Größe wären zwei Wahrheiten.

    Damit zählt der **Werkzeugkatalog nicht mit**: er geht als ``tools`` über
    dieselbe Leitung, aber ``message_character_count`` summiert nur ``content``
    (rund 45.000 Zeichen, siehe ``katalog_zeichen`` in ``ai_stream_service``).
    Der ausgewiesene Anteil ist deshalb der Präfixanteil **der Nachrichten**,
    nicht der zwischenspeicherbare Anteil der ganzen Anfrage — er ist mit den
    Prozentzahlen aus ``Teilbudgets`` nicht vergleichbar und untertreibt, weil
    der Katalog innerhalb eines Laufs feststeht. Vergleichbar ist er mit sich
    selbst über mehrere Läufe, und dafür ist er da.
    """
    gleich = 0
    for a, b in zip(links, rechts):
        if a != b:
            break
        gleich += 1
    return ai_context_service.message_character_count(links[:gleich])


@dataclass
class Runde:
    """Eine einzelne Anfrage an den Anbieter."""

    start: float
    erstes_zeichen: float | None = None
    ende: float | None = None

    #: Was **diese eine** Anfrage verbraucht hat.
    #:
    #: Die Summe über alle Runden stand schon immer im Protokoll — sie kommt aus
    #: dem Verbrauchsereignis, das ``usage_addieren`` gefüllt hat. Nur ist "50 %
    #: aus dem Zwischenspeicher" damit nicht von "Runde 1 traf nichts, Runde 2
    #: traf alles" zu unterscheiden, und genau dieser Fall lag vor. Der
    #: Unterschied ist keine Feinheit: im ersten Fall wäre der Präfix zur Hälfte
    #: kaputt, im zweiten ist er heil und nur die erste Runde einer Unterhaltung
    #: zahlt ihn.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    #: Die Anfrage in Zeichen, und wieviel davon mit der vorigen Anfrage
    #: übereinstimmte.
    #:
    #: ``None`` heißt "es gab keine Vorgängerin" und nie "der Präfix war
    #: kaputt". Die erste Anfrage einer geleerten Unterhaltung hat keine, und
    #: eine Null an dieser Stelle läse sich als Totalausfall des
    #: Zwischenspeichers — also als Befund, wo nur nichts zu messen war.
    anfrage_zeichen: int = 0
    praefix_zeichen: int | None = None

    #: Der Werkzeugkatalog dieser Anfrage in Zeichen.
    #:
    #: Er geht als ``tools`` über dieselbe Leitung wie die Nachrichten, zählt
    #: aber in ``anfrage_zeichen`` nicht mit (``message_character_count``
    #: summiert nur ``content``). Ohne diese Zahl fehlt der Anfrage genau der
    #: Teil, der laut ``local-plans/ai-performance-plan.md`` **94 %** der
    #: Eingabetokens ausmacht — und eine Anfragegrösse, die ihren grössten
    #: Posten nicht enthält, führt jede Optimierung in die Irre.
    katalog_zeichen: int = 0

    #: Wieviele Werkzeugaufrufe diese Runde erzeugt hat.
    #:
    #: Die Zahl beantwortet zwei der gestellten Fragen unmittelbar: eine Runde
    #: mit **einem** Aufruf kann per Konstruktion nicht parallelisiert werden,
    #: egal wieviel Nebenläufigkeit der Dienst anbietet; und eine lange Kette
    #: von Runden mit je einem Aufruf ist genau die sequenzielle Planung, nach
    #: der gefragt wurde.
    werkzeug_zahl: int = 0

    #: Wann die Antwortkopfzeilen des Anbieters eintrafen.
    #:
    #: Trennt die zwei Hälften der Wartezeit bis zum ersten Zeichen, die vorher
    #: als eine Zahl dastanden: **Netz** (Verbindung, TLS, Warteschlange des
    #: Anbieters, bis die Kopfzeilen da sind) gegen **Prefill** (das Modell
    #: liest die Eingabe und hat noch kein Zeichen erzeugt). Nur die zweite
    #: Hälfte schrumpft, wenn die Anfrage kleiner wird — wer beide zusammen
    #: misst, optimiert womöglich am falschen Ende.
    kopf: float | None = None

    @property
    def dauer(self) -> float:
        return (self.ende or self.start) - self.start

    @property
    def ttft(self) -> float | None:
        if self.erstes_zeichen is None:
            return None
        return self.erstes_zeichen - self.start

    @property
    def netz(self) -> float | None:
        """Anfrage raus bis Kopfzeilen rein."""
        if self.kopf is None:
            return None
        return self.kopf - self.start

    @property
    def prefill(self) -> float | None:
        """Kopfzeilen rein bis erstes Zeichen — das Modell liest."""
        if self.kopf is None or self.erstes_zeichen is None:
            return None
        return max(0.0, self.erstes_zeichen - self.kopf)

    @property
    def dekodier(self) -> float | None:
        """Erstes Zeichen bis Stromende — das Modell schreibt."""
        if self.erstes_zeichen is None or self.ende is None:
            return None
        return max(0.0, self.ende - self.erstes_zeichen)

    @property
    def praefix_anteil(self) -> float | None:
        if self.praefix_zeichen is None or not self.anfrage_zeichen:
            return None
        return self.praefix_zeichen / self.anfrage_zeichen


@dataclass
class Werkzeuglauf:
    name: str
    dauer: float
    art: str = "read"

    #: Anfang und Ende auf der Zeitachse des Laufs, in Sekunden ab Segmentstart.
    #:
    #: Die Dauer allein sagt nicht, **ob zwei Werkzeuge gleichzeitig liefen**.
    #: Drei Aufrufe zu je zwei Sekunden ergeben dieselbe Summe, ob sie
    #: nacheinander sechs Sekunden brauchten oder nebeneinander zwei. Genau
    #: dieser Unterschied war gefragt, und er ist nur aus den Fenstern zu lesen.
    start: float = 0.0
    ende: float = 0.0


@dataclass
class Messung:
    szenario: str
    ok: bool = True
    fehler: str | None = None

    gesamt: float = 0.0
    #: Was passiert, **bevor** der Lauf ueberhaupt anlaeuft — und damit bevor das
    #: erste Ereignis fliessen kann.
    #:
    #: Diese Zahl fehlte, und ihr Fehlen war ein Messfehler mit Ansage: alle
    #: uebrigen Zahlen zaehlen ab Segmentstart, der Benutzer zaehlt ab dem
    #: Klick auf "Senden". Dazwischen liegt der ganze Vorlauf aus
    #: `routers/ai_chat.stream_message` — die Denkvorgabe und das Kontextfenster
    #: (beide ueber den Modellkatalog, also potenziell HTTP) und `lauf_beginnen`,
    #: das den vollstaendigen Kontext synchron im Request aufbaut.
    anlauf: float = 0.0
    #: Segmentstart bis zur **ersten** Anbieteranfrage.
    #:
    #: Was `segment_ausfuehren` tut, bevor irgendetwas gefragt wird: Sitzung
    #: aufmachen, Schlüssel beim Beistelldienst holen (HTTP), Modellkatalog
    #: befragen, Werkzeugkatalog nach Rechten zuschneiden, Kontextbudget
    #: rechnen. In `msm_zeit` steckte das bisher mit allem anderen in einer
    #: Zahl — dabei ist es der einzige Posten, den der Benutzer **vor** jedem
    #: Lebenszeichen wartet, und damit der teuerste.
    ruestzeit: float = 0.0
    #: Ende der letzten Anbieterrunde bis Laufende: Aufräumen, Abschnitte
    #: bauen, Nachricht schreiben, Verbrauch buchen. Der Teil, den niemand
    #: sieht und der trotzdem zur Gesamtzeit zählt.
    nacharbeit: float = 0.0
    ttfe: float | None = None            # erstes Ereignis ueberhaupt
    ttft_denken: float | None = None     # erster Denkschritt, sichtbar
    ttft_text: float | None = None       # erstes Antwortzeichen, sichtbar
    ttfw: float | None = None            # erstes Werkzeug im Verlauf, sichtbar
    #: Wann der **Sprachmodus** das erste Wort an die Stimme geben könnte.
    #:
    #: Nicht dasselbe wie ``ttft_text``, und der Unterschied ist der ganze
    #: Grund für diese Zahl: der Chat zeigt jedes Zeichen sofort, die Stimme
    #: braucht einen abgeschlossenen Satz. `ai_voice_bridge.Belegfilter` gibt
    #: erst frei, wenn eine Zeile fertig ist oder ein Satzzeichen mit
    #: mindestens zehn Zeichen davor kommt — und Codeblöcke gar nicht. Beginnt
    #: die Antwort mit einem Beleg, hört der Mensch minutenlang nichts,
    #: während im Chat längst Text steht. Gemessen wird mit **demselben**
    #: Filter, den die Brücke benutzt; eine nachgebaute Regel wäre eine zweite
    #: Wahrheit.
    ttfs: float | None = None
    #: Wieviel Text durch den Filter lief, bevor das erste Wort sprechbar war.
    #:
    #: ``ttfs`` allein sagt „die Stimme wartete", nicht warum. Diese Zahl
    #: trennt die beiden möglichen Gründe: ein paar Dutzend Zeichen heisst,
    #: der erste Satz war einfach noch nicht zu Ende geschrieben — mehrere
    #: hundert heisst, ein Codeblock am Anfang der Antwort hat alles
    #: verschluckt. Das sind verschiedene Fehler mit verschiedenen Antworten.
    stummzeichen: int = 0
    stille_max: float = 0.0

    loop_block_max: float = 0.0
    loop_block_summe: float = 0.0

    anbieterzeit: float = 0.0
    runden: list[Runde] = field(default_factory=list)
    werkzeuge: list[Werkzeuglauf] = field(default_factory=list)

    status: str = ""
    stop_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    kosten_mikro: int = 0
    antwortlaenge: int = 0
    denklaenge: int = 0
    abschnitte: int = 0
    #: Die zusammengefasste Ereignisfolge — was der Benutzer wann sah.
    folge: list[dict] = field(default_factory=list)

    #: Die Nachrichten der **letzten** Anfrage dieses Laufs, damit die erste
    #: Anfrage der nächsten Frage ihren Präfix dagegen messen kann.
    #:
    #: Steht bewusst **nicht** in ``als_dict``. In diesen Nachrichten steht der
    #: entschlüsselte Gedächtnisblock im Klartext; das Protokoll unter
    #: ``logs/ai-benchmark/`` ist eine Datei auf der Platte und kein Ort für
    #: Benutzerdaten. Sie lebt nur so lange wie der Durchlauf.
    letzte_anfrage: list[dict] = field(default_factory=list, repr=False)

    @property
    def msm_zeit(self) -> float:
        """Alles, was nicht der Anbieter war. Der Teil, der uns gehoert."""
        return max(0.0, self.gesamt - self.anbieterzeit)

    @property
    def werkzeugzeit(self) -> float:
        return sum(w.dauer for w in self.werkzeuge)

    # ── Die Anbieterzeit, aufgeteilt ─────────────────────────────────────
    #
    # ``anbieterzeit`` war eine Zahl und damit als Befund unbrauchbar: sie
    # sagt "das Modell war langsam", ohne zu sagen, woran. Diese drei sagen
    # es. Netz schrumpft nur ueber weniger Runden, Prefill ueber kleinere
    # Anfragen, Dekodier ueber kuerzere Antworten — drei verschiedene Hebel.

    @property
    def netzzeit(self) -> float:
        return sum(r.netz or 0.0 for r in self.runden)

    @property
    def prefillzeit(self) -> float:
        return sum(r.prefill or 0.0 for r in self.runden)

    @property
    def dekodierzeit(self) -> float:
        return sum(r.dekodier or 0.0 for r in self.runden)

    @property
    def zwischenzeit(self) -> float:
        """Die Luecken zwischen den Anbieterrunden.

        Alles, was MSM zwischen zwei Anfragen tut: Werkzeuge ausfuehren,
        Ergebnisse schwaerzen, in die Datenbank schreiben, Verlauf kuerzen.
        Zusammen mit ``ruestzeit`` und ``nacharbeit`` ergibt das ``msm_zeit``
        — nur eben aufgeteilt auf die drei Stellen, an denen sie entsteht.
        """
        return sum(
            max(0.0, spaeter.start - (frueher.ende or frueher.start))
            for frueher, spaeter in zip(self.runden, self.runden[1:])
        )

    @property
    def werkzeug_wand(self) -> float:
        """Wieviel Wanduhrzeit die Werkzeuge belegt haben, Ueberlappung nur einmal.

        Die Gegenzahl zu ``werkzeugzeit``. Sind beide gleich, lief jedes
        Werkzeug allein; ist die Wanduhr kleiner, liefen welche nebeneinander.
        """
        fenster = sorted(
            (w.start, w.ende) for w in self.werkzeuge if w.ende > w.start
        )
        summe = 0.0
        offen_ab, offen_bis = None, None
        for beginn, ende in fenster:
            if offen_bis is not None and beginn <= offen_bis:
                offen_bis = max(offen_bis, ende)
                continue
            if offen_bis is not None:
                summe += offen_bis - offen_ab
            offen_ab, offen_bis = beginn, ende
        if offen_bis is not None:
            summe += offen_bis - offen_ab
        return summe

    @property
    def nebenlaeufigkeit(self) -> float | None:
        """Wieviele Werkzeuge im Schnitt gleichzeitig liefen. 1,0 heisst: keines.

        **Die Antwort auf "werden unabhaengige Werkzeuge parallel gerufen?"**
        — als Zahl statt als Codelektuere. Sie faellt auch dann auf 1,0, wenn
        der Dienst durchaus nebenlaeufig koennte, das Modell aber je Runde nur
        ein Werkzeug bestellt: dann liegt der Fehler nicht im Ausfuehren,
        sondern im Planen, und das ist ein anderer Fix.
        """
        if not self.werkzeuge or self.werkzeug_wand <= 0:
            return None
        return self.werkzeugzeit / self.werkzeug_wand

    def als_dict(self) -> dict:
        return {
            "szenario": self.szenario,
            "ok": self.ok,
            "fehler": self.fehler,
            "gesamt": round(self.gesamt, 3),
            "ttfe": round(self.ttfe, 3) if self.ttfe is not None else None,
            "ttft_denken": (
                round(self.ttft_denken, 3) if self.ttft_denken is not None else None
            ),
            "ttft_text": (
                round(self.ttft_text, 3) if self.ttft_text is not None else None
            ),
            "ttfw": round(self.ttfw, 3) if self.ttfw is not None else None,
            "ttfs": round(self.ttfs, 3) if self.ttfs is not None else None,
            "stummzeichen": self.stummzeichen,
            "stille_max": round(self.stille_max, 3),
            "loop_block_max": round(self.loop_block_max, 3),
            "loop_block_summe": round(self.loop_block_summe, 3),
            "anbieterzeit": round(self.anbieterzeit, 3),
            "msm_zeit": round(self.msm_zeit, 3),
            "werkzeugzeit": round(self.werkzeugzeit, 3),
            # Die Gesamtzeit, restlos aufgeteilt. `anlauf` + `ruestzeit` +
            # `netz` + `prefill` + `dekodier` + `zwischen` + `nacharbeit`
            # ergibt `anlauf` + `gesamt`; bleibt ein Rest, fehlt eine Phase.
            "phasen": {
                "anlauf": round(self.anlauf, 3),
                "ruestzeit": round(self.ruestzeit, 3),
                "netz": round(self.netzzeit, 3),
                "prefill": round(self.prefillzeit, 3),
                "dekodier": round(self.dekodierzeit, 3),
                "zwischen": round(self.zwischenzeit, 3),
                "nacharbeit": round(self.nacharbeit, 3),
                "werkzeug_summe": round(self.werkzeugzeit, 3),
                "werkzeug_wand": round(self.werkzeug_wand, 3),
                "nebenlaeufigkeit": (
                    round(self.nebenlaeufigkeit, 2)
                    if self.nebenlaeufigkeit is not None else None
                ),
            },
            "runden": len(self.runden),
            # Je Runde und nicht nur als Summe: eine Summe beantwortet nicht,
            # **welche** Runde den Zwischenspeicher traf. Die Summen oben bleiben
            # unangetastet, damit ältere Auswertungen weiterlesen können.
            "runden_detail": [
                {
                    "dauer": round(r.dauer, 3),
                    "ttft": round(r.ttft, 3) if r.ttft is not None else None,
                    "netz": round(r.netz, 3) if r.netz is not None else None,
                    "prefill": (
                        round(r.prefill, 3) if r.prefill is not None else None
                    ),
                    "dekodier": (
                        round(r.dekodier, 3) if r.dekodier is not None else None
                    ),
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "cached_tokens": r.cached_tokens,
                    "reasoning_tokens": r.reasoning_tokens,
                    "anfrage_zeichen": r.anfrage_zeichen,
                    "katalog_zeichen": r.katalog_zeichen,
                    "werkzeug_zahl": r.werkzeug_zahl,
                    "praefix_zeichen": r.praefix_zeichen,
                    "praefix_anteil": (
                        round(r.praefix_anteil, 4)
                        if r.praefix_anteil is not None else None
                    ),
                }
                for r in self.runden
            ],
            "werkzeuge": [
                {
                    "name": w.name,
                    "dauer": round(w.dauer, 3),
                    "art": w.art,
                    "ab": round(w.start, 3),
                    "bis": round(w.ende, 3),
                }
                for w in self.werkzeuge
            ],
            "status": self.status,
            "stop_reason": self.stop_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "kosten_mikro": self.kosten_mikro,
            "antwortlaenge": self.antwortlaenge,
            "denklaenge": self.denklaenge,
            "abschnitte": self.abschnitte,
            "anlauf": round(self.anlauf, 3),
            "folge": self.folge,
        }


@dataclass
class Szenario:
    """Ein Auftrag, wie ein Mensch ihn stellt — plus die Erwartung daran.

    ``erwartet`` ist bewusst eine Menge von Alternativen und keine feste Folge.
    Ein Modell, das statt ``read_server_backups`` erst ``list_my_servers`` ruft,
    hat nichts falsch gemacht. Gemessen wird, ob es **ueberhaupt** das richtige
    Werkzeug findet — die Reihenfolge ist seine Sache.
    """

    name: str
    auftrag: str
    erwartet: frozenset[str] = frozenset()
    vorlauf: list[dict] = field(default_factory=list)
    #: Was vor dem Szenario in der Datenbank stehen muss.
    saat: Callable[[Session, User], None] | None = None
    beschreibung: str = ""
    #: Weitere Fragen, gestellt in **derselben** Unterhaltung wie ``auftrag``
    #: und ohne sie vorher zu leeren.
    #:
    #: Leer ist der Normalfall und bleibt es: jedes andere Szenario startet
    #: sauber, sonst misst das zwölfte den Kontext der elf davor mit (siehe
    #: ``_unterhaltung``). Genau deshalb konnte dieser Benchmark bisher aber
    #: nichts über den Verlauf sagen — jede gemessene Anfrage bestand aus drei
    #: Nachrichten. Das ändert sich hier.
    #:
    #: Der **Werkzeugrückfluss** hängt zusätzlich am gewählten Werkzeug:
    #: ``_recent_tool_results`` nimmt ``DOCS_TOOLS`` und ``SKILL_TOOLS`` aus.
    #: Eine Nachfrage auf ein Werkzeug aus diesen Mengen ergäbe dort einen
    #: Nullbefund, und ein Nullbefund ist kein Freispruch.
    #:
    #: Jede Nachfrage ist ein eigener Lauf und wird einzeln gemessen; sie
    #: erscheint als ``<name>#2``, ``<name>#3`` in Tabelle und Protokoll. Der
    #: Median über drei Wiederholungen bleibt damit je Frage sauber — die dritte
    #: Frage ist ein anderer Lauf als die erste und gehört nicht in denselben
    #: Topf.
    #:
    #: ``MSM_BENCH_ONLY`` kennt trotzdem nur den Szenarionamen und fährt dann
    #: alle Fragen: eine Nachfrage ohne ihre Vorgängerin hätte den Verlauf
    #: nicht, den zu messen ihr einziger Zweck ist.
    nachfragen: tuple[str, ...] = ()


def _saat_gedaechtnis(db: Session, user: User) -> None:
    """Ein Fakt, den die KI wiederfinden soll.

    Direkt gesetzt und nicht ueber einen vorherigen Lauf erzeugt: sonst
    haenge das Rueckruf-Szenario davon ab, ob das Schreib-Szenario gelungen
    ist, und ein Ausfall dort faelschte hier eine zweite Zahl.
    """
    ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="lieblingsspiel", value="Valheim, am liebsten mit acht Mitspielern",
        origin="user",
    )
    db.commit()


SZENARIEN: list[Szenario] = [
    Szenario(
        name="chat_trivial",
        auftrag="Sag mir in einem Satz Hallo.",
        beschreibung="Untergrenze. Keine Werkzeuge, keine Datenbank — was das "
                     "Modell allein braucht.",
    ),
    Szenario(
        name="memory_schreiben",
        auftrag="Servus erstmal, ich bin Maik und ich hoste hauptsaechlich Valheim.",
        erwartet=frozenset({"remember"}),
        beschreibung="Ungefragtes Merken.",
    ),
    Szenario(
        name="memory_lesen",
        auftrag="Was weisst du eigentlich ueber meine Spielvorlieben?",
        erwartet=frozenset({"search_memory"}),
        saat=_saat_gedaechtnis,
        beschreibung="Rueckruf aus dem Gedaechtnis.",
    ),
    Szenario(
        name="skill_lesen",
        auftrag=(
            "Mein Valheim-Server laeuft laut Panel, aber niemand kann sich "
            "verbinden. Was tun?"
        ),
        erwartet=frozenset({"read_skill", "list_my_servers"}),
        beschreibung="Greift das mitgelieferte Vorgehenswissen?",
    ),
    Szenario(
        name="skill_lernen",
        auftrag="Perfekt, jetzt laeuft er. Danke!",
        vorlauf=[
            {"role": "user", "content": "Mein Server laeuft, aber niemand kommt drauf."},
            {
                "role": "assistant",
                "content": (
                    "Ich habe die Ursache gefunden: Der Server war an die "
                    "Docker-Bruecke 172.17.0.1 gebunden. Nach der Umstellung auf "
                    "0.0.0.0 ist er erreichbar. Das passiert bei jedem Server, "
                    "der aus einem aelteren Backup wiederhergestellt wird."
                ),
            },
        ],
        erwartet=frozenset({"learn_skill", "remember"}),
        beschreibung="Lernt die KI aus einer geloesten Sache?",
    ),
    Szenario(
        name="docs",
        auftrag="Wie richte ich in MSM ein automatisches Backup ein?",
        erwartet=frozenset({"search_docs", "read_docs"}),
        beschreibung="Belegpflicht auf die eigene Dokumentation.",
    ),
    Szenario(
        name="websuche",
        auftrag=(
            "Such bitte im Netz nach, welche Ports ein Valheim-Dedicated-Server "
            "aktuell braucht."
        ),
        erwartet=frozenset({"web_search"}),
        beschreibung="Aussenwelt.",
    ),
    Szenario(
        name="server_liste",
        auftrag="Welche Server habe ich denn?",
        erwartet=frozenset({"list_my_servers"}),
        beschreibung="Einfachster Werkzeugweg: eine Runde, ein Werkzeug.",
    ),
    Szenario(
        name="server_uebersicht",
        auftrag=(
            "Gib mir einen Ueberblick ueber alle meine Server: Zustand, Ports "
            "und was an Backups da ist."
        ),
        erwartet=frozenset({
            "list_my_servers", "read_server_ports", "read_server_backups",
        }),
        beschreibung="Mehrere Werkzeuge, mehrere Server — hier zeigt sich, ob "
                     "nebenlaeufig gearbeitet wird oder eins nach dem anderen.",
    ),
    Szenario(
        name="test_mail",
        auftrag="Schick mir bitte eine Test-E-Mail.",
        erwartet=frozenset({"send_test_email"}),
        beschreibung="Der gemeldete Fall: die Mail war laengst da, die Antwort "
                     "kam eine Minute spaeter.",
    ),
    Szenario(
        name="backup_alle",
        auftrag="Erstelle mir bitte von jedem meiner Server ein Backup.",
        erwartet=frozenset({"propose_backup", "list_my_servers"}),
        beschreibung="Der zweite gemeldete Fall: danach lud die Seite nicht "
                     "mehr, bis der Auftrag durch war.",
    ),
    Szenario(
        name="aufgabe_planen",
        auftrag=(
            "Richte mir bitte fuer alle meine Server einen Neustart alle acht "
            "Stunden ein."
        ),
        erwartet=frozenset({"propose_task_set", "list_my_servers"}),
        beschreibung="Mehrstufiger Auftrag ueber Lesen und Schreiben hinweg.",
    ),
    # ── Das einzige Szenario mit Gedächtnis ──────────────────────────────
    #
    # **Warum Blueprints.** Gesucht war eine Quelle mit echtem und
    # **deterministischem** Umfang, die der Rückfluss auch annimmt.
    # `read_server_logs` wäre im Betrieb der dickste (bis 24.000 Zeichen aus
    # einem Container), liefert hier aber nichts: ohne Docker und Node antwortet
    # das Werkzeug mit `available: false`. Die Doku hat Umfang, wird aber von
    # `_recent_tool_results` ausdrücklich ausgenommen — `read_docs` und
    # `search_docs` stehen in `DOCS_TOOLS`, und das Szenario füllte den
    # Rückfluss deshalb mit null Zeichen. `read_blueprint` steht in keiner der
    # beiden Mengen und liest Dateien aus `blueprints/native/`: gemessen 2.567
    # bis 5.824 Zeichen je Aufruf, `list_blueprints` 3.348.
    #
    # **Warum diese sieben Spiele.** Große Blueprints, damit wenige Aufrufe
    # reichen — gemessen über `execute_read_tool` und `json.dumps` wie beim
    # Persistieren: 7 Days to Die 5.824 (der größte der 27), Conan Exiles (UE5)
    # 3.689, ARK: Survival Evolved 3.552, ARK: Survival Ascended 3.534, DayZ
    # 3.260, Enshrouded 3.149, Rust 3.023 — zusammen 26.031 Zeichen und damit
    # gut das Anderthalbfache des Deckels von 16.000
    # (`MAX_TOOL_RESULT_CONTEXT_CHARS`). Mit den kleinsten Blueprints (2.567)
    # wären für dieselbe Summe elf Aufrufe nötig, und elf Spiele zählt so kein
    # Betreiber auf. Ein knapper Vorrat wäre hier ohnehin wertlos: läge die
    # Summe unter dem Deckel, meldete das Szenario einen Nullbefund und sähe aus
    # wie ein Freispruch.
    #
    # Reichlich heißt aber nicht "in jeder Teilmenge scharf". Nachgerechnet über
    # die Blockformel (Kopf 86 Zeichen, je Zeile `- read_blueprint: ` vor dem
    # Ergebnis, dazwischen je eine Trennzeile): alle sieben ergeben ungekürzt
    # 26.249 Zeichen. Gemessen bleiben davon 16.090 = 86 + 16.000 + 4
    # Trennzeilen: fünf Zeilen passen hinein, die fünfte wird abgeschnitten,
    # zwei Blueprints fehlen ganz. Nach Teilmengen: fünf beliebige der sieben
    # reißen den Deckel immer (Zeilensumme 16.608 bis 19.949), vier je nach
    # Auswahl (13.038 bis 16.671), drei nie (höchstens 13.119). Fehlt die Marke
    # `[...gekuerzt]`, ist das kein Freispruch, sondern der Hinweis, in der
    # Werkzeugspalte nachzusehen, wie viele Blueprints tatsächlich gelesen
    # wurden.
    Szenario(
        name="kontext_folge",
        auftrag=(
            "Wir wollen unser Hosting-Angebot um ein Survival-Paket erweitern, "
            "und ich muss vorher wissen, was die Spiele an Ports und Umgebung "
            "brauchen. Lies mir bitte die Blueprints von 7 Days to Die, Conan "
            "Exiles, ARK: Survival Evolved, ARK: Survival Ascended, DayZ, Rust "
            "und Enshrouded und stell mir gegenüber, welche Ports jeder öffnet "
            "und welche Umgebungsvariablen gesetzt sein müssen."
        ),
        nachfragen=(
            # Die eigentliche Frage des Szenarios. Beantwortbar nur, wenn die
            # Blueprints noch vor dem Modell stehen — und mit Deckel sieht es
            # davon einen Ausschnitt, markiert mit `[...gekuerzt]`. Ob es
            # daraufhin nachliest oder falsch antwortet, ist genau der Befund,
            # den dieses Szenario liefern soll. Die Einzelheiten sind scharf
            # gewählt: die App-IDs sind sieben verschiedene Zahlen (294420,
            # 443030, 376030, 2430930, 223350, 258550, 2278520), und ohne
            # RCON-Port kommen nur zwei der sieben aus — 7 Days to Die und
            # Enshrouded. Die Frage zielt bewusst auf **alle** sieben: welche
            # Zeile die Kürzung trifft, hängt an der Lesereihenfolge, aber zwei
            # fehlen in jedem Fall. Die App-ID steht dabei in `meta.description`
            # innerhalb der ersten 290 Zeichen und überlebt ein Abschneiden; die
            # Ports stehen ab Offset 1.065 bis 3.797 und sind der härtere Teil.
            "Welche Steam-App-ID steht bei jedem der sieben, und welche von "
            "ihnen kommen ohne RCON-Port aus?",
            "Fass mir deine Empfehlung bitte in fünf Stichpunkten für mein "
            "Runbook zusammen.",
        ),
        # Eine Menge von Alternativen, geprüft über **alle** Fragen des
        # Szenarios (siehe `_treffer`) — der vorhandene Mechanismus trägt den
        # interessanten Fall schon: die zweite Frage darf direkt antworten
        # (kein Werkzeug, kein Fehlschlag) oder `read_blueprint` erneut rufen.
        # Beides ist richtig. Ein *anderes* Werkzeug fiele in der
        # Werkzeugspalte der Tabelle auf.
        erwartet=frozenset({"read_blueprint", "list_blueprints"}),
        beschreibung="Drei Fragen in einer Unterhaltung — hier und nur hier "
                     "wachsen Verlauf und Präfix über mehrere Fragen, und nur "
                     "hier ist der Werkzeugrückfluss gefüllt.",
    ),
]


# ── Aufbau ───────────────────────────────────────────────────────────────


def _provider(db: Session) -> AiProvider:
    provider = AiProvider(
        name="OpenRouter (benchmark)",
        provider_kind="openrouter",
        default_model=BENCH_MODEL,
        enabled=True,
        requires_api_key=True,
    )
    db.add(provider)
    db.flush()
    # Ueber die echte Verschluesselung und nicht am Feld vorbei: `resolve_api_key`
    # entschluesselt im gemessenen Pfad, und diese Millisekunden gehoeren dazu.
    provider.operator_api_key_encrypted = DisClient.encrypt(
        BENCH_KEY, aad=_operator_aad(provider.id)
    )
    db.commit()
    db.refresh(provider)
    return provider


def _server_anlegen(db: Session, anzahl: int = 3) -> list[Server]:
    """Ein paar Server, damit die Werkzeuge etwas zu tun haben.

    Drei und nicht einer: die interessante Frage bei ``server_uebersicht`` und
    ``backup_alle`` ist gerade, was passiert, wenn dasselbe Werkzeug mehrfach
    laeuft. Bei einem einzigen Server waere sie nicht zu stellen.
    """
    server = []
    for i in range(anzahl):
        s = Server(
            name=f"Bench {i + 1}",
            game_type="valheim" if i == 0 else "minecraft",
            install_dir=f"/tmp/bench_{i + 1}",
            container_name=f"msm-srv-bench{i + 1}",
            status="running" if i < 2 else "stopped",
        )
        db.add(s)
        server.append(s)
    db.commit()
    for s in server:
        db.refresh(s)
    return server


def _unterhaltung(db: Session, user: User, *, leeren: bool = True) -> AiConversation:
    """Die eine Unterhaltung des Benutzers, vor jedem Szenario geleert.

    Geleert und nicht neu angelegt: ``ai_conversations.user_id`` ist eindeutig,
    ein Benutzer hat genau einen Chat. Das ist keine Huerde des Benchmarks,
    sondern eine Zusicherung des Produkts — und der Verlauf **muss** weg, sonst
    misst das zwoelfte Szenario den Kontext der elf davor mit und jede Zahl
    waechst monoton, ohne dass sich etwas verschlechtert haette.

    ``leeren=False`` ist die eine Ausnahme, und sie ist der Zweck einer
    Nachfrage: sie soll den Verlauf ihrer Vorgängerin vorfinden. Gelöscht
    werden hier nämlich nicht nur Nachrichten, sondern auch die
    ``AiToolResult``-Zeilen — und die sind es, aus denen
    ``build_provider_messages`` den Werkzeugkontext der nächsten Frage baut.
    Solange jede Messung leert, gibt es diesen Kontext nie, und alles was mit
    ihm zusammenhängt bleibt unmessbar.
    """
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    if leeren:
        ai_chat_service.clear_history(db, conversation)
        db.commit()
    db.refresh(conversation)
    return conversation


# ── Die Messung ──────────────────────────────────────────────────────────


def _schritt_name(szenario: Szenario, schritt: int) -> str:
    """Wie die Frage Nummer ``schritt`` in Tabelle und Protokoll heißt."""
    return szenario.name if schritt == 0 else f"{szenario.name}#{schritt + 1}"


async def _messen(
    db: Session,
    *,
    user: User,
    provider: AiProvider,
    szenario: Szenario,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    schritt: int = 0,
    vorige_anfrage: list[dict] | None = None,
) -> Messung:
    """Ein Lauf, vollständig vermessen.

    ``schritt`` ist die Nummer der Frage innerhalb des Szenarios: 0 ist
    ``szenario.auftrag``, alles darüber greift in ``szenario.nachfragen``. Ein
    Parameter statt drei, weil Frage, Name und die Entscheidung über das Leeren
    des Verlaufs sich alle aus dieser einen Zahl ergeben — und weil ``0`` genau
    das Verhalten von vorher ist.

    ``vorige_anfrage`` sind die Nachrichten der letzten Anbieteranfrage der
    vorigen Frage. Nur dafür da, dass die erste Runde hier ihren gemeinsamen
    Präfix dagegen messen kann; sie wird gelesen und nie geschrieben.
    """
    frage = szenario.auftrag if schritt == 0 else szenario.nachfragen[schritt - 1]
    messung = Messung(szenario=_schritt_name(szenario, schritt))
    ai_run_broker.zuruecksetzen_fuer_tests()

    # Eine Nachfrage lebt von dem, was vorher da war — sie ist der einzige Fall,
    # in dem der Verlauf stehenbleibt.
    conversation = _unterhaltung(db, user, leeren=schritt == 0)

    # ── Der Anlauf ───────────────────────────────────────────────────────
    #
    # Genau die Schritte, die `routers/ai_chat.stream_message` **vor** dem Lauf
    # macht — und die der Benutzer als Teil seiner Wartezeit erlebt, weil bis
    # dahin kein einziges Ereignis geflossen ist.
    #
    # Sie standen hier vorher ausserhalb der Messung. Das war ein Messfehler mit
    # Ansage: alles andere zaehlte ab Segmentstart, der Mensch zaehlt ab dem
    # Klick. Was dazwischen liegt, war damit per Konstruktion unsichtbar — und
    # es sind ausgerechnet zwei Abrufe des Modellkatalogs und ein vollstaendiger
    # Kontextaufbau.
    anlauf_beginn = perf_counter()
    denken, stufe = await ai_reasoning.vorgabe(
        client, db, user=user, provider=provider, aktiv=True, wunsch=None,
    )
    fenster = await ai_context_window.ermitteln(client, provider)
    run, fehler = ai_stream_service.lauf_beginnen(
        db,
        user=user,
        conversation=conversation,
        provider=provider,
        request_id=uuid4(),
        content=frage,
        reasoning=denken,
        reasoning_effort=stufe,
        context_chars=fenster.zeichen if fenster.bekannt else None,
    )
    messung.anlauf = perf_counter() - anlauf_beginn
    if run is None:
        messung.ok = False
        messung.fehler = f"lauf_beginnen: {fehler}"
        return messung

    # Ein Vorlauf gehoert in den Zustand des Laufs und nicht in die
    # Unterhaltung: `lauf_beginnen` hat die Nachrichten bereits gebaut, und ein
    # nachtraeglicher Eingriff in die Datenbank wuerde sie nicht mehr erreichen.
    if szenario.vorlauf:
        zustand = json.loads(run.state_json) if run.state_json else {}
        nachrichten = zustand.get("provider_messages") or []
        # Vor die letzte (die Frage des Benutzers) einsortiert.
        zustand["provider_messages"] = (
            nachrichten[:-1] + list(szenario.vorlauf) + nachrichten[-1:]
        )
        run.state_json = json.dumps(zustand, ensure_ascii=True)
        db.commit()

    run_id = run.id
    ai_run_broker.eroeffnen(run_id)

    blockaden: list[tuple[float, float]] = []
    ereignisse: list[tuple[str, float]] = []
    runden: list[Runde] = []
    werkzeuge: list[Werkzeuglauf] = []

    # Veroeffentlichte Ereignisse mit Zeitstempel. Der Vermittler ruft das
    # synchron aus dem Lauf heraus — der Stempel ist damit exakt der
    # Entstehungszeitpunkt und nicht der Empfang.
    echt_veroeffentlichen = ai_run_broker.veroeffentlichen

    # Derselbe Filter, den die Sprachbruecke je Lauf anlegt. Er entscheidet,
    # wann aus fliessendem Text ein sprechbarer Satz wird — und damit, wann der
    # Mensch im Sprachmodus das erste Wort hoert.
    belegfilter = Belegfilter()
    sprechbar: list[float] = []

    def _veroeffentlichen(rid: str, ereignis: str, daten: dict) -> None:
        if rid == run_id:
            ereignisse.append((ereignis, perf_counter()))
            # Gezaehlt wird, was **beim Benutzer ankommt**, nicht was in der
            # Datenbank steht. Der erste Anlauf las die fertige Nachricht ueber
            # die Testsitzung nach und bekam fuer jedes Szenario null — der Lauf
            # schreibt sein Ergebnis in einer eigenen Sitzung, und die
            # Momentaufnahme des Tests ist aelter. Eine Kennzahl, die immer null
            # ist, faellt nicht auf; sie taeuscht nur.
            #
            # Der Strom ist ohnehin die ehrlichere Quelle: gefragt ist, wieviel
            # Text der Mensch zu sehen bekam.
            if ereignis == "delta":
                stueck = str(daten.get("content") or "")
                messung.antwortlaenge += len(stueck)
                # Denselben Weg wie im Sprachmodus mitlaufen lassen. Der
                # Filter ist zustandsbehaftet und billig; er kostet hier
                # nichts ausser dem Aufruf und liefert die einzige Zahl, die
                # fuer das Gehoer zaehlt.
                if not sprechbar:
                    messung.stummzeichen += len(stueck)
                    gesprochen, _ = belegfilter.fuettern(stueck)
                    if gesprochen.strip():
                        sprechbar.append(perf_counter())
            elif ereignis == "reasoning":
                messung.denklaenge += len(str(daten.get("content") or ""))
        echt_veroeffentlichen(rid, ereignis, daten)

    # Anbieteranfragen einzeln vermessen: nur so laesst sich "das Modell war
    # langsam" von "wir waren langsam" trennen.
    echt_stream = ai_stream_service.stream_chat_completion

    # Die Nachrichten der zuletzt gestellten Anfrage — Ausgangspunkt für den
    # Präfixvergleich der nächsten. Beim Eintritt sind das die der vorigen
    # Frage, sofern es eine gab.
    vorige: list[dict] = list(vorige_anfrage or [])

    async def _stream(*args, **kwargs):
        nonlocal vorige
        runde = Runde(start=perf_counter())
        runden.append(runde)
        # **Vor** dem ersten Stück und nicht im `finally`: `provider_messages`
        # ist dieselbe Liste, an die der Lauf nach der Runde den Assistentenzug
        # und die Werkzeugergebnisse anhängt. Wer sie erst am Ende ansieht,
        # misst die nächste Anfrage und nennt sie diese. Die flache Kopie
        # kostet nichts und friert die Reihenfolge ein.
        nachrichten = list(kwargs.get("messages") or [])
        runde.anfrage_zeichen = ai_context_service.message_character_count(nachrichten)
        # Genauso gezaehlt wie in `ai_stream_service._werkzeuge_und_grenze`,
        # das mit derselben Zeile sein Kontextbudget kuerzt. Zwei Zaehlweisen
        # fuer dieselbe Groesse waeren zwei Wahrheiten.
        werkzeugkatalog = kwargs.get("tools") or []
        runde.katalog_zeichen = (
            len(json.dumps(werkzeugkatalog, ensure_ascii=False))
            if werkzeugkatalog else 0
        )
        if vorige:
            runde.praefix_zeichen = _gemeinsamer_praefix(nachrichten, vorige)
        vorige = nachrichten
        # Der Haken schreibt gleich hinein; ein Rest der vorigen Runde wuerde
        # sonst als deren Kopfzeit durchgehen, falls diese hier vor dem ersten
        # Zeichen stirbt.
        _KOPFZEIT.pop("letzte", None)
        # Der Verbrauchszähler **dieser** Runde. Der Lauf legt für jede
        # Werkzeugrunde ein frisches `StreamUsage` an und addiert es hinterher
        # in die Laufsumme; wer hier hineinsieht, sieht die Runde allein.
        verbrauch = kwargs.get("usage")
        try:
            async for chunk in echt_stream(*args, **kwargs):
                if runde.erstes_zeichen is None:
                    runde.erstes_zeichen = perf_counter()
                    runde.kopf = _KOPFZEIT.get("letzte")
                yield chunk
        finally:
            runde.ende = perf_counter()
            # Gelesen wird erst hier: der Anbieter meldet seinen Verbrauch in
            # der letzten Zeile des Stroms, vorher steht dort nichts.
            if verbrauch is not None:
                runde.prompt_tokens = int(verbrauch.prompt_tokens or 0)
                runde.completion_tokens = int(verbrauch.completion_tokens or 0)
                runde.cached_tokens = int(verbrauch.cached_tokens or 0)
                runde.reasoning_tokens = int(verbrauch.reasoning_tokens or 0)
                # Die Bestellung dieser Runde. `usage.tool_calls` gehoert der
                # Runde allein — der Lauf legt fuer jede ein frisches
                # `StreamUsage` an.
                runde.werkzeug_zahl = len(verbrauch.tool_calls or [])

    # Werkzeuge einzeln vermessen. Die Summe gegen die Wanduhr zeigt, ob sie
    # nacheinander oder nebeneinander liefen.
    echt_read_tool = ai_stream_service.execute_read_tool

    def _read_tool(*args, **kwargs):
        name = str(kwargs.get("tool_name") or "?")
        start = perf_counter()
        try:
            return echt_read_tool(*args, **kwargs)
        finally:
            if BENCH_TOOL_DELAY:
                # Synchron und ohne `await` — so verhaelt sich jeder echte
                # Aufruf an einen Node auch.
                time.sleep(BENCH_TOOL_DELAY)
            ende = perf_counter()
            werkzeuge.append(
                Werkzeuglauf(name, ende - start, "read", start=start, ende=ende)
            )

    echt_write = ai_stream_service._persist_write_proposals

    def _write(*args, **kwargs):
        start = perf_counter()
        aufrufe = kwargs.get("tool_calls") or []
        try:
            return echt_write(*args, **kwargs)
        finally:
            if BENCH_TOOL_DELAY:
                # Je Aufruf, nicht je Runde: eine Schreibrunde ueber drei Server
                # legt auch drei Backups an.
                time.sleep(BENCH_TOOL_DELAY * max(1, len(aufrufe)))
            ende = perf_counter()
            dauer = ende - start
            namen = [getattr(c, "name", "?") for c in aufrufe] or ["propose_*"]
            # Die Dauer der ganzen Schreibrunde auf ihre Aufrufe verteilt: sie
            # laufen ohnehin in einer Schleife, und die einzelne Zuordnung
            # brauchte einen Eingriff tiefer im Vorschlagsdienst.
            #
            # Die **Fenster** dagegen werden nicht verteilt, sondern hinter-
            # einander gelegt: die Schleife im Vorschlagsdienst ist
            # nachweislich sequenziell, und ein Nebenlaeufigkeitswert von 3,0
            # aus drei identischen Fenstern waere eine erfundene Zahl.
            for nummer, name in enumerate(namen):
                teil = dauer / len(namen)
                werkzeuge.append(Werkzeuglauf(
                    name, teil, "write",
                    start=start + nummer * teil,
                    ende=start + (nummer + 1) * teil,
                ))

    monkeypatch.setattr(ai_run_broker, "veroeffentlichen", _veroeffentlichen)
    monkeypatch.setattr(ai_stream_service.ai_run_broker, "veroeffentlichen", _veroeffentlichen)
    monkeypatch.setattr(ai_stream_service, "stream_chat_completion", _stream)
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", _read_tool)
    monkeypatch.setattr(ai_stream_service, "_persist_write_proposals", _write)

    waechter = asyncio.create_task(_waechter(blockaden))
    t0 = perf_counter()
    try:
        await asyncio.wait_for(
            ai_stream_service.segment_ausfuehren(run_id, client=client),
            timeout=BENCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        messung.ok = False
        messung.fehler = f"Zeitgrenze {BENCH_TIMEOUT:.0f}s ueberschritten"
    except Exception as exc:  # noqa: BLE001 — ein Ausfall ist ein Messergebnis
        messung.ok = False
        messung.fehler = f"{type(exc).__name__}: {exc}"
    finally:
        messung.gesamt = perf_counter() - t0
        waechter.cancel()
        try:
            await waechter
        except asyncio.CancelledError:
            pass
        monkeypatch.undo()

    # ── Auswertung ───────────────────────────────────────────────────────

    messung.runden = runden
    # Die Werkzeugfenster auf die Zeitachse des Laufs umrechnen. Gesammelt
    # werden sie als rohe `perf_counter`-Werte, weil `t0` erst nach den
    # Attrappen feststeht; im Protokoll waeren solche Zahlen unlesbar und
    # zwischen zwei Messungen nicht vergleichbar.
    for werkzeug in werkzeuge:
        werkzeug.start -= t0
        werkzeug.ende -= t0
    messung.werkzeuge = werkzeuge
    messung.anbieterzeit = sum(r.dauer for r in runden)
    if runden:
        # Vor der ersten Anfrage: Sitzung, Schluessel, Modellkatalog,
        # Werkzeugkatalog, Kontextbudget. Nach der letzten: Abschnitte,
        # Nachricht, Verbrauch.
        messung.ruestzeit = max(0.0, runden[0].start - t0)
        letztes_ende = runden[-1].ende or runden[-1].start
        messung.nacharbeit = max(0.0, (t0 + messung.gesamt) - letztes_ende)
    # Weitergereicht an die nächste Frage derselben Unterhaltung. Kam dieser
    # Lauf gar nicht bis zu einer Anfrage, steht hier weiter die des Vorgängers
    # — die Kette bricht dann nicht zusätzlich noch an einer leeren Liste.
    messung.letzte_anfrage = vorige
    if blockaden:
        dauern = [ende - beginn for beginn, ende in blockaden]
        messung.loop_block_max = max(dauern)
        messung.loop_block_summe = sum(dauern)

    def _seit_start(art_filter) -> float | None:
        for art, zeitpunkt in ereignisse:
            if art_filter(art):
                return _sichtbar_ab(zeitpunkt, blockaden) - t0
        return None

    messung.ttfe = _seit_start(lambda a: True)
    messung.ttft_denken = _seit_start(lambda a: a == "reasoning")
    messung.ttft_text = _seit_start(lambda a: a == "delta")
    messung.ttfw = _seit_start(lambda a: a in {"tool", "proposal", "action"})
    if sprechbar:
        messung.ttfs = _sichtbar_ab(sprechbar[0], blockaden) - t0
    else:
        # Kein Satzende im Strom heisst nicht „nicht gemessen", sondern
        # „nicht vor Schluss". Die Bruecke ruft am Laufende `ausklingen()`;
        # gibt der Filter dort noch Text her, war genau das der frueheste
        # Moment, in dem die Stimme haette sprechen koennen. Ein Strich in
        # der Spalte laese die kurzen Antworten als die schnellsten
        # erscheinen, obwohl sie im Sprachmodus die langsamsten sind.
        rest, _ = belegfilter.ausklingen()
        if rest.strip():
            messung.ttfs = messung.gesamt

    # Die Ereignisfolge selbst, zusammengefasst. Zwei Zahlen (`ttft_denken`
    # gegen `ttft_text`) sagen, **ob** der Denktext zu spaet kam; sie sagen
    # nicht, was stattdessen passierte. Fuer eine Beschwerde wie "der
    # Nachdenken-Block kam erst am Ende" ist genau das die gesuchte Auskunft.
    #
    # Zusammengefasst und nicht roh: ein Lauf erzeugt tausende `delta`, und eine
    # Liste davon waere unlesbar. Aufeinanderfolgende gleiche Ereignisse werden
    # zu einem Eintrag mit Anzahl und Zeitspanne.
    for art, zeitpunkt in ereignisse:
        sichtbar = round(_sichtbar_ab(zeitpunkt, blockaden) - t0, 2)
        if messung.folge and messung.folge[-1]["art"] == art:
            messung.folge[-1]["bis"] = sichtbar
            messung.folge[-1]["anzahl"] += 1
            continue
        messung.folge.append({"art": art, "ab": sichtbar, "bis": sichtbar, "anzahl": 1})

    # Die laengste Stille — gemessen an dem, was der Benutzer **sieht**.
    # `message` zaehlt als Anfang: ab da steht die Blase im Chat.
    sichtbare = [
        _sichtbar_ab(zeitpunkt, blockaden) - t0
        for art, zeitpunkt in ereignisse
        if art in {"message", "delta", "reasoning", "tool", "proposal", "action",
                   "question", "done", "error"}
    ]
    vorher = 0.0
    for zeitpunkt in [*sichtbare, messung.gesamt]:
        messung.stille_max = max(messung.stille_max, zeitpunkt - vorher)
        vorher = zeitpunkt

    # Was am Ende in der Datenbank steht — die ehrliche Abrechnung.
    #
    # In einer **eigenen** Sitzung gelesen und nicht in der des Tests. Der Lauf
    # schreibt sein Ergebnis ueber `SessionLocal()`, also ausserhalb der
    # Transaktion, in der dieser Test sitzt; deren Momentaufnahme ist aelter.
    # Der erste Baseline-Lauf meldete deswegen fuer **jedes** Szenario eine
    # Antwortlaenge von 0 — bei gleichzeitig gezaehlten Antworttokens. Eine
    # Kennzahl, die immer null ist, faellt nicht auf, sie taeuscht nur.
    from models import AiMessage, AiRun, AiUsageEvent

    with SessionLocal() as frisch:
        lauf = frisch.get(AiRun, run_id)
        if lauf is not None:
            messung.status = lauf.status or ""
            messung.stop_reason = lauf.stop_reason or ""
            if lauf.message_id:
                nachricht = frisch.get(AiMessage, lauf.message_id)
                if nachricht is not None:
                    # Nicht der Text — der kommt aus dem Strom. Hier interessiert
                    # nur, ob die **Gliederung** angekommen ist: sie ist der
                    # Grund, warum der Verlauf nach einem Neuladen die Werkzeuge
                    # noch zeigt, und ohne diese Zahl wuerde ein stiller Ausfall
                    # dort niemandem auffallen.
                    messung.abschnitte = len(
                        json.loads(nachricht.sections_json)
                        if nachricht.sections_json else []
                    )
        verbrauch = (
            frisch.query(AiUsageEvent)
            .filter(AiUsageEvent.user_id == user.id)
            .order_by(AiUsageEvent.id.desc())
            .first()
        )
        if verbrauch is not None:
            messung.prompt_tokens = int(verbrauch.prompt_tokens or 0)
            messung.completion_tokens = int(verbrauch.completion_tokens or 0)
            # Zwischengespeicherte Tokens getrennt: sie sind der Hebel bei den
            # Kosten, und ob das Zwischenspeichern ueberhaupt greift, sieht man
            # nirgends sonst.
            messung.cached_tokens = int(verbrauch.cached_tokens or 0)
            messung.reasoning_tokens = int(verbrauch.reasoning_tokens or 0)
            messung.kosten_mikro = int(verbrauch.accounted_cost_microunits or 0)

    return messung


# ── Darstellung ──────────────────────────────────────────────────────────


def _median(werte: list[float | None]) -> float | None:
    echte = [w for w in werte if w is not None]
    if not echte:
        return None
    return statistics.median(echte)


#: Die Ausgabe bleibt reines ASCII. Die Windows-Konsole dieses Projekts laeuft
#: unter cp1252, und ein einziges Kaestchenzeichen in der Tabelle beendete den
#: Durchlauf mit einem `UnicodeEncodeError` — **nach** fuenf Minuten bezahlter
#: Messung. Eine huebschere Tabelle ist das nicht wert.
def _z(wert: float | None, breite: int = 7) -> str:
    if wert is None:
        return "-".rjust(breite)
    return f"{wert:.2f}s".rjust(breite)


def _tabelle(nach_szenario: dict[str, list[Messung]]) -> str:
    kopf = (
        f"{'Szenario':<20} {'TTFT':>7} {'Denken':>7} {'Werkz.':>7} "
        f"{'Stille':>7} {'BLOCK':>7} {'Gesamt':>7} {'Anbiet.':>7} {'MSM':>7} "
        f"{'Rd':>3} {'Werkzeuge':<34}"
    )
    zeilen = [kopf, "-" * len(kopf)]
    for name, messungen in nach_szenario.items():
        gelungen = [m for m in messungen if m.ok]
        if not gelungen:
            grund = messungen[0].fehler if messungen else "keine Messung"
            zeilen.append(f"{name:<20} AUSFALL - {grund}")
            continue
        werkzeugnamen = sorted({w.name for m in gelungen for w in m.werkzeuge})
        anzeige = ", ".join(werkzeugnamen)[:33] or "-"
        zeilen.append(
            f"{name:<20}"
            f" {_z(_median([m.ttft_text for m in gelungen]))}"
            f" {_z(_median([m.ttft_denken for m in gelungen]))}"
            f" {_z(_median([m.ttfw for m in gelungen]))}"
            f" {_z(_median([m.stille_max for m in gelungen]))}"
            f" {_z(_median([m.loop_block_max for m in gelungen]))}"
            f" {_z(_median([m.gesamt for m in gelungen]))}"
            f" {_z(_median([m.anbieterzeit for m in gelungen]))}"
            f" {_z(_median([m.msm_zeit for m in gelungen]))}"
            f" {statistics.median([len(m.runden) for m in gelungen]):>3.0f}"
            f" {anzeige:<34}"
        )
    return "\n".join(zeilen)


def _phasentabelle(nach_szenario: dict[str, list[Messung]]) -> str:
    """Die Wartezeit, restlos auf ihre Stationen aufgeteilt.

    Die Tabelle darueber sagt **wie lange**, diese sagt **wo**. Der Unterschied
    ist der ganze Zweck: eine Gesamtzeit von 30 Sekunden ist kein Befund,
    solange offen ist, ob sie im Netz, im Modell, in den Werkzeugen oder in
    unserem eigenen Code entstanden ist — und die vier verlangen vier
    verschiedene Aenderungen.

    Die Stationen in der Reihenfolge, in der sie durchlaufen werden:

    ``Anlauf``
        Vor dem Lauf: Denkvorgabe, Kontextfenster, Kontextaufbau. Router.
    ``Ruest``
        Segmentstart bis zur ersten Anbieteranfrage: Schluessel, Modellkatalog,
        Werkzeugkatalog, Budget.
    ``Netz``
        Anfrage raus bis Kopfzeilen rein, ueber alle Runden summiert.
    ``Prefill``
        Kopfzeilen bis erstes Zeichen: das Modell liest die Eingabe. **Die
        Zahl, die mit der Anfragegroesse waechst** — und damit die, an der ein
        geschrumpfter Werkzeugkatalog sichtbar wuerde.
    ``Dekod``
        Erstes Zeichen bis Stromende: das Modell schreibt.
    ``Zwisch``
        Zwischen zwei Anbieterrunden: Werkzeuge, Schwaerzung, Datenbank.
    ``Nacharb``
        Nach der letzten Runde: Abschnitte, Nachricht, Verbrauch.

    ``Wz Sum/Wand`` stellt die Summe der Werkzeugdauern der belegten Wanduhr
    gegenueber, ``nb`` ist ihr Quotient: **1,0 heisst, es lief nie mehr als
    eines gleichzeitig**. ``Rd/Wz`` sind Runden und Werkzeugaufrufe — stehen
    dort 6/6, hat das Modell in jeder Runde genau eines bestellt, und dann ist
    Nebenlaeufigkeit im Ausfuehren per Konstruktion wirkungslos.

    ``TTFS`` ist der Sprachmodus, ``stumm`` sein Grund: soviele Zeichen liefen
    durch den Filter, bevor das erste Wort sprechbar war. Steht ``TTFS`` auf
    ``Gesamt`` und ``stumm`` auf der ganzen Antwortlaenge, gab der Filter
    waehrend des Stroms nie etwas frei — die Stimme begann erst, als der Lauf
    schon fertig war.
    """
    kopf = (
        f"{'Szenario':<20} {'Anlauf':>7} {'Ruest':>7} {'Netz':>7} {'Prefill':>7} "
        f"{'Dekod':>7} {'Zwisch':>7} {'Nacharb':>7} {'Gesamt':>7} "
        f"{'WzSumme':>7} {'WzWand':>7} {'nb':>5} {'TTFS':>7} {'stumm':>6} "
        f"{'Rd/Wz':>7}"
    )
    zeilen = [kopf, "-" * len(kopf)]
    for name, messungen in nach_szenario.items():
        gelungen = [m for m in messungen if m.ok]
        if not gelungen:
            continue
        neben = _median([m.nebenlaeufigkeit for m in gelungen])
        werkzeugzahl = statistics.median(
            [sum(r.werkzeug_zahl for r in m.runden) for m in gelungen]
        )
        zeilen.append(
            f"{name:<20}"
            f" {_z(_median([m.anlauf for m in gelungen]))}"
            f" {_z(_median([m.ruestzeit for m in gelungen]))}"
            f" {_z(_median([m.netzzeit for m in gelungen]))}"
            f" {_z(_median([m.prefillzeit for m in gelungen]))}"
            f" {_z(_median([m.dekodierzeit for m in gelungen]))}"
            f" {_z(_median([m.zwischenzeit for m in gelungen]))}"
            f" {_z(_median([m.nacharbeit for m in gelungen]))}"
            f" {_z(_median([m.gesamt for m in gelungen]))}"
            f" {_z(_median([m.werkzeugzeit for m in gelungen]))}"
            f" {_z(_median([m.werkzeug_wand for m in gelungen]))}"
            f" {(f'{neben:.1f}' if neben is not None else '-'):>5}"
            f" {_z(_median([m.ttfs for m in gelungen]))}"
            f" {statistics.median([m.stummzeichen for m in gelungen]):>6.0f}"
            f" {statistics.median([len(m.runden) for m in gelungen]):>3.0f}"
            f"/{werkzeugzahl:<3.0f}"
        )
    return "\n".join(zeilen)


def _k(zeichen: int) -> str:
    """Tausender kurz — die Tabelle ist eng und ASCII."""
    return f"{zeichen / 1000:.1f}k"


def _rueckfluss(nach_szenario: dict[str, list[Messung]]) -> list[str]:
    """Was jede Anfrage an Verlauf mitschleppt und was davon wiedererkannt wurde.

    **Zwei Zahlen nebeneinander, weil eine allein nicht trägt.** Die Cache-Quote
    kommt vom Anbieter und ist bei drei Wiederholungen als Kennzahl untauglich:
    im Protokoll ``20260814-194720-vorher.json`` meldet ``websuche`` für drei
    Durchgänge derselben Frage 100 %, 100 % und 0 % — bei je einer Runde. Byte-
    gleich waren diese Anfragen deswegen nicht: der Lageblock trägt die Uhrzeit
    auf die Minute genau (``ai_lage``). Nur eben auch nicht so verschieden, dass
    der Sprung daher käme. Der gemeinsame Präfix dagegen steht im Code fest und
    schwankt nicht. Laufen beide auseinander, liegt es am Anbieter und
    nicht an der Anfrage — und genau das ist eine Auskunft, die vorher niemand
    hatte.

    **Je Runde und nicht als Summe.** "Die Hälfte kam aus dem Zwischenspeicher"
    heißt bei zwei Runden entweder "beide zur Hälfte" oder "die erste gar nicht,
    die zweite ganz". Nur das zweite ist der Normalfall, und aus einer Summe ist
    es nicht zu lesen.

    Die Rundenreihe stammt aus dem **ersten gelungenen** Durchgang und nicht aus
    einem Median: die Zahl der Runden schwankt zwischen den Durchgängen, und ein
    Median über verschieden lange Reihen ist keine Zahl. Alle Durchgänge stehen
    vollständig im JSON.
    """
    zeilen: list[str] = []
    for name, messungen in nach_szenario.items():
        gelungen = [m for m in messungen if m.ok and m.runden]
        if not gelungen:
            continue
        # Die **erste** Runde jedes Durchgangs: sie trägt Verlauf und
        # Werkzeugkontext so, wie die Frage sie vorgefunden hat. Die späteren
        # Runden hängen nur an, was dieser Lauf selbst gelesen hat.
        erste = [m.runden[0] for m in gelungen]
        gemessen = [r for r in erste if r.praefix_zeichen is not None]
        praefix = (
            f"praefix="
            f"{_k(int(_median([float(r.praefix_zeichen or 0) for r in gemessen]) or 0)):>7} Z"
            f" ({(_median([r.praefix_anteil for r in gemessen]) or 0) * 100:3.0f}%)"
            # 24 Zeichen wie der befüllte Zweig, sonst verrutscht die Spalte.
            if gemessen else "praefix=      - Z       "
        )
        # Beides in Tokens und beides vom Anbieter gemeldet — Zeichen und Tokens
        # in einem Bruch wären eine Zahl, die nichts bedeutet.
        reihe = " ".join(
            f"{_k(r.cached_tokens)}/{_k(r.prompt_tokens)}" for r in gelungen[0].runden
        )
        # Der Werkzeugkatalog daneben, weil er **nicht** in `anfrage` steckt und
        # trotzdem ueber dieselbe Leitung geht. Ohne ihn liest sich eine Anfrage
        # von 20.000 Zeichen als klein, waehrend in Wahrheit 65.000 zum Anbieter
        # gehen und der groessere Teil davon der Katalog ist.
        katalog = int(_median([float(r.katalog_zeichen) for r in erste]) or 0)
        gesamtzeichen = int(_median([float(r.anfrage_zeichen) for r in erste]) or 0)
        zeilen.append(
            f"  {name:<20} "
            f"anfrage={_k(gesamtzeichen):>7} Z  "
            f"katalog={_k(katalog):>7} Z"
            f" ({katalog / max(1, katalog + gesamtzeichen) * 100:3.0f}%)  "
            f"{praefix}  Rd cache/prompt Tk: {reihe}"
        )
    return zeilen


def _treffer(nach_szenario: dict[str, list[Messung]]) -> tuple[int, int, list[str]]:
    """Wie oft das erwartete Werkzeug tatsaechlich kam.

    Ohne diese Zahl waere der Benchmark gefaehrlich: eine "Optimierung", die
    Werkzeuge weglaesst, sieht in jeder Zeitspalte grossartig aus. Die
    Funktionalitaet muss also mitgemessen werden, sonst optimiert man sie weg.
    """
    from services.ai_web_search_service import is_configured

    erfuellt = 0
    gesamt = 0
    verfehlt: list[str] = []
    for szenario in SZENARIEN:
        if not szenario.erwartet:
            continue
        # Ein Szenario, das gar nicht gefahren wurde (`MSM_BENCH_ONLY`), hat
        # nichts verfehlt. Ohne diese Zeile meldete ein gezielter Lauf ueber drei
        # Szenarien "Werkzeugtreffer 2/10" und neun Fehlschlaege — eine Zahl, die
        # aussieht wie ein Rueckschritt und keiner ist.
        if szenario.name not in nach_szenario:
            continue
        # Ein Werkzeug, das gar nicht angeboten wurde, kann das Modell nicht
        # rufen. `web_search` steht nur im Katalog, wenn ein Suchschluessel
        # hinterlegt ist (`_global_tool_definitions`) — ohne ihn waere die
        # Erwartung eine Anklage gegen das Modell fuer eine Entscheidung des
        # Betreibers. Der erste Baseline-Lauf meldete genau diesen falschen
        # Fehlschlag.
        if szenario.erwartet == frozenset({"web_search"}) and not is_configured():
            verfehlt.append(
                "websuche: uebersprungen — kein Suchschluessel hinterlegt, das "
                "Werkzeug wurde dem Modell nie angeboten"
            )
            continue
        gesamt += 1
        # Über alle Fragen des Szenarios: bei einem mit Nachfragen liest die
        # erste die Blueprints und die dritte fasst zusammen. Die Erwartung gilt
        # dem Szenario, nicht der einzelnen Frage — sonst wäre jede Nachfrage
        # ohne Werkzeug ein gemeldeter Fehlschlag und richtiges Verhalten stünde
        # als Rückschritt in der Tabelle. Genau daran hängt auch der offene Fall
        # der zweiten Frage: direkt antworten und erneut lesen sind beide
        # richtig, und beide bestehen diese Prüfung.
        messungen = [
            m
            for schritt in range(len(szenario.nachfragen) + 1)
            for m in nach_szenario.get(_schritt_name(szenario, schritt), [])
        ]
        genutzt = {w.name for m in messungen if m.ok for w in m.werkzeuge}
        if genutzt & szenario.erwartet:
            erfuellt += 1
        else:
            verfehlt.append(
                f"{szenario.name}: erwartet eines aus "
                f"{sorted(szenario.erwartet)}, gerufen {sorted(genutzt) or 'nichts'}"
            )
    return erfuellt, gesamt, verfehlt


# ── Der Lauf ─────────────────────────────────────────────────────────────


@NUR_MIT_SCHLUESSEL
@pytest.mark.asyncio
async def test_ai_benchmark(
    db: Session, owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Misst den ganzen Assistenten und schreibt das Ergebnis fort.

    Der Benutzer ist der Betreiber (``is_owner``): er umgeht jede
    Rechtepruefung. Das ist hier richtig — gemessen werden soll die Maschine,
    nicht die Rechteverwaltung, und ein an einer fehlenden Berechtigung
    gescheitertes Werkzeug waere eine Null in der Zeitspalte und ein falscher
    Befund in der Trefferspalte.
    """
    # Die Tabelle ist ASCII, aber `messung.fehler` traegt den Text einer
    # beliebigen Ausnahme — und die Fehlermeldungen dieses Projekts sind
    # deutsch. Ein einzelnes Umlautzeichen aus einem Ausfall wuerde sonst den
    # Bericht ueber alle uebrigen Szenarien mitreissen.
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")

    ai_skill_service.reset_shipped_cache_for_tests()
    provider = _provider(db)
    _server_anlegen(db)

    nach_szenario: dict[str, list[Messung]] = {}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=15.0),
        # Der einzige Ort, an dem die Zeit bis zu den Antwortkopfzeilen
        # ueberhaupt existiert — siehe `_kopfzeit_mitschreiben`.
        event_hooks={"response": [_kopfzeit_mitschreiben]},
    ) as client:
        # Genau das, was `main.lifespan` beim Start tut. Ohne diese zwei Zeilen
        # misst der Benchmark einen Prozess, den es so nicht gibt: der
        # Modellkatalog haette keinen Hintergrund-Client, wuerde also nie
        # nebenher auffrischen, und `anlauf` zeigte eine Wartezeit, die in der
        # Anwendung niemand mehr zahlt. Ein Benchmark, der guenstiger *oder*
        # teurer misst als die Wirklichkeit, ist gleich wertlos.
        from services import ai_model_catalog

        ai_model_catalog.laufzeit_setzen(client)
        ai_model_catalog.vorwaermen_anstossen()

        # Aufwaermen. Der erste Lauf zahlt den ersten Verbindungsaufbau — und
        # frueher auch den Modellkatalog, den jetzt das Vorwaermen oben
        # nebenher holt. Beides gehoert zur Wahrheit ueber den Kaltstart, aber
        # nicht in den Median eines Szenarios — sonst traegt `chat_trivial` als
        # erstes Szenario dauerhaft Kosten, die keinem Szenario gehoeren.
        aufwaermen = perf_counter()
        erster = await _messen(
            db, user=owner_user, provider=provider,
            szenario=Szenario(name="_aufwaermen", auftrag="Sag Hallo."),
            client=client, monkeypatch=monkeypatch,
        )
        kaltstart = perf_counter() - aufwaermen
        print(f"\n\n  Aufwaermlauf (Kaltstart inkl. Modellkatalog): {kaltstart:.2f}s\n")

        # Ein abgelehnter Schluessel wird nicht besser, wenn man es
        # sechsunddreissig Mal versucht. Genau das ist aber passiert: der
        # hinterlegte Schluessel war abgelaufen, und der Durchlauf lief bis zum
        # Ende durch, um eine Tabelle aus lauter AUSFALL-Zeilen auszugeben —
        # in denen der eigentliche Grund als eine von zwoelf Fussnoten stand.
        #
        # **Nur bei Authentifizierung, nicht bei jedem Fehler.** Ein 503 des
        # Anbieters heilt sich von selbst und darf keinen zwanzigminuetigen
        # Durchlauf abbrechen; ein 401 heilt nie.
        if not erster.ok and "AI_PROVIDER_AUTH_FAILED" in (erster.fehler or ""):
            pytest.fail(
                "Der Anbieter lehnt MSM_BENCH_AI_KEY ab (401/403). Der "
                "Benchmark misst nichts, solange der Schluessel nicht gilt — "
                "abgebrochen vor dem ersten Szenario."
            )

        for szenario in SZENARIEN:
            if BENCH_NUR and szenario.name not in BENCH_NUR:
                continue
            if szenario.saat is not None:
                szenario.saat(db, owner_user)
            for durchgang in range(BENCH_WIEDERHOLUNGEN):
                # Eine Unterhaltung von vorne: Schritt 0 leert den Verlauf, jede
                # Nachfrage findet ihn vor. `vorige` trägt die letzte Anfrage
                # weiter, damit die nächste ihren gemeinsamen Präfix dagegen
                # messen kann — und fängt bei jedem Durchgang wieder leer an,
                # weil der geleerte Verlauf keine Vorgängerin hat.
                #
                # Ohne Nachfragen ist das genau der Ablauf von vorher: eine
                # Runde durch die Schleife, `schritt=0`.
                vorige: list[dict] = []
                for schritt in range(len(szenario.nachfragen) + 1):
                    messung = await _messen(
                        db, user=owner_user, provider=provider, szenario=szenario,
                        client=client, monkeypatch=monkeypatch,
                        schritt=schritt, vorige_anfrage=vorige,
                    )
                    vorige = messung.letzte_anfrage
                    nach_szenario.setdefault(messung.szenario, []).append(messung)
                    marke = "ok" if messung.ok else f"AUSFALL {messung.fehler}"
                    print(
                        f"    {messung.szenario:<20} #{durchgang + 1} "
                        f"ttft={_z(messung.ttft_text).strip():>8} "
                        f"gesamt={messung.gesamt:6.2f}s "
                        f"block={messung.loop_block_max:5.2f}s "
                        f"runden={len(messung.runden)} {marke}"
                    )

    erfuellt, gesamt_erwartet, verfehlt = _treffer(nach_szenario)
    alle = [m for messungen in nach_szenario.values() for m in messungen if m.ok]

    # **Erst sichern, dann darstellen.** Die Reihenfolge ist hier nicht Stil,
    # sondern eine Lehre: der erste Durchlauf lief fuenf Minuten, kostete Tokens
    # und starb danach beim `print` an einem Zeichen, das die Konsole nicht
    # kennt. Eine Messung, die nur im Terminal existiert, ist eine Messung, die
    # man verlieren kann.
    ziel = Path(__file__).resolve().parents[1] / "logs" / "ai-benchmark"
    ziel.mkdir(parents=True, exist_ok=True)
    stempel = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    datei = ziel / f"{stempel}-{BENCH_LABEL}.json"
    datei.write_text(
        json.dumps(
            {
                "label": BENCH_LABEL,
                "modell": BENCH_MODEL,
                "zeitpunkt": datetime.now(timezone.utc).isoformat(),
                "wiederholungen": BENCH_WIEDERHOLUNGEN,
                "kaltstart": round(kaltstart, 3),
                "werkzeugtreffer": {"erfuellt": erfuellt, "gesamt": gesamt_erwartet},
                "verfehlt": verfehlt,
                "szenarien": {
                    name: [m.als_dict() for m in messungen]
                    for name, messungen in nach_szenario.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    linie = "=" * 118
    print("\n\n" + linie)
    print(f"  MSM AI-BENCHMARK - {BENCH_LABEL}   Modell: {BENCH_MODEL}   "
          f"n={BENCH_WIEDERHOLUNGEN} je Szenario (Median)")
    print(linie)
    print(_tabelle(nach_szenario))
    print(linie)

    # Wo die Zeit hingeht — die Tabelle darueber sagt nur, dass sie hingeht.
    print("  Phasen je Lauf   (nb = Werkzeuge gleichzeitig, 1.0 = keine)")
    print(_phasentabelle(nach_szenario))
    print(linie)

    rueckfluss = _rueckfluss(nach_szenario)
    if rueckfluss:
        print("  Rueckfluss und Praefix   (Z = Zeichen, Tk = Tokens)")
        for zeile in rueckfluss:
            print(zeile)
        print(linie)

    # Die Summenzeile ohne die Nachfragen (`#2`, `#3`). Sie existiert, um Läufe
    # über die Zeit zu vergleichen, und dieser Vergleich darf nicht daran
    # hängen, wie viele Nachfragen ein Szenario hat: `kontext_folge` liefert
    # neun Messungen, jedes andere drei. Ohne diesen Filter spränge die Summe
    # zwischen zwei Läufen aus einem Grund, der keine Änderung an MSM ist. In
    # Tabelle und JSON stehen die Nachfragen unverändert mit allen Zahlen.
    #
    # Die längste Blockade bleibt bewusst über **alles**: sie ist ein Höchstwert
    # und keine Summe, und eine im Nachfragelauf blockierte Schleife ist ein
    # Befund über MSM. Den zu verschweigen wäre schlimmer als eine Zahl, die um
    # einen Ausreißer schwankt.
    summen = [m for m in alle if "#" not in m.szenario]
    if alle:
        print(
            f"  Werkzeugtreffer: {erfuellt}/{gesamt_erwartet}   "
            f"laengste Blockade: {max(m.loop_block_max for m in alle):.2f}s (alles)   "
            f"TTFT Median: {_median([m.ttft_text for m in summen]) or 0:.2f}s   "
            f"Anbieterzeit: {sum(m.anbieterzeit for m in summen):.1f}s   "
            f"MSM-Zeit: {sum(m.msm_zeit for m in summen):.1f}s   (ohne Nachfragen)"
        )
    for zeile in verfehlt:
        print(f"  ! {zeile}")
    print(linie)
    print(f"  Ergebnis: {datei}\n")

    # Der Benchmark faellt nicht wegen einer Zahl durch — er misst. Durchfallen
    # soll er nur, wenn gar nichts gemessen werden konnte: dann stimmt etwas am
    # Aufbau nicht, und eine Tabelle voller Ausfaelle waere keine Grundlage.
    assert alle, "Kein einziger Lauf ist durchgekommen — Aufbau pruefen"


# =========================================================================
# Gleichzeitigkeit — wo MSM kippt, wenn viele Laeufe zugleich arbeiten
# =========================================================================
#
# Der Anlass steht in einer Frage des Betreibers: was passiert, wenn 10.000
# Auftraege zur selben Sekunde faellig werden? Die Szenarienmessung oben kann
# das nicht beantworten — sie faehrt einen Lauf nach dem anderen und misst den
# Anbieter mit. Hier laeuft es umgekehrt: **kein** echter Anbieter, dafuer
# beliebig viele Laeufe zur selben Zeit.
#
# Was dabei gemessen wird, ist bewusst nicht "wie schnell ist die KI". Gemessen
# wird, **welche Grenze zuerst zuschlaegt** und bei welcher Stufe:
#
#   * das Kontingent je Benutzer (`ai_limit_service.concurrent_operations`,
#     geprueft in `ai_usage_service.reserve_ai_usage`),
#   * der Verbindungspool der Datenbank (`database.py`: pool_size=10,
#     max_overflow=20, pool_timeout=60 — nur bei PostgreSQL),
#   * die Kanalgrenze des Vermittlers (`ai_run_broker.MAX_KANAELE` = 256),
#   * die Ereignisschleife selbst.
#
# **Was diese Messung nicht sehen kann, und warum das im Bericht stehen muss.**
# Die Testsuite haengt an einer SQLite-Datenbank im Arbeitsspeicher mit einer
# einzigen geteilten Verbindung (`conftest.py`, StaticPool). Ein erschoepfter
# Verbindungspool ist hier per Konstruktion unmoeglich, und `with_for_update`
# in `reserve_ai_usage` ist unter SQLite eine leere Anweisung. Beide Grenzen
# sind damit **ungemessen**, nicht etwa unproblematisch. Der Bericht sagt das
# ausdruecklich, statt eine gruene Zeile fuer eine Zusage auszugeben.

import logging as _logging
import shutil
import tempfile

import database as _database
from sqlalchemy import create_engine, event as _sa_event
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SaTimeoutError

from models.ai_run import BEENDET as LAUF_BEENDET
from services.ai_run_broker import MAX_KANAELE, MAX_RUECKSTAU
from services.openai_compatible_adapter import ProviderToolCall, StreamChunk

#: Die Poolwerte aus ``database.py`` — hier woertlich nachgebaut, damit die
#: Messung dieselbe Grenze trifft wie der Betrieb.
POOL_SIZE = 10
POOL_MAX_OVERFLOW = 20
POOL_TIMEOUT = 60.0


#: Der Auftrag jedes Lastlaufs. Bewusst einer mit **einer** Werkzeugrunde: eine
#: reine Textantwort wuerde den Werkzeugpfad ueberspringen, und genau der ist
#: der Teil, der Datenbank und Ereignisschleife anfasst.
PARALLEL_AUFTRAG = "Welche Server habe ich denn?"


def _gefaelschter_strom(latenz: float):
    """Ein Anbieter, der sich verhaelt wie einer, aber nichts kostet.

    Zwei Runden, weil ein Lauf mit einer Runde die Haelfte des Pfades auslaesst:
    zuerst ein Werkzeugaufruf, danach Text. Damit laeuft in jedem Lastlauf
    dasselbe wie im Betrieb — Werkzeugausfuehrung, Datenbankzugriff,
    Ereignisse an den Vermittler, Abrechnung.

    Die Wartezeit ist **echtes** ``await`` und kein ``sleep``: ein Anbieter
    blockiert die Ereignisschleife nicht, er laesst warten. Wuerde hier
    synchron geschlafen, waere jede gemessene Blockade eine des Messaufbaus.
    """

    async def _strom(
        _client, *, provider, api_key, messages, usage, tools=None, tool_choice=None,
        reasoning=False, reasoning_effort=None, cache_marke=False, model=None,
    ):
        del provider, api_key, reasoning, reasoning_effort, cache_marke
        await asyncio.sleep(latenz)
        letzte = messages[-1] if messages else {}
        if tools and letzte.get("role") != "tool":
            # Erste Runde: das Modell will ein Werkzeug.
            usage.total_tokens = 900
            usage.tool_calls = [
                ProviderToolCall(
                    id=f"call_{uuid4().hex[:12]}",
                    name="list_my_servers",
                    arguments={},
                )
            ]
            return
        for stueck in ("Ich habe nachgesehen. ", "Es sieht alles gut aus. ",
                       "Sag Bescheid, wenn du mehr brauchst."):
            await asyncio.sleep(latenz / 6)
            usage.output_chars += len(stueck)
            yield StreamChunk("content", stueck)
        usage.total_tokens = 1200

    return _strom


class _Kanalzaehler(_logging.Handler):
    """Zaehlt, wie oft der Vermittler einen laufenden Kanal wegwerfen musste.

    Der Vermittler meldet das als Warnung und macht danach weiter — der Lauf
    laeuft, aber niemand kann ihm mehr zusehen. Fuer den Benutzer ist das ein
    Chat, der mitten im Satz stehenbleibt und erst nach dem Neuladen wieder
    etwas zeigt. Ohne Zaehler waere dieser Ausfall in keiner Zahl sichtbar: der
    Lauf selbst gilt als gelungen.
    """

    def __init__(self) -> None:
        super().__init__(level=_logging.WARNING)
        self.verdraengt = 0

    def emit(self, record: _logging.LogRecord) -> None:
        if "Kanalgrenze" in str(record.msg):
            self.verdraengt += 1


class _Fehlerlupe(_logging.Handler):
    """Faengt die **echte** Ausnahme hinter einem gescheiterten Lauf ein.

    ``segment_ausfuehren`` behandelt seine Fehler selbst: es protokolliert
    ``error=InterfaceError`` und beendet den Lauf mit ``AI_STREAM_FAILED``.
    Nach aussen bleibt davon ein Code uebrig, der fuer jede Ursache derselbe
    ist — Datenbank, Werkzeug, Programmierfehler. Eine Lastmessung, die nur
    "12 x AI_STREAM_FAILED" melden kann, sagt dem Betreiber nichts darueber,
    **woran** es lag, und genau danach hat er gefragt.

    Der Griff nach ``sys.exc_info()`` funktioniert, weil die Protokollzeile
    innerhalb des ``except``-Blocks steht: die Ausnahme ist in diesem Moment
    noch die aktive. Das ist ein Mitlesen und kein Eingriff — der Lauf merkt
    nichts davon.
    """

    def __init__(self) -> None:
        super().__init__(level=_logging.WARNING)
        self.ursachen: dict[str, int] = {}

    def emit(self, record: _logging.LogRecord) -> None:
        if "fehlgeschlagen" not in str(record.msg):
            return
        typ, wert, _ = sys.exc_info()
        if typ is None:
            return
        text = f"{typ.__name__}: {str(wert)[:120]}"
        self.ursachen[text] = self.ursachen.get(text, 0) + 1


@dataclass
class Lastlauf:
    """Ein einzelner Lauf innerhalb einer Stufe."""

    ok: bool = False
    grund: str = ""
    anlauf: float = 0.0
    dauer: float = 0.0
    status: str = ""
    stop_reason: str = ""


@dataclass
class Stufe:
    """Was eine Stufe der Lastmessung ergeben hat."""

    laeufe: int
    ergebnisse: list[Lastlauf] = field(default_factory=list)
    wanduhr: float = 0.0
    anlauf_summe: float = 0.0
    loop_block_max: float = 0.0
    loop_block_summe: float = 0.0
    loop_block_anzahl: int = 0
    kanal_verdraengt: int = 0
    kanaele_am_ende: int = 0
    #: Die echten Ausnahmen hinter den Ausfaellen, gezaehlt.
    ursachen: dict[str, int] = field(default_factory=dict)
    #: Wieviele Datenbankverbindungen zur selben Zeit ausgeliehen waren.
    pool_hoechststand: int = 0

    @property
    def gelungen(self) -> int:
        return sum(1 for e in self.ergebnisse if e.ok)

    @property
    def gescheitert(self) -> int:
        return len(self.ergebnisse) - self.gelungen

    @property
    def gruende(self) -> dict[str, int]:
        zaehler: dict[str, int] = {}
        for eintrag in self.ergebnisse:
            if eintrag.ok:
                continue
            zaehler[eintrag.grund] = zaehler.get(eintrag.grund, 0) + 1
        return dict(sorted(zaehler.items(), key=lambda paar: -paar[1]))

    def als_dict(self) -> dict:
        dauern = [e.dauer for e in self.ergebnisse]
        anlaeufe = [e.anlauf for e in self.ergebnisse]
        return {
            "laeufe": self.laeufe,
            "gelungen": self.gelungen,
            "gescheitert": self.gescheitert,
            "gruende": self.gruende,
            "wanduhr": round(self.wanduhr, 3),
            "dauer_median": round(_med(dauern), 3),
            "dauer_p90": round(_p90(dauern), 3),
            "dauer_max": round(max(dauern, default=0.0), 3),
            "anlauf_median": round(_med(anlaeufe), 4),
            "anlauf_summe": round(self.anlauf_summe, 3),
            "loop_block_max": round(self.loop_block_max, 3),
            "loop_block_summe": round(self.loop_block_summe, 3),
            "loop_block_anzahl": self.loop_block_anzahl,
            "kanal_verdraengt": self.kanal_verdraengt,
            "kanaele_am_ende": self.kanaele_am_ende,
            "pool_hoechststand": self.pool_hoechststand,
            "ursachen": self.ursachen,
        }


def _med(werte: list[float]) -> float:
    return statistics.median(werte) if werte else 0.0


def _p90(werte: list[float]) -> float:
    """Das schlechteste Zehntel — die Zahl, die der unglueckliche Benutzer sieht.

    Der Median sagt, wie es den meisten ging. Er ist genau deshalb die falsche
    Zahl fuer eine Lastfrage: bei 1000 Laeufen sind 100 schlechter als er, und
    diese 100 sind die, die sich beschweren.
    """
    if not werte:
        return 0.0
    if len(werte) < 10:
        return max(werte)
    return statistics.quantiles(werte, n=10)[-1]


def _lastdatenbank(ordner: Path):
    """Eine eigene Datenbank fuer die Lastmessung — mit **echtem** Pool.

    **Warum das sein muss.** Die Testsuite haengt an einer SQLite im
    Arbeitsspeicher, deren ``StaticPool`` allen Sitzungen *dieselbe* Verbindung
    gibt. Der erste Messversuch scheiterte daran sichtbar: ab zehn
    gleichzeitigen Laeufen meldete jeder fuenfte einen
    ``sqlite3.InterfaceError: bad parameter or other API misuse`` — zwei
    Sitzungen, ein Cursor. Das ist kein Befund ueber MSM, sondern einer ueber
    den Messaufbau, und ``ai_stream_service._leseplaetze`` beschreibt genau
    diese Eigenschaft bereits im Quelltext.

    Eine Messung, deren Ausfaelle vom Messgeraet stammen, beantwortet die
    gestellte Frage nicht. Deshalb bekommt die Lastmessung eine Datei-Datenbank
    mit den Poolwerten aus ``database.py`` — jede Sitzung ihre eigene
    Verbindung, ``pool_size`` und ``max_overflow`` als echte Schranke. Damit
    ist die Frage "greift der Verbindungspool zuerst?" ueberhaupt erst
    stellbar.

    Was das **nicht** herstellt: PostgreSQL. SQLite kennt keinen echten
    Schreibnebenlauf, ``with_for_update`` ist eine leere Anweisung, und
    ``_leseplaetze`` liefert unter SQLite bewusst 1 statt 8. Der Bericht sagt
    das dazu, statt die Zahlen fuer mehr auszugeben, als sie sind.
    """
    pfad = ordner / "last.sqlite"
    engine = create_engine(
        f"sqlite:///{pfad.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        pool_timeout=POOL_TIMEOUT,
        pool_pre_ping=True,
    )

    @_sa_event.listens_for(engine, "connect")
    def _pragmas(verbindung, _record) -> None:
        # WAL, damit ein Leser einen Schreiber nicht aussperrt — sonst misst
        # die Stufe die Sperrstrategie von SQLite und nicht den KI-Pfad.
        verbindung.execute("PRAGMA journal_mode=WAL")
        verbindung.execute("PRAGMA busy_timeout=30000")
        verbindung.execute("PRAGMA foreign_keys=ON")

    _database.Base.metadata.create_all(engine)
    return engine


def _lastbenutzer(db: Session, anzahl: int) -> list[int]:
    """Ein eigener Benutzer je gleichzeitigem Lauf.

    Nicht ein Benutzer mit vielen Laeufen, und das ist keine Bequemlichkeit,
    sondern die Bauform des Produkts: ``ai_conversations.user_id`` ist
    eindeutig, ein Benutzer hat genau eine Unterhaltung, und ``lauf_beginnen``
    loest ueber ``vorgaenger_abloesen`` jeden noch laufenden Lauf derselben
    Unterhaltung ab. Zehn gleichzeitige Laeufe eines Benutzers sind damit **per
    Konstruktion** neun abgeloeste und einer — das misst nicht Last, sondern
    die Abloeseregel. Die Frage des Betreibers ("10.000 gleichzeitig faellige
    Auftraege") meint ohnehin viele Benutzer.

    Betreiberrechte, aus demselben Grund wie beim Szenariendurchlauf: gemessen
    werden soll die Maschine und nicht die Rechteverwaltung. Ein an einer
    fehlenden Berechtigung gescheitertes Werkzeug waere ein kuerzerer Lauf und
    damit eine geschoente Zahl.
    """
    from services.auth_service import AuthService

    ids: list[int] = []
    vorhandene = {
        name: uid
        for name, uid in db.query(User.username, User.id)
        .filter(User.username.like("last%"))
        .all()
    }
    for i in range(anzahl):
        name = f"last{i:05d}"
        if name in vorhandene:
            ids.append(vorhandene[name])
            continue
        benutzer = AuthService.create_user(
            db, name, f"{name}@bench.invalid", "LastPass123!"
        )
        benutzer.email_verified = True
        benutzer.is_owner = True
        db.flush()
        ids.append(benutzer.id)
    db.commit()
    return ids


def _grund_aus_fehler(code: str | None, exc: BaseException | None) -> str:
    """Woran ein Lauf gescheitert ist — in den Worten der Frage, nicht des Codes.

    Der Betreiber will nicht wissen, dass eine ``AiQuotaExceeded`` flog, sondern
    ob ihn **das Kontingent**, **die Datenbank**, **eine Sperre** oder ein
    schlichter Fehler ausgebremst hat. Genau diese vier Toepfe hat er benannt,
    und die Tabelle zaehlt in sie hinein.
    """
    if code:
        if code.startswith("AI_QUOTA"):
            return "kontingent"
        if code == "AI_REQUEST_CONFLICT":
            return "nebenlaeufersperre"
        if code in {"AI_CREDENTIAL_UNAVAILABLE", "AI_PROVIDER_KEY_MISSING"}:
            return "schluessel"
        if code == "AI_PREPARATION_FAILED":
            return "vorbereitung"
        return code.lower()
    if isinstance(exc, asyncio.TimeoutError):
        return "zeitgrenze"
    if isinstance(exc, SaTimeoutError):
        return "datenbankverbindung"
    if isinstance(exc, OperationalError):
        return "datenbank"
    if exc is not None:
        return f"ausnahme:{type(exc).__name__}"
    return "unbekannt"


async def _ein_lastlauf(
    *, user_id: int, provider_id: int, client: httpx.AsyncClient
) -> Lastlauf:
    """Ein vollstaendiger Lauf, wie ihn ``routers/ai_chat`` ausloest.

    Mit **eigener** Datenbanksitzung je Lauf und nicht mit der des Tests: so
    macht es der Request auch (``get_db``), und die Frage nach dem
    Verbindungspool laesst sich anders gar nicht stellen. Die Sitzung wird
    geschlossen, bevor der Lauf zu arbeiten beginnt — genau wie im Betrieb, wo
    der Request an dieser Stelle endet.

    Der Anlauf geht ueber ``lauf_beginnen_nebenher`` und nicht ueber
    ``lauf_beginnen``, weil der Endpunkt es so tut. Ein Benchmark, der den
    synchronen Weg misst, misst einen Weg, den im Betrieb niemand mehr geht —
    und haette die Blockade, um die es hier geht, weiterhin angezeigt, obwohl
    sie weg ist.
    """
    ergebnis = Lastlauf()
    beginn = perf_counter()
    run_id: str | None = None
    fehler: tuple[str, str] | None = None
    try:
        with SessionLocal() as db:
            benutzer = db.get(User, user_id)
            unterhaltung = ai_chat_service.get_or_create_primary_conversation(
                db, benutzer
            )
            db.commit()
            conversation_id = unterhaltung.id
        run_id, fehler = await ai_stream_service.lauf_beginnen_nebenher(
            user_id=user_id,
            conversation_id=conversation_id,
            provider_id=provider_id,
            request_id=uuid4(),
            content=PARALLEL_AUFTRAG,
            reasoning=False,
            reasoning_effort=None,
            context_chars=None,
        )
    except Exception as exc:  # noqa: BLE001 — ein Ausfall ist ein Messergebnis
        ergebnis.anlauf = perf_counter() - beginn
        ergebnis.dauer = ergebnis.anlauf
        ergebnis.grund = _grund_aus_fehler(None, exc)
        return ergebnis
    ergebnis.anlauf = perf_counter() - beginn
    if run_id is None:
        ergebnis.dauer = ergebnis.anlauf
        ergebnis.grund = _grund_aus_fehler(fehler[0] if fehler else None, None)
        return ergebnis

    ai_run_broker.eroeffnen(run_id)
    try:
        await asyncio.wait_for(
            ai_stream_service.segment_ausfuehren(run_id, client=client),
            timeout=BENCH_PARALLEL_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        ergebnis.dauer = perf_counter() - beginn
        ergebnis.grund = _grund_aus_fehler(None, exc)
        return ergebnis
    ergebnis.dauer = perf_counter() - beginn

    # Das Urteil kommt aus der Datenbank und nicht daraus, dass die Koroutine
    # zurueckkam. `segment_ausfuehren` faengt seine eigenen Fehler ab und endet
    # brav — ein Lauf, der an einem Kontingent gescheitert ist, sieht von aussen
    # aus wie einer, der gearbeitet hat. Der Unterschied steht in `status`.
    from models import AiRun

    with SessionLocal() as frisch:
        lauf = frisch.get(AiRun, run_id)
        if lauf is None:
            ergebnis.grund = "lauf_verschwunden"
            return ergebnis
        ergebnis.status = lauf.status or ""
        ergebnis.stop_reason = lauf.stop_reason or ""
    if ergebnis.status == "completed":
        ergebnis.ok = True
        return ergebnis
    if ergebnis.status not in LAUF_BEENDET:
        ergebnis.grund = f"haengt:{ergebnis.status}"
        return ergebnis
    ergebnis.grund = _grund_aus_fehler(ergebnis.stop_reason.split(":")[0], None)
    return ergebnis


async def _eine_stufe(
    *, laeufe: int, benutzer_ids: list[int], provider_id: int,
    client: httpx.AsyncClient, zaehler: _Kanalzaehler, lupe: _Fehlerlupe,
    pool,
) -> Stufe:
    """Eine Stufe: ``laeufe`` Laeufe, alle zur selben Zeit losgelassen."""
    stufe = Stufe(laeufe=laeufe)
    ai_run_broker.zuruecksetzen_fuer_tests()
    zaehler.verdraengt = 0
    lupe.ursachen = {}

    hoechststand = [0]

    def _pool_ablesen() -> None:
        hoechststand[0] = max(hoechststand[0], pool.checkedout())

    blockaden: list[tuple[float, float]] = []
    waechter = asyncio.create_task(_waechter(blockaden, _pool_ablesen))
    # Ein Takt Vorlauf, damit der Waechter seine erste Messung nicht als
    # Blockade des Starts verbucht.
    await asyncio.sleep(WAECHTER_TAKT * 2)

    t0 = perf_counter()
    ergebnisse = await asyncio.gather(
        *(
            _ein_lastlauf(
                user_id=benutzer_ids[i], provider_id=provider_id, client=client
            )
            for i in range(laeufe)
        ),
        return_exceptions=True,
    )
    stufe.wanduhr = perf_counter() - t0

    waechter.cancel()
    try:
        await waechter
    except asyncio.CancelledError:
        pass

    for eintrag in ergebnisse:
        if isinstance(eintrag, BaseException):
            stufe.ergebnisse.append(
                Lastlauf(grund=_grund_aus_fehler(None, eintrag))
            )
            continue
        stufe.ergebnisse.append(eintrag)
    stufe.anlauf_summe = sum(e.anlauf for e in stufe.ergebnisse)
    if blockaden:
        dauern = [ende - beginn for beginn, ende in blockaden]
        stufe.loop_block_max = max(dauern)
        stufe.loop_block_summe = sum(dauern)
        stufe.loop_block_anzahl = len(dauern)
    stufe.kanal_verdraengt = zaehler.verdraengt
    stufe.kanaele_am_ende = len(ai_run_broker._KANAELE)
    stufe.ursachen = dict(
        sorted(lupe.ursachen.items(), key=lambda paar: -paar[1])
    )
    stufe.pool_hoechststand = hoechststand[0]
    return stufe


def _lasttabelle(stufen: list[Stufe]) -> str:
    kopf = (
        f"{'Stufe':>6} {'ok':>6} {'Fehl':>5} {'Anl-Med':>8} {'Anl-Sum':>8} "
        f"{'Dau-Med':>8} {'Dau-P90':>8} {'Dau-Max':>8} {'Wanduhr':>8} "
        f"{'BLK-max':>8} {'BLK-Sum':>8} {'Kanal-w':>8} {'Pool':>6}  {'Gruende':<28}"
    )
    zeilen = [kopf, "-" * len(kopf)]
    for stufe in stufen:
        dauern = [e.dauer for e in stufe.ergebnisse]
        anlaeufe = [e.anlauf for e in stufe.ergebnisse]
        gruende = ", ".join(
            f"{name}x{anzahl}" for name, anzahl in stufe.gruende.items()
        )
        zeilen.append(
            f"{stufe.laeufe:>6}"
            f" {stufe.gelungen:>6}"
            f" {stufe.gescheitert:>5}"
            f" {_med(anlaeufe):>7.3f}s"
            f" {stufe.anlauf_summe:>7.2f}s"
            f" {_med(dauern):>7.2f}s"
            f" {_p90(dauern):>7.2f}s"
            f" {max(dauern, default=0.0):>7.2f}s"
            f" {stufe.wanduhr:>7.2f}s"
            f" {stufe.loop_block_max:>7.2f}s"
            f" {stufe.loop_block_summe:>7.2f}s"
            f" {stufe.kanal_verdraengt:>8}"
            f" {stufe.pool_hoechststand:>6}"
            f"  {(gruende or '-')[:28]:<28}"
        )
    return "\n".join(zeilen)


def _engpassbericht(stufen: list[Stufe]) -> list[str]:
    """Welche Grenze zuerst zuschlaegt — als Satz, nicht als Zahlenkolonne.

    Die Tabelle beantwortet "wieviel". Der Betreiber hat aber "was zuerst"
    gefragt, und diese Antwort steht nirgends in einer Spalte: sie ergibt sich
    aus dem Vergleich mehrerer Spalten ueber mehrere Stufen. Sie hier
    auszurechnen ist ehrlicher, als sie dem Leser zu ueberlassen — er wuerde
    sonst die groesste Zahl fuer den Engpass halten, und das ist sie oft nicht.
    """
    zeilen: list[str] = []

    erste_fehler = next((s for s in stufen if s.gescheitert), None)
    if erste_fehler is None:
        zeilen.append(
            "  Kein Lauf ist gescheitert. Bis zur hoechsten gefahrenen Stufe "
            f"({stufen[-1].laeufe}) kommt jede Nachricht durch."
        )
    else:
        haupt = next(iter(erste_fehler.gruende), "?")
        zeilen.append(
            f"  ERSTER AUSFALL bei Stufe {erste_fehler.laeufe}: "
            f"{erste_fehler.gescheitert} von {erste_fehler.laeufe}, "
            f"haeufigster Grund '{haupt}'."
        )

    erste_kanal = next((s for s in stufen if s.kanal_verdraengt), None)
    if erste_kanal is not None:
        zeilen.append(
            f"  KANALGRENZE ({MAX_KANAELE}) greift ab Stufe {erste_kanal.laeufe}: "
            f"{erste_kanal.kanal_verdraengt} laufende Kanaele verdraengt. Diese "
            "Laeufe arbeiten zu Ende, aber niemand kann ihnen mehr zusehen - "
            "der Chat bleibt stehen, bis der Benutzer neu laedt."
        )
    else:
        zeilen.append(
            f"  Kanalgrenze ({MAX_KANAELE}) nicht erreicht; Rueckstaugrenze je "
            f"Zuhoerer ist {MAX_RUECKSTAU} und wurde hier nicht gemessen "
            "(kein Zuhoerer angehaengt)."
        )

    erste_blockade = next((s for s in stufen if s.loop_block_max >= 1.0), None)
    if erste_blockade is not None:
        zeilen.append(
            f"  EREIGNISSCHLEIFE steht ab Stufe {erste_blockade.laeufe} am Stueck "
            f"{erste_blockade.loop_block_max:.2f}s, in Summe "
            f"{erste_blockade.loop_block_summe:.2f}s von "
            f"{erste_blockade.wanduhr:.2f}s Wanduhr. In dieser Zeit antwortet das "
            "Panel niemandem - auch keinem Benutzer, der gar nichts mit der KI "
            "zu tun hat."
        )
    else:
        schlimmste = max(stufen, key=lambda s: s.loop_block_max)
        zeilen.append(
            f"  Laengste Blockade am Stueck ueber alle Stufen: "
            f"{schlimmste.loop_block_max:.2f}s (Stufe {schlimmste.laeufe}), "
            f"Summe {schlimmste.loop_block_summe:.2f}s."
        )

    # Der Anlauf ist der Teil, den der Benutzer abwartet, bevor er seine
    # Lauf-Kennung bekommt. Seit `lauf_beginnen_nebenher` laeuft er in einem
    # eigenen Thread — die Summe steht also weiterhin da, sie kostet aber nicht
    # mehr die Ereignisschleife. Was sie kostet, steht in BLK.
    teuerste = max(stufen, key=lambda s: s.anlauf_summe)
    if teuerste.wanduhr > 0:
        anteil = 100.0 * teuerste.anlauf_summe / teuerste.wanduhr
        zeilen.append(
            f"  ANLAUF (lauf_beginnen_nebenher, im Thread, Breite "
            f"{ai_stream_service._anlauf_nebenlaeufigkeit()}): bei Stufe "
            f"{teuerste.laeufe} zusammen {teuerste.anlauf_summe:.2f}s = "
            f"{anteil:.0f}% der Wanduhr, Median je Lauf "
            f"{_med([e.anlauf for e in teuerste.ergebnisse]):.3f}s. Die Summe "
            "enthaelt die Wartezeit an der Schranke, nicht nur die Arbeit."
        )

    hoechster_pool = max(s.pool_hoechststand for s in stufen)
    grenze = POOL_SIZE + POOL_MAX_OVERFLOW
    if hoechster_pool >= grenze:
        zeilen.append(
            f"  VERBINDUNGSPOOL erschoepft: {hoechster_pool} von {grenze} "
            f"(pool_size={POOL_SIZE} + max_overflow={POOL_MAX_OVERFLOW}). Jeder "
            f"weitere Lauf wartet bis zu {POOL_TIMEOUT:.0f}s auf eine Verbindung "
            "und scheitert danach."
        )
    elif hoechster_pool > POOL_SIZE:
        ueberzug = next(s for s in stufen if s.pool_hoechststand > POOL_SIZE)
        zeilen.append(
            f"  VERBINDUNGSPOOL: ab Stufe {ueberzug.laeufe} wird der Ueberzug "
            f"gebraucht: hoechstens {hoechster_pool} von {grenze} Verbindungen "
            f"gleichzeitig, davon {hoechster_pool - POOL_SIZE} ueber "
            f"pool_size={POOL_SIZE} hinaus. Noch keine Absage, aber der Abstand "
            f"zur Grenze ist nur noch {grenze - hoechster_pool}."
        )
    else:
        zeilen.append(
            f"  VERBINDUNGSPOOL: hoechstens {hoechster_pool} von {grenze} "
            f"Verbindungen gleichzeitig ausgeliehen (pool_size={POOL_SIZE} + "
            f"max_overflow={POOL_MAX_OVERFLOW}). Er ist nicht der Engpass: der "
            "Lauf haelt seine Sitzung nur in kurzen Stuecken, nicht waehrend er "
            "auf den Anbieter wartet."
        )
    zeilen.append(
        "  UNGEMESSEN: die Nebenlaeuferschranke je Benutzer "
        "(concurrent_operations) greift im Chatpfad nie, weil ein Benutzer "
        "genau eine Unterhaltung hat und eine neue Nachricht den laufenden Lauf "
        "abloest. Sie zaehlt fuer Aufgaben- und Guardian-Laeufe."
    )
    zeilen.append(
        "  VORBEHALT: gemessen auf SQLite (Datei, WAL, eigener Pool). "
        "PostgreSQL bringt echten Schreibnebenlauf, ein wirksames "
        "'SELECT ... FOR UPDATE' in reserve_ai_usage und laut "
        "ai_stream_service._leseplaetze 8 statt 1 Werkzeugplatz. Die Zahlen "
        "hier sind eine Untergrenze fuer den Durchsatz und eine Obergrenze "
        "fuer die Sperrkosten."
    )
    return zeilen


@NUR_MIT_STUFEN
@pytest.mark.asyncio
async def test_ai_last_gleichzeitigkeit(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Misst, wo der KI-Pfad unter vielen gleichzeitigen Laeufen kippt.

    Ohne echten Anbieter und ohne Schluessel: gemessen wird MSM, nicht
    OpenRouter. Der Anbieter wird durch ``_gefaelschter_strom`` ersetzt, der
    Modellkatalog durch eine Antwort ohne Netz — beides sind die einzigen
    Stellen im Lauf, die hinaus wollen.

    Der Test faellt nicht wegen einer Zahl durch. Er faellt durch, wenn die
    Messung selbst nicht zustande kam: eine Stufe ohne einen einzigen gelungenen
    Lauf ist kein Befund ueber die Last, sondern einer ueber den Aufbau.
    """
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")

    ai_skill_service.reset_shipped_cache_for_tests()

    # ``db`` bleibt in der Signatur, weil die Fixture die Tabellen und die
    # Systemrollen der Suite bereitstellt — benutzt wird sie hier nicht. Die
    # Lastmessung arbeitet auf ihrer eigenen Datenbank (siehe
    # `_lastdatenbank`), und dieselbe Sitzung fuer beides zu nehmen waere genau
    # der Fehler, den der eigene Pool vermeiden soll.
    del db
    ordner = Path(tempfile.mkdtemp(prefix="msm-last-"))
    lastengine = _lastdatenbank(ordner)
    alte_bindung = _database.SessionLocal.kw.get("bind")

    from services import ai_model_catalog

    async def _kein_katalog(_client, _kind, _modell):
        """Der Katalog antwortet, ohne zu fragen.

        Er wird je Segment einmal gerufen. Unter Last waeren das tausend
        Anfragen an OpenRouter, die alle scheitern — gemessen waere danach die
        Wiederholungslogik einer HTTP-Bibliothek und nicht der KI-Pfad. Ein
        unbekanntes Modell ist im Code ohnehin ein vorgesehener Fall: dann geht
        keine Zwischenspeichermarke mit.
        """
        return None

    monkeypatch.setattr(ai_model_catalog, "finde", _kein_katalog)
    monkeypatch.setattr(
        ai_stream_service, "stream_chat_completion",
        _gefaelschter_strom(BENCH_PARALLEL_LATENZ),
    )

    zaehler = _Kanalzaehler()
    broker_logger = _logging.getLogger("services.ai_run_broker")
    broker_logger.addHandler(zaehler)
    lupe = _Fehlerlupe()
    strom_logger = _logging.getLogger("services.ai_stream_service")
    strom_logger.addHandler(lupe)

    stufen: list[Stufe] = []
    try:
        # **Eine Umleitung statt vieler Attrappen.** Jedes Modul im Lauf holt
        # sich seine Sitzung ueber dasselbe `SessionLocal`-Objekt; es umzubinden
        # trifft alle auf einmal. Einzelne `SessionLocal`-Verweise zu ersetzen
        # haette jeden Import einzeln erwischen muessen — und der erste
        # vergessene haette still auf der alten Datenbank weitergearbeitet.
        #
        # Innerhalb des ``try``, damit das ``finally`` sie in **jedem** Fall
        # wieder zurueckdreht. Stuende sie davor und ein Aufbauschritt faellt
        # um, arbeitete jeder folgende Test dieser Sitzung auf einer Datenbank,
        # die es dann nicht mehr gibt — ein Fehler, der nicht hier auffiele,
        # sondern irgendwo danach.
        _database.SessionLocal.configure(bind=lastengine)

        from services.role_service import ensure_system_roles

        with _database.SessionLocal() as aufbau:
            ensure_system_roles(aufbau)
            aufbau.commit()
            # Ein Anbieter ohne Schluesselzwang. `_segment_vorbereiten` wuerde
            # sonst mit AI_PROVIDER_KEY_MISSING abbrechen — und jede Zeile der
            # Tabelle waere ein Ausfall an einer Stelle, die mit Last nichts zu
            # tun hat.
            provider = AiProvider(
                name="Attrappe (last)",
                provider_kind="openrouter",
                default_model="attrappe/kein-echtes-modell",
                enabled=True,
                requires_api_key=False,
            )
            aufbau.add(provider)
            aufbau.commit()
            aufbau.refresh(provider)
            provider_id = provider.id
            _server_anlegen(aufbau)
            benutzer_ids = _lastbenutzer(aufbau, max(BENCH_PARALLEL_STUFEN))

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            for anzahl in BENCH_PARALLEL_STUFEN:
                stufe = await _eine_stufe(
                    laeufe=anzahl, benutzer_ids=benutzer_ids,
                    provider_id=provider_id, client=client, zaehler=zaehler,
                    lupe=lupe, pool=lastengine.pool,
                )
                stufen.append(stufe)
                print(
                    f"    Stufe {anzahl:>5}: ok={stufe.gelungen} "
                    f"fehl={stufe.gescheitert} wanduhr={stufe.wanduhr:.2f}s "
                    f"block={stufe.loop_block_max:.2f}s "
                    f"kanal_verdraengt={stufe.kanal_verdraengt}",
                    flush=True,
                )
    finally:
        broker_logger.removeHandler(zaehler)
        strom_logger.removeHandler(lupe)
        # Die Umleitung zurueck, **bevor** irgendetwas anderes laeuft: bliebe
        # sie stehen, arbeitete jeder folgende Test dieser Sitzung auf einer
        # Datenbank, die es gleich nicht mehr gibt.
        _database.SessionLocal.configure(bind=alte_bindung)
        lastengine.dispose()
        shutil.rmtree(ordner, ignore_errors=True)

    # **Erst sichern, dann darstellen** — dieselbe Lehre wie oben: eine Messung,
    # die nur im Terminal existiert, ist eine Messung, die man verlieren kann.
    ziel = Path(__file__).resolve().parents[1] / "logs" / "ai-benchmark"
    ziel.mkdir(parents=True, exist_ok=True)
    stempel = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    datei = ziel / f"{stempel}-{BENCH_LABEL}-parallel.json"
    datei.write_text(
        json.dumps(
            {
                "label": BENCH_LABEL,
                "art": "gleichzeitigkeit",
                "zeitpunkt": datetime.now(timezone.utc).isoformat(),
                "anbieter": "attrappe",
                "latenz_je_runde": BENCH_PARALLEL_LATENZ,
                "auftrag": PARALLEL_AUFTRAG,
                "datenbank": (
                    "eigene SQLite-Datei mit WAL und den Poolwerten aus "
                    "database.py; nicht die geteilte Speicherdatenbank der Suite"
                ),
                "grenzen_im_code": {
                    "ai_run_broker.MAX_KANAELE": MAX_KANAELE,
                    "ai_run_broker.MAX_RUECKSTAU": MAX_RUECKSTAU,
                    "database.pool_size": POOL_SIZE,
                    "database.max_overflow": POOL_MAX_OVERFLOW,
                    "database.pool_timeout": POOL_TIMEOUT,
                    "ai_stream_service._leseplaetze": (
                        "1 unter SQLite, 8 unter PostgreSQL"
                    ),
                    "ai_limit_service.concurrent_operations": (
                        "im Chatpfad wirkungslos, da ein Benutzer eine "
                        "Unterhaltung hat und der Vorgaenger abgeloest wird"
                    ),
                },
                "stufen": [stufe.als_dict() for stufe in stufen],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    linie = "=" * 130
    print("\n\n" + linie)
    print(
        f"  MSM AI-LAST (GLEICHZEITIGKEIT) - {BENCH_LABEL}   "
        f"Anbieter: Attrappe, {BENCH_PARALLEL_LATENZ:.2f}s je Runde, 2 Runden, "
        f"1 Werkzeug"
    )
    print(linie)
    print(_lasttabelle(stufen))
    print(linie)
    for stufe in stufen:
        for text, anzahl in stufe.ursachen.items():
            print(f"  Stufe {stufe.laeufe:>5}: {anzahl:>4}x {text}")
    for zeile in _engpassbericht(stufen):
        print(zeile)
    print(linie)
    print(f"  Ergebnis: {datei}\n")

    assert any(stufe.gelungen for stufe in stufen), (
        "Keine einzige Stufe hat einen Lauf durchgebracht - Aufbau pruefen"
    )
