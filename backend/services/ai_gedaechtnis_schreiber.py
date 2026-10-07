"""Der Gedächtnisschreiber: Singra merkt sich, was im Gespräch gesagt wurde.

Bis Gedächtnis v2, Stufe 2 schrieb das Chatmodell selbst — über die Werkzeuge
``remember`` und ``forget_memory``, mitten in der Antwort. Jeder Aufruf kostete
eine ganze Runde beim Anbieter (rund 3,3 s), und ob etwas hängen blieb, hing
davon ab, ob das Modell in diesem Moment daran dachte. Betreiberentscheid vom
06.10.2026, Vorbild ChatGPT Dreaming: ein Hintergrundprozess liest das Gespräch
und schreibt das Gedächtnis.

**Wann.** Fünf Minuten, nachdem das Gespräch zur Ruhe gekommen ist — jeder Lauf
schiebt die Frist nach hinten (`vormerken`) —, spätestens aber dreißig Minuten
nach der ältesten ungelesenen Nachricht, sonst käme ein Dauergespräch nie an
die Reihe. Sagt der Mensch „merk dir“ oder „vergiss“, sofort nach der Antwort
(`STICHWORT`). Ein Sprachgespräch kommt an seinem Ende als Mitschrift herein
(`aus_mitschrift`).

**Was er liest.** Die Nachrichten des Menschen, Singras Antworten und die
Berichte der Worker — keine Werkzeugergebnisse. Was ein Server ausgegeben hat,
ist eine Messung von damals; was Singra daraus schließt, steht in ihrer
Antwort. Eine Logzeile kann damit strukturell kein Wissen einschleusen.

**Was er darf.** Dieselben Grenzen wie jeder Schreibweg (`ai_memory_service`):
Rechte des Bereichs, keine Zugangsdaten, das Kontingent der Rolle — 100, eine
eingestellte Zahl, unbegrenzt, 0 heißt gesperrt. Persönliches nur mit
eingeschaltetem Gedächtnis und nur aus dem, was danach gesagt wurde
(`_vorbereiten`); Server- und Teamwissen hängen an den Rechten, nicht
am Schalter (Datenschutzerklärung: Betriebswissen ohne Personenbezug). Was der
Mensch selbst geschrieben hat, ändert oder vergisst er nur, wenn der Mensch es
im Gespräch sagt — die Nachricht steht als Beleg an der Änderung.

**Was er kostet.** Einen Modellaufruf je Durchgang, gebucht beim Benutzer mit
``zweck='gedaechtnis'``: in Tokens und Kosten wie jede Anfrage, aber nicht als
Anfrage pro Minute und nicht als gleichzeitiger Vorgang. Ohne beschreibbaren
Bereich gibt es keinen Aufruf.

Inhalte stehen in keinem Log: gezählt wird, was geschah, nie was darin stand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, or_, select, tuple_, update
from sqlalchemy.orm import Session, defer

from database import SessionLocal
from models import AiConversation, AiMemoryEntry, AiMessage, AiProvider, AiRun, Server, Team, User
from models.dis_text import vorab_entschluesselt
from services import ai_lage, ai_limit_service, ai_memory_service, ai_reasoning, permission_service
from services.ai_provider_service import (
    anbieter_ohne_auswahl,
    fuer_chat,
    fuer_gedaechtnis,
    resolve_api_key,
)
from services.ai_redaction import redact_sensitive_text
from services.dis_client import DisDecryptionError, DisSidecarError
from services.ai_usage_service import (
    ZWECK_GEDAECHTNIS,
    AiQuotaExceeded,
    AiUsageConflict,
    reserve_ai_usage,
    reservierung_abrechnen,
)
from services.openai_compatible_adapter import (
    AiProviderRequestError,
    StreamUsage,
    stream_chat_completion,
)


logger = logging.getLogger(__name__)

#: Wie lange ein Gespräch ruhen muss, bevor er liest.
RUHE = timedelta(minutes=5)
#: Spätestens so lange nach der ältesten ungelesenen Nachricht liest er.
SPAETESTENS = timedelta(minutes=30)
#: So lange hält ein Durchgang sein Gespräch. Stirbt der Prozess mittendrin,
#: ist es danach wieder fällig.
SPERRE = timedelta(minutes=10)
#: Ein Ausschnitt; was darüber hinausgeht, kommt im nächsten Durchgang.
MAX_FENSTER_ZEICHEN = 24_000
#: Was vor der Marke steht, kommt bis zu dieser Menge als Zusammenhang mit
#: („ja, genau das“ ist ohne die Frage davor nicht zu verstehen).
MAX_VORLAUF = 4
MAX_VORLAUF_ZEICHEN = 3_000
#: Je Nachricht. Lange Antworten Singras sind meist Auswertungen; ihr Ergebnis
#: steht vorne.
MAX_NACHRICHT_ZEICHEN = {"Benutzer": 4_000, "Singra": 2_000, "Bericht": 2_000}
#: Wieviele Bestandseinträge je Bereich er zu sehen bekommt.
MAX_BESTAND_JE_BEREICH = 40
MAX_SERVER = 10
MAX_TEAMS = 10
#: Mehr Änderungen aus einem Durchgang nimmt er nicht an.
MAX_AENDERUNGEN = 30
#: Ab hier gilt eine Neuanlage als Doppel eines Bestandseintrags. Hoch
#: angesetzt: ein Doppel führt der nächste Durchgang zusammen, ein
#: verworfener neuer Fakt wäre verloren.
FAST_GLEICH_AB = 0.95
#: Geschätzte Ausgabe für die Reservierung.
AUSGABE_SCHAETZUNG = 2_000
MAX_FEHLVERSUCHE = 5
RUECKSTELLUNG_BASIS = timedelta(minutes=5)
RUECKSTELLUNG_MAX = timedelta(hours=6)
#: Kein Kontingent oder kein Anbieter: das ändert sich nicht in Minuten.
SPAETER = timedelta(hours=1)
#: Wieviele Durchgänge gleichzeitig laufen dürfen.
MAX_GLEICHZEITIG = 4
#: Wie lange ein eigener HTTP-Client auf die Antwort wartet.
LESEFRIST_SEKUNDEN = 120.0

#: „Merk dir“, „vergiss“ und ihre Verwandten, deutsch und englisch. Ein
#: Fehlgriff kostet nur einen früheren Durchgang.
STICHWORT = re.compile(
    r"\b("
    r"merk(?:e|')?\s+(?:dir|euch|ihr)|merken\s+sie\s+sich|"
    r"notier(?:e)?\s+(?:dir|euch)|behalte?\s+(?:das|dir|im\s+kopf)|"
    r"vergiss|vergesst|vergessen\s+sie|"
    r"remember\s+(?:that|this|my|me|it)|keep\s+in\s+mind|"
    r"forget\s+(?:that|this|about|my|it)"
    r")\b",
    re.IGNORECASE,
)

#: Die Wochentage für die Zeitangaben im Ausschnitt.
WOCHENTAGE_KURZ = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

WERKZEUG_NAME = "gedaechtnis_schreiben"

#: Das eine Werkzeug, das dieser Aufruf anbietet und erzwingt — ein Formular,
#: kein Werkzeug: ausgeführt wird es nie, gelesen werden die Argumente.
WERKZEUG: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WERKZEUG_NAME,
        "description": (
            "Trage ein, was sich am Gedächtnis ändert. Eine leere Liste, wenn "
            "der Ausschnitt nichts Dauerhaftes enthält."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "aenderungen": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "aktion": {
                                "type": "string",
                                "enum": ["neu", "aendern", "zusammenfuehren", "vergessen"],
                            },
                            "eintrag": {
                                "type": "string",
                                "description": (
                                    "E-Nummer aus dem Bestand: der geänderte, das Ziel "
                                    "einer Zusammenführung oder der vergessene Eintrag."
                                ),
                            },
                            "aufgenommen": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Nur bei zusammenfuehren: die E-Nummern, die im "
                                    "Ziel aufgehen."
                                ),
                            },
                            "bereich": {
                                "type": "string",
                                "description": "Nur bei neu: die B-Nummer des Bereichs.",
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Ein bis fünf Sätze, ohne das Gespräch verständlich. "
                                    "Bei aendern und zusammenfuehren der ganze neue Stand."
                                ),
                            },
                            "titel": {"type": "string", "description": "Zwei bis sechs Wörter."},
                            "thema": {
                                "type": "string",
                                "description": "Ein oder zwei Wörter; vorhandene Themen bevorzugen.",
                            },
                            "art": {
                                "type": "string",
                                "enum": list(ai_memory_service.ARTEN),
                            },
                            "wichtigkeit": {"type": "integer", "minimum": 1, "maximum": 5},
                            "beleg": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "N-Nummern der Nachrichten, auf die sich das stützt.",
                            },
                        },
                        "required": ["aktion", "beleg"],
                    },
                },
            },
            "required": ["aenderungen"],
        },
    },
}

SYSTEMPROMPT = """Du führst das Gedächtnis von Singra, der KI eines Gameserver-Panels. Du liest einen Ausschnitt aus einem Gespräch zwischen einem Benutzer und Singra und trägst ein, was Singra sich dauerhaft merken soll. Was du einträgst, steht ihr in späteren Gesprächen zur Verfügung – auch in einem Jahr, ohne dieses Gespräch.

Eine gute Erinnerung
- sind ein bis fünf Sätze, die ohne das Gespräch verständlich sind. Über den Menschen in der dritten Person: „Der Benutzer spielt am liebsten abends nach 20 Uhr.“ Über einen Server mit seinem Namen: „Auf dem Server Werkstatt startet die Mod Expansion nur, wenn CF vor ihr geladen wird.“
- gilt über dieses Gespräch hinaus: Vorlieben, Gewohnheiten, Lebensumstände, Menschen und Beziehungen, Pläne, Anweisungen an Singra, Eigenheiten seiner Server und was sich dort bewährt hat.
- kann auch sagen, wie der Mensch redet – knapp oder ausführlich, direkt oder umsichtig, mit Fachbegriffen oder ohne –, wenn es mehr ist als die Laune eines Abends: „Der Benutzer schreibt knapp und ohne Höflichkeitsfloskeln.“ Das ist persönlich.
- nennt Zeit absolut. „Im Juli“ in einer Nachricht vom Juni 2026 heißt „im Juli 2026“, „morgen“ wird ein Datum.
- lässt einen Plan einen Plan sein. Aus „Ich fahre im Juli nach Singapur“ wird „Der Benutzer plant für Juli 2026 eine Reise nach Singapur.“ Ob er gefahren ist, steht erst fest, wenn er es erzählt.

Was nur für den Moment zählt, gehört nicht hinein: die Aufgabe, an der die beiden gerade sitzen, Zwischenstände, Höflichkeiten, was Singra eben nachgeschlagen hat. Was ein Server oder ein Werkzeug ausgegeben hat, ist eine Messung von damals. Ein Schluss, den Singra daraus zieht und der über den Moment hinaus gilt, kann Serverwissen sein. Zugangsdaten, Passwörter, Schlüssel und Token stehen nie im Gedächtnis.

Der Bestand zeigt, was Singra schon weiß.
- Gibt es zur Sache schon einen Eintrag, änderst du ihn (aendern), statt einen zweiten anzulegen. Der neue Text ersetzt den alten ganz, der alte bleibt im Verlauf. Schreib also den ganzen gültigen Stand, nicht nur das Neue.
- Stehen zwei oder mehr Einträge zur selben Sache da, führst du sie zusammen (zusammenfuehren): ein Text mit allem, was davon noch gilt.
- Einträge „vom Benutzer“ hat der Mensch selbst geschrieben. Sie ändern sich, wenn er im Gespräch etwas anderes sagt – mit dieser Nachricht als Beleg.
- vergessen ist für den Wunsch des Menschen („vergiss das“, „streich das, stimmt nicht mehr“). Was sich nur geändert hat, wird geändert.

Bereiche: Persönliches über den Menschen gehört in seinen persönlichen Bereich. Server- und Teamwissen lesen alle, die den Server betreuen oder im Team sind; dort steht Betriebswissen, nichts über einzelne Menschen. Die Grenze verläuft nach dem Inhalt, nicht danach, ob „wir“ gefallen ist – im Zweifel persönlich.

Jede Änderung nennt als Beleg die Nachrichten (N…), auf die sie sich stützt. Eine Nachricht von Singra oder ein Bericht belegt, was Singra festgestellt hat; was der Mensch über sich sagt, belegt nur seine eigene Nachricht.

Titel: zwei bis sechs Wörter. Thema: ein oder zwei Wörter, gleiche Dinge gleich benannt – vorhandene Themen zuerst. Art: fakt, vorliebe, anweisung, ereignis, plan, beziehung oder wissen. Wichtigkeit: 5 für den Kern (Name, Familie, feste Anweisungen an Singra), 3 für das Übliche, 1 für Nebensachen.

Schreib in der Sprache, in der der Mensch schreibt. Enthält der Ausschnitt nichts Dauerhaftes, gibst du eine leere Liste zurück – das ist oft die richtige Antwort."""


# ── Bausteine ────────────────────────────────────────────────────────────


@dataclass(frozen=True, repr=False)
class Zeile:
    """Eine Nachricht im Ausschnitt. ``nummer`` fehlt beim Vorlauf."""

    nummer: str | None
    sprecher: str  # "Benutzer", "Singra" oder "Bericht"
    text: str
    zeit: datetime | None = None
    nachricht_id: str | None = None

    @property
    def vom_menschen(self) -> bool:
        return self.sprecher == "Benutzer"


@dataclass(frozen=True, repr=False)
class Auftrag:
    """Was ein Durchgang liest: ein Gesprächsausschnitt oder eine Mitschrift.

    ``seit`` ist der früheste Zeitpunkt, aus dem etwas im Auftrag stammt —
    beim Gespräch die erste Zeile samt Zusammenhang, bei einer Mitschrift der
    Beginn der Sitzung. Persönliches schreibt er nur, wenn die Einwilligung
    schon davor galt (`_vorbereiten`).
    """

    user_id: int
    provider_id: int | None
    zeilen: tuple[Zeile, ...]
    seit: datetime
    server_hinweise: frozenset[int] = frozenset()
    gesprochen: bool = False
    quelle_ref: str | None = None


@dataclass
class Bereich:
    kennung: str
    scope: str
    identity: str
    name: str
    server_id: int | None = None
    team_id: int | None = None
    #: Wieviele neue Einträge noch passen; ``None`` heißt unbegrenzt.
    frei: int | None = None


@dataclass(repr=False)
class Kandidat:
    kennung: str
    id: str
    bereich: Bereich
    text: str
    titel: str | None
    thema: str | None
    vom_menschen: bool
    fassung: int


@dataclass
class Ergebnis:
    #: ``ok`` (gelesen, auch wenn nichts dabei war), ``leer`` (nichts zu
    #: lesen oder kein Bereich), ``fehler`` (Anbieter oder Antwort),
    #: ``spaeter`` (kein Kontingent, kein Anbieter).
    status: str
    neu: int = 0
    geaendert: int = 0
    zusammengefuehrt: int = 0
    vergessen: int = 0
    verworfen: int = 0


@dataclass(repr=False)
class _Vorbereitet:
    provider: AiProvider
    api_key: str | None
    modell: str
    preise: tuple[int | None, int | None, int | None]
    messages: list[dict]
    request_id: Any
    eingabe: int
    bereiche: list[Bereich]
    kandidaten: list[Kandidat]


class _Verworfen(Exception):
    """Eine einzelne Änderung passt nicht; die übrigen laufen weiter."""


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def rueckstellung(versuche: int) -> timedelta:
    """Wie lange nach dem n-ten Fehlschlag gewartet wird: 5 min, 10, 20 … höchstens 6 h.

    Der Exponent ist gedeckelt: ``timedelta`` läuft sonst beim vierzigsten
    Versuch über, und der Fehler fiele genau dort, wo freigegeben werden
    sollte.
    """
    return min(RUECKSTELLUNG_MAX, RUECKSTELLUNG_BASIS * 2 ** min(max(versuche, 1) - 1, 12))


def stichwort(text: str | None) -> bool:
    """Sagt der Mensch ausdrücklich, dass etwas ins Gedächtnis soll oder heraus?"""
    return bool(text) and STICHWORT.search(text) is not None


def _flach(text: str) -> str:
    """Eine Zeile — ein Umbruch im Inhalt öffnete sonst eine fremde Zeile.

    Dieselbe Form wie im Transkript der Faltung (`_foldable_window`): die
    Kennung steht am Zeilenanfang, und kein Inhalt kann sie nachbauen.
    """
    return " ".join(str(text or "").split())


def _kuerzen(text: str, grenze: int) -> str:
    return text if len(text) <= grenze else text[: grenze - 1].rstrip() + "…"


# ── Marke und Auslöser ───────────────────────────────────────────────────


def _nach_der_marke(gespraech: AiConversation):
    """Die Bedingung „ungelesen“: vollständig und hinter der Marke."""
    bedingungen = [
        AiMessage.conversation_id == gespraech.id,
        AiMessage.status == "complete",
    ]
    if gespraech.gedaechtnis_bis is not None:
        if gespraech.gedaechtnis_bis_id:
            bedingungen.append(
                tuple_(AiMessage.created_at, AiMessage.id)
                > tuple_(gespraech.gedaechtnis_bis, gespraech.gedaechtnis_bis_id)
            )
        else:
            bedingungen.append(AiMessage.created_at > gespraech.gedaechtnis_bis)
    return and_(*bedingungen)


def _vor_der_marke(gespraech: AiConversation):
    """Das Gegenstück: schon gelesen. Nur sinnvoll, wenn es eine Marke gibt."""
    if gespraech.gedaechtnis_bis_id:
        return tuple_(AiMessage.created_at, AiMessage.id) <= tuple_(
            gespraech.gedaechtnis_bis, gespraech.gedaechtnis_bis_id
        )
    return AiMessage.created_at <= gespraech.gedaechtnis_bis


def _setzen(db: Session, conversation_id: str, *bedingungen, **werte) -> int:
    """Ändert die Spalten des Schreibers, ohne `updated_at` anzufassen.

    Das Gespräch ist für den Menschen nicht neuer geworden, nur weil im
    Hintergrund jemand mitgelesen hat. `updated_at` trägt ein ``onupdate``;
    ausdrücklich auf sich selbst gesetzt, bleibt es stehen.
    """
    ergebnis = db.execute(
        update(AiConversation)
        .where(AiConversation.id == conversation_id, *bedingungen)
        .values(**werte, updated_at=AiConversation.updated_at)
        .execution_options(synchronize_session=False)
    )
    return int(ergebnis.rowcount or 0)


def vormerken(db: Session, conversation_id: str, *, jetzt: datetime | None = None) -> bool:
    """Ein Lauf ist zu Ende: der nächste Durchgang wird fällig.

    In fünf Minuten — es sei denn, die älteste ungelesene Nachricht wartet
    schon eine halbe Stunde, oder der Mensch hat „merk dir“ gesagt: dann
    sofort. Ist schon ein Durchgang fällig, bleibt es dabei; ein neuer Lauf
    schiebt ihn nicht wieder nach hinten.

    Nach einem Fehlschlag gilt die Rückstellung, auch gegen ein Stichwort:
    zöge jeder Lauf den nächsten Versuch auf fünf Minuten vor, wären die
    fünf Versuche verbraucht, bevor der Anbieter wieder antwortet, und der
    Ausschnitt fiele heraus. Läuft gerade ein Durchgang, wird trotzdem
    vorgemerkt — daran sieht `_freigeben`, dass seither Neues kam.

    Gibt zurück, ob der Mensch ein Stichwort gesagt hat — dann soll der
    Aufrufer gleich einen Durchgang beginnen (`starten`). Committet.
    """
    jetzt = jetzt or _jetzt()
    gespraech = db.get(AiConversation, conversation_id)
    if gespraech is None or gespraech.kind != "primary":
        return False
    bisher = gespraech.gedaechtnis_faellig
    laeuft = gespraech.gedaechtnis_sperre is not None and gespraech.gedaechtnis_sperre > jetzt
    if gespraech.gedaechtnis_fehlversuche and not laeuft and bisher is not None and bisher > jetzt:
        return False
    ungelesen = _nach_der_marke(gespraech)
    aelteste = (
        db.query(AiMessage.created_at)
        .filter(ungelesen)
        .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
        .first()
    )
    if aelteste is None:
        return False
    letzte_frage = (
        db.query(AiMessage)
        .filter(ungelesen, AiMessage.role == "user", AiMessage.intern.is_(False))
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .first()
    )
    sofort = letzte_frage is not None and stichwort(letzte_frage.content)
    if sofort or aelteste[0] <= jetzt - SPAETESTENS:
        faellig = jetzt
    else:
        faellig = jetzt + RUHE
    if bisher is not None and bisher <= jetzt:
        faellig = bisher
    _setzen(db, conversation_id, gedaechtnis_faellig=faellig)
    db.commit()
    return sofort


#: Laufende Durchgänge je Gespräch. Eine starke Referenz, sonst räumte der
#: Garbage Collector eine Aufgabe mitten im Lauf ab.
_LAUFEND: dict[str, asyncio.Task] = {}
_NEBENHER: set[asyncio.Task] = set()


def starten(conversation_id: str) -> bool:
    """Beginnt einen Durchgang nebenher, wenn Platz ist. Nur im Ereignisloop."""
    if conversation_id in _LAUFEND or len(_LAUFEND) >= MAX_GLEICHZEITIG:
        return False
    aufgabe = asyncio.get_running_loop().create_task(_durchgang_sicher(conversation_id))
    _LAUFEND[conversation_id] = aufgabe
    aufgabe.add_done_callback(lambda _a: _LAUFEND.pop(conversation_id, None))
    return True


async def nach_dem_lauf(conversation_id: str) -> None:
    """Für das Ende eines Laufs: vormerken, bei einem Stichwort gleich lesen."""

    def _merken() -> bool:
        with SessionLocal() as db:
            return vormerken(db, conversation_id)

    if await asyncio.to_thread(_merken):
        starten(conversation_id)


def faellige_starten(db: Session, *, jetzt: datetime | None = None) -> int:
    """Der Takt: fällige Gespräche suchen und je einen Durchgang beginnen."""
    jetzt = jetzt or _jetzt()
    frei = MAX_GLEICHZEITIG - len(_LAUFEND)
    if frei <= 0:
        return 0
    kennungen = [
        kennung
        for (kennung,) in db.query(AiConversation.id)
        .filter(
            AiConversation.gedaechtnis_faellig <= jetzt,
            or_(
                AiConversation.gedaechtnis_sperre.is_(None),
                AiConversation.gedaechtnis_sperre < jetzt,
            ),
        )
        .order_by(AiConversation.gedaechtnis_faellig.asc())
        .limit(frei)
        .all()
    ]
    return sum(1 for kennung in kennungen if starten(kennung))


# ── Ein Durchgang über ein Gespräch ──────────────────────────────────────


def _beanspruchen(db: Session, conversation_id: str, jetzt: datetime) -> datetime | None:
    """Nimmt das Gespräch für einen Durchgang — atomar, vor dem Lesen.

    `gedaechtnis_faellig` wird dabei auf das Ende der Sperre geschoben: stirbt
    der Prozess mittendrin, ist das Gespräch danach von selbst wieder fällig.
    Wer währenddessen vormerkt, überschreibt das; `_freigeben` sieht es am
    veränderten Wert und lässt die neue Frist stehen.
    """
    bis = jetzt + SPERRE
    genommen = _setzen(
        db, conversation_id,
        AiConversation.gedaechtnis_faellig <= jetzt,
        or_(
            AiConversation.gedaechtnis_sperre.is_(None),
            AiConversation.gedaechtnis_sperre < jetzt,
        ),
        gedaechtnis_sperre=bis,
        gedaechtnis_faellig=bis,
    )
    db.commit()
    return bis if genommen == 1 else None


def _weiter(ende: tuple[datetime, str], bis: datetime | None, bis_id: str | None) -> bool:
    """Liegt ``ende`` hinter der Marke? Die Marke geht nur vorwärts.

    Wird das Gedächtnis während eines Durchgangs eingeschaltet, steht die
    Marke danach auf „jetzt“ (`ai_memory_service._einschalten`). Der
    Durchgang darf sie beim Freigeben nicht auf sein älteres Fensterende
    zurücksetzen — sonst läse der nächste, was ohne Einwilligung gesagt wurde.
    Eine Marke ohne Kennung deckt alles bis einschließlich ihres Zeitpunkts.
    """
    if bis is None:
        return True
    if ende[0] != bis:
        return ende[0] > bis
    return bis_id is not None and ende[1] > bis_id


def _freigeben(
    db: Session,
    conversation_id: str,
    sperre: datetime,
    *,
    ende: tuple[datetime, str] | None = None,
    naechste: datetime | None = None,
    fehlversuche: int | None = None,
    nach_fehlschlag: bool = False,
) -> None:
    """Gibt das Gespräch frei, rückt die Marke vor und setzt die nächste Frist.

    ``naechste`` gilt nur, wenn niemand während des Durchgangs vorgemerkt hat
    (die Frist steht noch auf dem Ende der Sperre); sonst bleibt die neuere —
    außer ``naechste`` ist früher. Nach einem Fehlschlag gilt ``naechste`` in
    jedem Fall: ein Lauf, der währenddessen vormerkte, zöge den nächsten
    Versuch sonst auf fünf Minuten vor (`vormerken`). Committet nicht.
    """
    zeile = db.execute(
        select(
            AiConversation.gedaechtnis_faellig,
            AiConversation.gedaechtnis_bis,
            AiConversation.gedaechtnis_bis_id,
        )
        .where(AiConversation.id == conversation_id)
        .with_for_update()
    ).first()
    if zeile is None:
        return
    faellig, bis, bis_id = zeile
    if nach_fehlschlag or (faellig is not None and faellig == sperre):
        faellig = naechste
    elif naechste is not None and (faellig is None or naechste < faellig):
        faellig = naechste
    werte: dict[str, Any] = {"gedaechtnis_sperre": None, "gedaechtnis_faellig": faellig}
    if ende is not None and _weiter(ende, bis, bis_id):
        werte["gedaechtnis_bis"], werte["gedaechtnis_bis_id"] = ende
    if fehlversuche is not None:
        werte["gedaechtnis_fehlversuche"] = fehlversuche
    _setzen(db, conversation_id, **werte)


def _zeit_text(zeit: datetime | None, zone: ZoneInfo) -> str:
    if zeit is None:
        return ""
    lokal = (zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)).astimezone(zone)
    return f"{WOCHENTAGE_KURZ[lokal.weekday()]} {lokal:%d.%m.%Y %H:%M}"


def _als_zeile(row: AiMessage, nummer: str | None, zone_name: str) -> Zeile:
    from services.ai_context_service import _ZEITSTEMPEL_PRAEFIX_RE, _message_content_for_provider

    if row.role == "assistant":
        sprecher = "Singra"
    elif row.intern:
        sprecher = "Bericht"
    else:
        sprecher = "Benutzer"
    # Dieselbe Übersetzung wie im Kontext (Rückfragen stehen in
    # `question_json`), aber ohne den kurzen Zeitstempel: hier steht die
    # Zeit mit Jahr davor, sonst ließe sich „im Juli“ nicht auflösen.
    inhalt = _ZEITSTEMPEL_PRAEFIX_RE.sub("", _message_content_for_provider(row, zone_name))
    inhalt = _kuerzen(_flach(redact_sensitive_text(inhalt)), MAX_NACHRICHT_ZEICHEN[sprecher])
    return Zeile(nummer, sprecher, inhalt, row.created_at, row.id)


@dataclass(repr=False)
class _Gelesen:
    """Was `_gespraech_lesen` vorfand.

    Ohne ``auftrag`` gibt es nichts zu schreiben; ``ende`` sagt dann, bis
    wohin trotzdem als gelesen gilt.
    """

    auftrag: Auftrag | None
    ende: tuple[datetime, str] | None
    rest: bool = False


def _seite(db: Session, *bedingungen, absteigend: bool = False, grenze: int) -> list[AiMessage]:
    """Nachrichten samt Text, gebündelt entschlüsselt.

    Einzeln kostete jede Nachricht zwei Aufrufe beim Sidecar, nacheinander —
    bei 500 Nachrichten tausend. Denktext und Gliederung liest der Schreiber
    nie; geladen werden sie deshalb auch nicht (wie in
    `ai_context_service.build_provider_messages`).
    """
    if absteigend:
        reihe = (AiMessage.created_at.desc(), AiMessage.id.desc())
    else:
        reihe = (AiMessage.created_at.asc(), AiMessage.id.asc())
    seite = db.query(AiMessage).filter(*bedingungen).order_by(*reihe).limit(grenze)
    with vorab_entschluesselt(db, seite, AiMessage.content, AiMessage.question_json):
        return seite.options(defer(AiMessage.reasoning), defer(AiMessage.sections_json)).all()


def _neueste(db: Session, conversation_id: str) -> tuple[datetime, str] | None:
    """Die jüngste vollständige Nachricht — ohne ihren Text."""
    zeile = (
        db.query(AiMessage.created_at, AiMessage.id)
        .filter(AiMessage.conversation_id == conversation_id, AiMessage.status == "complete")
        .order_by(AiMessage.created_at.desc(), AiMessage.id.desc())
        .first()
    )
    return (zeile[0], zeile[1]) if zeile is not None else None


def _gespraech_lesen(db: Session, conversation_id: str) -> _Gelesen | None:
    """Der Ausschnitt hinter der Marke. Nie im Ereignisloop (`durchgang`)."""
    gespraech = db.get(AiConversation, conversation_id)
    if gespraech is None or gespraech.kind != "primary":
        return None
    user = db.get(User, gespraech.user_id)
    if user is None:
        return None
    if not user.is_active or not permission_service.has_global_permission(db, user, "ai.memory.use"):
        # Ohne das Recht schreibt er nichts, also entschlüsselt er auch
        # nichts. Was bis hierher gesagt wurde, gilt als gelesen, wie nach
        # jedem leeren Durchgang: wer das Recht später bekommt, bekommt kein
        # Gedächtnis über die Zeit davor.
        return _Gelesen(None, _neueste(db, conversation_id))
    einwilligung = ai_memory_service.einwilligung_seit(db, user.id)
    zone_name = ai_lage.zone_des_benutzers(user)
    ungelesen = _seite(db, _nach_der_marke(gespraech), grenze=500)
    if not ungelesen:
        return None
    zeilen: list[Zeile] = []
    laenge = 0
    for row in ungelesen:
        zeile = _als_zeile(row, f"N{len(zeilen) + 1}", zone_name)
        if zeilen and laenge + len(zeile.text) > MAX_FENSTER_ZEICHEN:
            break
        zeilen.append(zeile)
        laenge += len(zeile.text)
    rest = len(zeilen) < len(ungelesen)
    letzte = ungelesen[len(zeilen) - 1]

    vorlauf: list[Zeile] = []
    if gespraech.gedaechtnis_bis is not None:
        bedingungen = [
            AiMessage.conversation_id == gespraech.id,
            AiMessage.status == "complete",
            _vor_der_marke(gespraech),
        ]
        if einwilligung is not None:
            # Auch der Zusammenhang stammt nur aus der Zeit mit Einwilligung:
            # was davor gesagt wurde, stünde sonst als „schon gelesen“ im
            # Ausschnitt — und ein Satz daraus im persönlichen Gedächtnis.
            bedingungen.append(AiMessage.created_at > einwilligung)
        try:
            davor = _seite(db, *bedingungen, absteigend=True, grenze=MAX_VORLAUF)
        except DisSidecarError:
            # Der Zusammenhang hilft beim Verstehen, er trägt nichts: eine
            # unlesbare Nachricht davor hält den Ausschnitt nicht auf.
            davor = []
        zeichen = 0
        for row in davor:
            zeile = _als_zeile(row, None, zone_name)
            zeile = Zeile(None, zeile.sprecher, _kuerzen(zeile.text, 800), zeile.zeit, zeile.nachricht_id)
            if zeichen + len(zeile.text) > MAX_VORLAUF_ZEICHEN:
                break
            vorlauf.insert(0, zeile)
            zeichen += len(zeile.text)

    # Die Server, um die es in diesem Ausschnitt ging, nennt der Lauf selbst.
    erster = ungelesen[0].created_at
    hinweise = frozenset(
        server_id
        for (server_id,) in db.query(AiRun.last_server_id)
        .filter(
            AiRun.conversation_id == gespraech.id,
            AiRun.last_server_id.isnot(None),
            AiRun.created_at >= erster - timedelta(minutes=1),
            AiRun.created_at <= letzte.created_at,
        )
        .distinct()
        .limit(MAX_SERVER)
    )
    anbieter = next(
        (row.provider_id for row in reversed(ungelesen[: len(zeilen)])
         if row.role == "assistant" and row.provider_id),
        None,
    )
    alle = vorlauf + zeilen
    auftrag = Auftrag(
        user_id=user.id,
        provider_id=anbieter,
        zeilen=tuple(alle),
        seit=alle[0].zeit,
        server_hinweise=hinweise,
    )
    return _Gelesen(auftrag, (letzte.created_at, letzte.id), rest)


def _nehmen(conversation_id: str, jetzt: datetime) -> datetime | None:
    with SessionLocal() as db:
        return _beanspruchen(db, conversation_id, jetzt)


def _lesen(conversation_id: str) -> _Gelesen | None:
    with SessionLocal() as db:
        return _gespraech_lesen(db, conversation_id)


async def durchgang(conversation_id: str) -> Ergebnis:
    """Ein Durchgang über ein Gespräch: beanspruchen, lesen, schreiben, freigeben.

    Datenbank und Sidecar nur in einem Thread: ein Ausschnitt sind bis zu
    500 Nachrichten, und was davon im Ereignisloop liefe, hielte jeden Chat
    und jede Sprachsitzung des Panels an.
    """
    sperre = await asyncio.to_thread(_nehmen, conversation_id, _jetzt())
    if sperre is None:
        return Ergebnis("leer")

    gelesen: _Gelesen | None = None
    unlesbar = False
    try:
        gelesen = await asyncio.to_thread(_lesen, conversation_id)
        if gelesen is None or gelesen.auftrag is None:
            ergebnis = Ergebnis("leer")
        else:
            ende, rest = gelesen.ende, gelesen.rest

            def _abschluss(db: Session, fertig: Ergebnis) -> None:
                # Marke und Änderungen in **einer** Transaktion: was
                # geschrieben ist, ist auch gelesen, und umgekehrt.
                _freigeben(
                    db, conversation_id, sperre, ende=ende,
                    naechste=_jetzt() if rest else None, fehlversuche=0,
                )

            ergebnis = await schreiben(gelesen.auftrag, abschluss=_abschluss)
    except Exception as exc:  # noqa: BLE001 - ein Durchgang darf nichts mitreißen
        logger.warning(
            "Gedaechtnis: Durchgang gescheitert conversation=%s error=%s",
            conversation_id, type(exc).__name__,
        )
        unlesbar = isinstance(exc, DisDecryptionError)
        ergebnis = Ergebnis("fehler")

    if ergebnis.status != "ok":  # sonst hat `_abschluss` freigegeben
        await asyncio.to_thread(
            _nachbereiten, conversation_id, sperre, ergebnis.status, gelesen, unlesbar
        )
    return ergebnis


def _nachbereiten(
    conversation_id: str,
    sperre: datetime,
    status: str,
    gelesen: _Gelesen | None,
    unlesbar: bool,
) -> None:
    """Gibt frei, wenn nichts geschrieben wurde: leer, später oder gescheitert."""
    ende = gelesen.ende if gelesen is not None else None
    rest = gelesen.rest if gelesen is not None else False
    with SessionLocal() as db:
        if status == "leer":
            _freigeben(
                db, conversation_id, sperre, ende=ende,
                naechste=_jetzt() if rest else None, fehlversuche=0,
            )
        elif status == "spaeter":
            _freigeben(db, conversation_id, sperre, naechste=_jetzt() + SPAETER)
        else:
            versuche = int(
                db.query(AiConversation.gedaechtnis_fehlversuche)
                .filter(AiConversation.id == conversation_id)
                .scalar()
                or 0
            ) + 1
            if versuche >= MAX_FEHLVERSUCHE and gelesen is None and unlesbar:
                # Eine Nachricht, die sich nicht entschlüsseln lässt, wird es
                # auch beim sechsten Mal nicht. Welche es ist, verrät der
                # Fehler nicht: übersprungen wird alles Ungelesene. Ein
                # stummer Sidecar dagegen kommt wieder, und ein Leseversuch
                # kostet beim Anbieter nichts — dort wird weiter gewartet.
                ende = _neueste(db, conversation_id)
            if versuche >= MAX_FEHLVERSUCHE and ende is not None:
                # Nichts darf ewig hängen: nach dem fünften Fehlschlag am
                # selben Ausschnitt wird er übersprungen. Gelesen hat ihn dann
                # niemand — das ist ehrlicher als ein Gedächtnis, das ab hier
                # für immer stehen bleibt.
                logger.warning(
                    "Gedaechtnis: Ausschnitt nach %s Fehlschlaegen uebersprungen conversation=%s",
                    versuche, conversation_id,
                )
                _freigeben(
                    db, conversation_id, sperre, ende=ende,
                    naechste=_jetzt() if rest else None, fehlversuche=0,
                )
            else:
                _freigeben(
                    db, conversation_id, sperre, naechste=_jetzt() + rueckstellung(versuche),
                    fehlversuche=min(versuche, 999), nach_fehlschlag=True,
                )
        db.commit()


async def _durchgang_sicher(conversation_id: str) -> None:
    try:
        ergebnis = await durchgang(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gedaechtnis: Durchgang abgebrochen error=%s", type(exc).__name__)
        return
    if ergebnis.status == "ok":
        logger.info(
            "Gedaechtnis: conversation=%s neu=%s geaendert=%s zusammengefuehrt=%s "
            "vergessen=%s verworfen=%s",
            conversation_id, ergebnis.neu, ergebnis.geaendert,
            ergebnis.zusammengefuehrt, ergebnis.vergessen, ergebnis.verworfen,
        )


# ── Mitschrift eines Sprachgesprächs ─────────────────────────────────────


def aus_mitschrift(
    *,
    user_id: int,
    provider_id: int | None,
    zeilen: list[tuple[str, str, datetime]],
    beginn: datetime,
) -> bool:
    """Ein Sprachgespräch ist zu Ende: die Mitschrift geht an den Schreiber.

    ``zeilen`` sind (sprecher, text, zeit) mit sprecher ``ich`` (der Mensch)
    oder ``ki``. Gespeichert wird die Mitschrift nirgends: sie lebt bis zum
    Ende dieser Aufgabe. Eine lange Sitzung wird in Ausschnitten gelesen, der
    Reihe nach. Gibt zurück, ob ein Durchgang beginnt. Nur im Ereignisloop.
    """
    if not any(sprecher == "ich" and text.strip() for sprecher, text, _z in zeilen):
        return False
    stuecke: list[list[Zeile]] = [[]]
    laenge = 0
    for sprecher, text, zeit in zeilen:
        inhalt = _flach(redact_sensitive_text(text or ""))
        if not inhalt:
            continue
        name = "Benutzer" if sprecher == "ich" else "Singra"
        inhalt = _kuerzen(inhalt, MAX_NACHRICHT_ZEICHEN[name])
        if stuecke[-1] and laenge + len(inhalt) > MAX_FENSTER_ZEICHEN:
            stuecke.append([])
            laenge = 0
        stuecke[-1].append(Zeile(f"N{len(stuecke[-1]) + 1}", name, inhalt, zeit))
        laenge += len(inhalt)
    auftraege = [
        Auftrag(
            user_id=user_id,
            provider_id=provider_id,
            zeilen=tuple(stueck),
            seit=beginn,
            gesprochen=True,
            quelle_ref=f"sprache:{beginn.astimezone(timezone.utc):%Y-%m-%dT%H:%M}",
        )
        for stueck in stuecke
        if any(zeile.vom_menschen for zeile in stueck)
    ]
    if not auftraege:
        return False
    aufgabe = asyncio.get_running_loop().create_task(_mitschrift_schreiben(auftraege))
    _NEBENHER.add(aufgabe)
    aufgabe.add_done_callback(_NEBENHER.discard)
    return True


async def _mitschrift_schreiben(auftraege: list[Auftrag]) -> None:
    for auftrag in auftraege:
        for versuch in range(2):
            try:
                ergebnis = await schreiben(auftrag)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gedaechtnis: Mitschrift gescheitert error=%s", type(exc).__name__)
                ergebnis = Ergebnis("fehler")
            if ergebnis.status != "fehler" or versuch:
                break
            await asyncio.sleep(60)
        logger.info(
            "Gedaechtnis: Mitschrift user=%s status=%s neu=%s geaendert=%s verworfen=%s",
            auftrag.user_id, ergebnis.status, ergebnis.neu, ergebnis.geaendert, ergebnis.verworfen,
        )


# ── Bereiche und Bestand ─────────────────────────────────────────────────


def _bestand_zahl(db: Session, identity: str) -> int:
    return int(
        db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv")
        .count()
    )


def _server_im_text(db: Session, user: User, texte: list[str]) -> set[int]:
    """Sichtbare Server, deren Name im Ausschnitt steht (höchstens `MAX_SERVER`)."""
    sichtbar = permission_service.list_visible_server_ids(db, user)
    if sichtbar is not None and not sichtbar:
        return set()
    abfrage = db.query(Server.id, Server.name)
    if sichtbar is not None:
        abfrage = abfrage.filter(Server.id.in_(sichtbar))
    gesamt = " ".join(texte).casefold()
    gefunden: set[int] = set()
    for server_id, name in abfrage.all():
        name = (name or "").strip().casefold()
        if len(name) < 3:
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", gesamt):
            gefunden.add(server_id)
            if len(gefunden) >= MAX_SERVER:
                break
    return gefunden


def _bereiche(db: Session, user: User, auftrag: Auftrag, persoenlich: bool) -> list[Bereich]:
    """Wohin dieser Durchgang schreiben darf — mit Platz und Rechten."""
    roh: list[tuple[str, int | None, int | None, str]] = []
    if persoenlich:
        roh.append(("user", None, None, "Persönlich: über den Benutzer"))
    texte = [zeile.text for zeile in auftrag.zeilen]
    server_ids = (set(auftrag.server_hinweise) | _server_im_text(db, user, texte))
    for server in (
        db.query(Server).filter(Server.id.in_(sorted(server_ids)[: MAX_SERVER * 2])).all()
        if server_ids else []
    ):
        if not permission_service.has_server_permission(
            db=db, user=user, server_id=server.id, key="server.view"
        ):
            continue
        if persoenlich:
            roh.append(("server", server.id, None, f"Eigene Notizen des Benutzers zum Server „{server.name}“"))
        if permission_service.has_server_permission(
            db=db, user=user, server_id=server.id, key="server.config.write"
        ):
            roh.append((
                "server_shared", server.id, None,
                f"Wissen des Servers „{server.name}“ (lesen alle, die ihn betreuen)",
            ))
    from services import team_service

    for team in (
        db.query(Team)
        .filter(Team.id.in_(team_service.user_team_ids(db, user)))
        .order_by(Team.id)
        .limit(MAX_TEAMS * 2)
        .all()
    ):
        if team.is_personal or not team_service.can_manage_team_memory(db, user, team.id):
            continue
        # Der ansprechbare Name, nicht der blanke: Teamnamen sind nur je
        # Gründer eindeutig, und zwei Bereiche „Alpha“ ließen ihn raten.
        name = team_service.ansprechbarer_name(db, user, team)
        roh.append(("team", None, team.id, f"Teamwissen „{name}“ (lesen alle im Team)"))

    bereiche: list[Bereich] = []
    for scope, server_id, team_id, name in roh:
        try:
            identity, _o, sid, tid = ai_memory_service.scope_identity(db, user, scope, server_id, team_id)
        except HTTPException:
            continue
        grenze = ai_limit_service.resolve_scope_memory_limit(
            db, scope, user, team_id=tid, server_id=sid,
        )
        if grenze == 0:
            continue
        frei = None if grenze is None else max(0, grenze - _bestand_zahl(db, identity))
        bereiche.append(Bereich(
            kennung=f"B{len(bereiche) + 1}", scope=scope, identity=identity, name=name,
            server_id=sid, team_id=tid, frei=frei,
        ))
    return bereiche


def _bestand(db: Session, bereiche: list[Bereich], texte: list[str]) -> list[Kandidat]:
    """Was der Schreiber vom Bestand sieht: je Bereich das, was den Texten am nächsten ist.

    Die Texte sind die neuen Nachrichten eines Ausschnitts oder die Absätze
    eines importierten Teils (`ai_memory_import_service`).
    """
    from services import ai_gedaechtnis_abruf

    kandidaten: list[Kandidat] = []
    for bereich in bereiche:
        # Feste Ordnung: derselbe Bestand ergibt denselben Text, und die
        # Kennungen E1, E2 … bedeuten bei jedem Durchgang dasselbe.
        abfrage = db.query(AiMemoryEntry).filter(
            AiMemoryEntry.scope_identity == bereich.identity,
            AiMemoryEntry.status == "aktiv",
        )
        if _bestand_zahl(db, bereich.identity) > MAX_BESTAND_JE_BEREICH:
            # Ein großer Bereich wird nicht ganz geladen: die Kandidaten kommen
            # aus Vektorspeicher und Wortindex (Stufe 4), höchstens das
            # Doppelte dessen, was der Schreiber sieht.
            abfrage = abfrage.filter(AiMemoryEntry.id.in_(
                ai_gedaechtnis_abruf.naechste(db, bereich.identity, texte, MAX_BESTAND_JE_BEREICH)
            ))
        rows = abfrage.order_by(AiMemoryEntry.created_at.asc(), AiMemoryEntry.id.asc()).all()
        if len(rows) > MAX_BESTAND_JE_BEREICH:
            werte = dict(zip(
                [row.id for row in rows],
                ai_memory_service.aehnlichkeit_zu_texten(db, rows, texte),
            ))

            def _rang(row: AiMemoryEntry) -> tuple[float, float]:
                # Ohne Vergleich (kein Modell, kein Vektor) zählt, was
                # zuletzt gebraucht oder geändert wurde.
                wert = werte.get(row.id)
                zeit = row.last_used_at or row.updated_at or row.created_at
                return (wert if wert is not None else -1.0, zeit.timestamp() if zeit else 0.0)

            rows = sorted(rows, key=_rang, reverse=True)[:MAX_BESTAND_JE_BEREICH]
        geoeffnet = ai_memory_service._entschluesseln_lesbare(rows)
        themen = ai_memory_service.themennamen(
            db, [(row.thema_id, row.scope_identity) for row, _t in geoeffnet]
        )
        for row, text in geoeffnet:
            kandidaten.append(Kandidat(
                kennung=f"E{len(kandidaten) + 1}",
                id=row.id,
                bereich=bereich,
                text=_flach(text),
                titel=_flach(row.titel or row.key or "") or None,
                thema=themen.get(row.thema_id) if row.thema_id else None,
                vom_menschen=row.origin == "user",
                fassung=int(row.fassung or 1),
            ))
    return kandidaten


def _themen(db: Session, bereiche: list[Bereich]) -> list[str]:
    from models import AiMemoryTopic

    paare = [
        (thema_id, identity)
        for thema_id, identity in db.query(AiMemoryTopic.id, AiMemoryTopic.scope_identity)
        .filter(AiMemoryTopic.scope_identity.in_([b.identity for b in bereiche]))
        .limit(300)
        .all()
    ]
    return sorted(set(ai_memory_service.themennamen(db, paare).values()), key=str.casefold)


def _mit_gedaechtnismodell(
    provider: AiProvider,
) -> tuple[AiProvider, str, tuple[int | None, int | None, int | None]]:
    return provider, str(provider.memory_model).strip(), (
        provider.memory_input_price_micro_usd_per_million,
        provider.memory_output_price_micro_usd_per_million,
        provider.memory_cache_price_micro_usd_per_million,
    )


def gedaechtnis_anbieter(
    db: Session, user: User, bevorzugt: int | None
) -> tuple[AiProvider, str, tuple[int | None, int | None, int | None]] | None:
    """Zugang, Modell und Rollenpreise für alles, was das Gedächtnis liest.

    Schreiber, Altbestand und Import nehmen denselben Weg — der Reihe nach
    (Betreiberentscheid 07.10.2026, so einfach wie möglich):

    1. das Gedächtnismodell des Zugangs, mit dem gesprochen wird: der der
       letzten Antwort im Ausschnitt, sonst der am Konto gewählte, sonst
       `anbieter_ohne_auswahl`;
    2. sonst das Gedächtnismodell eines anderen aktiven Zugangs, der einen
       Betreiberschlüssel hat oder keinen braucht, der älteste zuerst — wer es
       nur bei Google einrichtet, dessen Gedächtnis liest auch Gespräche, die
       über OpenAI liefen;
    3. sonst das Standardmodell des Zugangs aus 1. Ohne eigene Einrichtung
       läuft das Gedächtnis also weiter.

    Bis 07.10. stand an der Stelle von 1 und 2 das Worker-Modell. Ein eigener
    Platz sagt, was er tut; der Worker arbeitet an Aufträgen.
    """
    provider = None
    for kennung in (bevorzugt, getattr(user, "ai_provider_id", None)):
        if kennung:
            gefunden = db.get(AiProvider, kennung)
            if gefunden is not None and fuer_chat(gefunden):
                provider = gefunden
                break
    if provider is None:
        provider = anbieter_ohne_auswahl(db, user)
    if provider is not None and fuer_gedaechtnis(provider):
        return _mit_gedaechtnismodell(provider)
    for kandidat in (
        db.query(AiProvider)
        .filter(
            AiProvider.enabled.is_(True),
            AiProvider.memory_enabled.is_(True),
            AiProvider.memory_model.isnot(None),
        )
        .order_by(AiProvider.id.asc())
        .all()
    ):
        if fuer_gedaechtnis(kandidat) and (
            not kandidat.requires_api_key or kandidat.operator_api_key_encrypted
        ):
            return _mit_gedaechtnismodell(kandidat)
    if provider is None:
        return None
    return provider, str(provider.default_model), (
        provider.standard_input_price_micro_usd_per_million,
        provider.standard_output_price_micro_usd_per_million,
        provider.standard_cache_price_micro_usd_per_million,
    )


def _schaetzung(provider: AiProvider, preise, eingabe: int, ausgabe: int) -> int:
    ein, aus, _cache = preise
    if ein is not None and aus is not None:
        return (eingabe * int(ein) + ausgabe * int(aus)) // 1_000_000
    preis = provider.token_price_micro_usd_per_million
    return ((eingabe + ausgabe) * int(preis)) // 1_000_000 if preis else 0


def _platz(bereich: Bereich) -> str:
    if bereich.frei is None:
        return ""
    if bereich.frei == 0:
        return " — voll: nur ändern, zusammenführen, vergessen"
    return f" — noch {bereich.frei} frei"


def _kandidat_text(kandidat: Kandidat) -> str:
    herkunft = "vom Benutzer" if kandidat.vom_menschen else "von Singra"
    merkmale = [kandidat.bereich.kennung, herkunft]
    if kandidat.thema:
        merkmale.append(f"Thema {kandidat.thema}")
    kopf = f"{kandidat.kennung} ({', '.join(merkmale)})"
    titel = f" {kandidat.titel}:" if kandidat.titel else ""
    return f"{kopf}{titel} {_kuerzen(kandidat.text, 600)}"


def _nachricht(
    auftrag: Auftrag,
    bereiche: list[Bereich],
    kandidaten: list[Kandidat],
    themen: list[str],
    zone_name: str,
    jetzt: datetime,
) -> str:
    zone = ZoneInfo(zone_name)
    lokal = jetzt.astimezone(zone)
    teile = [
        f"Heute: {ai_lage.WOCHENTAGE[lokal.weekday()]}, {lokal:%d.%m.%Y}, "
        f"{lokal:%H:%M} ({zone_name})."
    ]
    if auftrag.gesprochen:
        teile.append(
            "Der Ausschnitt ist die Mitschrift eines Sprachgesprächs. Abschriften "
            "können Hörfehler enthalten: was du nicht sicher verstehst, trägst du "
            "nicht ein."
        )
    teile.append(
        "Bereiche, in die du schreiben darfst:\n"
        + "\n".join(f"{b.kennung}: {b.name}{_platz(b)}" for b in bereiche)
    )
    if themen:
        teile.append("Vorhandene Themen: " + ", ".join(_flach(t) for t in themen))
    if kandidaten:
        teile.append(
            "Bestand (was Singra dazu schon weiß):\n"
            + "\n".join(_kandidat_text(k) for k in kandidaten)
        )
    else:
        teile.append("Bestand: noch nichts, was zu diesem Ausschnitt passt.")
    vorlauf = [zeile for zeile in auftrag.zeilen if zeile.nummer is None]
    if vorlauf:
        teile.append(
            "Davor (schon gelesen, nur zum Verständnis):\n"
            + "\n".join(
                f"{z.sprecher}, {_zeit_text(z.zeit, zone)}: {z.text}" for z in vorlauf
            )
        )
    teile.append(
        "Neu:\n"
        + "\n".join(
            f"{z.nummer} {z.sprecher}"
            + (f", {_zeit_text(z.zeit, zone)}" if z.zeit else "")
            + f": {z.text}"
            for z in auftrag.zeilen
            if z.nummer
        )
    )
    return "\n\n".join(teile)


def _vorbereiten(auftrag: Auftrag, jetzt: datetime) -> _Vorbereitet | Ergebnis:
    """Rechte, Bereiche, Bestand, Zugang und Reservierung — in einer Sitzung."""
    with SessionLocal() as db:
        user = db.get(User, auftrag.user_id)
        if user is None or not user.is_active:
            return Ergebnis("leer")
        if not permission_service.has_global_permission(db, user, "ai.memory.use"):
            return Ergebnis("leer")
        # Persönliches nur, wenn die Einwilligung schon galt, als das Früheste
        # im Auftrag gesagt wurde, und seither ohne Unterbrechung. Sonst käme
        # hinein, was ohne sie gesagt wurde: ein Ausschnitt, gelesen kurz vor
        # dem Einschalten, oder eine Sitzung, in der jemand aus- und wieder
        # eingeschaltet hat.
        einwilligung = ai_memory_service.einwilligung_seit(db, user.id)
        seit = auftrag.seit if auftrag.seit.tzinfo else auftrag.seit.replace(tzinfo=timezone.utc)
        persoenlich = einwilligung is not None and einwilligung <= seit
        if auftrag.gesprochen and not persoenlich:
            # Eine Mitschrift gibt es nur mit der Einwilligung
            # (`realtime_session.mitschreiben`). Fehlt sie jetzt oder fehlte
            # sie zwischendurch, liest sie niemand — auch nicht für Team- und
            # Serverwissen.
            return Ergebnis("leer")
        bereiche = _bereiche(db, user, auftrag, persoenlich)
        if not bereiche:
            return Ergebnis("leer")
        kandidaten = _bestand(db, bereiche, [z.text for z in auftrag.zeilen if z.nummer])
        gewaehlt = gedaechtnis_anbieter(db, user, auftrag.provider_id)
        if gewaehlt is None:
            return Ergebnis("spaeter")
        provider, modell, preise = gewaehlt
        api_key = resolve_api_key(db, provider, user.id)
        if provider.requires_api_key and not api_key:
            return Ergebnis("spaeter")
        zone_name = ai_lage.zone_des_benutzers(user)
        inhalt = _nachricht(auftrag, bereiche, kandidaten, _themen(db, bereiche), zone_name, jetzt)
        messages = [
            {"role": "system", "content": SYSTEMPROMPT},
            {"role": "user", "content": inhalt},
        ]
        eingabe = max(1, (len(SYSTEMPROMPT) + len(inhalt)) // 4)
        request_id = _reservieren(db, user, provider, modell, preise, eingabe, AUSGABE_SCHAETZUNG)
        if request_id is None:
            return Ergebnis("spaeter")
        db.refresh(provider)
        db.expunge(provider)
    return _Vorbereitet(
        provider=provider, api_key=api_key, modell=modell, preise=preise,
        messages=messages, request_id=request_id, eingabe=eingabe,
        bereiche=bereiche, kandidaten=kandidaten,
    )


def _reservieren(
    db: Session,
    user: User,
    provider: AiProvider,
    modell: str,
    preise: tuple[int | None, int | None, int | None],
    eingabe: int,
    ausgabe: int,
) -> Any | None:
    """Reserviert einen Aufruf beim Benutzer. Committet.

    ``None`` heißt: kein Kontingent. Dann wird eben später gelesen — am
    Kontingent vorbei zu lesen wäre der unsichtbare Verbrauch, den die Buchung
    verhindern soll.
    """
    request_id = uuid4()
    try:
        reserve_ai_usage(
            db,
            user,
            request_id=request_id,
            estimated_tokens=eingabe + ausgabe,
            estimated_cost_microunits=_schaetzung(provider, preise, eingabe, ausgabe),
            provider_id=provider.id,
            model=modell,
            zweck=ZWECK_GEDAECHTNIS,
        )
        db.commit()
    except (AiQuotaExceeded, AiUsageConflict):
        db.rollback()
        return None
    return request_id


async def _formular(
    provider: AiProvider,
    api_key: str | None,
    modell: str,
    messages: list[dict],
    werkzeug: dict[str, Any],
) -> tuple[StreamUsage, Any | None, bool]:
    """Ein Modellaufruf mit genau einem erzwungenen Werkzeug.

    Gibt den Verbrauch zurück, den Aufruf des Werkzeugs (``None``, wenn keiner
    kam) und ob der Anbieter scheiterte. Ausgeführt wird das Werkzeug nie,
    gelesen werden seine Argumente.
    """
    from services import ai_run_service

    name = werkzeug["function"]["name"]
    usage = StreamUsage()
    client = ai_run_service.http_client()
    eigener: httpx.AsyncClient | None = None
    gescheitert = False
    try:
        if client is None:
            eigener = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=LESEFRIST_SEKUNDEN, write=10.0, pool=5.0),
                follow_redirects=False,
            )
            client = eigener
        # Lesen ist Fleißarbeit, keine Überlegung — und „nichts gesetzt“ hieße
        # bei manchen Anbietern „nimm deine Vorgabe“ samt Denkschritten.
        denken, denkstufe = await ai_reasoning.aus_fuer(
            client, provider, api_key=api_key, model_id=modell
        )
        async for _stueck in stream_chat_completion(
            client,
            provider=provider,
            api_key=api_key,
            messages=messages,
            usage=usage,
            model=modell,
            reasoning=denken,
            reasoning_effort=denkstufe,
            tools=[werkzeug],
            tool_choice={"type": "function", "function": {"name": name}},
        ):
            pass
    except (AiProviderRequestError, httpx.HTTPError) as exc:
        logger.info("Gedaechtnis: Anbieter scheiterte error=%s", type(exc).__name__)
        gescheitert = True
    finally:
        if eigener is not None:
            await eigener.aclose()
    aufruf = next((ruf for ruf in usage.tool_calls if ruf.name == name), None)
    return usage, aufruf, gescheitert


def _abrechnen(
    db: Session,
    request_id: Any,
    usage: StreamUsage,
    aufruf: Any | None,
    *,
    provider: AiProvider,
    preise: tuple[int | None, int | None, int | None],
    eingabe: int,
    gescheitert: bool,
) -> None:
    """Schließt die Reservierung mit dem ab, was der Aufruf verbraucht hat."""
    ausgabe = (
        len(json.dumps(aufruf.arguments, ensure_ascii=False)) // 4
        if aufruf is not None
        else usage.output_chars // 4
    )
    reservierung_abrechnen(
        db, request_id, usage=usage,
        estimated_actual_tokens=eingabe + ausgabe,
        token_price_micro_usd_per_million=provider.token_price_micro_usd_per_million,
        gescheitert=gescheitert,
        rollenpreise=preise,
        geschaetzte_teile=(eingabe, ausgabe),
    )


async def schreiben(
    auftrag: Auftrag,
    *,
    abschluss: Callable[[Session, Ergebnis], None] | None = None,
) -> Ergebnis:
    """Liest einen Auftrag und schreibt, was daraus folgt.

    ``abschluss`` läuft in derselben Transaktion wie die Änderungen, vor dem
    Commit — der Durchgang über ein Gespräch rückt dort seine Marke vor.
    """
    jetzt = _jetzt()
    vorbereitet = await asyncio.to_thread(_vorbereiten, auftrag, jetzt)
    if isinstance(vorbereitet, Ergebnis):
        return vorbereitet
    v = vorbereitet

    usage, aufruf, gescheitert = await _formular(
        v.provider, v.api_key, v.modell, v.messages, WERKZEUG
    )
    aenderungen = None if gescheitert or aufruf is None else _aenderungen_lesen(aufruf.arguments)

    def _abrechnen_und_anwenden() -> Ergebnis:
        with SessionLocal() as db:
            _abrechnen(
                db, v.request_id, usage, aufruf,
                provider=v.provider, preise=v.preise, eingabe=v.eingabe,
                gescheitert=gescheitert,
            )
            # Die Buchung steht für sich: sie gilt auch, wenn das Anwenden
            # gleich scheitert.
            db.commit()
            if aenderungen is None:
                return Ergebnis("fehler")
            user = db.get(User, auftrag.user_id)
            if user is None:
                return Ergebnis("leer")
            ergebnis = _anwenden(db, user, auftrag, v.bereiche, v.kandidaten, aenderungen)
            if abschluss is not None:
                abschluss(db, ergebnis)
            db.commit()
            return ergebnis

    return await asyncio.to_thread(_abrechnen_und_anwenden)


# ── Die Antwort lesen und anwenden ───────────────────────────────────────


def _aenderungen_lesen(argumente: Any) -> list[dict] | None:
    """Die Liste der Änderungen — nachsichtig gelesen.

    Ein JSON-Text statt eines Objekts, eine nackte Liste statt
    ``{"aenderungen": [...]}``, der Name mit Umlaut oder englisch, eine
    einzelne Änderung ohne Hülle: angenommen. Ein leeres Objekt heißt
    „nichts“ — manche Modelle lassen eine leere Liste ganz weg. Ein Objekt
    mit anderem Inhalt ist kein „nichts“: ``None``, der Durchgang gilt als
    gescheitert, und die Marke bleibt stehen. Sonst wäre der Ausschnitt
    gelesen und das, was darin stand, still verloren.
    """
    if isinstance(argumente, str):
        try:
            argumente = json.loads(argumente)
        except (TypeError, ValueError):
            return None
    if isinstance(argumente, dict):
        schluessel = next(
            (s for s in ("aenderungen", "änderungen", "changes") if s in argumente), None
        )
        if schluessel is not None:
            argumente = argumente[schluessel]
            if argumente is None:
                return []
        elif "aktion" in argumente:
            argumente = [argumente]
        elif not argumente:
            return []
    if isinstance(argumente, list):
        return [eintrag for eintrag in argumente if isinstance(eintrag, dict)]
    return None


_AKTIONEN = {
    "neu": "neu", "new": "neu", "anlegen": "neu", "create": "neu",
    "aendern": "aendern", "ändern": "aendern", "aktualisieren": "aendern",
    "update": "aendern", "korrigieren": "aendern",
    "zusammenfuehren": "zusammenfuehren", "zusammenführen": "zusammenfuehren",
    "merge": "zusammenfuehren",
    "vergessen": "vergessen", "forget": "vergessen", "loeschen": "vergessen",
    "löschen": "vergessen", "delete": "vergessen",
}


def _kennungen(roh: Any, praefix: str) -> list[str]:
    """``"E3"``, ``"e3"``, ``3``, ``["E3", "E5"]``, ``"E3, E5"`` — alles dasselbe."""
    if roh is None:
        return []
    if isinstance(roh, (list, tuple)):
        teile = [str(teil) for teil in roh]
    else:
        teile = re.split(r"[,\s;]+", str(roh))
    ergebnis: list[str] = []
    for teil in teile:
        treffer = re.fullmatch(rf"\s*{praefix}?\s*(\d{{1,4}})\s*", teil, re.IGNORECASE)
        if treffer:
            kennung = f"{praefix}{int(treffer.group(1))}"
            if kennung not in ergebnis:
                ergebnis.append(kennung)
    return ergebnis


def _text(roh: Any) -> str:
    text = _flach(roh) if isinstance(roh, str) else ""
    if not text:
        raise _Verworfen("ohne_text")
    return text


#: Was ein freiwilliges Feld höchstens fassen darf (`ai_memory_service`).
_GRENZEN = {
    "titel": ai_memory_service.MAX_TITEL_ZEICHEN,
    "thema": ai_memory_service.MAX_THEMA_ZEICHEN,
}


def _wahlweise(roh: dict, feld: str) -> Any:
    """Ein freiwilliges Textfeld: fehlt es oder ist es leer, bleibt es, wie es ist.

    Zu lang heißt ebenso „nicht angegeben“, wie beim Altbestand
    (`ai_gedaechtnis_altbestand._kurz`): ein Satz, den das Modell ins Thema
    schrieb, verwarf sonst die ganze Änderung samt ihrem Text — bei gelesenem
    Ausschnitt.
    """
    wert = roh.get(feld)
    if not isinstance(wert, str):
        return ai_memory_service.UNVERAENDERT
    wert = _flach(wert)
    if not wert or len(wert) > _GRENZEN[feld]:
        return ai_memory_service.UNVERAENDERT
    return wert


def _art(roh: dict) -> Any:
    wert = roh.get("art")
    if not isinstance(wert, str):
        return ai_memory_service.UNVERAENDERT
    wert = wert.strip().casefold()
    return wert if wert in ai_memory_service.ARTEN else ai_memory_service.UNVERAENDERT


def _wichtigkeit(roh: dict) -> int | None:
    wert = roh.get("wichtigkeit")
    try:
        zahl = int(str(wert).strip())
    except (TypeError, ValueError):
        return None
    return min(5, max(1, zahl))


@dataclass
class _Lage:
    """Was beim Anwenden einer einzelnen Änderung bekannt sein muss."""

    auftrag: Auftrag
    bereiche: dict[str, Bereich]
    kandidaten: dict[str, Kandidat]
    zeilen: dict[str, Zeile]
    verbraucht: set[str] = field(default_factory=set)


def _anwenden(
    db: Session,
    user: User,
    auftrag: Auftrag,
    bereiche: list[Bereich],
    kandidaten: list[Kandidat],
    aenderungen: list[dict],
) -> Ergebnis:
    lage = _Lage(
        auftrag=auftrag,
        bereiche={b.kennung: b for b in bereiche},
        kandidaten={k.kennung: k for k in kandidaten},
        zeilen={z.nummer: z for z in auftrag.zeilen if z.nummer},
    )
    ergebnis = Ergebnis("ok")
    # `DisSidecarError` bleibt mit Absicht ungefangen: antwortet die
    # Verschlüsselung nicht, scheitert jede Änderung gleich. Dann ist der
    # ganze Durchgang gescheitert und kommt später wieder — statt dreißig
    # Änderungen als verworfen zu zählen, die nie eine Chance hatten.
    for roh in aenderungen[:MAX_AENDERUNGEN]:
        try:
            with db.begin_nested():
                art = _einzeln(db, user, roh, lage)
        except (_Verworfen, HTTPException) as exc:
            ergebnis.verworfen += 1
            grund = exc.args[0] if isinstance(exc, _Verworfen) else exc.status_code
            logger.info("Gedaechtnis: Aenderung verworfen grund=%s", grund)
            continue
        if art == "neu":
            ergebnis.neu += 1
        elif art == "aendern":
            ergebnis.geaendert += 1
        elif art == "zusammenfuehren":
            ergebnis.zusammengefuehrt += 1
        elif art == "vergessen":
            ergebnis.vergessen += 1
    ergebnis.verworfen += max(0, len(aenderungen) - MAX_AENDERUNGEN)
    return ergebnis


def _quelle_ref(lage: _Lage, belege: list[Zeile]) -> str | None:
    if lage.auftrag.quelle_ref:
        return lage.auftrag.quelle_ref
    erste = next((zeile.nachricht_id for zeile in belege if zeile.nachricht_id), None)
    return f"nachricht:{erste}" if erste else None


def _fast_gleich(db: Session, lage: _Lage, bereich: Bereich, text: str, titel: Any) -> bool:
    """Steht dasselbe schon da? Verglichen wird mit dem Bestand, den er sah."""
    ids = [k.id for k in lage.kandidaten.values() if k.bereich.identity == bereich.identity]
    if not ids:
        return False
    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.id.in_(ids)).all()
    probe = f"{titel}: {text}" if isinstance(titel, str) else text
    werte = ai_memory_service.aehnlichkeit_zu_texten(db, rows, [probe])
    return any(wert is not None and wert >= FAST_GLEICH_AB for wert in werte)


def _einzeln(db: Session, user: User, roh: dict, lage: _Lage) -> str:
    aktion = _AKTIONEN.get(str(roh.get("aktion") or "").strip().casefold())
    if aktion is None:
        raise _Verworfen("aktion")
    belege = [lage.zeilen[n] for n in _kennungen(roh.get("beleg"), "N") if n in lage.zeilen]
    if not belege:
        raise _Verworfen("ohne_beleg")
    vom_menschen = any(zeile.vom_menschen for zeile in belege)
    wichtigkeit = _wichtigkeit(roh)

    if aktion == "neu":
        bereich = next(
            (lage.bereiche[k] for k in _kennungen(roh.get("bereich"), "B") if k in lage.bereiche),
            None,
        )
        if bereich is None:
            raise _Verworfen("bereich")
        if bereich.scope == "team" and not vom_menschen:
            raise _Verworfen("team_ohne_menschen")
        text = _text(roh.get("text"))
        titel = _wahlweise(roh, "titel")
        if _fast_gleich(db, lage, bereich, text, titel):
            raise _Verworfen("doppelt")
        thema = _wahlweise(roh, "thema")
        art = _art(roh)
        ai_memory_service.erinnerung_anlegen(
            db,
            user=user,
            scope=bereich.scope,
            server_id=bereich.server_id,
            team_id=bereich.team_id,
            text=text,
            titel=None if titel is ai_memory_service.UNVERAENDERT else titel,
            thema=None if thema is ai_memory_service.UNVERAENDERT else thema,
            art=None if art is ai_memory_service.UNVERAENDERT else art,
            wichtigkeit=wichtigkeit or 3,
            quelle="gespraech",
            quelle_ref=_quelle_ref(lage, belege),
            origin="ai",
            commit=False,
        )
        return "neu"

    ziel = next(
        (lage.kandidaten[k] for k in _kennungen(roh.get("eintrag"), "E") if k in lage.kandidaten),
        None,
    )
    if ziel is None or ziel.kennung in lage.verbraucht:
        raise _Verworfen("eintrag")
    if ziel.bereich.scope == "team" and not vom_menschen:
        raise _Verworfen("team_ohne_menschen")

    if aktion == "vergessen":
        if not vom_menschen:
            raise _Verworfen("vergessen_ohne_menschen")
        ai_memory_service.erinnerung_vergessen(db, user=user, entry_id=ziel.id, commit=False)
        lage.verbraucht.add(ziel.kennung)
        return "vergessen"

    if aktion == "aendern":
        text = roh.get("text")
        ai_memory_service.erinnerung_aendern(
            db,
            user=user,
            entry_id=ziel.id,
            text=_text(text) if isinstance(text, str) and text.strip() else None,
            titel=_wahlweise(roh, "titel"),
            thema=_wahlweise(roh, "thema"),
            art=_art(roh),
            wichtigkeit=wichtigkeit,
            erwartete_fassung=ziel.fassung,
            von="ai",
            grund="aktualisiert",
            ueberschreibt_mensch=vom_menschen,
            commit=False,
        )
        lage.verbraucht.add(ziel.kennung)
        return "aendern"

    # zusammenfuehren
    andere = [
        lage.kandidaten[k]
        for k in _kennungen(roh.get("aufgenommen"), "E")
        if k in lage.kandidaten and k != ziel.kennung and k not in lage.verbraucht
    ]
    if not andere:
        raise _Verworfen("nichts_aufzunehmen")
    if any(k.bereich.identity != ziel.bereich.identity for k in andere):
        raise _Verworfen("bereichsgrenze")
    if (ziel.vom_menschen or any(k.vom_menschen for k in andere)) and not vom_menschen:
        raise _Verworfen("mensch_ohne_beleg")
    ai_memory_service.erinnerung_aendern(
        db,
        user=user,
        entry_id=ziel.id,
        text=_text(roh.get("text")),
        titel=_wahlweise(roh, "titel"),
        thema=_wahlweise(roh, "thema"),
        art=_art(roh),
        wichtigkeit=wichtigkeit,
        erwartete_fassung=ziel.fassung,
        von="ai",
        grund="zusammengefuehrt",
        ueberschreibt_mensch=vom_menschen,
        commit=False,
    )
    for kandidat in andere:
        ai_memory_service.erinnerung_aufnehmen(
            db, user=user, ziel_id=ziel.id, quelle_id=kandidat.id,
            ueberschreibt_mensch=vom_menschen, commit=False,
        )
        lage.verbraucht.add(kandidat.kennung)
    lage.verbraucht.add(ziel.kennung)
    return "zusammenfuehren"


__all__ = [
    "Auftrag",
    "Ergebnis",
    "Zeile",
    "aus_mitschrift",
    "durchgang",
    "faellige_starten",
    "gedaechtnis_anbieter",
    "nach_dem_lauf",
    "schreiben",
    "starten",
    "stichwort",
    "vormerken",
]
