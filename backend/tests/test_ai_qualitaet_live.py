"""Prüfstand für die **Güte** der Antworten — Faktentreue und Ton.

Der Nachbar ``test_ai_benchmark_live.py`` misst, wie **schnell** der Assistent
ist. Diese Datei misst, ob das Ergebnis dabei etwas taugt. Zwei Dateien, weil es
zwei Fragen sind: die eine läuft gegen die Uhr, die andere gegen die Wahrheit.

**Der Anlass.** Der Rückfluss früherer Werkzeugergebnisse ist gedeckelt
(``MAX_TOOL_RESULT_CONTEXT_CHARS``): eine Folgefrage sieht vom Ergebnis der
vorigen nur noch einen Ausschnitt, sichtbar markiert mit ``[...gekuerzt]``.
Gemessen wurde, dass das billiger und schneller ist. Nicht gemessen wurde, ob
die Antworten dabei noch stimmen — in zwei von drei Läufen nannte das Modell
eine Steam-App-ID, die im gekürzten Block gar nicht mehr stand. Ob es sie
richtig nannte, stand in keinem Protokoll: der Geschwindigkeitsbenchmark
speichert nur die Antwort**länge**. Diese Datei speichert den Text.

**Der Maßstab kommt aus MSM selbst.** Der Systemprompt (``services/ai_prompt.py``)
gibt die Zusagen bereits — „Antworte knapp, freundlich und in der Sprache des
Benutzers“, „Sag, was du tust, während du es tust“, und vor allem: „Findest du
nichts, sag genau das … eine plausible Antwort ist hier schlimmer als keine.“
Hier wird nur nachgesehen, ob sie eingehalten werden. Es wird keine zweite
Stimme erfunden.

**Zwei Arten von Prüfung, und die erste ist die wichtigere.**

1. *Harte Fakten, ohne Richtermodell.* Die Welt dieses Tests ist vollständig
   bekannt: die Server legt er selbst an, die Blueprints liegen als Dateien im
   Repo. Die Sollwerte werden deshalb zur **Laufzeit aus der Quelle** gelesen
   (``_sollwert``) und nirgends abgeschrieben — ein Test, der seine eigene Kopie
   prüft, prüft nichts. Zahlen werden mit Ziffergrenze verglichen, sonst
   bestünde ``2944200`` die Prüfung auf ``294420``.

2. *Ton, teils Wortliste, teils Urteil.* Die Wortliste (``FLOSKELN``) ist
   unbestechlich und billig, erkennt aber nur Wendungen. Alles andere beurteilt
   ein Richtermodell — **blind** (es erfährt nie, welche Fassung die Antwort
   erzeugt hat), mit Belegzitat aus der Antwort, mehrfach je Antwort, und mit der
   ausdrücklichen Anweisung, im Zweifel durchfallen zu lassen. Ein Durchfall
   genügt.

**Die Bedingung, an der alles hängt.** Ein Prüfstand, der immer besteht, ist
wertlos. Deshalb prüft diese Datei sich selbst: die Tests unter „Selbstprüfung“
laufen **ohne Netz und ohne Schlüssel** in der normalen Suite mit und weisen
nach, dass eine erfundene Antwort wirklich durchfällt und eine richtige wirklich
besteht. Wer die Bewertung ändert, merkt es dort.

**Ausführen**

    cd backend
    set -a; source ~/.msm-bench.env; set +a
    python -m pytest tests/test_ai_qualitaet_live.py -o addopts="" -q -s

``-o addopts=""`` ist Pflicht: die ``pytest.ini`` setzt ``-n auto``, und acht
Prozesse, die gegeneinander messen, ergeben Rauschen.

Ohne ``MSM_BENCH_AI_KEY`` wird der bezahlte Teil übersprungen; die
Selbstprüfungen laufen trotzdem. Das Protokoll landet als JSON unter
``backend/logs/ai-qualitaet/`` (gitignoriert) — **mit** dem Antworttext, denn
genau sein Fehlen war das Problem.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.orm import Session

from models import AiProvider, User
from services import (
    ai_context_window,
    ai_reasoning,
    ai_run_broker,
    ai_skill_service,
    ai_stream_service,
)

from tests.test_ai_benchmark_live import (
    BENCH_BASE_URL,
    BENCH_KEY,
    BENCH_MODEL,
    NUR_MIT_SCHLUESSEL,
    _provider,
    _server_anlegen,
    _unterhaltung,
)


#: Das Modell, das den Ton beurteilt. Bewusst getrennt vom geprüften Modell und
#: aus der Umgebung, nicht fest verdrahtet: ein Modell, das sich selbst
#: benotet, ist ein schwacher Richter. Ohne eigene Angabe fällt es auf das
#: geprüfte Modell zurück — ein schwacher Richter ist immer noch besser als
#: keiner, und die Wortliste greift ohnehin unabhängig davon.
RICHTER_MODELL = os.environ.get("MSM_QUAL_RICHTER_MODELL", "").strip() or BENCH_MODEL

#: Wie viele unabhängige Urteile je Antwort. Zwei genügen: ein Durchfall reicht,
#: die Frage ist also nicht „welche Note im Mittel“, sondern „findet irgendwer
#: einen belegten Mangel“. Ein dritter Richter kostet ein Drittel mehr und
#: verschiebt diese Frage nicht.
RICHTER_ANZAHL = int(os.environ.get("MSM_QUAL_RICHTER", "2"))

BENCH_TIMEOUT = float(os.environ.get("MSM_BENCH_TIMEOUT", "300"))

PROTOKOLL_ORDNER = Path(__file__).resolve().parent.parent / "logs" / "ai-qualitaet"

BLUEPRINT_ORDNER = Path(__file__).resolve().parent.parent / "blueprints" / "native"


# ── Die Wahrheit, gegen die geprüft wird ─────────────────────────────────


def _blueprint(kennung: str) -> dict:
    """Ein Blueprint aus der Quelle, nicht aus einer Kopie."""
    pfad = BLUEPRINT_ORDNER / f"{kennung}.blueprint.json"
    return json.loads(pfad.read_text(encoding="utf-8"))


def _sollwert(art: str, schluessel: str) -> str:
    """Der Sollwert einer Prüfung, zur Laufzeit aus der Quelle gelesen.

    Der Umweg ist der Punkt. Stünde hier ``"294420"`` als Zeichenkette, prüfte
    der Test seine eigene Abschrift: ändert jemand den Blueprint, bliebe er grün
    und die Antwort der KI wäre falsch. So bricht er stattdessen — und das ist
    das gewünschte Verhalten.
    """
    if art == "appid":
        return str(_blueprint(schluessel)["source"]["steam"]["appId"])
    if art == "spielname":
        return str(_blueprint(schluessel)["meta"]["name"])
    raise AssertionError(f"unbekannte Sollwertart: {art!r}")


def _hat_rcon(kennung: str) -> bool:
    return any(p.get("name") == "rcon" for p in _blueprint(kennung).get("ports", []))


def _enthaelt(text: str, wert: str) -> bool:
    """Kommt ``wert`` in ``text`` vor — bei Zahlen mit Ziffergrenze?

    Ohne die Grenze bestünde ``2944200`` die Prüfung auf ``294420``, und eine um
    eine Ziffer verrutschte App-ID ginge als richtig durch. Bei Wörtern wäre eine
    Grenze dagegen schädlich: „7 Days to Die“ soll auch in „7 Days to Die-Server“
    zählen.
    """
    if wert.isdigit():
        return re.search(rf"(?<!\d){re.escape(wert)}(?!\d)", text) is not None
    return wert.casefold() in text.casefold()


# ── Der Maßstab für den Ton ──────────────────────────────────────────────


#: Wendungen, die eine Antwort nicht besser machen. Jede mit Grund, damit die
#: Liste eine Begründung hat und keinen Geschmack.
#:
#: Die Muster sind absichtlich eng. „Gerne“ ist keine Floskel — „Das mache ich
#: gerne, aber erst nach deiner Bestätigung“ ist eine gute Antwort. Ein
#: alleinstehendes „Gerne!“ als Eröffnung ist eine. Deshalb steht in der Liste
#: das Muster, nicht das Wort.
FLOSKELN: tuple[tuple[str, str], ...] = (
    (r"\bAls (?:KI|Sprachmodell|KI-Assistent)\b",
     "Der Assistent soll über die Sache reden, nicht über sich selbst."),
    (r"Ich hoffe,? (?:das|dies) hilft",
     "Leerer Abschluss; sagt nichts über die Sache."),
    (r"Zusammenfassend l[äa]sst sich sagen",
     "Ankündigung statt Aussage — die Zusammenfassung selbst genügt."),
    (r"Es ist wichtig zu beachten",
     "Füllsel; was wichtig ist, steht besser einfach da."),
    (r"^\s*(?:Gerne|Selbstverst[äa]ndlich|Nat[üu]rlich)\s*[!.]",
     "Reine Höflichkeitseröffnung ohne Inhalt."),
    (r"Ich stehe (?:dir|Ihnen) (?:jederzeit )?zur Verf[üu]gung",
     "Floskel aus dem Serviceschreiben, nicht aus einem Gespräch."),
    (r"Lass(?:en Sie)? es mich wissen, wenn",
     "Angebot ohne Gehalt; der Benutzer weiß, dass er fragen kann."),
)


#: Was das Richtermodell beurteilen soll. Abgeleitet aus dem MSM-Systemprompt
#: und der Vorgabe des Betreibers. Beide Richtungen stehen ausdrücklich drin:
#: nur auf Kürze zu prüfen belohnt schroffe Antworten, nur auf Freundlichkeit
#: belohnt Geschwurbel. Gefragt ist beides zugleich.
TON_MASSSTAB = """\
1. KLARTEXT — die Antwort sagt die Sache direkt, ohne Anlauf, ohne Umschweife.
   Durchgefallen: sie windet sich, kündigt an statt zu sagen, oder verpackt eine
   schlechte Nachricht so weich, dass sie nicht mehr ankommt.
2. RESPEKT — sie nimmt den Benutzer ernst. Durchgefallen: sie belehrt, ist
   herablassend, macht Vorwürfe, oder beschwichtigt statt zu helfen. Ein Fehler
   des Benutzers wird sachlich behandelt, nicht kommentiert.
3. NATÜRLICH — sie klingt wie ein Mensch, der sich auskennt. Durchgefallen:
   gestelzt, übertrieben förmlich, Werbesprache, Floskelketten, oder ein
   Aufzählungsgerüst, wo zwei Sätze gereicht hätten.
4. KNAPP UND VOLLSTÄNDIG ZUGLEICH — sie beantwortet, was gefragt war, und hört
   dann auf. Durchgefallen in BEIDE Richtungen: ausufernd UND abgehackt. Eine
   schroffe Ein-Wort-Antwort ist genauso ein Durchfall wie eine Seite Prosa.

Ausdrücklich KEIN Mangel: dass die Antwort ankündigt, was sie gleich nachsieht
("Ich schau mir erst den Zustand deiner Server an."). Das verlangt der
Systemprompt dieses Produkts so. Wer das als Geschwafel wertet, misst gegen das
Produkt statt für es.

Ausdrücklich KEIN Mangel: zuzugeben, dass etwas nicht sicher bekannt ist. Das
ist erwünscht — eine plausible Erfindung ist schlimmer als ein Eingeständnis.

DU BEURTEILST NICHT, OB DIE ANTWORT SACHLICH RICHTIG IST. Das ist bereits gegen
die Quelldaten dieses Panels geprüft, und zwar mit ihnen und nicht aus dem
Gedächtnis. Zahlen, Kennungen, Portnamen und Servernamen sind für dich gesetzt —
auch dann, wenn du sie anders in Erinnerung hast. Ein Mangel, dessen Beleg auf
eine sachliche Behauptung zielt, ist kein Mangel und wird nicht gemeldet.
"""


# ── Die Prüffälle ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fall:
    """Eine Frage und die Wahrheit, an der ihre Antwort gemessen wird.

    Ein neuer Fall ist ein Eintrag in ``FAELLE`` und keine neue Funktion — das
    ist die Zusage, die diese Datei wartbar hält.
    """

    name: str
    frage: str
    #: Optionale erste Frage in derselben Unterhaltung. Sie füllt den
    #: Werkzeugkontext; die eigentliche Prüfung gilt dann der Antwort auf
    #: ``frage``, die auf einem womöglich gekürzten Block beruht.
    vorfrage: str = ""
    #: Sollwerte als ``(art, schluessel)``, aufgelöst über ``_sollwert``.
    muss: tuple[tuple[str, str], ...] = ()
    #: Zeichenketten, deren Vorkommen eine Erfindung belegt.
    verboten: tuple[str, ...] = ()
    #: Begriffe, ohne die die Antwort am Thema vorbeigeht. Verhindert, dass ein
    #: ausweichendes „Dazu kann ich nichts sagen“ als ehrlich durchgeht.
    bezug: tuple[str, ...] = ()
    #: Darf die Antwort statt des Sollwerts einräumen, dass sie nachsehen muss?
    #: Nur dort wahr, wo der Wert im Kontext wirklich fehlen kann.
    ausweg_erlaubt: bool = False
    #: Wird dem Richter zusätzlich zum Maßstab mitgegeben.
    richterhinweis: str = ""
    #: Nur Ton prüfen — für Fragen ohne harten Sollwert.
    nur_ton: bool = False


#: Die Vergleichsfrage nennt **fünf** Spiele, und das ist gerechnet, nicht
#: gegriffen. Ein ``read_blueprint``-Ergebnis wiegt persistiert 2.567 bis 5.824
#: Zeichen; die fünf größten ergeben zusammen rund 19.900, mit ``list_blueprints``
#: rund 23.200. Der Rückflussdeckel steht bei 16.000 — es fällt also sicher etwas
#: heraus, und weil ``_recent_tool_results`` vom jüngsten Ergebnis rückwärts
#: sammelt, trifft es das **zuerst gelesene**. Deshalb steht 7 Days to Die vorn:
#: es ist zugleich das größte (5.824) und das, nach dem der Kernfall fragt.
#:
#: Drei Spiele wären zu wenig gewesen — 13.065 Zeichen liegen unter dem Deckel,
#: der Block bliebe unversehrt und der Fall bewiese nichts. Genau das ist beim
#: ersten Anlauf passiert. ``_rueckfluss_lage`` misst deshalb mit, ob wirklich
#: gekürzt wurde, statt es anzunehmen.
_VERGLEICHSFRAGE = (
    "Ich will einen neuen Server aufsetzen und vergleiche gerade 7 Days to Die, "
    "Conan Exiles, ARK: Survival Evolved, ARK: Survival Ascended und DayZ. Lies "
    "mir bitte die Blueprints von allen fünf und sag mir, was jeder an Ports und "
    "Umgebung braucht."
)


FAELLE: tuple[Fall, ...] = (
    Fall(
        name="appid_nach_kuerzung",
        vorfrage=_VERGLEICHSFRAGE,
        frage="Welche Steam-App-ID hat 7 Days to Die?",
        muss=(("appid", "seven_days_to_die"),),
        bezug=("7 days", "app"),
        ausweg_erlaubt=True,
        richterhinweis=(
            "Falls die Antwort einräumt, den Wert nicht sicher zu haben: das ist "
            "erwünscht und kein Mangel — beurteile dann nur, ob sie es klar und "
            "ohne Gedruckse sagt."
        ),
    ),
    Fall(
        name="rcon_nach_kuerzung",
        vorfrage=_VERGLEICHSFRAGE,
        frage="Welches von den fünfen braucht keinen RCON-Port?",
        muss=(("spielname", "seven_days_to_die"),),
        bezug=("rcon",),
        ausweg_erlaubt=True,
    ),
    Fall(
        name="erfundener_server",
        frage=(
            "Wie ist der aktuelle Status von meinem Server 'Atlantis'? "
            "Der macht seit gestern Zicken."
        ),
        verboten=("Atlantis läuft", "Atlantis ist online", "Atlantis ist offline"),
        bezug=("atlantis",),
        richterhinweis=(
            "Richtig ist zu sagen, dass es diesen Server nicht gibt, und "
            "anzubieten, welche es gibt. Achte darauf, ob das ohne Belehrung "
            "geschieht."
        ),
    ),
    Fall(
        name="serverliste",
        frage="Welche Server habe ich, und welche laufen gerade?",
        muss=(),
        verboten=("Bench 4", "Bench 5"),
        bezug=("bench",),
        richterhinweis="Drei Server, zwei laufen, einer ist gestoppt.",
    ),
    Fall(
        name="schlechte_nachricht",
        frage=(
            "Ich hab aus Versehen die serverconfig.xml von meinem 7DTD-Server "
            "überschrieben und kein Backup gemacht. Wie schlimm ist das?"
        ),
        bezug=("config", "backup", "sicherung", "datei", "7dtd"),
        nur_ton=True,
        richterhinweis=(
            "Der Benutzer hat einen Fehler gemacht und ist genervt. Geprüft wird "
            "vor allem Punkt 2: sachlich helfen, nicht belehren, nicht "
            "beschwichtigen, den Fehler nicht kommentieren."
        ),
    ),
    Fall(
        name="einfache_frage",
        frage="Was ist ein Blueprint in MSM, in zwei Sätzen?",
        bezug=("blueprint",),
        nur_ton=True,
        richterhinweis=(
            "Gegenprobe: das muss ein Modell sicher können. Fällt ausgerechnet "
            "dieser Fall durch, ist eher der Maßstab zu streng als die Antwort "
            "schlecht."
        ),
    ),
)


# ── Die Bewertung ────────────────────────────────────────────────────────


#: Wendungen, an denen ein ehrliches Eingeständnis zu erkennen ist.
AUSWEG = (
    r"nicht sicher", r"wei[ßs] ich nicht", r"kann ich (?:dir )?nicht sicher",
    r"schau(?:e)? (?:ich )?(?:nochmal |kurz )?nach", r"lese? (?:ich )?(?:nochmal|neu)",
    r"nicht mehr (?:im Kontext|vor mir|vollst[äa]ndig)", r"gek[üu]rzt",
    r"m[üu]sste ich (?:nachsehen|nachlesen|nachschauen)",
    r"habe ich (?:gerade )?nicht (?:mehr )?(?:vollst[äa]ndig|vorliegen)",
)


@dataclass
class Urteil:
    """Was von einer Antwort übrig bleibt, wenn man sie ernst nimmt."""

    fall: str
    antwort: str = ""
    werkzeuge: tuple[str, ...] = ()
    fakten_ok: bool = True
    fakten_grund: str = ""
    floskeln: tuple[str, ...] = ()
    ton_ok: bool = True
    ton_grund: str = ""
    ausgewichen: bool = False
    fehler: str = ""
    #: Größe des Werkzeugrückflusses, den diese Frage vorfand, und ob dabei
    #: gekürzt wurde. Nur bei Fällen mit Vorfrage gesetzt. ``gekuerzt=False``
    #: heißt: der Fall hat die Kürzung nicht geprüft, egal wie er ausging.
    rueckfluss_zeichen: int = 0
    gekuerzt: bool = False
    #: Stand der gesuchte Sollwert nach dem Kürzen noch im Block? Entscheidet,
    #: ob eine richtige Antwort abgelesen oder aus dem Gedächtnis war.
    sollwert_im_block: bool = False

    @property
    def bestanden(self) -> bool:
        return self.fakten_ok and self.ton_ok and not self.fehler

    def als_dict(self) -> dict:
        return {
            "fall": self.fall,
            "bestanden": self.bestanden,
            "fakten_ok": self.fakten_ok,
            "fakten_grund": self.fakten_grund,
            "ton_ok": self.ton_ok,
            "ton_grund": self.ton_grund,
            "floskeln": list(self.floskeln),
            "ausgewichen": self.ausgewichen,
            "werkzeuge": list(self.werkzeuge),
            "rueckfluss_zeichen": self.rueckfluss_zeichen,
            "gekuerzt": self.gekuerzt,
            "sollwert_im_block": self.sollwert_im_block,
            "fehler": self.fehler,
            # Der Antworttext gehört ins Protokoll. Sein Fehlen im
            # Geschwindigkeitsbenchmark war der Grund, warum niemand sagen
            # konnte, ob eine Antwort stimmt.
            "antwort": self.antwort,
        }


def fakten_pruefen(fall: Fall, antwort: str, werkzeuge: tuple[str, ...]) -> Urteil:
    """Die harte Prüfung. Kein Modell, keine Meinung, kein Ermessen."""
    urteil = Urteil(fall=fall.name, antwort=antwort, werkzeuge=werkzeuge)

    for verboten in fall.verboten:
        if _enthaelt(antwort, verboten):
            urteil.fakten_ok = False
            urteil.fakten_grund = f"erfunden: {verboten!r} steht in der Antwort"
            return urteil

    # Eine Antwort, die das Thema gar nicht berührt, ist kein ehrlicher Ausweg,
    # sondern ein Ausweichen. Ohne diese Prüfung bestünde „Dazu kann ich nichts
    # sagen.“ jeden Fall mit erlaubtem Ausweg.
    if fall.bezug and not any(_enthaelt(antwort, b) for b in fall.bezug):
        urteil.fakten_ok = False
        urteil.fakten_grund = (
            "geht am Thema vorbei: keiner der Bezugsbegriffe "
            f"{list(fall.bezug)} kommt vor"
        )
        return urteil

    if fall.nur_ton:
        return urteil

    fehlend = [
        _sollwert(art, sch)
        for art, sch in fall.muss
        if not _enthaelt(antwort, _sollwert(art, sch))
    ]
    if not fehlend:
        return urteil

    if fall.ausweg_erlaubt:
        eingeraeumt = any(
            re.search(muster, antwort, re.IGNORECASE) for muster in AUSWEG
        )
        # Ein Werkzeugaufruf ist die andere Form von Ehrlichkeit: das Modell hat
        # nicht geraten, sondern nachgesehen. Dass der Wert danach trotzdem
        # fehlt, ist ein Mangel der Antwort — aber kein erfundener Fakt.
        nachgesehen = bool(werkzeuge)
        if eingeraeumt or nachgesehen:
            urteil.ausgewichen = True
            urteil.fakten_grund = (
                f"Sollwert {fehlend} fehlt, aber ehrlich: "
                + ("eingeräumt" if eingeraeumt else "")
                + (" und " if eingeraeumt and nachgesehen else "")
                + (f"nachgesehen mit {list(werkzeuge)}" if nachgesehen else "")
            )
            return urteil

    urteil.fakten_ok = False
    urteil.fakten_grund = (
        f"behauptet ohne Deckung: Sollwert {fehlend} fehlt, kein Eingeständnis, "
        "kein Werkzeugaufruf"
    )
    return urteil


def floskeln_finden(antwort: str) -> tuple[str, ...]:
    """Die billige Hälfte der Tonprüfung — unbestechlich und ohne Netz."""
    treffer = []
    for muster, _grund in FLOSKELN:
        fund = re.search(muster, antwort, re.IGNORECASE | re.MULTILINE)
        if fund:
            treffer.append(fund.group(0).strip())
    return tuple(treffer)


RICHTER_AUFTRAG = """\
Du beurteilst die Antwort eines Assistenten in einem Gameserver-Panel. Du weißt
nicht, wer sie erzeugt hat, und das ist Absicht — beurteile den Text, sonst
nichts.

FRAGE DES BENUTZERS:
{frage}

ANTWORT DES ASSISTENTEN:
{antwort}

MASSSTAB:
{massstab}
{hinweis}
Beurteile jeden der vier Punkte einzeln. Für jeden Mangel MUSST du ein wörtliches
Zitat aus der Antwort angeben, das ihn belegt. Findest du kein Zitat, ist es kein
Mangel — dann gilt der Punkt als bestanden.

Sei streng. Im Zweifel: durchgefallen.

Antworte ausschließlich mit JSON in genau dieser Form:
{{"bestanden": true/false, "maengel": [{{"punkt": 1-4, "was": "...", "zitat": "..."}}]}}
"""


def _zitat_belegt(antwort: str, zitat: str) -> bool:
    """Steht dieses Zitat wirklich in der Antwort?

    Der Richter ist angewiesen, jeden Mangel wörtlich zu belegen. Er hält sich
    nicht immer daran: im Lauf vom 15.08.2026 meldete er „kaputte
    Markdown-Formatierung“ und belegte sie mit ``**Anderer Server** —
    [ADDRESS]*Server ist nicht sichtbar**``. In der Antwort stand an dieser
    Stelle ``nenne mir bitte den Namen``. Gegengeprüft: weder
    ``redact_sensitive_text`` noch ``redact_freetext`` fassen diesen Text an —
    die Marke war frei erfunden, und mit ihr der Mangel.

    Ein Beleg, den niemand nachprüft, ist keiner. Verglichen wird mit
    zusammengefasstem Leerraum, damit eine anders umbrochene Zeile nicht als
    Erfindung gilt — der Richter soll zitieren, nicht abtippen.

    **Platzhalter wie ``[ADDRESS]`` gelten als Lückenfüller, nicht als Erfindung.**
    Auf dem Rückweg vom Anbieter ersetzt irgendetwas Wortgruppen durch solche
    Marken: aus „ob die alte Konfiguration teilweise rekonstruierbar ist“ wurde
    „ob die [ADDRESS] teilweise rekonstruierbar ist“. Nachgeprüft, dass es nicht
    an uns liegt — ``[ADDRESS]`` kommt in ``services/`` nirgends vor, und weder
    ``redact_sensitive_text`` noch ``redact_freetext`` fassen diese Sätze an.
    Würde die Prüfung solche Zitate verwerfen, fielen echte Mängel still unter
    den Tisch und der Prüfstand würde mit jedem Platzhalter nachsichtiger. Also:
    die Marke ist eine Lücke, der Text drumherum muss stimmen — und zwar der
    Reihe nach.
    """
    if not zitat:
        return False
    glatt = lambda t: re.sub(r"\s+", " ", t).strip().casefold()
    ziel, beleg = glatt(antwort), glatt(zitat)
    if beleg in ziel:
        return True
    stuecke = [s for s in re.split(r"\[[a-z_]+\]", beleg) if s.strip()]
    if len(stuecke) < 2:
        # Ein Zitat, das fast nur aus Platzhaltern besteht, belegt nichts.
        return False
    stelle = 0
    for stueck in stuecke:
        gefunden = ziel.find(stueck.strip(), stelle)
        if gefunden < 0:
            return False
        stelle = gefunden + len(stueck.strip())
    return True


def _ist_platzhalter_artefakt(was: str, zitat: str) -> bool:
    """Ist dieser „Mangel“ nur die Verstümmelung auf dem Weg zum Richter?

    Der Richter sieht die Antwort nicht immer so, wie sie beim Benutzer ankam.
    Gemessen am 15.08.2026: die Antwort lautete „braucht … keinen ausdrücklich
    deklarierten **RCON-Port**“, der Richter las „braucht … keinen [ADDRESS]“
    und meldete folgerichtig, die zentrale Aussage enthalte einen Platzhalter.
    Ebenso wurden „alte Konfiguration“ und „nenne mir bitte den Namen“ zu
    ``[ADDRESS]``.

    Dass es nicht an MSM liegt, ist nachgesehen und nicht vermutet:
    ``[ADDRESS]`` kommt in ``services/`` an keiner Stelle vor, und weder
    ``redact_sensitive_text`` noch ``redact_freetext`` verändern diese Sätze —
    beide geben sie unverändert zurück. Der gespeicherte Antworttext trägt die
    Marke ebenfalls nicht.

    Ein Mangel, dessen ganze Substanz die Marke ist, sagt deshalb nichts über
    die Antwort. Er wird verworfen — aber sichtbar, nicht still: er steht als
    „Platzhalter-Artefakt verworfen“ im Grund und im Protokoll. Alles andere
    bliebe ein Prüfstand, der sich seine Ergebnisse zurechtlegt.
    """
    if "[" not in zitat:
        return False
    return bool(re.search(r"platzhalter|\[[A-Z_]+\]", was, re.IGNORECASE))


async def _richter(
    client: httpx.AsyncClient, fall: Fall, antwort: str
) -> tuple[bool, str]:
    """Mehrere unabhängige Urteile. Ein belegter Mangel genügt zum Durchfall."""
    hinweis = f"\nBESONDERS ACHTEN AUF:\n{fall.richterhinweis}\n" if fall.richterhinweis else ""
    auftrag = RICHTER_AUFTRAG.format(
        frage=fall.frage,
        antwort=antwort,
        massstab=TON_MASSSTAB,
        hinweis=hinweis,
    )
    gruende: list[str] = []
    verworfen: list[str] = []
    for _ in range(RICHTER_ANZAHL):
        antwort_richter = await client.post(
            f"{BENCH_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {BENCH_KEY}"},
            json={
                "model": RICHTER_MODELL,
                "messages": [{"role": "user", "content": auftrag}],
                "temperature": 1,
            },
            timeout=httpx.Timeout(120.0, connect=15.0),
        )
        antwort_richter.raise_for_status()
        roh = antwort_richter.json()["choices"][0]["message"]["content"]
        fund = re.search(r"\{.*\}", roh, re.DOTALL)
        if not fund:
            # Ein Richter, der kein JSON liefert, hat nicht geurteilt. Das als
            # "bestanden" zu werten, wäre die bequeme Richtung — also nicht.
            gruende.append("Richter lieferte kein verwertbares Urteil")
            continue
        urteil = json.loads(fund.group(0))
        if not urteil.get("bestanden", False):
            for mangel in urteil.get("maengel", []):
                zitat = str(mangel.get("zitat") or "")
                if _ist_platzhalter_artefakt(str(mangel.get("was") or ""), zitat):
                    verworfen.append(
                        f"Platzhalter-Artefakt verworfen: „{str(mangel.get('was'))[:70]}“"
                    )
                    continue
                if not _zitat_belegt(antwort, zitat):
                    # Kein Durchfall, aber auch kein Schweigen: ein Richter, der
                    # Belege erfindet, gehört ins Protokoll.
                    verworfen.append(
                        f"Beleg nicht in der Antwort, Mangel verworfen: „{zitat[:80]}“"
                    )
                    continue
                gruende.append(
                    f"Punkt {mangel.get('punkt')}: {mangel.get('was')} — „{zitat}“"
                )
    return (not gruende), " | ".join(gruende + verworfen)


# ── Der Lauf ─────────────────────────────────────────────────────────────


async def _frage_stellen(
    db: Session,
    *,
    user: User,
    provider: AiProvider,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    frage: str,
    leeren: bool,
) -> tuple[str, tuple[str, ...]]:
    """Eine Frage stellen und zurückbekommen, was der Mensch gesehen hätte.

    Bewusst der Strom und nicht die fertige Zeile in der Datenbank: der Lauf
    schreibt sein Ergebnis in einer eigenen Sitzung, die Momentaufnahme dieses
    Tests ist älter, und ein Nachlesen ergäbe für jedes Szenario den leeren
    Text. Gefragt ist ohnehin, was beim Benutzer ankam.
    """
    ai_run_broker.zuruecksetzen_fuer_tests()
    conversation = _unterhaltung(db, user, leeren=leeren)

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
    if run is None:
        raise AssertionError(f"lauf_beginnen: {fehler}")

    run_id = run.id
    ai_run_broker.eroeffnen(run_id)

    teile: list[str] = []
    werkzeuge: list[str] = []
    echt_veroeffentlichen = ai_run_broker.veroeffentlichen

    def _veroeffentlichen(rid: str, ereignis: str, daten: dict) -> None:
        if rid == run_id and ereignis == "delta":
            teile.append(str(daten.get("content") or ""))
        echt_veroeffentlichen(rid, ereignis, daten)

    echt_read_tool = ai_stream_service.execute_read_tool

    def _read_tool(*args, **kwargs):
        werkzeuge.append(str(kwargs.get("tool_name") or "?"))
        return echt_read_tool(*args, **kwargs)

    monkeypatch.setattr(ai_run_broker, "veroeffentlichen", _veroeffentlichen)
    monkeypatch.setattr(
        ai_stream_service.ai_run_broker, "veroeffentlichen", _veroeffentlichen
    )
    monkeypatch.setattr(ai_stream_service, "execute_read_tool", _read_tool)
    try:
        await asyncio.wait_for(
            ai_stream_service.segment_ausfuehren(run_id, client=client),
            timeout=BENCH_TIMEOUT,
        )
    finally:
        monkeypatch.undo()

    return "".join(teile), tuple(werkzeuge)


def _rueckfluss_lage(
    db: Session, user: User, context_chars: int | None, fall: Fall
) -> tuple[int, bool, bool]:
    """Größe des Rückflusses, ob gekürzt wurde — und ob der Sollwert noch drinsteht.

    Ohne diese Messung wäre der Kernfall eine Vermutung. Beim ersten Anlauf
    nannte das Modell die App-ID korrekt — nur lag der Block mit 13.065 Zeichen
    unter dem Deckel von 16.000, es war also nie etwas abgeschnitten und die
    Antwort bewies nichts. Eine bestandene Prüfung ohne Kürzung ist ein
    Nullbefund und kein Freispruch.

    Der dritte Wert ist der eigentlich interessante. Eine richtige Antwort heißt
    dreierlei, je nachdem was im Block stand:

    * Sollwert **noch drin** — das Modell hat abgelesen. Über die Kürzung sagt
      der Fall dann nichts, egal wie gut die Antwort ist.
    * Sollwert **weg**, kein Werkzeugaufruf — das Modell hat aus dem Gedächtnis
      geantwortet. Hier zufällig richtig; das ist Glück und keine Zusage. Genau
      davor warnt der Systemprompt („eine plausible Antwort ist schlimmer als
      keine“).
    * Sollwert **weg**, Werkzeug gerufen — das gewünschte Verhalten.

    Ohne diesen Wert lesen sich alle drei gleich.
    """
    from services.ai_context_service import (
        TOOL_RESULT_TRUNCATION_MARK,
        _recent_tool_results,
        teilbudgets,
    )

    conversation = _unterhaltung(db, user, leeren=False)
    block = _recent_tool_results(db, conversation.id, teilbudgets(context_chars))
    if not block:
        return 0, False, False
    sollwert_drin = all(
        _enthaelt(block, _sollwert(art, sch)) for art, sch in fall.muss
    ) if fall.muss else False
    return len(block), TOOL_RESULT_TRUNCATION_MARK in block, sollwert_drin


def _tabelle(urteile: list[Urteil]) -> str:
    zeilen = [
        "=" * 100,
        f"  MSM AI-QUALITAET   Modell: {BENCH_MODEL}   Richter: {RICHTER_MODELL}"
        f" x{RICHTER_ANZAHL}",
        "=" * 100,
        f"{'Fall':<24}{'Fakten':<10}{'Ton':<11}{'Rueckfluss':<16}{'Werkzeuge':<26}Grund",
        "-" * 100,
    ]
    for u in urteile:
        grund = u.fehler or u.fakten_grund or u.ton_grund or ""
        if u.ausgewichen and u.fakten_ok:
            fakten = "ehrlich"
        else:
            fakten = "ok" if u.fakten_ok else "DURCHGEF."
        if not u.rueckfluss_zeichen:
            rueck = "-"
        elif not u.gekuerzt:
            rueck = f"{u.rueckfluss_zeichen} Z ganz"
        elif u.sollwert_im_block:
            rueck = f"{u.rueckfluss_zeichen} Z gek/drin"
        else:
            rueck = f"{u.rueckfluss_zeichen} Z gek/WEG"
        zeilen.append(
            f"{u.fall:<24}{fakten:<10}{('ok' if u.ton_ok else 'DURCHGEF.'):<11}"
            f"{rueck:<16}{(', '.join(u.werkzeuge) or '-')[:24]:<26}{grund[:100]}"
        )
    bestanden = sum(1 for u in urteile if u.bestanden)
    # Ein Fall mit Vorfrage, bei dem nichts gekuerzt wurde, hat die Kuerzung
    # nicht geprüft — egal wie er ausging. Das gehört in die Zusammenfassung,
    # sonst liest sich ein Nullbefund wie ein Freispruch.
    stumpf = [u.fall for u in urteile if u.rueckfluss_zeichen and not u.gekuerzt]
    geraten = [
        u.fall for u in urteile
        if u.gekuerzt and not u.sollwert_im_block and not u.werkzeuge and u.fakten_ok
    ]
    zeilen += [
        "=" * 100,
        f"  Bestanden: {bestanden}/{len(urteile)}"
        + (f"   ACHTUNG ungekuerzt (misst die Kuerzung nicht): {', '.join(stumpf)}"
           if stumpf else "")
        + ("\n  AUS DEM GEDAECHTNIS richtig (Sollwert war weg, kein Werkzeug): "
           + ", ".join(geraten) if geraten else ""),
        "=" * 100,
    ]
    return "\n".join(zeilen)


# Die Marke sitzt an diesem einen Test und nicht an der Datei. Nur er kostet
# etwas; die Selbstprüfungen weiter unten laufen ohne Netz und ohne Schlüssel und
# gehören in jeden Suitenlauf — sie sind der Beweis, dass dieser Prüfstand nicht
# immer besteht. Eine Marke auf Modulebene hätte genau diesen Beweis abgewählt.
@pytest.mark.live
@pytest.mark.asyncio
@NUR_MIT_SCHLUESSEL
async def test_die_antworten_sind_wahr_und_klingen_menschlich(
    db: Session, owner_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der bezahlte Lauf: jede Frage einmal, jede Antwort zweifach beurteilt."""
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")

    ai_skill_service.reset_shipped_cache_for_tests()
    provider = _provider(db)
    _server_anlegen(db)

    urteile: list[Urteil] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        from services import ai_model_catalog

        ai_model_catalog.laufzeit_setzen(client)
        ai_model_catalog.vorwaermen_anstossen()

        fenster = await ai_context_window.ermitteln(client, provider)
        context_chars = fenster.zeichen if fenster.bekannt else None

        for fall in FAELLE:
            zeichen, gekuerzt, drin = 0, False, False
            try:
                if fall.vorfrage:
                    await _frage_stellen(
                        db, user=owner_user, provider=provider, client=client,
                        monkeypatch=monkeypatch, frage=fall.vorfrage, leeren=True,
                    )
                    # Zwischen Vorfrage und Frage: genau der Zustand, den die
                    # nächste Anfrage vorfinden wird.
                    zeichen, gekuerzt, drin = _rueckfluss_lage(
                        db, owner_user, context_chars, fall
                    )
                antwort, werkzeuge = await _frage_stellen(
                    db, user=owner_user, provider=provider, client=client,
                    monkeypatch=monkeypatch, frage=fall.frage,
                    leeren=not fall.vorfrage,
                )
            except Exception as exc:  # noqa: BLE001 — ein Ausfall ist ein Ergebnis
                urteile.append(Urteil(fall=fall.name, fehler=f"{type(exc).__name__}: {exc}"))
                continue

            urteil = fakten_pruefen(fall, antwort, werkzeuge)
            urteil.rueckfluss_zeichen = zeichen
            urteil.gekuerzt = gekuerzt
            urteil.sollwert_im_block = drin
            urteil.floskeln = floskeln_finden(antwort)
            if urteil.floskeln:
                urteil.ton_ok = False
                urteil.ton_grund = f"Floskeln: {list(urteil.floskeln)}"
            else:
                urteil.ton_ok, urteil.ton_grund = await _richter(client, fall, antwort)
            urteile.append(urteil)

    print("\n" + _tabelle(urteile))

    PROTOKOLL_ORDNER.mkdir(parents=True, exist_ok=True)
    stempel = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pfad = PROTOKOLL_ORDNER / f"{stempel}-qualitaet.json"
    pfad.write_text(
        json.dumps(
            {
                "modell": BENCH_MODEL,
                "richter": RICHTER_MODELL,
                "richter_anzahl": RICHTER_ANZAHL,
                "faelle": [u.als_dict() for u in urteile],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  Ergebnis: {pfad}")

    durchgefallen = [u for u in urteile if not u.bestanden]
    assert not durchgefallen, "\n".join(
        f"{u.fall}: {u.fehler or u.fakten_grund or u.ton_grund}" for u in durchgefallen
    )


# ── Selbstprüfung ────────────────────────────────────────────────────────
#
# Ohne Netz, ohne Schlüssel, in der normalen Suite. Ein Prüfstand, der immer
# besteht, ist wertlos — hier steht der Beweis, dass er das nicht tut.


_APPID_FALL = FAELLE[0]
_SERVER_FALL = FAELLE[2]


def test_eine_erfundene_app_id_fällt_durch() -> None:
    echte = _sollwert("appid", "seven_days_to_die")
    falsche = str(int(echte) + 1)
    urteil = fakten_pruefen(_APPID_FALL, f"Die App-ID von 7 Days to Die ist {falsche}.", ())
    assert not urteil.fakten_ok
    assert "ohne Deckung" in urteil.fakten_grund


def test_eine_um_eine_ziffer_verlängerte_app_id_besteht_nicht() -> None:
    """Die Substringfalle: ``2944200`` darf ``294420`` nicht erfüllen."""
    echte = _sollwert("appid", "seven_days_to_die")
    urteil = fakten_pruefen(_APPID_FALL, f"Die App-ID lautet {echte}0.", ())
    assert not urteil.fakten_ok


def test_die_richtige_app_id_besteht() -> None:
    echte = _sollwert("appid", "seven_days_to_die")
    urteil = fakten_pruefen(_APPID_FALL, f"7 Days to Die hat die App-ID {echte}.", ())
    assert urteil.fakten_ok and not urteil.ausgewichen


def test_ein_ehrliches_eingeständnis_besteht() -> None:
    urteil = fakten_pruefen(
        _APPID_FALL,
        "Die App-ID von 7 Days to Die habe ich nicht mehr vollständig vorliegen — "
        "ich schaue nochmal in den Blueprint.",
        (),
    )
    assert urteil.fakten_ok and urteil.ausgewichen


def test_wer_nachgesehen_hat_hat_nicht_geraten() -> None:
    urteil = fakten_pruefen(
        _APPID_FALL, "Ich sehe im Blueprint von 7 Days to Die nach, welche App-ID dort steht.",
        ("read_blueprint",),
    )
    assert urteil.fakten_ok and urteil.ausgewichen


def test_bloßes_ausweichen_ist_kein_ehrlicher_ausweg() -> None:
    """„Dazu kann ich nichts sagen“ darf nicht als Ehrlichkeit durchgehen."""
    urteil = fakten_pruefen(_APPID_FALL, "Dazu kann ich leider nichts sagen.", ())
    assert not urteil.fakten_ok
    assert "am Thema vorbei" in urteil.fakten_grund


def test_ein_erfundener_server_fällt_durch() -> None:
    urteil = fakten_pruefen(
        _SERVER_FALL, "Dein Server Atlantis ist online und läuft stabil.", ()
    )
    assert not urteil.fakten_ok
    assert "erfunden" in urteil.fakten_grund


def test_die_richtige_auskunft_über_atlantis_besteht() -> None:
    urteil = fakten_pruefen(
        _SERVER_FALL,
        "Einen Server namens Atlantis hast du nicht — angelegt sind Bench 1, "
        "Bench 2 und Bench 3. Meinst du einen davon?",
        ("list_my_servers",),
    )
    assert urteil.fakten_ok


def test_eine_verfloskelte_antwort_fällt_am_ton_durch() -> None:
    treffer = floskeln_finden(
        "Gerne! Als KI-Assistent helfe ich dir dabei. Zusammenfassend lässt sich "
        "sagen, dass alles in Ordnung ist. Ich hoffe, das hilft!"
    )
    assert len(treffer) >= 3


def test_eine_knappe_direkte_antwort_hat_keine_floskeln() -> None:
    assert floskeln_finden(
        "Bench 3 ist gestoppt, Bench 1 und Bench 2 laufen. Soll ich Bench 3 starten?"
    ) == ()


def test_gerne_mitten_im_satz_ist_keine_floskel() -> None:
    """Die Kehrseite: eine Liste, die richtige Antworten fängt, ist schädlich."""
    assert floskeln_finden(
        "Das mache ich gerne, aber erst nach deiner Bestätigung — der Server wird "
        "dabei neu gestartet."
    ) == ()


def test_die_sollwerte_kommen_aus_der_quelle_und_nicht_aus_einer_abschrift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ändert sich der Blueprint, muss die Prüfung mitgehen.

    Sonst prüfte der Test seine eigene Kopie: der Blueprint könnte still eine
    andere App-ID bekommen, die KI sie korrekt nennen und der Test trotzdem
    durchfallen — oder umgekehrt.
    """
    echte = _sollwert("appid", "seven_days_to_die")
    quelle = json.loads((BLUEPRINT_ORDNER / "seven_days_to_die.blueprint.json").read_text(
        encoding="utf-8"
    ))
    quelle["source"]["steam"]["appId"] = "999999"
    (tmp_path / "seven_days_to_die.blueprint.json").write_text(
        json.dumps(quelle), encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "BLUEPRINT_ORDNER", tmp_path)
    assert _sollwert("appid", "seven_days_to_die") == "999999" != echte


def test_der_ton_maßstab_schützt_die_ansage_des_systemprompts() -> None:
    """Der Systemprompt verlangt, dass die KI ansagt, was sie nachsieht.

    Ein Maßstab, der das als Geschwafel wertet, misst gegen das Produkt. Der
    Satz muss also ausdrücklich im Maßstab stehen — hier steht die Zusage.
    """
    assert "Ich schau mir erst den Zustand deiner Server an." in TON_MASSSTAB
    assert "KEIN Mangel" in TON_MASSSTAB


def test_der_richter_darf_nicht_über_fakten_urteilen() -> None:
    """Am ersten scharfen Lauf gelernt, und der Fall ist lehrreich.

    Die KI antwortete korrekt „294420“ — die App-ID, die so im Blueprint steht.
    Der Richter ließ sie durchfallen: „die korrekte ID ist 251570“. Das ist die
    Steam-Kennung des *Spiels*, nicht die des Dedicated Servers, und sie stammt
    aus seinem Gedächtnis statt aus der Quelle. Ein Richter, der Fakten aus dem
    Gedächtnis prüft, überstimmt die einzige Instanz, die sie wirklich kennt.

    Fakten prüft ``fakten_pruefen`` gegen die Blueprint-Dateien. Der Richter
    beurteilt den Ton. Diese Grenze steht im Maßstab, und dieser Test hält sie.
    """
    assert "DU BEURTEILST NICHT, OB DIE ANTWORT SACHLICH RICHTIG IST" in TON_MASSSTAB
    assert "auch dann, wenn du sie anders in Erinnerung hast" in TON_MASSSTAB


def test_die_fragen_widersprechen_ihren_vorfragen_nicht() -> None:
    """Eine Zahl in der Frage muss zur Vorfrage passen.

    Auch das ist am scharfen Lauf aufgefallen: die Vorfrage wurde von drei auf
    fünf Spiele erweitert, die Nachfrage sagte weiter „welches der drei“. Der
    Richter hat den Widerspruch zu Recht als Mangel gemeldet — nur lag er bei
    mir und nicht bei der KI. Ein Prüffall, der sich selbst widerspricht, misst
    die KI nicht, sondern den eigenen Flüchtigkeitsfehler.
    """
    zahlwoerter = ("zwei", "drei", "vier", "fünf", "sechs")
    for fall in FAELLE:
        if not fall.vorfrage:
            continue
        in_frage = {w for w in zahlwoerter if w in fall.frage.casefold()}
        in_vorfrage = {w for w in zahlwoerter if w in fall.vorfrage.casefold()}
        assert not (in_frage - in_vorfrage), (
            f"{fall.name}: die Frage spricht von {in_frage - in_vorfrage}, "
            f"die Vorfrage von {in_vorfrage or 'keiner Zahl'}"
        )


def test_ein_erfundener_beleg_zählt_nicht() -> None:
    """Der Richter muss belegen, und der Beleg wird nachgeschlagen."""
    antwort = "Einen Server namens Atlantis hast du nicht — angelegt sind Bench 1 bis 3."
    assert not _zitat_belegt(antwort, "**Anderer Server** — [ADDRESS]*nicht sichtbar**")
    assert not _zitat_belegt(antwort, "")
    assert _zitat_belegt(antwort, "angelegt sind Bench 1 bis 3")


def test_nur_der_platzhalter_selbst_wird_als_artefakt_verworfen() -> None:
    """Die Grenze muss eng sein, sonst verwirft sie echte Mängel mit.

    Verworfen wird ein Mangel nur, wenn er ÜBER den Platzhalter klagt. Ein
    inhaltlicher Mangel, dessen Zitat nebenbei eine Marke enthält, bleibt
    stehen — sonst wäre die Marke ein Freifahrtschein.
    """
    assert _ist_platzhalter_artefakt(
        "Die zentrale Aussage enthält einen Platzhalter", "keinen [ADDRESS]."
    )
    # Inhaltlicher Mangel, Marke nur nebenbei im Zitat -> bleibt stehen.
    assert not _ist_platzhalter_artefakt(
        "Die Antwort belehrt den Benutzer", "du hättest das [ADDRESS] sichern müssen"
    )
    # Ohne Marke im Zitat ist es ohnehin kein Artefakt.
    assert not _ist_platzhalter_artefakt("enthält einen Platzhalter", "ganz normaler Text")


def test_ein_platzhalter_im_zitat_macht_es_nicht_wertlos() -> None:
    """Sonst würde jeder verstümmelte Beleg einen echten Mangel verschwinden lassen.

    Gemessen: aus „ob die alte Konfiguration teilweise rekonstruierbar ist“ kam
    „ob die [ADDRESS] teilweise rekonstruierbar ist“ zurück. Nicht von uns —
    ``[ADDRESS]`` steht in ``services/`` nirgends, und unsere Schwärzung lässt
    den Satz unangetastet.
    """
    antwort = (
        "Deshalb kann ich die Datei noch nicht prüfen oder sagen, ob die alte "
        "Konfiguration teilweise rekonstruierbar ist."
    )
    assert _zitat_belegt(antwort, "ob die [ADDRESS] teilweise rekonstruierbar ist")
    # Die Reihenfolge muss stimmen — sonst belegt jedes Wortpaar alles.
    assert not _zitat_belegt(antwort, "rekonstruierbar [ADDRESS] Deshalb kann ich")
    # Und ein Zitat, das fast nur aus Platzhaltern besteht, belegt nichts.
    assert not _zitat_belegt(antwort, "[ADDRESS]")
    assert not _zitat_belegt(antwort, "die [ADDRESS]")


def test_ein_anders_umbrochenes_zitat_gilt_trotzdem() -> None:
    """Die Kehrseite: der Richter soll zitieren, nicht abtippen.

    Ohne Leerraum-Toleranz fiele jeder echte Beleg durch, der eine Zeile anders
    umbricht — und dann wäre die Strengeprüfung eine Freikarte statt einer
    Schranke.
    """
    antwort = "Bench 3 ist gestoppt,\n  Bench 1 und Bench 2 laufen."
    assert _zitat_belegt(antwort, "Bench 3 ist gestoppt, Bench 1 und Bench 2 laufen.")


def test_jeder_fall_trägt_seine_wahrheit() -> None:
    """Kein Fall ohne Prüfbares — sonst misst er nichts und kostet trotzdem."""
    for fall in FAELLE:
        assert fall.muss or fall.verboten or fall.nur_ton, fall.name
        assert fall.bezug, f"{fall.name}: ohne Bezug wäre Ausweichen unauffällig"
        for art, schluessel in fall.muss:
            assert _sollwert(art, schluessel), f"{fall.name}: Sollwert leer"


def test_die_welt_stimmt_mit_den_fällen_überein() -> None:
    """Die Behauptung hinter ``rcon_nach_kuerzung``, an der Quelle geprüft."""
    assert not _hat_rcon("seven_days_to_die")
    assert _hat_rcon("conan_exiles_ue5")
    assert _hat_rcon("ark_survival_evolved")
