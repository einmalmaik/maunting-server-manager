"""Gedächtnis v2, Stufe 4: der Abruf — auch bei 100.000 Erinnerungen ohne Wartezeit.

Bis hierher lud jede Chatnachricht **alle** sichtbaren Erinnerungen samt
Vektoren, öffnete Namen und Titel jeder Zeile beim Sidecar, nur um zu
sortieren, und wählte danach aus. Bei 5.000 Einträgen kostete das gemessene
0,7 s vor dem ersten Token, linear mit dem Bestand; bei 100.000 wären es
Sekunden gewesen. Der Block stand außerdem vor dem Verlauf und wechselte mit
jeder Frage — der Zwischenspeicher des Anbieters traf danach nie.

Jetzt sind es zwei Teile:

* **Im Kopf** — angeheftet, dann wichtig, dann neu; gewählt von der Datenbank
  (`ix_ai_memory_kopf`), geöffnet werden nur diese Zeilen. Der Teil hängt
  nicht an der Frage und bleibt bytegleich, bis jemand schreibt: er steht vor
  dem Verlauf und wird zwischengespeichert. Passt der ganze Bestand hinein,
  steht er ganz hier, wie bisher.
* **Passend zur Frage** — nur wenn nicht alles im Kopf steht. Gesucht wird
  zweigleisig ohne Klartext: nach Bedeutung im Vektorspeicher dieses
  Prozesses (je Bereich eine Matrix) und nach Wörtern im Wortindex der
  Datenbank (`ai_memory_begriffe`). Geöffnet werden nur die Kandidaten, und
  mit kommt nur, was die Frage trifft (`ai_memory_service.TREFFER_AB`). Der
  Teil steht im Nachspann hinter dem Verlauf (`ai_context_service`).

Rechte und Einwilligung entscheiden, **welche Bereiche** gefragt werden
(`sichtbare_bereiche`); alles danach fragt nur noch in diesen. Der
Vektorspeicher hält Kennungen und Zahlen, keinen Text, und frischt sich an
Zählern der Datenbank auf (`_staende`) — mehrere Prozesse halten je ihren.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiMemoryBegriff, AiMemoryEntry, User
from services import ai_embedding_service, ai_memory_service, permission_service
from services.ai_embedding_service import EMBEDDING_BYTES, EMBEDDING_DIMENSIONS
from services.dis_client import DisSidecarError


logger = logging.getLogger(__name__)

#: Wieviel vom Budget der Kopf bekommt, wenn nicht alles hineinpasst. Der Rest
#: gehört dem, was zur Frage passt.
KOPF_ANTEIL = 0.5
#: Kandidaten je Weg (Bedeutung, Wörter), bevor geöffnet wird. Mehr kommt in
#: keiner Anfrage in den Sidecar, egal wie groß der Bestand ist.
KANDIDATEN = 60
#: Treffer, die eine Suche in der Verwaltung höchstens zeigt.
MAX_SUCHTREFFER = 50
#: Wieviele Vektoren der Speicher eines Prozesses höchstens hält (je Vektor
#: 1 KB): darüber fällt der am längsten nicht gefragte Bereich heraus und wird
#: beim nächsten Mal neu geladen.
MAX_IM_SPEICHER = 250_000
#: Wieviele Erinnerungen der Takt je Lauf nachzieht (`nachziehen`).
NACHZIEHEN_JE_TAKT = 1_000
#: Über die Bedeutung allein gilt sonst ab `ai_memory_service.TREFFER_AB`
#: (0,35) als getroffen. In einem großen Bereich reicht weniger, wenn eine
#: Zeile klar aus dem Rauschen ragt: mindestens `BEDEUTUNG_BODEN` und
#: `BEDEUTUNG_ABSTAND` über dem 99. Perzentil des Bereichs (`_Bedeutung.trifft`).
#:
#: Gemessen am 07.10.2026 an 100.000 Einträgen mit dem lokalen Modell: auf
#: englische Fragen stand die deutsche Antwort meist auf Platz 1, aber mit
#: 0,22 bis 0,32 bei einem Rauschen um 0,12 bis 0,18 — und fiel an 0,35 heraus.
#: Unter `BEDEUTUNG_AB_ZEILEN` Zeilen sagt ein Perzentil nichts; dort gilt
#: allein die feste Schwelle.
BEDEUTUNG_BODEN = 0.2
BEDEUTUNG_ABSTAND = 0.08
BEDEUTUNG_AB_ZEILEN = 100
#: Wie weit das Nachladen zurückschaut. Zwei Schreiber, die sich beim Festschreiben
#: überholen, tragen Zeiten in falscher Reihenfolge ein; ohne den Nachlauf fiele
#: der langsamere durch das Raster.
_NACHLAUF = timedelta(minutes=2)


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


# ── Welche Bereiche ──────────────────────────────────────────────────────────


def sichtbare_bereiche(
    db: Session, user: User, *, persoenlich: bool, anlage: int | bool | None
) -> list[str]:
    """Die Bereiche, aus denen dieser Benutzer gerade etwas wissen darf.

    Panel immer, die eigenen nur mit Einwilligung (``persoenlich``), Teams nach
    Mitgliedschaft *jetzt*. Servernotizen und Anlagenwissen nur für Server, die
    er *jetzt* sehen darf — verliert er den Zugriff, verschwindet das Wissen
    aus dem Kontext, ohne dass jemand es nachpflegt. Je Server wird einmal
    gefragt, nicht je Zeile; die Antwort lebt nur in diesem Aufruf.

    ``anlage``: die Nummer des Servers, um den es gerade geht (sein Wissen
    kommt mit), ``True`` für das Wissen aller sichtbaren Server (die Suche),
    ``None`` für keines. Zwanzig Betriebsanleitungen nebeneinander wären kein
    Kontext, sondern Rauschen.
    """
    from services import team_service

    kennungen = {"panel"}
    if persoenlich:
        kennungen.add(f"user:{user.id}")
    kennungen.update(f"team:{team_id}" for team_id in team_service.user_team_ids(db, user))

    bedingungen = []
    if persoenlich:
        bedingungen.append(and_(AiMemoryEntry.scope == "server", AiMemoryEntry.owner_user_id == user.id))
    if anlage is True:
        bedingungen.append(AiMemoryEntry.scope == "server_shared")
    elif isinstance(anlage, int) and not isinstance(anlage, bool):
        bedingungen.append(and_(AiMemoryEntry.scope == "server_shared", AiMemoryEntry.server_id == anlage))
    if not bedingungen:
        return sorted(kennungen)
    # `None` heißt alle Server, `[]` keinen — beide sind falsy, und ein
    # `if sichtbare:` machte aus „sieht nichts“ ein „sieht alles“.
    sichtbare = permission_service.list_visible_server_ids(db, user)
    if sichtbare is not None and not sichtbare:
        return sorted(kennungen)
    abfrage = db.query(AiMemoryEntry.scope_identity, AiMemoryEntry.server_id).filter(
        or_(*bedingungen),
        AiMemoryEntry.status == "aktiv",
        AiMemoryEntry.server_id.is_not(None),
    )
    if sichtbare is not None:
        abfrage = abfrage.filter(AiMemoryEntry.server_id.in_(sichtbare))
    geprueft: dict[int, bool] = {}
    for kennung, server_id in abfrage.distinct().all():
        erlaubt = geprueft.get(server_id)
        if erlaubt is None:
            erlaubt = permission_service.has_server_permission(
                db=db, user=user, server_id=server_id, key="server.view"
            )
            geprueft[server_id] = erlaubt
        if erlaubt:
            kennungen.add(kennung)
    return sorted(kennungen)


def eigene_bereiche(db: Session, user: User) -> list[str]:
    """Die Bereiche der Profilansicht: das eigene Gedächtnis und alle eigenen Servernotizen.

    Ohne Prüfung auf `server.view`, wie `ai_memory_service.personal_entries`:
    es sind die eigenen Einträge, und wer einen Server nicht mehr sieht, soll
    seine Notiz dazu trotzdem finden und löschen können.
    """
    server = [
        kennung
        for (kennung,) in db.query(AiMemoryEntry.scope_identity)
        .filter(AiMemoryEntry.owner_user_id == user.id, AiMemoryEntry.scope == "server")
        .distinct()
        .all()
    ]
    return sorted({f"user:{user.id}", *server})


# ── Vektorspeicher ───────────────────────────────────────────────────────────


@dataclass
class _Bereichsvektoren:
    """Die Vektoren eines Bereichs als Matrix — Kennungen und Zahlen, kein Text."""

    modell: str
    #: (aktive Zeilen, jüngstes `updated_at`, jüngstes `indiziert_am`)
    stand: tuple
    ids: list[str]
    matrix: Any
    #: Aktive Zeilen ohne Vektor: sie zählen im Stand mit, stehen aber nicht
    #: in der Matrix. Ohne sie fiele ein Löschen nicht auf.
    ohne: set[str]
    position: dict[str, int]
    benutzt: float = 0.0


_SPEICHER: dict[str, _Bereichsvektoren] = {}
_SPERRE = threading.Lock()


def leeren() -> None:
    """Vergisst alle Vektoren dieses Prozesses (Tests, nach einem Modellwechsel)."""
    with _SPERRE:
        _SPEICHER.clear()


def _staende(db: Session, kennungen: list[str]) -> dict[str, tuple]:
    """Je Bereich die drei Zahlen, an denen der Speicher erkennt, ob er noch stimmt.

    Eine Abfrage für alle Bereiche. Neues und Geändertes hebt `updated_at` oder
    `indiziert_am`, Vergessenes und Zurückgeholtes `updated_at`, Gelöschtes
    senkt die Zahl der aktiven Zeilen.
    """
    zeilen = (
        db.query(
            AiMemoryEntry.scope_identity,
            func.count(AiMemoryEntry.id).filter(AiMemoryEntry.status == "aktiv"),
            func.max(AiMemoryEntry.updated_at),
            func.max(AiMemoryEntry.indiziert_am),
        )
        .filter(AiMemoryEntry.scope_identity.in_(kennungen))
        .group_by(AiMemoryEntry.scope_identity)
        .all()
    )
    return {kennung: (int(anzahl or 0), geaendert, indiziert) for kennung, anzahl, geaendert, indiziert in zeilen}


def _vektor(roh: bytes | None, gespeichert: str | None, modell: str):
    import numpy as np

    vektor = ai_memory_service.vektor_lesen(roh, gespeichert, modell)
    return None if vektor is None else np.frombuffer(vektor, dtype=np.float32)


def _matrix(vektoren: list) -> Any:
    import numpy as np

    if not vektoren:
        return np.zeros((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
    return np.stack(vektoren).astype(np.float32, copy=False)


def _voll_laden(db: Session, kennung: str, modell: str, stand: tuple) -> _Bereichsvektoren:
    ids: list[str] = []
    vektoren: list = []
    ohne: set[str] = set()
    abfrage = (
        db.query(AiMemoryEntry.id, AiMemoryEntry.embedding_bytes, AiMemoryEntry.embedding_model)
        .filter(AiMemoryEntry.scope_identity == kennung, AiMemoryEntry.status == "aktiv")
        .yield_per(5_000)
    )
    for zeilen_id, roh, gespeichert in abfrage:
        vektor = _vektor(roh, gespeichert, modell)
        if vektor is None:
            ohne.add(zeilen_id)
        else:
            ids.append(zeilen_id)
            vektoren.append(vektor)
    return _Bereichsvektoren(
        modell=modell, stand=stand, ids=ids, matrix=_matrix(vektoren), ohne=ohne,
        position={zeilen_id: nummer for nummer, zeilen_id in enumerate(ids)},
    )


def _nachladen(
    db: Session, alt: _Bereichsvektoren, kennung: str, stand: tuple
) -> _Bereichsvektoren:
    """Liest nur, was sich seit dem letzten Stand geändert hat — sonst alles."""
    import numpy as np

    _, alt_geaendert, alt_indiziert = alt.stand
    seit = [
        spalte > zeit - _NACHLAUF
        for spalte, zeit in (
            (AiMemoryEntry.updated_at, alt_geaendert),
            (AiMemoryEntry.indiziert_am, alt_indiziert),
        )
        if zeit is not None
    ]
    if not seit:
        return _voll_laden(db, kennung, alt.modell, stand)
    geaendert = (
        db.query(
            AiMemoryEntry.id, AiMemoryEntry.status,
            AiMemoryEntry.embedding_bytes, AiMemoryEntry.embedding_model,
        )
        .filter(AiMemoryEntry.scope_identity == kennung, or_(*seit))
        .all()
    )
    neu: dict[str, Any] = {}
    ohne = set(alt.ohne)
    for zeilen_id, status, roh, gespeichert in geaendert:
        vektor = _vektor(roh, gespeichert, alt.modell) if status == "aktiv" else None
        neu[zeilen_id] = vektor
        ohne.discard(zeilen_id)
        if status == "aktiv" and vektor is None:
            ohne.add(zeilen_id)
    bleiben = [nummer for nummer, zeilen_id in enumerate(alt.ids) if zeilen_id not in neu]
    dazu = [(zeilen_id, vektor) for zeilen_id, vektor in neu.items() if vektor is not None]
    ids = [alt.ids[nummer] for nummer in bleiben] + [zeilen_id for zeilen_id, _ in dazu]
    if len(ids) + len(ohne) != stand[0]:
        # Gelöscht ohne Spur (eine Zeile ist weg, nicht vergessen): alles neu.
        return _voll_laden(db, kennung, alt.modell, stand)
    teile = [alt.matrix[bleiben]] if bleiben else []
    if dazu:
        teile.append(_matrix([vektor for _, vektor in dazu]))
    matrix = np.concatenate(teile) if teile else _matrix([])
    return _Bereichsvektoren(
        modell=alt.modell, stand=stand, ids=ids, matrix=matrix, ohne=ohne,
        position={zeilen_id: nummer for nummer, zeilen_id in enumerate(ids)},
    )


def _aufraeumen(behalten: str) -> None:
    """Hält den Speicher unter `MAX_IM_SPEICHER`; ruft man unter `_SPERRE`."""
    gesamt = sum(len(eintrag.ids) for eintrag in _SPEICHER.values())
    for kennung, eintrag in sorted(_SPEICHER.items(), key=lambda paar: paar[1].benutzt):
        if gesamt <= MAX_IM_SPEICHER:
            break
        if kennung == behalten:
            continue
        gesamt -= len(eintrag.ids)
        del _SPEICHER[kennung]


def _vektoren(db: Session, kennungen: list[str], modell: str) -> dict[str, _Bereichsvektoren]:
    """Die Vektoren der Bereiche, frisch genug für diese Anfrage."""
    staende = _staende(db, kennungen)
    ergebnis: dict[str, _Bereichsvektoren] = {}
    for kennung in kennungen:
        stand = staende.get(kennung)
        if stand is None or stand[0] == 0:
            continue
        with _SPERRE:
            alt = _SPEICHER.get(kennung)
        if alt is not None and alt.modell == modell and alt.stand == stand:
            eintrag = alt
        elif alt is not None and alt.modell == modell:
            eintrag = _nachladen(db, alt, kennung, stand)
        else:
            eintrag = _voll_laden(db, kennung, modell, stand)
        eintrag.benutzt = time.monotonic()
        with _SPERRE:
            _SPEICHER[kennung] = eintrag
            _aufraeumen(kennung)
        ergebnis[kennung] = eintrag
    return ergebnis


@dataclass
class _Bedeutung:
    """Wie ähnlich jede Erinnerung der gefragten Bereiche den Texten ist."""

    je_bereich: dict[str, tuple[_Bereichsvektoren, Any]]
    #: Je Bereich, ab welcher Ähnlichkeit eine Zeile allein über die Bedeutung
    #: getroffen ist (`BEDEUTUNG_BODEN`); fehlt der Bereich, gilt `TREFFER_AB`.
    schwellen: dict[str, float] = field(default_factory=dict)

    def wert(self, row: AiMemoryEntry) -> float | None:
        """``None`` heißt: kein Vergleich möglich — nicht „unähnlich“."""
        paar = self.je_bereich.get(row.scope_identity)
        if paar is None:
            return None
        bestand, werte = paar
        nummer = bestand.position.get(row.id)
        return None if nummer is None else float(werte[nummer])

    def trifft(self, row: AiMemoryEntry, bezug: int) -> bool:
        """Ob die Frage diese Zeile trifft — über Wörter oder Bedeutung.

        Daran hängt beides: ob sie „passend zur Frage“ mitkommt und ob sie als
        gebraucht zählt. Eine Stelle, damit beides dieselbe Antwort bekommt.
        """
        wert = self.wert(row)
        if ai_memory_service._reiz(wert, bezug) >= ai_memory_service.TREFFER_AB:
            return True
        schwelle = self.schwellen.get(row.scope_identity, ai_memory_service.TREFFER_AB)
        return wert is not None and wert >= schwelle

    def beste(self, anzahl: int) -> dict[str, float]:
        import numpy as np

        ids: list[str] = []
        teile = []
        for bestand, werte in self.je_bereich.values():
            ids.extend(bestand.ids)
            teile.append(werte)
        if not ids:
            return {}
        alle = np.concatenate(teile)
        if len(alle) > anzahl:
            auswahl = np.argpartition(-alle, anzahl - 1)[:anzahl]
        else:
            auswahl = np.arange(len(alle))
        return {ids[nummer]: float(alle[nummer]) for nummer in auswahl}


_KEINE_BEDEUTUNG = _Bedeutung({})


def _bedeutung(db: Session, kennungen: list[str], texte: list[str]) -> _Bedeutung:
    """Bedeutungsähnlichkeit zu den Texten, je Zeile die höchste.

    Ohne Modell, ohne numpy oder ohne Vektoren: keine Bedeutung, und der Abruf
    sucht allein nach Wörtern. Ein Gedächtnis darf nicht daran scheitern, dass
    ein Modell fehlt.
    """
    texte = [text for text in texte if text and text.strip()]
    if not texte or not kennungen:
        return _KEINE_BEDEUTUNG
    try:
        import numpy as np

        kodierung = ai_embedding_service.encode(texte, db=db)
        if kodierung is None or len(kodierung.vektoren) != len(texte):
            return _KEINE_BEDEUTUNG
        fragen = np.asarray(kodierung.vektoren, dtype=np.float32)
        je_bereich = {}
        schwellen = {}
        for kennung, bestand in _vektoren(db, kennungen, kodierung.modell).items():
            if not bestand.ids:
                continue
            werte = (bestand.matrix @ fragen.T).max(axis=1)
            je_bereich[kennung] = (bestand, werte)
            if len(werte) >= BEDEUTUNG_AB_ZEILEN:
                stelle = int(len(werte) * 0.99)
                rauschen = float(np.partition(werte, stelle)[stelle])
                schwellen[kennung] = min(
                    ai_memory_service.TREFFER_AB,
                    max(BEDEUTUNG_BODEN, rauschen + BEDEUTUNG_ABSTAND),
                )
        return _Bedeutung(je_bereich, schwellen)
    except Exception as exc:  # noqa: BLE001 - ohne Bedeutung bleiben die Wörter
        logger.warning("Gedaechtnis: Bedeutungssuche fehlgeschlagen error=%s", type(exc).__name__)
        return _KEINE_BEDEUTUNG


# ── Wörter ───────────────────────────────────────────────────────────────────


def _woerter(db: Session, kennungen: list[str], text: str, anzahl: int) -> dict[str, int]:
    """Die Erinnerungen mit den meisten Wörtern des Textes — ohne einen Text zu öffnen."""
    woerter = ai_memory_service._tokens(text)
    if not woerter or not kennungen:
        return {}
    werte = [
        wert for kennung in kennungen for wert in ai_memory_service.begriff_werte(kennung, woerter)
    ]
    treffer = func.count(AiMemoryBegriff.begriff).label("treffer")
    zeilen = (
        db.query(AiMemoryBegriff.memory_id, treffer)
        .join(AiMemoryEntry, AiMemoryEntry.id == AiMemoryBegriff.memory_id)
        .filter(
            AiMemoryBegriff.begriff.in_(werte),
            AiMemoryEntry.status == "aktiv",
            AiMemoryEntry.scope_identity.in_(kennungen),
        )
        .group_by(AiMemoryBegriff.memory_id)
        .order_by(treffer.desc(), AiMemoryBegriff.memory_id)
        .limit(anzahl)
        .all()
    )
    return {zeilen_id: int(zahl) for zeilen_id, zahl in zeilen}


# ── Treffer ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Treffer:
    row: AiMemoryEntry
    text: str
    rang: float


def _treffer(
    db: Session,
    kennungen: list[str],
    frage: str,
    *,
    still: bool,
    ausser: set[str] = frozenset(),
    bedeutung: _Bedeutung | None = None,
) -> list[Treffer]:
    """Was die Frage trifft, das Passendste zuerst.

    Kandidaten aus beiden Wegen, höchstens 2 × `KANDIDATEN`, in einem Aufruf
    geöffnet; mit kommt nur, wen die Frage über Bedeutung oder ein Wort trifft
    (`ai_memory_service._reiz`). Geordnet wie bisher nach Bedeutung, Wortbezug,
    Nutzung und Aktualität (`ai_memory_service._relevance`).
    """
    if bedeutung is None:
        bedeutung = _bedeutung(db, kennungen, [frage])
    ids = set(bedeutung.beste(KANDIDATEN)) | set(_woerter(db, kennungen, frage, KANDIDATEN))
    ids -= set(ausser)
    if not ids:
        return []
    rows = (
        db.query(AiMemoryEntry)
        .filter(
            AiMemoryEntry.id.in_(sorted(ids)),
            AiMemoryEntry.status == "aktiv",
            AiMemoryEntry.scope_identity.in_(kennungen),
        )
        .all()
    )
    geoeffnet = (
        ai_memory_service._entschluesseln(rows)
        if still
        else ai_memory_service._entschluesseln_lesbare(rows)
    )
    jetzt = _jetzt()
    woerter = ai_memory_service._tokens(frage)
    ergebnis: list[Treffer] = []
    for row, text in geoeffnet:
        bezug = len(woerter & ai_memory_service._tokens(ai_memory_service._wortquelle(row, text)))
        if not bedeutung.trifft(row, bezug):
            continue
        wert = bedeutung.wert(row)
        ergebnis.append(Treffer(row, text, ai_memory_service._relevance(row, text, woerter, jetzt, wert)))
    ergebnis.sort(key=lambda treffer: (-treffer.rang, treffer.row.id))
    return ergebnis


def _gebraucht(db: Session, rows: list[AiMemoryEntry]) -> None:
    """Gebraucht ist, wen die Frage getroffen hat — Anzeigen ist kein Gebrauch."""
    if not rows:
        return
    jetzt = _jetzt()
    for row in rows:
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = jetzt
    db.flush()


# ── Der Abruf im Chat ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Abruf:
    """Die beiden Teile des Gedächtnisses für eine Anfrage — jeder darf fehlen."""

    kopf: str | None
    passend: str | None


def _kopf(
    db: Session, kennungen: list[str], zeichen: int
) -> tuple[list[tuple[AiMemoryEntry, str]], int, int]:
    """Was „im Kopf“ steht: die Zeilen, ihre Zeichen und wieviele ausgelassen sind.

    Passt der ganze Bestand ins Budget, steht er ganz hier. Sonst bekommt der
    Kopf `KOPF_ANTEIL` davon und der Rest gehört dem, was zur Frage passt.
    """
    gesamt = (
        db.query(func.count(AiMemoryEntry.id))
        .filter(AiMemoryEntry.scope_identity.in_(kennungen), AiMemoryEntry.status == "aktiv")
        .scalar()
        or 0
    )
    if not gesamt:
        return [], 0, 0
    zeilen = max(
        1,
        ai_memory_service.MAX_CONTEXT_ROWS * zeichen // ai_memory_service.MAX_CONTEXT_CHARS,
    )
    rows = (
        db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.scope_identity.in_(kennungen), AiMemoryEntry.status == "aktiv")
        .order_by(
            AiMemoryEntry.angeheftet.desc(),
            AiMemoryEntry.wichtigkeit.desc(),
            AiMemoryEntry.created_at.desc(),
            AiMemoryEntry.id.desc(),
        )
        .limit(zeilen)
        .all()
    )
    geoeffnet = ai_memory_service._entschluesseln(rows)
    # Zeilen von vor Stufe 4 bekommen Vektor und Wortindex, sobald ihr Text
    # ohnehin offen liegt; den Rest holt der Takt nach (`nachziehen`).
    ai_memory_service.indizes_nachziehen(db, geoeffnet)
    zeilen_text = [ai_memory_service._memory_line(row, text) for row, text in geoeffnet]
    laenge = sum(len(zeile) + 1 for zeile in zeilen_text)
    if len(rows) == gesamt and laenge <= zeichen:
        return geoeffnet, laenge, 0
    grenze = int(zeichen * KOPF_ANTEIL)
    gewaehlt: list[tuple[AiMemoryEntry, str]] = []
    benutzt = 0
    for paar, zeile in zip(geoeffnet, zeilen_text):
        if benutzt + len(zeile) + 1 > grenze:
            # Schluss an der ersten Zeile, die nicht passt — kein Auffüllen mit
            # kürzeren von weiter hinten. Sonst hinge der Kopf an der Länge
            # statt am Rang, und was dahinter zur Frage passt, fände hinten
            # keinen Platz, weil es zufällig vorn steht.
            break
        gewaehlt.append(paar)
        benutzt += len(zeile) + 1
    return gewaehlt, benutzt, gesamt - len(gewaehlt)


def abrufen(
    db: Session,
    user: User,
    query: str = "",
    server_id: int | None = None,
    budget: int | None = None,
) -> Abruf:
    """Das Gedächtnis für eine Anfrage: was im Kopf steht und was zur Frage passt.

    ``budget`` ist der Platz in Zeichen für beide Teile zusammen
    (`ai_context_service.teilbudgets(...).gedaechtnis_zeichen`); ohne Angabe
    gilt `ai_memory_service.MAX_CONTEXT_CHARS`. Ohne ``query`` gibt es nur den
    Kopf — die Stimme bekommt ihn zu Beginn einer Sitzung.

    Als gebraucht vermerkt wird, wen die Frage getroffen hat, im Kopf wie
    hinten; was nur mitgeht, zählt nicht. Das Zählwerk entscheidet mit, was bei
    knappem Platz vorn bleibt, und darf nicht am bloßen Danebenliegen hängen.
    """
    zeichen = budget if budget is not None else ai_memory_service.MAX_CONTEXT_CHARS
    kennungen = sichtbare_bereiche(
        db, user, persoenlich=ai_memory_service.preference(db, user.id), anlage=server_id,
    )
    kopf, kopf_zeichen, ausgelassen = _kopf(db, kennungen, zeichen)
    frage = query.strip()
    passend: list[Treffer] = []
    gebraucht: list[AiMemoryEntry] = []
    if frage and (kopf or ausgelassen):
        bedeutung = _bedeutung(db, kennungen, [frage])
        woerter = ai_memory_service._tokens(frage)
        for row, text in kopf:
            bezug = len(woerter & ai_memory_service._tokens(ai_memory_service._wortquelle(row, text)))
            if bedeutung.trifft(row, bezug):
                gebraucht.append(row)
        if ausgelassen:
            platz = zeichen - kopf_zeichen
            for treffer in _treffer(
                db, kennungen, frage, still=True,
                ausser={row.id for row, _ in kopf}, bedeutung=bedeutung,
            ):
                zeile = ai_memory_service._memory_line(treffer.row, treffer.text)
                if len(zeile) + 1 > platz:
                    continue
                passend.append(treffer)
                platz -= len(zeile) + 1
            gebraucht.extend(treffer.row for treffer in passend)
    _gebraucht(db, gebraucht)

    kopf_text = "\n".join(ai_memory_service._memory_line(row, text) for row, text in kopf)
    if ausgelassen:
        # Ehrlich bleiben: das Modell soll aus einer Lücke nicht schließen, es
        # gebe nichts. Die Zahl hängt am Bestand, nicht an der Frage — der
        # Kopf bleibt bytegleich.
        kopf_text += (
            ("\n" if kopf_text else "")
            + f"[Hinweis] {ausgelassen} weitere Eintraege sind hier ausgelassen; "
            "was zur Frage passt, steht weiter hinten, und search_memory findet gezielt."
        )
    passend_text = "\n".join(
        ai_memory_service._memory_line(treffer.row, treffer.text) for treffer in passend
    )
    return Abruf(kopf=kopf_text or None, passend=passend_text or None)


def anlagenwissen(db: Session, user: User, server_id: int, query: str = "") -> str | None:
    """Nur das Wissen **einer** Anlage, als fertiger Block — nachgereicht mitten im Lauf.

    Der Kontext entsteht beim Anlegen des Laufs; um welchen Server es geht,
    klärt oft erst das erste Werkzeug. Gezählt wird wie im ganzen Abruf: wen
    die Frage getroffen hat. Eine Anlage fasst höchstens
    `ai_limit_service.MAX_SYSTEM_SCOPE_ENTRIES` Einträge; sie gehen ganz mit.
    """
    if not permission_service.has_server_permission(
        db=db, user=user, server_id=server_id, key="server.view"
    ):
        return None
    kennung = f"server:{server_id}:shared"
    rows = (
        db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.scope_identity == kennung, AiMemoryEntry.status == "aktiv")
        .order_by(
            AiMemoryEntry.angeheftet.desc(),
            AiMemoryEntry.wichtigkeit.desc(),
            AiMemoryEntry.created_at.desc(),
            AiMemoryEntry.id.desc(),
        )
        .limit(ai_memory_service.MAX_CONTEXT_ROWS)
        .all()
    )
    geoeffnet = ai_memory_service._entschluesseln(rows)
    if not geoeffnet:
        return None
    frage = query.strip()
    if frage:
        bedeutung = _bedeutung(db, [kennung], [frage])
        woerter = ai_memory_service._tokens(frage)
        _gebraucht(db, [
            row for row, text in geoeffnet
            if bedeutung.trifft(
                row, len(woerter & ai_memory_service._tokens(ai_memory_service._wortquelle(row, text))),
            )
        ])
    return "\n".join(ai_memory_service._memory_line(row, text) for row, text in geoeffnet)


# ── Suchen ───────────────────────────────────────────────────────────────────


def suchen(db: Session, user: User, query: str, limit: int) -> list[Treffer]:
    """`search_memory`: über alles, was der Benutzer gerade sehen darf.

    Dieselben Bereiche wie im Chat — **einschließlich der Einwilligung**: eine
    Suche kann nichts aufdecken, was ohne sie verborgen wäre —, dazu das
    Wissen aller sichtbaren Anlagen. Die gemeldeten Treffer gelten als
    gebraucht: hier hat jemand ausdrücklich gesucht.
    """
    frage = query.strip()
    if not frage:
        return []
    kennungen = sichtbare_bereiche(
        db, user, persoenlich=ai_memory_service.preference(db, user.id), anlage=True,
    )
    treffer = _treffer(db, kennungen, frage, still=True)[:limit]
    _gebraucht(db, [eintrag.row for eintrag in treffer])
    return treffer


def verwaltung_suchen(db: Session, kennungen: list[str], query: str) -> list[tuple[AiMemoryEntry, str]]:
    """Die Suche der Verwaltungsansicht: über den ganzen Bereich, nicht nur die Seite.

    Zählt nicht als Gebrauch — wer aufräumt, benutzt nichts. Ein toter Sidecar
    fliegt bis zum Router (503), wie beim Blättern.
    """
    frage = query.strip()
    if not frage:
        return []
    return [
        (eintrag.row, eintrag.text)
        for eintrag in _treffer(db, kennungen, frage, still=False)[:MAX_SUCHTREFFER]
    ]


# ── Für den Schreiber ────────────────────────────────────────────────────────


def naechste(db: Session, kennung: str, texte: list[str], anzahl: int) -> list[str]:
    """Die Erinnerungen eines Bereichs, die den Texten am nächsten sind — als Kennungen.

    Für den Gedächtnisschreiber und den Import bei großen Bereichen: statt den
    ganzen Bereich zu laden, kommen bis zu 2 × ``anzahl`` Kandidaten aus
    Bedeutung und Wörtern; ohne beides die zuletzt gebrauchten.
    """
    kennungen = [kennung]
    ids = list(_bedeutung(db, kennungen, texte).beste(anzahl))
    for zeilen_id in _woerter(db, kennungen, "\n".join(texte), anzahl):
        if zeilen_id not in ids:
            ids.append(zeilen_id)
    if ids:
        return ids
    zuletzt = func.coalesce(AiMemoryEntry.last_used_at, AiMemoryEntry.updated_at, AiMemoryEntry.created_at)
    return [
        zeilen_id
        for (zeilen_id,) in db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.scope_identity == kennung, AiMemoryEntry.status == "aktiv")
        .order_by(zuletzt.desc(), AiMemoryEntry.id)
        .limit(anzahl)
        .all()
    ]


# ── Nachziehen im Takt ───────────────────────────────────────────────────────


def nachziehen(stapel: int = NACHZIEHEN_JE_TAKT) -> int:
    """Vektor und Wortindex für Erinnerungen, die noch keine haben. Läuft im Takt.

    Offen ist eine Zeile ohne `indiziert_am` (vor Stufe 4 entstanden), und bei
    geladenem Modell eine ohne Vektor aus diesem Modell oder mit unverpacktem.
    Je Lauf höchstens ``stapel``, in einem Aufruf geöffnet; was zuletzt dran
    war, kommt zuletzt wieder. Antwortet der Sidecar nicht, bleibt alles
    offen — sonst stünden Zeilen als erledigt da, deren Wörter fehlen.
    """
    with SessionLocal() as db:
        modell = ai_embedding_service.aktives_modell(db=db)
        offen = [AiMemoryEntry.indiziert_am.is_(None)]
        if modell is not None:
            offen += [
                AiMemoryEntry.embedding_model.is_(None),
                AiMemoryEntry.embedding_model != modell,
                func.length(AiMemoryEntry.embedding_bytes) == EMBEDDING_BYTES,
            ]
        rows = (
            db.query(AiMemoryEntry)
            .filter(AiMemoryEntry.status == "aktiv", or_(*offen))
            .order_by(AiMemoryEntry.indiziert_am.asc().nulls_first(), AiMemoryEntry.id)
            .limit(stapel)
            .all()
        )
        if not rows:
            return 0
        try:
            geoeffnet = ai_memory_service._entschluesseln_lesbare(rows)
        except DisSidecarError:
            db.rollback()
            return 0
        anzahl = ai_memory_service.indizes_nachziehen(db, geoeffnet, auch_vektoren=True)
        lesbar = {id(row) for row, _text in geoeffnet}
        jetzt = _jetzt()
        for row in rows:
            if id(row) not in lesbar:
                # Unlesbar: hinten anstellen, statt jeden Takt vorn zu stehen.
                row.indiziert_am = jetzt
        db.commit()
        return anzahl
