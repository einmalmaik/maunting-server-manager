# -*- coding: utf-8 -*-
"""Typen, Datenklassen, Fehlerklassen und Konstanten fuer das KI-Streaming."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from models import AiActionProposal, AiMessage, AiProvider, AiRun, User
from services.ai_proposal_service import AufgabenKontext, GuardianKontext


# Wieviel Ergebnistext eine Runde hoechstens erzeugen darf.
#
# Die Grenze war frueher eine feste Anzahl Aufrufe. Das war das falsche Mass:
# zwanzig Statusabfragen sind zusammen kleiner als ein einziger Logauszug, und
# `read_server_logs` liefert bis zu 24.000 Zeichen. Eine Zahl behandelt beide
# gleich und wird dadurch entweder zu eng (die KI kann nicht durchfragen) oder
# zu weit (ein halbes Kontextfenster in einer Runde).
#
# Gezaehlt wird deshalb das, was tatsaechlich knapp ist. Billige Aufrufe laufen
# alle; sobald das Budget aufgebraucht ist, werden die restlichen vertagt statt
# abgewiesen. Rund 48.000 Zeichen sind grob 12.000 Tokens — Platz fuer etwa
# dreissig Statusabfragen oder zwei volle Logauszuege.
MAX_TOOL_RESULT_CHARS_PER_ROUND = 48_000
# Absolute Reissleine gegen ein durchgedrehtes Modell: so viele Werkzeugaufrufe
# darf **eine** Runde höchstens enthalten. Hier wird nichts vertagt und nichts
# begründet abgelehnt — die Sequenz bricht hart ab, weil eine Runde mit mehr
# Aufrufen kein Arbeitsplan mehr ist, sondern ein Fehler.
#
# Hier standen zweiunddreissig, gemessen an einer Frage. Ein Auftrag ist
# breiter: "sieh dir alle Server an" ist bei einem Dutzend Anlagen schon eine
# Runde mit einem Dutzend Statusabfragen, und danach kommen die Logs. Was
# tatsächlich knapp ist, deckelt ohnehin die Zeile darüber — Zeichen, nicht
# Aufrufe. Diese Zahl muss nur gross genug sein, um eine breite Bestandsaufnahme
# durchzulassen, und klein genug, um eine Aufzählung ohne Ende zu stoppen.
MAX_TOOL_CALLS = 64
# Leserunden **je Lauf**, nicht je Nachricht.
#
# Hier standen erst vier, dann sechzehn. Vier war die Zahl aus der Zeit, in der
# ein Zug eine Frage beantwortete: lesen, lesen, antworten. Sechzehn trug eine
# Diagnose, aber keinen Auftrag wie "richte den Server ein, stell das ein,
# starte ihn und sag Bescheid" — die KI kam bis zur Hälfte und musste aufhören,
# obwohl sie wusste, was noch fehlte. Genau die Beschwerde: *"die muss das
# wirklich komplett bis zum Ende machen, Aufgaben zu Ende bringen, Ende zu
# Ende."*
#
# Achtundvierzig ist der Punkt, an dem eine Kette nicht mehr länger wird,
# sondern im Kreis läuft — und dagegen ist die Signaturzählung weiter unten das
# passende Mittel, nicht diese Grenze. Sie bricht auch nichts ab: sie nimmt die
# Werkzeuge weg, und das Modell antwortet aus dem, was es hat.
MAX_TOOL_ROUNDS = 48
# Schreibrunden je Lauf. Zwei reichten für "pass die Config an und starte
# danach", acht für eine Einrichtung aus Anlegen, Konfigurieren, Starten und
# Melden. Was sie nicht trugen, ist die Wiederholung: eine Einrichtung, die beim
# ersten Versuch schiefgeht, korrigiert wird und neu startet, braucht dieselben
# Schritte ein zweites Mal. Vierundzwanzig lassen einem Auftrag diesen zweiten
# Anlauf und bleiben weit unter dem, was ein durchgedrehtes Modell bräuchte, um
# Schaden anzurichten — jede einzelne Aktion durchläuft weiterhin die
# Rechteprüfung und, wo nötig, die Bestätigung eines Menschen. An der Grenze
# endet die Werkzeugnutzung, nicht der Lauf.
MAX_WRITE_ROUNDS = 24


# Wie lange **ein einzelner** Werkzeugaufruf antworten darf, in Sekunden.
#
# Ein Rückhalt, keine Regel. Wer unterwegs ist, meldet sich selbst: der
# `node_client` wartet 30 s, die Websuche 15 s. Diese Grenze liegt bewusst über
# beiden — sie soll nie vor der zuständigen Stelle greifen, sondern nur dort,
# wo es keine gibt: eine SSH-Sitzung ohne Gegenstelle, ein Wiederholungslauf,
# eine Datenbank unter Sperre.
#
# Ohne sie hält ein einziger hängender Aufruf die **ganze** Antwort fest, und
# zwar bis `MAX_STREAM_SECONDS` (300 s) im Adapter. Gemessen wurde das nicht —
# im Benchmark liegt die gesamte Werkzeugzeit bei 0,00–0,10 s je Lauf. Es ist
# der Ausreisser, gegen den hier nichts stand.
#
# **Was sie nicht kann:** einen Thread abbrechen. `asyncio.wait_for` bricht das
# Warten ab, nicht die Arbeit. Der Aufruf läuft im Threadpool weiter und darf
# zu Ende committen — bei `remember`, `learn_skill` und `forget_memory` ist das
# eine echte Schreibung, von der das Modell nichts mehr erfährt. Genau deshalb
# sagt die Meldung an das Modell *nicht* "fehlgeschlagen", sondern "nicht
# abgewartet, prüfe nach".
WERKZEUG_ZEITGRENZE = 60.0


# Wie oft derselbe Werkzeugaufruf mit **denselben** Argumenten laufen darf,
# gezählt über Runden hinweg. Ein Modell, das die gleiche Auskunft zum fünften
# Mal holt, bekommt keine neue Antwort — es hängt. Der Aufruf wird dann nicht
# ausgeführt, sondern begründet abgelehnt: eine Grenze, die erklärt, statt
# einer, die abbricht.
MAX_GLEICHE_AUFRUFE = 4
# Für drei Werkzeuge ist die Wiederholung kein Hängen, sondern Warten.
#
# Ihr Ergebnis hängt an der Zeit und nicht an den Argumenten: zwischen
# "gestartet" und "läuft" liegt bei einem Spielserver eine Minute oder mehr, und
# wer in dieser Zeit nachsieht, stellt dieselbe Frage mit denselben Argumenten —
# bekommt aber jedes Mal eine andere Antwort. `read_server_status` sagt, ob der
# Container schon oben ist, `read_server_logs` zeigt, wie weit das Hochfahren
# gekommen ist, `check_server_reachability` beantwortet die Frage, auf die es am
# Ende ankommt. Darin unterscheiden sich genau diese drei von allem anderen:
# `read_config` ein zweites Mal zu lesen bringt nichts Neues.
#
# Acht Runden reichen, um ein Hochfahren zu begleiten, und sind wenig genug,
# dass ein wirklich festgefahrenes Modell nicht den ganzen Lauf damit verbringt.
# Danach gilt dieselbe begründete Ablehnung wie oben.
MAX_GLEICHE_POLLING_AUFRUFE = 8
POLLING_WERKZEUGE = {"read_server_status", "read_server_logs", "check_server_reachability"}


_FREITEXT_WERKZEUGE = frozenset({
    "read_server_logs",
    "read_guardian_incidents",
    "read_config",
    "search_server_files",
})


#: Felder, die der Rechner **nur** vom Panel entgegennimmt. Sie stehen in
#: keinem Werkzeugschema, und was das Modell unter diesen Namen mitschickt,
#: wird verworfen, bevor es den Auftrag erreicht.
GESETZTE_FELDER = ("autonom", "systembereich")


#: Das Feld, unter dem der Rechner ein Bildschirmfoto meldet (`bildschirm.rs`).
BILDFELD = "bild_jpeg_base64"
#: Was an der Stelle eines aelteren Bildes stehenbleibt.
BILD_VERBRAUCHT = "[aelteres Bildschirmfoto — nicht mehr aktuell, entfernt]"
#: Woran eine Desktop-Meldung im Verlauf zu erkennen ist.
#:
#: Gebraucht, damit `_alte_bilder_entwerten` **nur** Bildschirmfotos wegwirft
#: und nicht jedes Bild im Verlauf. Im selben `provider_messages` stehen naemlich
#: auch die Bildanhaenge des Benutzers (`ai_context_service` baut sie aus der
#: Datenbank ein) — die gehoeren ihm, sie sind Teil seiner Frage und veralten
#: nicht. Eine Fassung, die schlicht jedes `image_url` ersetzt, haette dem
#: Benutzer beim ersten Bildschirmfoto sein eigenes hochgeladenes Bild aus der
#: Unterhaltung genommen.
MELDUNGSMARKE = (
    "Meldung des Panels (nicht vom Benutzer geschrieben): Der Rechner "
)


#: Die technische Wahrheit gehoert dorthin, wo man sie brauchen kann: als
#: Markierung „sieht Bilder" neben der Modellwahl in den Einstellungen.
KEIN_BLICK_GRUND = (
    "Der Aufruf lief nicht: du kannst im Moment keine Bilder ansehen. Sag das "
    "dem Benutzer in eigenen Worten und in der ersten Person — als eine "
    "Fähigkeit, die dir gerade fehlt. Sprich dabei nicht über Modelle, "
    "Anbieter oder Einstellungen. Frag ihn stattdessen, was auf dem Bildschirm "
    "steht, oder hilf ihm auf einem anderen Weg weiter."
)


_ANLAUF_SEMAPHOR: asyncio.Semaphore | None = None
_ANLAUF_SCHLEIFE: asyncio.AbstractEventLoop | None = None
_ANLAUF_SCHRANKE: asyncio.Semaphore | None = None
_ANLAUF_SCHLOESSER: dict[str, asyncio.Lock] = {}
_ANLAUF_WARTENDE: dict[str, int] = {}


class GuardianRahmenUnlesbar(RuntimeError):
    """Der Laufzustand nennt eine Guardian-Heilung, aber nicht mehr, welche.

    Eigene Klasse, damit der Aufrufer sie von einem gewoehnlichen Chatlauf
    unterscheiden kann. Sie beendet den Lauf, statt ihn ohne Verschaerfungen
    weiterlaufen zu lassen.
    """


@dataclass(frozen=True)
class _Vorbereitung:
    """Alles, was ein Segment braucht — in einer kurzen Transaktion geholt."""

    run_id: str
    user_id: int
    conversation_id: str
    provider: AiProvider
    api_key: str | None
    message_id: str
    usage_event_id: int
    request_id: str
    reasoning: bool
    reasoning_effort: str | None
    token_price_micro_usd_per_million: int | None
    zustand: dict
    # Welche Werkzeuge diesem Benutzer angeboten werden. Hier geholt und nicht
    # im Segment: die Frage braucht eine Datenbanksitzung, und waehrend der
    # Anbieter streamt, steht keine offen. Je Segment neu — eine Fortsetzung
    # nach einer Bestaetigung Stunden spaeter soll den Rechtestand von *jetzt*
    # sehen, nicht den von damals.
    angebotene_werkzeuge: frozenset[str]


@dataclass(frozen=True)
class _Anlauf:
    """Alles, was ein Segment nach dem Anlauf in der Hand haelt."""

    vorbereitung: "_Vorbereitung"
    client: httpx.AsyncClient
    zustand: dict
    provider_messages: list[dict]
    conversation_id: str
    user_id: int
    message_id: str
    guardian: "GuardianKontext | None"
    aufgabe: "AufgabenKontext | None"
    rolle: str
    herkunft: str
    worker: dict | None
    unbeaufsichtigt: bool


@dataclass(frozen=True)
class _FragenErgebnis:
    """Was aus einem `ask_user`-Aufruf wurde.

    ``signal`` ist "frage" (Segment endet, Mensch ist dran) oder "weiter"
    (Runde verworfen — abgewiesen oder Formfehler — und die naechste beginnt).
    Die Flags gelten nur bei "weiter" und sind dann die Werte, die der
    Orchestrator uebernimmt.
    """

    signal: str
    frage: dict | None = None
    budget_erschoepft: bool = False
    letzte_runde: bool = False


@dataclass(frozen=True)
class _SchreibrundenErgebnis:
    """Wie eine reine Schreibrunde ausging.

    ``abgeloest`` beendet das Segment ohne jede weitere Wirkung; alle anderen
    Kombinationen heissen "naechste Runde beginnt", mit genau den Flags, die
    der jeweilige Ausgang im alten Fliesstext setzte. ``denknaht`` traegt den
    bestellten Absatz fuer den ersten Gedanken der Folgerunde zurueck.
    """

    denknaht: str
    abgeloest: bool = False
    geparkt: bool = False
    budget_erschoepft: bool = False
    letzte_runde: bool = False


@dataclass(frozen=True)
class _WartenErgebnis:
    """Was aus einem `wait_until`-Aufruf wurde.

    ``signal`` ist "parken" (Segment endet, der Takt weckt zu ``wake_at``)
    oder "weiter" (Formfehler — Runde beantwortet und gezaehlt, die naechste
    beginnt). Die Flags gelten nur bei "weiter", wie bei `_FragenErgebnis`.
    """

    signal: str
    wake_at: datetime | None = None
    budget_erschoepft: bool = False
    letzte_runde: bool = False

