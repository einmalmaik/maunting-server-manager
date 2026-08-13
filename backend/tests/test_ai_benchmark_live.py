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
    ai_context_window,
    ai_memory_service,
    ai_reasoning,
    ai_run_broker,
    ai_skill_service,
    ai_stream_service,
)
from services.ai_provider_service import _operator_aad
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

pytestmark = pytest.mark.skipif(
    not BENCH_KEY,
    reason="MSM_BENCH_AI_KEY nicht gesetzt — Benchmark uebersprungen",
)


# ── Der Waechter ─────────────────────────────────────────────────────────


async def _waechter(blockaden: list[tuple[float, float]]) -> None:
    """Merkt sich, wann die Ereignisschleife nicht zur Verfuegung stand.

    Eine Koroutine, die 20 ms schlafen will und erst nach 3 Sekunden wieder
    drankommt, hat 3 Sekunden lang nicht existiert — und genauso ging es in
    dieser Zeit jeder Anfrage jedes anderen Benutzers. Das ist der ganze Trick:
    der Waechter misst nichts am Lauf, er misst die **Verfuegbarkeit des
    Prozesses**, und das ist die Groesse, die der Betreiber als "die Seite laedt
    nicht mehr" erlebt hat.

    Aufgezeichnet wird das Fenster, nicht nur die Dauer. Damit laesst sich
    spaeter fuer jedes Ereignis ausrechnen, wann es fruehestens sichtbar war.
    """
    letzte = perf_counter()
    while True:
        await asyncio.sleep(WAECHTER_TAKT)
        jetzt = perf_counter()
        verzug = (jetzt - letzte) - WAECHTER_TAKT
        if verzug >= WAECHTER_SCHWELLE:
            # Beginn der Blockade ist der Zeitpunkt, an dem der Schlaf haette
            # enden sollen — vorher war die Schleife nachweislich frei.
            blockaden.append((letzte + WAECHTER_TAKT, jetzt))
        letzte = jetzt


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


@dataclass
class Runde:
    """Eine einzelne Anfrage an den Anbieter."""

    start: float
    erstes_zeichen: float | None = None
    ende: float | None = None

    @property
    def dauer(self) -> float:
        return (self.ende or self.start) - self.start

    @property
    def ttft(self) -> float | None:
        if self.erstes_zeichen is None:
            return None
        return self.erstes_zeichen - self.start


@dataclass
class Werkzeuglauf:
    name: str
    dauer: float
    art: str = "read"


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
    ttfe: float | None = None            # erstes Ereignis ueberhaupt
    ttft_denken: float | None = None     # erster Denkschritt, sichtbar
    ttft_text: float | None = None       # erstes Antwortzeichen, sichtbar
    ttfw: float | None = None            # erstes Werkzeug im Verlauf, sichtbar
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

    @property
    def msm_zeit(self) -> float:
        """Alles, was nicht der Anbieter war. Der Teil, der uns gehoert."""
        return max(0.0, self.gesamt - self.anbieterzeit)

    @property
    def werkzeugzeit(self) -> float:
        return sum(w.dauer for w in self.werkzeuge)

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
            "stille_max": round(self.stille_max, 3),
            "loop_block_max": round(self.loop_block_max, 3),
            "loop_block_summe": round(self.loop_block_summe, 3),
            "anbieterzeit": round(self.anbieterzeit, 3),
            "msm_zeit": round(self.msm_zeit, 3),
            "werkzeugzeit": round(self.werkzeugzeit, 3),
            "runden": len(self.runden),
            "runden_detail": [
                {
                    "dauer": round(r.dauer, 3),
                    "ttft": round(r.ttft, 3) if r.ttft is not None else None,
                }
                for r in self.runden
            ],
            "werkzeuge": [
                {"name": w.name, "dauer": round(w.dauer, 3), "art": w.art}
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


def _unterhaltung(db: Session, user: User) -> AiConversation:
    """Die eine Unterhaltung des Benutzers, vor jedem Szenario geleert.

    Geleert und nicht neu angelegt: ``ai_conversations.user_id`` ist eindeutig,
    ein Benutzer hat genau einen Chat. Das ist keine Huerde des Benchmarks,
    sondern eine Zusicherung des Produkts — und der Verlauf **muss** weg, sonst
    misst das zwoelfte Szenario den Kontext der elf davor mit und jede Zahl
    waechst monoton, ohne dass sich etwas verschlechtert haette.
    """
    conversation = ai_chat_service.get_or_create_primary_conversation(db, user)
    ai_chat_service.clear_history(db, conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


# ── Die Messung ──────────────────────────────────────────────────────────


async def _messen(
    db: Session,
    *,
    user: User,
    provider: AiProvider,
    szenario: Szenario,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> Messung:
    """Ein Lauf, vollstaendig vermessen."""
    messung = Messung(szenario=szenario.name)
    ai_run_broker.zuruecksetzen_fuer_tests()

    conversation = _unterhaltung(db, user)

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
        content=szenario.auftrag,
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
                messung.antwortlaenge += len(str(daten.get("content") or ""))
            elif ereignis == "reasoning":
                messung.denklaenge += len(str(daten.get("content") or ""))
        echt_veroeffentlichen(rid, ereignis, daten)

    # Anbieteranfragen einzeln vermessen: nur so laesst sich "das Modell war
    # langsam" von "wir waren langsam" trennen.
    echt_stream = ai_stream_service.stream_chat_completion

    async def _stream(*args, **kwargs):
        runde = Runde(start=perf_counter())
        runden.append(runde)
        try:
            async for chunk in echt_stream(*args, **kwargs):
                if runde.erstes_zeichen is None:
                    runde.erstes_zeichen = perf_counter()
                yield chunk
        finally:
            runde.ende = perf_counter()

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
            werkzeuge.append(Werkzeuglauf(name, perf_counter() - start, "read"))

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
            dauer = perf_counter() - start
            namen = [getattr(c, "name", "?") for c in aufrufe] or ["propose_*"]
            # Die Dauer der ganzen Schreibrunde auf ihre Aufrufe verteilt: sie
            # laufen ohnehin in einer Schleife, und die einzelne Zuordnung
            # brauchte einen Eingriff tiefer im Vorschlagsdienst.
            for name in namen:
                werkzeuge.append(Werkzeuglauf(name, dauer / len(namen), "write"))

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
    messung.werkzeuge = werkzeuge
    messung.anbieterzeit = sum(r.dauer for r in runden)
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
        messungen = nach_szenario.get(szenario.name, [])
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        # Aufwaermen. Der erste Lauf zahlt den Modellkatalog (ein HTTP-Abruf mit
        # sechs Stunden Frist) und den ersten Verbindungsaufbau. Beides gehoert
        # zur Wahrheit ueber den Kaltstart, aber nicht in den Median eines
        # Szenarios — sonst traegt `chat_trivial` als erstes Szenario dauerhaft
        # Kosten, die keinem Szenario gehoeren.
        aufwaermen = perf_counter()
        await _messen(
            db, user=owner_user, provider=provider,
            szenario=Szenario(name="_aufwaermen", auftrag="Sag Hallo."),
            client=client, monkeypatch=monkeypatch,
        )
        kaltstart = perf_counter() - aufwaermen
        print(f"\n\n  Aufwaermlauf (Kaltstart inkl. Modellkatalog): {kaltstart:.2f}s\n")

        for szenario in SZENARIEN:
            if BENCH_NUR and szenario.name not in BENCH_NUR:
                continue
            if szenario.saat is not None:
                szenario.saat(db, owner_user)
            messungen: list[Messung] = []
            for durchgang in range(BENCH_WIEDERHOLUNGEN):
                messung = await _messen(
                    db, user=owner_user, provider=provider, szenario=szenario,
                    client=client, monkeypatch=monkeypatch,
                )
                messungen.append(messung)
                marke = "ok" if messung.ok else f"AUSFALL {messung.fehler}"
                print(
                    f"    {szenario.name:<20} #{durchgang + 1} "
                    f"ttft={_z(messung.ttft_text).strip():>8} "
                    f"gesamt={messung.gesamt:6.2f}s "
                    f"block={messung.loop_block_max:5.2f}s "
                    f"runden={len(messung.runden)} {marke}"
                )
            nach_szenario[szenario.name] = messungen

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

    if alle:
        print(
            f"  Werkzeugtreffer: {erfuellt}/{gesamt_erwartet}   "
            f"TTFT Median ueber alles: {_median([m.ttft_text for m in alle]) or 0:.2f}s   "
            f"laengste Blockade: {max(m.loop_block_max for m in alle):.2f}s   "
            f"Anbieterzeit gesamt: {sum(m.anbieterzeit for m in alle):.1f}s   "
            f"MSM-Zeit gesamt: {sum(m.msm_zeit for m in alle):.1f}s"
        )
    for zeile in verfehlt:
        print(f"  ! {zeile}")
    print(linie)
    print(f"  Ergebnis: {datei}\n")

    # Der Benchmark faellt nicht wegen einer Zahl durch — er misst. Durchfallen
    # soll er nur, wenn gar nichts gemessen werden konnte: dann stimmt etwas am
    # Aufbau nicht, und eine Tabelle voller Ausfaelle waere keine Grundlage.
    assert alle, "Kein einziger Lauf ist durchgekommen — Aufbau pruefen"
