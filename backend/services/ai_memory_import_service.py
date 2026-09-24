"""Memory Bridge: Erinnerungen aus einer anderen KI ins eigene Gedächtnis holen.

ChatGPT, Gemini und Claude bieten für ihr Langzeitgedächtnis keinen Export an.
Der Weg führt deshalb über die KI selbst: der Benutzer schickt ihr einen
Auszugs-Prompt (steht in der Oberfläche), kopiert die Antwort und fügt sie hier
ein. Dieser Dienst liest daraus einzelne Fakten, der Benutzer sucht in der
Vorschau aus, und erst dann wird geschrieben.

Zwei Schritte, zwei Funktionen:

* `analyze_preview` schreibt **nichts**. Sie zerlegt den Text, weist
  Zugangsdaten aus und gleicht jeden Fakt gegen den Zielbereich ab.
* `execute_import` übernimmt, was der Benutzer ausgewählt hat — über
  `ai_memory_service.upsert_entry`, also mit denselben Rechten, derselben
  Verschlüsselung, derselben Bereichsgrenze und demselben Protokoll wie ein
  von Hand angelegter Eintrag.

Herkunft ist ``user``: der Benutzer hat jeden Eintrag gesehen und bestätigt.
Damit genießt er denselben Schutz vor stillschweigender Korrektur durch die KI
wie eine eigene Notiz.

Die Zerlegung bleibt bewusst eine Heuristik ohne Modellaufruf. Der Text ist
persönlich; ihn für die Zerlegung an einen Anbieter zu schicken, hieße, ihn
ein zweites Mal aus der Hand zu geben. Was die Heuristik falsch schneidet,
sieht der Benutzer in der Vorschau und korrigiert es dort.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import unicodedata

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import AiMemoryEntry, User
from schemas.ai_memory import (
    MAX_IMPORT_ITEMS,
    AiMemoryImportItem,
    AiMemoryImportPreviewItem,
    AiMemoryImportPreviewRequest,
    AiMemoryImportPreviewResponse,
    AiMemoryImportRequest,
    AiMemoryImportResponse,
    AiMemoryImportSkipped,
)
from services import ai_memory_service, audit_service
from services.ai_redaction import enthaelt_zugangsdaten, redact_sensitive_text

#: Die Abschnitte des Auszugs-Prompts und woran man sie erkennt — deutsch und
#: englisch, denn die fremde KI antwortet oft in der Sprache ihrer Oberfläche.
#: Der erste Name ist die Kategorie, die die Vorschau anzeigt — in den
#: Schlüssel geht sie nicht ein (siehe `normalize_key`).
KATEGORIEN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("demografie", ("demograf", "demograph", "persönliche daten", "personal info")),
    ("vorlieben", ("interess", "vorlieb", "präferenz", "preferen", "hobby", "hobbies")),
    ("soziales", ("sozial", "social", "beziehung", "relationship", "kontakt", "contact", "famil")),
    ("projekte", ("termin", "projekt", "project", "pläne", "plans", "event", "dated", "aktivität", "activit")),
    ("anweisung", ("anweisung", "instruction", "regeln", "rules")),
)
#: Womit eine Überschrift **beginnt**, wenn nur eine Nummer oder ein
#: Doppelpunkt sie auszeichnet. Dort reicht ein Stichwort irgendwo im Titel
#: nicht: "2. Interessiert sich für Rust" ist ein Punkt einer nummerierten
#: Liste, "2. Interessen und Vorlieben" eine Überschrift.
_UEBERSCHRIFT_ANFANG = frozenset(
    """
    demografische demografie demographische demographic demographics persönliche
    interessen interests vorlieben präferenzen preferences hobbys hobbies
    soziales soziale social beziehungen relationships kontakte contacts familie family
    termine projekte dated projects aktivitäten activities
    anweisungen instructions
    """.split()
)
#: Mehr Wörter hat keine Überschrift des Prompts ("Termine, Projekte und Pläne
#: mit Datum" sind sechs); ein längerer Titel hinter einer Nummer ist ein Satz.
_UEBERSCHRIFT_WOERTER = 6
#: Kategorie für Fakten ohne erkannten Abschnitt.
OHNE_KATEGORIE = "allgemein"

#: Ab wieviel Zeichen ein Fakt gekürzt wird — die Grenze eines Eintrags.
MAX_WERT = 2_000
MAX_SCHLUESSEL = 64
MAX_BELEG = 500

_ZAUN_RE = re.compile(r"^\s*```[\w-]*\s*$", re.MULTILINE)
_QUELLE_RE = re.compile(
    r"^\s*[*_>#\s]*(?:importiert aus|imported from)\s*:?\s*[*_]*\s*(?P<name>[^\n]+?)\s*[*_.]*\s*$",
    re.IGNORECASE,
)
_PUNKT_RE = re.compile(r"^(?P<einzug>[ \t]*)(?:[*\-•+–]|\d{1,3}[.)])\s+(?P<text>\S.*)$")
_UEBERSCHRIFT_RE = re.compile(
    r"^\s*(?P<raute>#{1,6}\s*)?(?P<fett>\*\*|__)?\s*(?P<nummer>\d{1,2}[.)]\s*)?"
    r"(?P<titel>[^\n]+?)\s*(?:\*\*|__)?\s*(?P<doppelpunkt>:)?\s*(?:\*\*|__)?\s*$"
)
_BELEG_RE = re.compile(r"^(?:beleg|nachweis|quelle|evidence|source)\s*:", re.IGNORECASE)
_PAAR_RE = re.compile(r"^(?P<label>[A-Za-zÄÖÜäöüß][\wÄÖÜäöüß .&/-]{0,38}?)\s*:\s+(?P<wert>\S.*)$")
_WORT_RE = re.compile(r"[a-z0-9äöüß]+")

#: Wörter, die keinen Schlüssel tragen. Dazu gehören ausdrücklich "Person" und
#: "User": der Prompt verlangt genau diese Umschreibung, sie steht also in
#: fast jedem Fakt und unterscheidet keinen.
_FUELLWOERTER = frozenset(
    """
    der die das den dem des ein eine einer eines einem einen und oder aber
    ist sind war waren wird werden hat haben hatte mit von vom zum zur zu im in
    am an auf aus bei für fuer über ueber als auch nicht kein keine sehr gern
    gerne immer oft meist meistens regelmäßig regelmaessig sich sein seine
    ihr ihre ihren deren dessen person personen nutzer nutzerin benutzer
    benutzerin user möchte moechte mag will soll sollte bevorzugt
    the a an and or but is are was were be been has have had with of for to
    in on at by from as not very often always usually prefers likes wants
    person persons user users their his her they them s
    """.split()
)

#: Wendungen, deren Subjekt der Schlüssel ist ("Der Wohnort der Person ist …").
_SUBJEKT_MUSTER: tuple[tuple[re.Pattern[str], str | None], ...] = (
    (re.compile(r"^(?:der|die|das)\s+(?P<wort>[\wäöüß-]+)\s+(?:der|des)\s+(?:person|nutzers?|benutzers?)\b", re.I), None),
    (re.compile(r"^the\s+(?:person|user)'?s\s+(?P<wort>[\w-]+(?:\s+[\w-]+)?)\s+(?:is|are|was)\b", re.I), None),
    (re.compile(r"^(?:die\s+person\s+)?(?:heißt|heisst|wird\s+.*genannt|möchte\s+.*genannt\s+werden)\b", re.I), "name"),
    (re.compile(r"^(?:the\s+person\s+)?(?:is\s+called|goes\s+by|prefers\s+to\s+be\s+called)\b", re.I), "name"),
    (re.compile(r"^(?:die\s+person\s+)?(?:wohnt|lebt)\s+in\b", re.I), "wohnort"),
    (re.compile(r"^(?:the\s+person\s+)?lives\s+in\b", re.I), "location"),
    (re.compile(r"^(?:die\s+person\s+)?(?:arbeitet\s+als|ist\s+von\s+beruf)\b", re.I), "beruf"),
    (re.compile(r"^(?:the\s+person\s+)?works\s+as\b", re.I), "occupation"),
)
_PERSON_VORNE_RE = re.compile(r"^(?:die\s+person|the\s+person|the\s+user|der\s+nutzer|der\s+benutzer)\s+", re.I)


@dataclass
class Rohfakt:
    """Ein Fakt, wie ihn der Text hergab — noch ohne Abgleich."""

    kategorie: str
    text: str
    beleg: str | None = None
    #: Ein Schlüssel, den der Text selbst nennt (JSON, "Name: Alex").
    schluessel: str | None = None


@dataclass
class Importtext:
    quelle: str | None
    fakten: list[Rohfakt]


# ---------------------------------------------------------------------------
# Zerlegung
# ---------------------------------------------------------------------------


def parse_import_text(raw_text: str) -> Importtext:
    """Zerlegt die Antwort einer fremden KI in einzelne Fakten.

    Versucht der Reihe nach: JSON, das Abschnittsformat des Auszugs-Prompts,
    Schlüssel-Wert-Zeilen und zuletzt eine schlichte Liste (so kopiert man die
    Gedächtnisansicht von ChatGPT). Die Reihenfolge ist die von eng nach weit:
    JSON ist eindeutig oder gar nicht, eine Liste passt auf fast alles.
    """
    text = _ZAUN_RE.sub("", raw_text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    aus_json = _aus_json(text)
    if aus_json is not None:
        return Importtext(quelle=None, fakten=aus_json)

    quelle: str | None = None
    zeilen: list[str] = []
    for zeile in text.split("\n"):
        fund = _QUELLE_RE.match(zeile)
        if fund:
            quelle = _quelle_bereinigen(fund.group("name"))
            continue
        zeilen.append(zeile)

    if any(_kategorie_der_ueberschrift(zeile) for zeile in zeilen):
        return Importtext(quelle=quelle, fakten=_aus_abschnitten(zeilen))
    paare = _aus_paaren(zeilen)
    if paare is not None:
        return Importtext(quelle=quelle, fakten=paare)
    return Importtext(quelle=quelle, fakten=_aus_abschnitten(zeilen))


def _quelle_bereinigen(name: str) -> str | None:
    name = re.sub(r"[<>\"'`*_]", "", name).strip(" .")
    return name[:40] or None


def _kategorie_der_ueberschrift(zeile: str) -> str | None:
    """Die Kategorie, wenn die Zeile eine Abschnittsüberschrift ist.

    Eine Überschrift braucht ein **Zeichen** — Raute, Fettdruck, Nummer oder
    einen Doppelpunkt am Ende. Ein Stichwort allein reicht nicht: "Interessiert
    sich für Rust" ist ein Fakt aus einer schlichten Liste, keine Überschrift.
    Und sie endet nicht auf einem Punkt; Fakten sind Sätze, Überschriften nicht.
    """
    if not zeile.strip() or zeile.strip().endswith("."):
        return None
    fund = _UEBERSCHRIFT_RE.match(zeile)
    if fund is None:
        return None
    if not (fund.group("raute") or fund.group("fett") or fund.group("nummer") or fund.group("doppelpunkt")):
        return None
    if zeile.lstrip().startswith(("* ", "- ", "• ", "+ ")) and not fund.group("fett"):
        return None
    titel = fund.group("titel").strip(" *_#:").lower()
    if len(titel) > 60 or "person" in titel or "user" in titel.split():
        return None
    if not (fund.group("raute") or fund.group("fett")):
        woerter = titel.split()
        if not woerter or len(woerter) > _UEBERSCHRIFT_WOERTER:
            return None
        if woerter[0].strip(",;") not in _UEBERSCHRIFT_ANFANG:
            return None
    for name, stichworte in KATEGORIEN:
        if any(wort in titel for wort in stichworte):
            return name
    return None


def _einzug(roh: str) -> int:
    return len(roh.replace("\t", "    "))


def _aus_abschnitten(zeilen: list[str]) -> list[Rohfakt]:
    """Liest Abschnitte, Hauptpunkte und eingerückte Belege.

    Ein eingerückter Unterpunkt oder eine Zeile, die mit "Beleg:" beginnt,
    gehört zum Fakt darüber. Alles andere auf oberster Ebene ist ein eigener
    Fakt — auch ohne Aufzählungszeichen, denn manche Modelle lassen sie weg.
    """
    fakten: list[Rohfakt] = []
    kategorie = OHNE_KATEGORIE
    grundeinzug: int | None = None
    for roh in zeilen:
        if not roh.strip():
            continue
        erkannt = _kategorie_der_ueberschrift(roh)
        if erkannt:
            kategorie = erkannt
            grundeinzug = None
            continue
        punkt = _PUNKT_RE.match(roh)
        einzug = _einzug(punkt.group("einzug")) if punkt else _einzug(roh[: len(roh) - len(roh.lstrip())])
        inhalt = (punkt.group("text") if punkt else roh).strip()
        inhalt = _markdown_weg(inhalt)
        if not inhalt:
            continue
        if grundeinzug is None:
            grundeinzug = einzug
        ist_beleg = bool(_BELEG_RE.match(inhalt)) or (bool(fakten) and einzug > grundeinzug)
        if ist_beleg and fakten:
            beleg = _BELEG_RE.sub("", inhalt).strip()
            vorher = fakten[-1].beleg
            fakten[-1].beleg = (f"{vorher} {beleg}" if vorher else beleg)[:MAX_BELEG]
            continue
        if inhalt.endswith(":") and not punkt:
            # Eine Zwischenzeile ("Hier ist, was ich weiß:") trägt keinen Fakt.
            continue
        fakten.append(Rohfakt(kategorie=kategorie, text=inhalt, schluessel=_label(inhalt)))
    return fakten


def _aus_paaren(zeilen: list[str]) -> list[Rohfakt] | None:
    """``Name: Alex``-Zeilen — wenn fast alle Zeilen so aussehen.

    Vier Fünftel statt aller: eine einzelne Zeile ohne Doppelpunkt (etwa ein
    Satz dazwischen) soll das Format nicht kippen. Sie wird dann als eigener
    Fakt gelesen.
    """
    gefuellt = [zeile.strip() for zeile in zeilen if zeile.strip()]
    if not gefuellt:
        return None
    treffer = [_PAAR_RE.match(_markdown_weg(zeile)) for zeile in gefuellt]
    if sum(1 for t in treffer if t) * 5 < len(gefuellt) * 4:
        return None
    fakten: list[Rohfakt] = []
    for zeile, fund in zip(gefuellt, treffer):
        if fund is None:
            fakten.append(Rohfakt(kategorie=OHNE_KATEGORIE, text=_markdown_weg(zeile)))
            continue
        fakten.append(Rohfakt(
            kategorie=OHNE_KATEGORIE,
            text=fund.group("wert").strip(),
            schluessel=fund.group("label").strip(),
        ))
    return fakten


def _aus_json(text: str) -> list[Rohfakt] | None:
    """``[{"key": …, "value": …}]``, ``{"memories": [...]}`` oder ``{key: value}``."""
    if not text or text[0] not in "[{":
        return None
    try:
        daten = json.loads(text)
    except ValueError:
        return None
    if isinstance(daten, dict):
        for huelle in ("memories", "memory", "entries", "items", "facts"):
            if isinstance(daten.get(huelle), list):
                daten = daten[huelle]
                break
    fakten: list[Rohfakt] = []
    if isinstance(daten, dict):
        for key, value in daten.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                fakten.append(Rohfakt(kategorie=OHNE_KATEGORIE, text=str(value).strip(), schluessel=str(key)))
            elif isinstance(value, list):
                # {"Demografische Informationen": ["...", "..."]} — die Antwort
                # nach Abschnitten, nur eben als JSON.
                kategorie = _kategorie_aus_name(str(key))
                fakten.extend(
                    Rohfakt(kategorie=kategorie, text=str(eintrag).strip())
                    for eintrag in value
                    if isinstance(eintrag, (str, int, float)) and str(eintrag).strip()
                )
        return fakten
    if not isinstance(daten, list):
        return None
    for eintrag in daten:
        if isinstance(eintrag, str) and eintrag.strip():
            fakten.append(Rohfakt(kategorie=OHNE_KATEGORIE, text=eintrag.strip()))
        elif isinstance(eintrag, dict):
            wert = next(
                (eintrag[f] for f in ("value", "content", "text", "memory", "fact")
                 if isinstance(eintrag.get(f), (str, int, float)) and str(eintrag[f]).strip()),
                None,
            )
            if wert is None:
                continue
            key = next(
                (eintrag[f] for f in ("key", "name", "title") if isinstance(eintrag.get(f), str)),
                None,
            )
            kategorie = eintrag.get("category")
            fakten.append(Rohfakt(
                kategorie=_kategorie_aus_name(kategorie) if isinstance(kategorie, str) else OHNE_KATEGORIE,
                text=str(wert).strip(),
                schluessel=key,
            ))
    return fakten


def _kategorie_aus_name(name: str) -> str:
    klein = name.lower()
    for kategorie, stichworte in KATEGORIEN:
        if klein == kategorie or any(wort in klein for wort in stichworte):
            return kategorie
    return OHNE_KATEGORIE


def _markdown_weg(text: str) -> str:
    return re.sub(r"(\*\*|__|`)", "", text).strip()


def _label(text: str) -> str | None:
    """"Wohnort: Berlin" nennt seinen Schlüssel selbst — höchstens drei Wörter."""
    fund = _PAAR_RE.match(text)
    if fund is None or len(fund.group("label").split()) > 3:
        return None
    return fund.group("label")


# ---------------------------------------------------------------------------
# Schlüssel und Werte
# ---------------------------------------------------------------------------


def _ascii(text: str) -> str:
    text = text.lower()
    for alt, neu in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(alt, neu)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _slug(text: str, *, punkte: bool = False) -> str:
    erlaubt = r"[^a-z0-9.]+" if punkte else r"[^a-z0-9]+"
    slug = re.sub(erlaubt, "_", _ascii(text))
    slug = re.sub(r"_*\._*", ".", slug) if punkte else slug
    return re.sub(r"_+", "_", slug).strip("_.")


def normalize_key(text: str, vorgabe: str | None = None) -> str:
    """Ein Schlüssel für einen Fakt — höchstens 64 Zeichen, ohne Kategorie.

    Nennt der Text selbst einen Schlüssel (JSON, "Wohnort: Berlin"), gilt der.
    Sonst trägt eine bekannte Wendung ihn ("Die Person heißt …" → ``name``),
    und zuletzt die ersten drei tragenden Wörter des Fakts.

    Der Schlüssel ist die Identität eines Fakts: ein zweiter Import derselben
    Antwort soll denselben Schlüssel treffen und damit als "schon da" erkannt
    werden, statt ein Doppel anzulegen. Deshalb nur Wörter aus dem Anfang —
    der hängt an der Sache, das Ende oft am Wert.

    **Kein Kategoriepräfix** (``demografie.name``), obwohl der Abschnitt
    bekannt ist. Der Schlüssel geht mit in den Vektor (`_embedding_source`),
    und ein gemeinsames Wort vor jedem Eintrag einer Kategorie macht sie alle
    einander ähnlich. Gemessen am 25.09.2026 mit potion-multilingual-128M:
    mit Präfix lagen verschiedene Fakten bei bis zu 0,66 ("Arbeitet als
    Softwareentwickler" zu "Wohnt in Berlin") und derselbe Fakt bei nur 0,50 —
    nicht mehr zu trennen, weder in der Vorschau noch später beim Abruf. Ohne
    Präfix: verschieden höchstens 0,14, gleich mindestens 0,59. Die Kategorie
    zeigt die Vorschau trotzdem an; sie steht in ``category``.
    """
    thema = ""
    if vorgabe:
        thema = _slug(vorgabe, punkte=True)
    if not thema:
        for muster, fest in _SUBJEKT_MUSTER:
            fund = muster.match(text.strip())
            if fund:
                thema = fest or _slug(fund.group("wort"))
                break
    if not thema:
        woerter = [
            wort for wort in _WORT_RE.findall(text.lower())
            if wort not in _FUELLWOERTER and (len(wort) > 2 or wort.isdigit())
        ]
        thema = _slug(" ".join(woerter[:3]))
    return (thema or "eintrag")[:MAX_SCHLUESSEL].strip("_.") or "eintrag"


def _wert(text: str) -> str:
    """Der Fakt als Eintrag: "Die Person heißt Alex." wird zu "Heißt Alex."

    Der Prompt verlangt die Umschreibung "die Person", damit die fremde KI
    nicht in der ersten Person antwortet. Im eigenen Gedächtnis ist klar, wer
    gemeint ist — das Subjekt vorne kostet nur Platz im Kontext.
    """
    rest = _PERSON_VORNE_RE.sub("", text.strip(), count=1)
    if rest != text.strip() and rest:
        rest = rest[0].upper() + rest[1:]
    return rest[:MAX_WERT].strip()


def _eindeutig(schluessel: str, vergeben: set[str]) -> str:
    if schluessel not in vergeben:
        return schluessel
    for nummer in range(2, 1_000):
        endung = f"_{nummer}"
        kandidat = schluessel[: MAX_SCHLUESSEL - len(endung)] + endung
        if kandidat not in vergeben:
            return kandidat
    return schluessel


# ---------------------------------------------------------------------------
# Vorschau und Übernahme
# ---------------------------------------------------------------------------


def analyze_preview(
    db: Session, user: User, request: AiMemoryImportPreviewRequest
) -> AiMemoryImportPreviewResponse:
    """Zerlegt, prüft und vergleicht — ohne ein Byte zu schreiben."""
    zerlegt = parse_import_text(request.raw_text)
    fakten = zerlegt.fakten[:MAX_IMPORT_ITEMS]

    vergeben: set[str] = set()
    vorbereitet: list[tuple[Rohfakt, str, str, bool]] = []
    for fakt in fakten:
        wert = _wert(fakt.text)
        if not wert:
            continue
        geheim = enthaelt_zugangsdaten(wert)
        # Der Schlüssel entsteht aus den ersten Wörtern des Fakts — bei einem
        # Geheimnis also womöglich aus dem Geheimnis selbst. Dann aus der
        # geschwärzten Fassung.
        schluessel = _eindeutig(
            normalize_key(redact_sensitive_text(fakt.text))
            if geheim
            else normalize_key(fakt.text, fakt.schluessel),
            vergeben,
        )
        vergeben.add(schluessel)
        vorbereitet.append((fakt, schluessel, wert, geheim))

    sauber = [(schluessel, wert) for _f, schluessel, wert, geheim in vorbereitet if not geheim]
    abgleich = ai_memory_service.importabgleich(
        db, user, request.scope, request.server_id, request.team_id, sauber,
    )
    aehnlich = iter(abgleich.aehnlich)

    items: list[AiMemoryImportPreviewItem] = []
    for fakt, schluessel, wert, geheim in vorbereitet:
        beleg = redact_sensitive_text(fakt.beleg) if fakt.beleg else None
        if geheim:
            # Zurück geht nur die geschwärzte Fassung: die Vorschau soll zeigen,
            # *dass* hier etwas gesperrt ist, nicht das Geheimnis ein zweites
            # Mal über die Leitung schicken.
            items.append(AiMemoryImportPreviewItem(
                key=schluessel, value=redact_sensitive_text(wert), category=fakt.kategorie,
                evidence=beleg, status="has_secret",
            ))
            continue
        fund = next(aehnlich)
        if schluessel in abgleich.gleicher_schluessel:
            items.append(AiMemoryImportPreviewItem(
                key=schluessel, value=wert, category=fakt.kategorie, evidence=beleg,
                status="exact_duplicate", existing_key=schluessel,
                existing_value=abgleich.gleicher_schluessel[schluessel],
            ))
        elif fund is not None:
            items.append(AiMemoryImportPreviewItem(
                key=schluessel, value=wert, category=fakt.kategorie, evidence=beleg,
                status="similar_existing", existing_key=fund[0], existing_value=fund[1],
                similarity=round(fund[2], 3),
            ))
        else:
            items.append(AiMemoryImportPreviewItem(
                key=schluessel, value=wert, category=fakt.kategorie, evidence=beleg, status="new",
            ))

    return AiMemoryImportPreviewResponse(
        detected_source=zerlegt.quelle or request.source_provider,
        items=items,
        total_detected=len(zerlegt.fakten),
        total_valid=sum(1 for item in items if item.status != "has_secret"),
        total_conflicts=sum(
            1 for item in items if item.status in ("exact_duplicate", "similar_existing")
        ),
        total_secrets_blocked=sum(1 for item in items if item.status == "has_secret"),
        available_slots=abgleich.frei,
        memory_enabled=(
            request.scope not in ai_memory_service.PERSOENLICHE_SCOPES
            or ai_memory_service.preference(db, user.id)
        ),
    )


def execute_import(
    db: Session, user: User, request: AiMemoryImportRequest
) -> AiMemoryImportResponse:
    """Übernimmt die ausgewählten Fakten — einzeln, mit Auskunft über jeden.

    Jeder Eintrag geht durch `upsert_entry` und wird dort für sich
    festgeschrieben. Ein voller Bereich bricht den Import deshalb nicht ab: was
    noch passte, steht, der Rest wird mit Grund zurückgemeldet. Dasselbe gilt
    für einen Wert, den `upsert_entry` abweist (Zugangsdaten, leer) — die
    Vorschau hat ihn zwar schon ausgewiesen, aber was hier ankommt, kann der
    Benutzer seither bearbeitet haben.

    Ein belegter Schlüssel wird nur mit ``replace_existing`` überschrieben. Die
    Vorschau kann veraltet sein; ohne diese Prüfung ersetzte ein Import still,
    was seither jemand anderes dort abgelegt hat.

    Rechte- und Bereichsfehler (403, 404) brechen dagegen sofort ab: sie gelten
    für jeden Eintrag gleich, und der erste scheitert, bevor etwas geschrieben
    ist.
    """
    identity, _owner, _sid, _tid = ai_memory_service.scope_identity(
        db, user, request.scope, request.server_id, request.team_id
    )
    importiert = 0
    aktualisiert = 0
    uebersprungen: list[AiMemoryImportSkipped] = []
    gesehen: set[str] = set()

    for item in request.items:
        if item.key in gesehen:
            uebersprungen.append(AiMemoryImportSkipped(key=item.key, reason="duplicate"))
            continue
        gesehen.add(item.key)
        vorhanden = db.query(AiMemoryEntry.id).filter(
            AiMemoryEntry.scope_identity == identity, AiMemoryEntry.key == item.key
        ).first() is not None
        if vorhanden and not item.replace_existing:
            uebersprungen.append(AiMemoryImportSkipped(key=item.key, reason="exists"))
            continue
        grund = _uebernehmen(db, user, request, item)
        if grund is not None:
            uebersprungen.append(AiMemoryImportSkipped(key=item.key, reason=grund))
        elif vorhanden:
            aktualisiert += 1
        else:
            importiert += 1

    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.imported", target_type="ai_memory",
        target_id=identity,
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
        skipped=uebersprungen,
    )


def _uebernehmen(
    db: Session, user: User, request: AiMemoryImportRequest, item: AiMemoryImportItem
) -> str | None:
    """Schreibt einen Eintrag; gibt den Grund zurück, falls er liegen bleibt."""
    try:
        ai_memory_service.upsert_entry(
            db, user=user, scope=request.scope, server_id=request.server_id,
            team_id=request.team_id, key=item.key, value=item.value, origin="user",
        )
    except ai_memory_service.MemoryScopeVoll:
        db.rollback()
        return "full"
    except HTTPException as exc:
        if exc.status_code == 422:
            db.rollback()
            return "rejected"
        if exc.status_code == 409:
            return "conflict"
        raise
    return None
