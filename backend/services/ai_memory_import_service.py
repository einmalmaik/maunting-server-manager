"""Import: Erinnerungen aus einem eingefügten Text, gelesen vom Gedächtnismodell.

Gedächtnis v2, Stufe 3. Bis hierher zerlegte eine Heuristik ohne Modell den
Text nach Zeilen: jede Zeile ein Fakt, der Name aus ihren ersten Wörtern. Eine
Liste aus einer anderen KI überstand das, eine erzählte Lebensgeschichte
zerfiel in Kapitelüberschriften und Satzhälften. Betreiberentscheid vom
06.10.2026: der Text geht einmal an das Gedächtnismodell
(`ai_gedaechtnis_schreiber.gedaechtnis_anbieter`), das ihn liest wie der
Hintergrundschreiber ein Gespräch, und schreibt Sätze. (Ein langer Text geht
in Teilen; jeder Teil trägt den Anfang des Textes als Zusammenhang mit.)

Zwei Schritte:

* `vorschau` liest und schreibt **nichts**. Der Text geht redigiert und in
  Teilen an den Anbieter — je Teil mit der Überschrift, unter der er steht,
  dem Anfang des Textes und dem Bestand des Zielbereichs, der dazu passt.
  Jeder Teil ist ein Aufruf, gebucht beim Benutzer mit ``zweck='gedaechtnis'``.
* `uebernehmen` schreibt, was der Benutzer ausgewählt hat — über
  `erinnerung_anlegen` und `erinnerung_aendern`, also mit denselben Rechten,
  derselben Verschlüsselung und demselben Kontingent wie eine Erinnerung von
  Hand: 100, eine eingestellte Zahl, unbegrenzt; 0 heißt gesperrt.

Herkunft ``user``: der Benutzer hat jeden Satz gesehen und bestätigt.
Inhalte stehen in keinem Log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiMemoryEntry, AiProvider, Server, Team, User
from schemas.ai_memory import (
    MAX_IMPORT_ERSETZT,
    MAX_IMPORT_ITEMS,
    AiMemoryImportBisher,
    AiMemoryImportItem,
    AiMemoryImportPreviewItem,
    AiMemoryImportPreviewRequest,
    AiMemoryImportPreviewResponse,
    AiMemoryImportRequest,
    AiMemoryImportResponse,
    AiMemoryImportSkipped,
)
from services import ai_gedaechtnis_schreiber as schreiber
from services import ai_lage, ai_memory_service, audit_service
from services.ai_provider_service import resolve_api_key
from services.ai_redaction import enthaelt_zugangsdaten, redact_sensitive_text
from services.openai_compatible_adapter import StreamUsage

logger = logging.getLogger(__name__)

#: Höchstens so viele Zeichen gehen in einem Aufruf an das Modell. Ein Teil
#: endet an einem Absatz; was darüber hinausgeht, kommt in den nächsten.
MAX_TEIL_ZEICHEN = 16_000
#: Der Anfang des Textes geht als Zusammenhang mit jedem weiteren Teil: dort
#: steht meist, wer „ich“ ist und wann er geboren wurde.
MAX_ANFANG_ZEICHEN = 1_500
#: Wieviele Teile gleichzeitig beim Anbieter sind.
GLEICHZEITIG = 4
#: Geschätzte Ausgabe je Teil für die Reservierung.
AUSGABE_SCHAETZUNG = 4_000
#: Länger ist eine Überschrift nicht. Ein längerer Absatz ohne Satzzeichen am
#: Ende ist ein Satz, dem der Punkt fehlt.
MAX_UEBERSCHRIFT = 100
#: Ab hier steht ein vorgeschlagener Satz schon da — dieselbe Grenze wie beim
#: Schreiber (`ai_gedaechtnis_schreiber.FAST_GLEICH_AB`).
SCHON_DA_AB = schreiber.FAST_GLEICH_AB

#: Woran die Schlusszeile des Auszugs-Prompts zu erkennen ist („Importiert
#: aus: Gemini“), in beiden Sprachen des Prompts.
_QUELLE_ANFAENGE = ("importiert aus", "imported from")
_SATZENDE = (".", "!", "?", "…", ":", ";", ",", '"', "“", "”", "»", "«", ")")

WERKZEUG_NAME = "erinnerungen_eintragen"

#: Ein Formular wie beim Schreiber: ausgeführt wird es nie, gelesen werden die
#: Argumente.
WERKZEUG: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WERKZEUG_NAME,
        "description": (
            "Trage die Erinnerungen aus diesem Teil des Textes ein. Eine leere "
            "Liste, wenn er nichts Dauerhaftes enthält."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "erinnerungen": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {
                                "type": "string",
                                "description": "Ein bis fünf Sätze, ohne den Text verständlich.",
                            },
                            "titel": {"type": "string", "description": "Zwei bis sechs Wörter."},
                            "thema": {
                                "type": "string",
                                "description": "Ein oder zwei Wörter; vorhandene Themen bevorzugen.",
                            },
                            "art": {"type": "string", "enum": list(ai_memory_service.ARTEN)},
                            "wichtigkeit": {"type": "integer", "minimum": 1, "maximum": 5},
                            "ersetzt": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "E-Nummern aus dem Bestand, die in dieser Erinnerung aufgehen."
                                ),
                            },
                        },
                        "required": ["text", "titel", "thema"],
                    },
                },
            },
            "required": ["erinnerungen"],
        },
    },
}

SYSTEMPROMPT = """Du führst das Gedächtnis von Singra, der KI eines Gameserver-Panels. Der Benutzer hat einen Text eingefügt, damit Singra sich merkt, was darin steht: meist die Antwort einer anderen KI auf die Frage, was sie über ihn weiß, manchmal eigene Notizen oder eine Erzählung aus seinem Leben. Du liest einen Teil davon und trägst ein, was Singra dauerhaft wissen soll. Was du einträgst, steht ihr in späteren Gesprächen zur Verfügung – auch in einem Jahr, ohne diesen Text.

„Ich“ im Text ist der Benutzer, „die Person“ oder „der Nutzer“ in der Antwort einer anderen KI ebenso – außer der Text sagt ausdrücklich etwas anderes. Das Ziel sagt, wohin die Erinnerungen gehen: in Server-, Team- oder Panelwissen gehört Betriebswissen, nichts über einzelne Menschen.

Eine gute Erinnerung
- sind ein bis fünf Sätze, die ohne den Text verständlich sind, über den Menschen in der dritten Person: „Der Benutzer ist in Leipzig aufgewachsen und lebt seit 2019 in Köln.“
- löst Bezüge auf: „er“, „dort“ und „damals“ werden zu dem, worauf sie zeigen. Steht das weder im Teil noch am Anfang des Textes, lässt du es weg, statt zu raten.
- fasst zusammen, was zusammengehört: eine Erinnerung je Sache, nicht eine je Satz und nicht zwei Sachen in einer.
- gilt über den Moment hinaus: Herkunft und Lebensstationen, Menschen und Beziehungen, Vorlieben, Gewohnheiten, Pläne, Anweisungen an eine KI.
- nennt Zeit absolut, wo der Text sie hergibt: „mit zwölf“ bei Geburtsjahr 1990 wird „um 2002“. Gibt er sie nicht her, erfindest du keine.
- lässt einen Plan einen Plan sein: ob etwas geschehen ist, steht erst fest, wenn der Text es sagt.
- sagt bei einem Bild, was es über den Menschen bedeutet, wenn das dauerhaft ist: aus „Der Wald ist mein Wohnzimmer“ wird „Der Benutzer verbringt viel Zeit in der Natur und fühlt sich dort zu Hause.“ Sonst trägst du es nicht ein.

Überschriften und Kapitelnamen sind Zusammenhang, nie selbst eine Erinnerung. Einleitungen, Floskeln und Quellenangaben („Importiert aus …“) ebenso wenig. Zugangsdaten, Passwörter, Schlüssel und Token stehen nie im Gedächtnis.

Der Bestand zeigt, was Singra zu diesem Teil schon weiß. Sagt eine Erinnerung dasselbe wie ein Eintrag oder mehr, nennst du ihn unter ersetzt und schreibst den ganzen gültigen Stand – auch mehrere Einträge, die in ihr aufgehen, etwa Bruchstücke aus einem früheren Import. Was schon genau so dasteht, lässt du weg.

Titel: zwei bis sechs Wörter. Thema: ein oder zwei Wörter, gleiche Dinge gleich benannt – vorhandene Themen zuerst. Art: fakt, vorliebe, anweisung, ereignis, plan, beziehung oder wissen. Wichtigkeit: 5 für den Kern (Name, Familie, feste Anweisungen an Singra), 3 für das Übliche, 1 für Nebensachen.

Schreib in der Sprache des Textes. Steht in diesem Teil nichts Dauerhaftes, gibst du eine leere Liste zurück."""


# ── Teile ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, repr=False)
class Teil:
    text: str
    #: Die letzte Überschrift vor dem Teil — ``None``, wenn er selbst mit
    #: einer beginnt oder keine davor steht.
    ueberschrift: str | None = None


def _ist_ueberschrift(absatz: str) -> bool:
    """Eine Zeile für sich, kurz, ohne Satzzeichen am Ende — oder Markdown."""
    if "\n" in absatz or len(absatz) > MAX_UEBERSCHRIFT:
        return False
    if absatz.startswith("#"):
        return True
    return not absatz.rstrip("*_ ").endswith(_SATZENDE)


def _zerteilen(absatz: str) -> list[str]:
    """Ein Absatz über der Grenze: an Zeilen geschnitten, sonst hart."""
    if len(absatz) <= MAX_TEIL_ZEICHEN:
        return [absatz]
    # Zeilen sammeln und einmal zusammenfügen: ein Absatz aus 50.000 kurzen
    # Zeilen baute vorher je Zeile die ganze Zeichenkette neu.
    stuecke: list[str] = []
    jetzt: list[str] = []
    laenge = 0
    for zeile in absatz.split("\n"):
        while len(zeile) > MAX_TEIL_ZEICHEN:
            if jetzt:
                stuecke.append("\n".join(jetzt))
                jetzt, laenge = [], 0
            stuecke.append(zeile[:MAX_TEIL_ZEICHEN])
            zeile = zeile[MAX_TEIL_ZEICHEN:]
        if jetzt and laenge + 1 + len(zeile) > MAX_TEIL_ZEICHEN:
            stuecke.append("\n".join(jetzt))
            jetzt, laenge = [], 0
        laenge += len(zeile) + (1 if jetzt else 0)
        jetzt.append(zeile)
    if jetzt:
        stuecke.append("\n".join(jetzt))
    return stuecke


def teile(text: str) -> list[Teil]:
    """Der Text in Teilen bis `MAX_TEIL_ZEICHEN`, geschnitten an Absätzen.

    Eine Überschrift beginnt einen neuen Teil, sobald der laufende halb voll
    ist — so bleibt ein Kapitel möglichst beisammen. Beginnt ein Teil mitten
    in einem Kapitel, nennt ``ueberschrift`` es: das Modell weiß dann, wovon
    die Rede ist.
    """
    normal = text.replace("\r\n", "\n").replace("\r", "\n")
    absaetze = [absatz.strip() for absatz in re.split(r"\n\s*\n", normal) if absatz.strip()]
    ergebnis: list[Teil] = []
    laufend: list[str] = []
    groesse = 0
    kopf: str | None = None
    letzte: str | None = None
    for absatz in absaetze:
        ueberschrift = _ist_ueberschrift(absatz)
        for stueck in _zerteilen(absatz):
            voll = laufend and groesse + 2 + len(stueck) > MAX_TEIL_ZEICHEN
            kapitel = ueberschrift and laufend and groesse >= MAX_TEIL_ZEICHEN // 2
            if voll or kapitel:
                ergebnis.append(Teil(text="\n\n".join(laufend), ueberschrift=kopf))
                laufend, groesse = [], 0
            if not laufend:
                kopf = None if ueberschrift else letzte
            groesse += len(stueck) + (2 if laufend else 0)
            laufend.append(stueck)
        if ueberschrift:
            letzte = " ".join(absatz.strip("#*_ ").split()) or None
    if laufend:
        ergebnis.append(Teil(text="\n\n".join(laufend), ueberschrift=kopf))
    return ergebnis


def _quelle(text: str) -> str | None:
    """„Importiert aus: Gemini“ — die Schlusszeile des Auszugs-Prompts.

    Zeile für Zeile mit Zeichenketten, ohne regulären Ausdruck. Ein Ausdruck
    mit ``re.MULTILINE`` über den ganzen Text, dessen Leerraum-Teile
    Zeilenumbrüche mitnahmen, kostete in der Prüfung am 07.10.2026 bei 2.000
    leeren Zeilen 15 s, bei 100.000 Wochen — und er hält dabei den ganzen
    Prozess an, nicht nur diese Anfrage. Gilt die letzte Zeile, die passt; ein
    Name mit Zugangsdaten gilt nicht.
    """
    gefunden: str | None = None
    for zeile in text.splitlines():
        kern = zeile.lstrip(" \t*_>#")
        for anfang in _QUELLE_ANFAENGE:
            if kern[: len(anfang)].casefold() != anfang:
                continue
            rest = kern[len(anfang):].lstrip(" \t")
            if rest.startswith(":"):
                rest = rest[1:]
            name = rest.strip(" \t*_.")
            name = "".join(zeichen for zeichen in name if zeichen not in "<>\"'`*_").strip(" .")
            if name:
                gefunden = name[:40]
            break
    if gefunden is None or enthaelt_zugangsdaten(gefunden):
        return None
    return gefunden


# ── Vorschau ─────────────────────────────────────────────────────────────


@dataclass(repr=False)
class _Lesung:
    """Ein Teil, fertig für den Anbieter."""

    messages: list[dict]
    eingabe: int
    kandidaten: dict[str, schreiber.Kandidat]


@dataclass(repr=False)
class _Vorbereitet:
    user_id: int
    provider: AiProvider
    api_key: str | None
    modell: str
    preise: tuple[int | None, int | None, int | None]
    lesungen: list[_Lesung]
    frei: int | None
    quelle: str | None
    memory_enabled: bool


def _zielname(db: Session, user: User, scope: str, ziel: ai_memory_service.Importziel) -> str:
    if scope == "user":
        return "das persönliche Gedächtnis des Benutzers"
    if scope in ("server", "server_shared"):
        server = db.get(Server, ziel.server_id) if ziel.server_id else None
        name = server.name if server is not None and server.name else str(ziel.server_id)
        if scope == "server":
            return f"die eigenen Notizen des Benutzers zum Server „{name}“"
        return f"das Wissen des Servers „{name}“, das alle lesen, die ihn betreuen"
    if scope == "team":
        from services import team_service

        team = db.get(Team, ziel.team_id) if ziel.team_id else None
        name = team_service.ansprechbarer_name(db, user, team) if team is not None else str(ziel.team_id)
        return f"das Wissen des Teams „{name}“, das alle im Team lesen"
    return "das Wissen des Panels, das alle lesen"


def _bestandszeile(kandidat: schreiber.Kandidat) -> str:
    merkmale = ["vom Benutzer" if kandidat.vom_menschen else "von Singra"]
    if kandidat.thema:
        merkmale.append(f"Thema {kandidat.thema}")
    titel = f" {kandidat.titel}:" if kandidat.titel else ""
    return f"{kandidat.kennung} ({', '.join(merkmale)}){titel} {schreiber._kuerzen(kandidat.text, 600)}"


def _nachricht(
    *,
    zielname: str,
    frei: int | None,
    themen: list[str],
    kandidaten: list[schreiber.Kandidat],
    teil: Teil,
    nummer: int,
    anzahl: int,
    anfang: str | None,
    zone_name: str,
    jetzt: datetime,
) -> str:
    lokal = jetzt.astimezone(ZoneInfo(zone_name))
    platz = "" if frei is None else (
        " — voll: nur Einträge ersetzen" if frei == 0 else f" — noch {frei} frei"
    )
    abschnitte = [
        f"Heute: {ai_lage.WOCHENTAGE[lokal.weekday()]}, {lokal:%d.%m.%Y} ({zone_name}).",
        f"Ziel: {zielname}{platz}",
    ]
    if themen:
        abschnitte.append("Vorhandene Themen: " + ", ".join(schreiber._flach(t) for t in themen))
    if kandidaten:
        abschnitte.append(
            "Bestand (was Singra dazu schon weiß):\n"
            + "\n".join(_bestandszeile(k) for k in kandidaten)
        )
    else:
        abschnitte.append("Bestand: noch nichts, was zu diesem Teil passt.")
    if anfang:
        abschnitte.append(
            "Anfang des Textes (nur Zusammenhang, er wird als eigener Teil gelesen):\n" + anfang
        )
    kopf = f"Teil {nummer} von {anzahl}" if anzahl > 1 else "Der Text"
    if teil.ueberschrift:
        kopf += f", steht unter der Überschrift „{schreiber._flach(teil.ueberschrift)}“"
    abschnitte.append(f"{kopf}:\n{teil.text}")
    return "\n\n".join(abschnitte)


def _nicht_gesperrt(ziel: ai_memory_service.Importziel) -> None:
    """0 heißt gesperrt — für neue Erinnerungen und ebenso für Änderungen.

    Wie beim Schreiber, der einen Bereich mit Kontingent 0 gar nicht erst
    anbietet: gesperrt ist nicht „voll“, sondern „kein Gedächtnis hier“.
    """
    if ziel.grenze == 0:
        raise HTTPException(
            status_code=409,
            detail="In diesem Bereich ist das Gedächtnis für deine Rolle gesperrt.",
        )


def _keine_einrichtung() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            "Für das Gedächtnis ist kein KI-Modell erreichbar. Ein Betreiber richtet "
            "es unter KI → Anbieter ein."
        ),
    )


def _vorbereiten(
    user_id: int, request: AiMemoryImportPreviewRequest, jetzt: datetime
) -> _Vorbereitet:
    """Ziel, Zugang und Teile — in einer Sitzung, bevor der Anbieter etwas sieht."""
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        ziel = ai_memory_service.importziel(
            db, user, request.scope, request.server_id, request.team_id
        )
        _nicht_gesperrt(ziel)
        gewaehlt = schreiber.gedaechtnis_anbieter(db, user, None)
        if gewaehlt is None:
            raise _keine_einrichtung()
        provider, modell, preise = gewaehlt
        api_key = resolve_api_key(db, provider, user.id)
        if provider.requires_api_key and not api_key:
            raise _keine_einrichtung()
        redigiert = redact_sensitive_text(request.raw_text)
        stuecke = teile(redigiert)
        if not stuecke:
            raise HTTPException(status_code=422, detail="Der Text ist leer.")
        zielname = _zielname(db, user, request.scope, ziel)
        bereich = schreiber.Bereich(
            kennung="B1", scope=request.scope, identity=ziel.identity, name=zielname,
            server_id=ziel.server_id, team_id=ziel.team_id, frei=ziel.frei,
        )
        themen = schreiber._themen(db, [bereich])
        zone_name = ai_lage.zone_des_benutzers(user)
        anfang = (
            schreiber._kuerzen(stuecke[0].text, MAX_ANFANG_ZEICHEN) if len(stuecke) > 1 else None
        )
        lesungen: list[_Lesung] = []
        for nummer, teil in enumerate(stuecke, start=1):
            absaetze = [absatz for absatz in teil.text.split("\n\n") if absatz.strip()]
            kandidaten = schreiber._bestand(db, [bereich], absaetze)
            inhalt = _nachricht(
                zielname=zielname, frei=ziel.frei, themen=themen, kandidaten=kandidaten,
                teil=teil, nummer=nummer, anzahl=len(stuecke),
                anfang=anfang if nummer > 1 else None, zone_name=zone_name, jetzt=jetzt,
            )
            lesungen.append(_Lesung(
                messages=[
                    {"role": "system", "content": SYSTEMPROMPT},
                    {"role": "user", "content": inhalt},
                ],
                eingabe=max(1, (len(SYSTEMPROMPT) + len(inhalt)) // 4),
                kandidaten={k.kennung: k for k in kandidaten},
            ))
        memory_enabled = (
            request.scope not in ai_memory_service.PERSOENLICHE_SCOPES
            or ai_memory_service.preference(db, user.id)
        )
        db.refresh(provider)
        db.expunge(provider)
    return _Vorbereitet(
        user_id=user_id, provider=provider, api_key=api_key, modell=modell, preise=preise,
        lesungen=lesungen, frei=ziel.frei,
        quelle=_quelle(redigiert) or request.source_provider,
        memory_enabled=memory_enabled,
    )


def _reservieren(v: _Vorbereitet, eingabe: int) -> Any | None:
    with SessionLocal() as db:
        user = db.get(User, v.user_id)
        if user is None:
            return None
        return schreiber._reservieren(
            db, user, v.provider, v.modell, v.preise, eingabe, AUSGABE_SCHAETZUNG
        )


def _abrechnen(v: _Vorbereitet, request_id: Any, usage, aufruf, eingabe: int, gescheitert: bool) -> None:
    with SessionLocal() as db:
        schreiber._abrechnen(
            db, request_id, usage, aufruf,
            provider=v.provider, preise=v.preise, eingabe=eingabe, gescheitert=gescheitert,
        )
        db.commit()


def _erinnerungen_lesen(argumente: Any) -> list[dict] | None:
    """Die Liste der Erinnerungen — nachsichtig gelesen wie beim Schreiber.

    JSON-Text, nackte Liste, englischer Name, eine einzelne Erinnerung ohne
    Hülle: angenommen. Ein leeres Objekt heißt „nichts“. Ein Objekt mit
    anderem Inhalt ist kein „nichts“, sondern unlesbar (``None``) — sonst
    zählte ein gescheiterter Teil als gelesen.
    """
    if isinstance(argumente, str):
        try:
            argumente = json.loads(argumente)
        except (TypeError, ValueError):
            return None
    if isinstance(argumente, dict):
        schluessel = next(
            (s for s in ("erinnerungen", "memories", "eintraege", "einträge", "items") if s in argumente),
            None,
        )
        if schluessel is not None:
            argumente = argumente[schluessel]
            if argumente is None:
                return []
        elif "text" in argumente:
            argumente = [argumente]
        elif not argumente:
            return []
    if isinstance(argumente, list):
        return [eintrag for eintrag in argumente if isinstance(eintrag, dict)]
    return None


async def _teil_lesen(
    v: _Vorbereitet, lesung: _Lesung, datenbank: asyncio.Lock
) -> list[dict] | str:
    """Ein Teil, ein Aufruf: die Erinnerungen, ``"kontingent"`` oder ``"fehler"``.

    Nebeneinander laufen nur die Aufrufe beim Anbieter. Reservieren und
    Abrechnen gehen hintereinander (``datenbank``): sie buchen auf dasselbe
    Kontingent, und nebeneinander verlöre der zweite gegen die Sperre des
    ersten nur Zeit.
    """
    async with datenbank:
        request_id = await asyncio.to_thread(_reservieren, v, lesung.eingabe)
    if request_id is None:
        return "kontingent"
    try:
        usage, aufruf, gescheitert = await schreiber._formular(
            v.provider, v.api_key, v.modell, lesung.messages, WERKZEUG
        )
    except BaseException:
        # Auch bei einem Abbruch (Neustart, abgebrochene Anfrage) wird die
        # Reservierung geschlossen — als gescheitert, also ohne Kosten. Offen
        # gelassen buchte sie das Aufräumen beim nächsten Start in voller
        # Schätzung.
        async with datenbank:
            await asyncio.to_thread(
                _abrechnen, v, request_id, StreamUsage(), None, lesung.eingabe, True
            )
        raise
    roh = None if gescheitert or aufruf is None else _erinnerungen_lesen(aufruf.arguments)
    async with datenbank:
        await asyncio.to_thread(
            _abrechnen, v, request_id, usage, aufruf, lesung.eingabe, gescheitert
        )
    return "fehler" if roh is None else roh


def _kurz(roh: dict, feld: str, grenze: int) -> str | None:
    wert = roh.get(feld)
    if not isinstance(wert, str):
        return None
    wert = schreiber._flach(wert)
    return wert if wert and len(wert) <= grenze else None


def _vergleichsform(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass(repr=False)
class _Vorschlag:
    text: str
    titel: str | None
    thema: str | None
    art: str | None
    wichtigkeit: int
    ersetzt: list[schreiber.Kandidat]


def _vorschau_bauen(v: _Vorbereitet, ergebnisse: list[list[dict] | str]) -> AiMemoryImportPreviewResponse:
    """Aus den Antworten der Teile wird die Vorschau — ohne zu schreiben."""
    if not any(isinstance(ergebnis, list) for ergebnis in ergebnisse):
        if "kontingent" in ergebnisse:
            raise HTTPException(
                status_code=409,
                detail="Dein KI-Kontingent reicht für diesen Import gerade nicht.",
            )
        raise HTTPException(
            status_code=502,
            detail="Das Gedächtnismodell hat den Text nicht gelesen. Versuch es später noch einmal.",
        )
    vorschlaege: list[_Vorschlag] = []
    gesperrt = 0
    gesehen: set[str] = set()
    vergeben: set[str] = set()
    for lesung, ergebnis in zip(v.lesungen, ergebnisse):
        if not isinstance(ergebnis, list):
            continue
        for roh in ergebnis:
            text = schreiber._flach(roh.get("text")) if isinstance(roh.get("text"), str) else ""
            if not text:
                continue
            text = schreiber._kuerzen(text, ai_memory_service.MAX_TEXT_ZEICHEN)
            titel = _kurz(roh, "titel", ai_memory_service.MAX_TITEL_ZEICHEN)
            if enthaelt_zugangsdaten(text) or (titel and enthaelt_zugangsdaten(titel)):
                gesperrt += 1
                continue
            form = _vergleichsform(text)
            if form in gesehen:
                continue
            gesehen.add(form)
            ersetzt: list[schreiber.Kandidat] = []
            for kennung in schreiber._kennungen(roh.get("ersetzt"), "E"):
                kandidat = lesung.kandidaten.get(kennung)
                if kandidat is not None and kandidat.id not in vergeben and kandidat not in ersetzt:
                    ersetzt.append(kandidat)
                # Mehr nimmt die Übernahme nicht an; was darüber liegt, bleibt
                # stehen und kann später zusammengeführt werden.
                if len(ersetzt) >= MAX_IMPORT_ERSETZT:
                    break
            vergeben.update(kandidat.id for kandidat in ersetzt)
            art = roh.get("art")
            vorschlaege.append(_Vorschlag(
                text=text,
                titel=titel,
                thema=_kurz(roh, "thema", ai_memory_service.MAX_THEMA_ZEICHEN),
                art=art.strip().casefold() if isinstance(art, str) and art.strip().casefold() in ai_memory_service.ARTEN else None,
                wichtigkeit=schreiber._wichtigkeit(roh) or 3,
                ersetzt=ersetzt,
            ))

    bekannt = 0
    with SessionLocal() as db:
        kandidaten = {k.id: k for lesung in v.lesungen for k in lesung.kandidaten.values()}
        rows = (
            db.query(AiMemoryEntry).filter(AiMemoryEntry.id.in_(list(kandidaten))).all()
            if kandidaten else []
        )
        bestand = {_vergleichsform(k.text) for k in kandidaten.values()}
        neu = [p for p in vorschlaege if not p.ersetzt]
        werte = ai_memory_service.aehnlichkeit_je_text(db, rows, [p.text for p in neu])
        schon_da = {
            id(p) for p, wert in zip(neu, werte)
            if _vergleichsform(p.text) in bestand or (wert is not None and wert >= SCHON_DA_AB)
        }
    behalten: list[_Vorschlag] = []
    for p in vorschlaege:
        gleich = len(p.ersetzt) == 1 and _vergleichsform(p.ersetzt[0].text) == _vergleichsform(p.text)
        if id(p) in schon_da or gleich:
            bekannt += 1
            continue
        behalten.append(p)

    return AiMemoryImportPreviewResponse(
        detected_source=v.quelle,
        items=[
            AiMemoryImportPreviewItem(
                text=p.text, titel=p.titel, thema=p.thema, art=p.art, wichtigkeit=p.wichtigkeit,
                ersetzt=[
                    AiMemoryImportBisher(id=k.id, fassung=k.fassung, text=k.text, titel=k.titel)
                    for k in p.ersetzt
                ],
            )
            for p in behalten[:MAX_IMPORT_ITEMS]
        ],
        total_detected=len(behalten),
        total_known=bekannt,
        total_secrets_blocked=gesperrt,
        unread_parts=sum(1 for ergebnis in ergebnisse if not isinstance(ergebnis, list)),
        available_slots=v.frei,
        memory_enabled=v.memory_enabled,
    )


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


#: Wessen Import gerade gelesen wird. Einer je Benutzer und Prozess: ein Text
#: von 100.000 Zeichen sind sieben Aufrufe, und zehn Fenster mit demselben
#: Text wären siebzig — auf dasselbe Kontingent, für dieselbe Vorschau.
_LAUFENDE: set[int] = set()


async def vorschau(
    user_id: int, request: AiMemoryImportPreviewRequest
) -> AiMemoryImportPreviewResponse:
    """Liest den Text mit dem Gedächtnismodell und schreibt nichts.

    Datenbank und Sidecar in Threads, die Aufrufe beim Anbieter nebeneinander
    (höchstens `GLEICHZEITIG`). Ein Teil, den der Anbieter nicht liest, fehlt
    in der Vorschau und wird gezählt; liest er keinen, gibt es eine Absage.
    Läuft für denselben Benutzer schon einer, gibt es 429.
    """
    if user_id in _LAUFENDE:
        raise HTTPException(
            status_code=429,
            detail="Ein Import wird gerade gelesen. Warte, bis er fertig ist.",
        )
    _LAUFENDE.add(user_id)
    try:
        return await _vorschau(user_id, request)
    finally:
        _LAUFENDE.discard(user_id)


async def _vorschau(
    user_id: int, request: AiMemoryImportPreviewRequest
) -> AiMemoryImportPreviewResponse:
    v = await asyncio.to_thread(_vorbereiten, user_id, request, _jetzt())
    schranke = asyncio.Semaphore(GLEICHZEITIG)
    datenbank = asyncio.Lock()

    async def _einer(lesung: _Lesung) -> list[dict] | str:
        async with schranke:
            try:
                return await _teil_lesen(v, lesung, datenbank)
            except Exception as exc:  # noqa: BLE001 - ein Teil fehlt, die anderen bleiben
                logger.warning("Import: ein Teil scheiterte error=%s", type(exc).__name__)
                return "fehler"

    ergebnisse = await asyncio.gather(*(_einer(lesung) for lesung in v.lesungen))
    return await asyncio.to_thread(_vorschau_bauen, v, list(ergebnisse))


# ── Übernahme ────────────────────────────────────────────────────────────


def _anlegen(
    db: Session, user: User, request: AiMemoryImportRequest, item: AiMemoryImportItem, ref: str
) -> str | None:
    try:
        ai_memory_service.erinnerung_anlegen(
            db, user=user, scope=request.scope, server_id=request.server_id,
            team_id=request.team_id, text=item.text, titel=item.titel, thema=item.thema,
            art=item.art, wichtigkeit=item.wichtigkeit, quelle="import", quelle_ref=ref,
            origin="user",
        )
    except ai_memory_service.MemoryScopeVoll:
        db.rollback()
        return "full"
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == 409:
            return "conflict"
        if exc.status_code == 422:
            return "rejected"
        raise
    return None


def _ersetzen(
    db: Session, user: User, ziel: ai_memory_service.Importziel, item: AiMemoryImportItem
) -> str | None:
    """Der erste Eintrag bekommt den neuen Stand, die übrigen gehen darin auf.

    Nur innerhalb des Zielbereichs: eine Kennung aus der Anfrage, die
    woanders hinzeigt, wird abgewiesen, statt dort etwas zu ändern.
    """
    ids = [bisher.id for bisher in item.ersetzt]
    # Gesperrt bis zum Commit: sonst prüfte der Vergleich der Fassung unten
    # einen Stand, den ein zweites Fenster im selben Moment ändern kann.
    rows = {
        row.id: row
        for row in db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.id.in_(ids))
        .order_by(AiMemoryEntry.id)
        .with_for_update()
        .populate_existing()
        .all()
    }
    if len(rows) != len(ids) or any(row.scope_identity != ziel.identity for row in rows.values()):
        db.rollback()
        return "rejected"
    erstes, *uebrige = item.ersetzt
    unveraendert = ai_memory_service.UNVERAENDERT
    try:
        ai_memory_service.erinnerung_aendern(
            db, user=user, entry_id=erstes.id, text=item.text,
            titel=item.titel or unveraendert, thema=item.thema or unveraendert,
            art=item.art or unveraendert, wichtigkeit=item.wichtigkeit,
            erwartete_fassung=erstes.fassung, von="user", grund="bearbeitet", commit=False,
        )
        for bisher in uebrige:
            if int(rows[bisher.id].fassung or 1) != bisher.fassung:
                raise HTTPException(status_code=409, detail="inzwischen geändert")
            ai_memory_service.erinnerung_aufnehmen(
                db, user=user, ziel_id=erstes.id, quelle_id=bisher.id,
                ueberschreibt_mensch=True, commit=False,
            )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == 409:
            return "conflict"
        if exc.status_code in (403, 404, 422):
            return "rejected"
        raise
    return None


def uebernehmen(
    db: Session, user: User, request: AiMemoryImportRequest
) -> AiMemoryImportResponse:
    """Schreibt die ausgewählten Erinnerungen — einzeln, mit Auskunft über jede.

    Jede wird für sich festgeschrieben: ein voller Bereich bricht den Import
    nicht ab, was noch passte, steht. Ersetzungen zuerst — sie kosten keinen
    Platz, und was dabei in einer anderen aufgeht, macht welchen frei.

    Rechte- und Bereichsfehler brechen sofort ab (`importziel`): sie gelten
    für jede Erinnerung gleich.
    """
    ziel = ai_memory_service.importziel(db, user, request.scope, request.server_id, request.team_id)
    _nicht_gesperrt(ziel)
    ref = f"import:{uuid4()}"
    importiert = 0
    aktualisiert = 0
    uebersprungen: list[AiMemoryImportSkipped] = []
    vergeben: set[str] = set()
    for index in sorted(range(len(request.items)), key=lambda i: not request.items[i].ersetzt):
        item = request.items[index]
        ids = [bisher.id for bisher in item.ersetzt]
        if len(set(ids)) != len(ids) or vergeben.intersection(ids):
            uebersprungen.append(AiMemoryImportSkipped(index=index, reason="duplicate"))
            continue
        vergeben.update(ids)
        grund = _ersetzen(db, user, ziel, item) if item.ersetzt else _anlegen(db, user, request, item, ref)
        if grund is not None:
            uebersprungen.append(AiMemoryImportSkipped(index=index, reason=grund))
        elif item.ersetzt:
            aktualisiert += 1
        else:
            importiert += 1

    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.imported", target_type="ai_memory",
        target_id=ziel.identity,
        details={
            "scope": request.scope,
            **({"server_id": request.server_id} if request.server_id else {}),
            **({"team_id": request.team_id} if request.team_id else {}),
            **({"source": request.source_provider} if request.source_provider else {}),
            "imported": importiert, "updated": aktualisiert, "skipped": len(uebersprungen),
        },
        origin="direct",
    )
    db.commit()
    return AiMemoryImportResponse(
        imported_count=importiert,
        updated_count=aktualisiert,
        skipped_count=len(uebersprungen),
        skipped=sorted(uebersprungen, key=lambda s: s.index),
    )


__all__ = ["Teil", "teile", "uebernehmen", "vorschau"]
