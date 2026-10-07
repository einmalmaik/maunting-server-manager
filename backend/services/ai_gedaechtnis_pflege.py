"""Gedächtnis v2, Stufe 5: die Pflege in der Nacht („Schlafphase“).

Zwei Dinge, die der Schreiber im Gespräch nicht leisten kann:

* **Stärke.** Einmal am Tag rechnet `rang_auffrischen` für jede Erinnerung
  Wichtigkeit × Präsenz (`ai_memory_service.kopf_rang`). Danach ordnet sich,
  was „im Kopf“ steht: Seltenes tritt zurück, Gebrauchtes bleibt vorn, nichts
  wird gelöscht. Einmal am Tag und nicht je Anfrage, weil der Kopf sonst mit
  jeder Frage anders aussähe und der Zwischenspeicher beim Anbieter bräche.
  Kein Modell, kein Klartext — eine Abfrage.
* **Pflege mit dem Gedächtnismodell**, je Bereich, nachts in der Zone des
  Zahlers (`NACHT`): Einträge, die seit der letzten Pflege neu oder geändert
  sind, gehen mit ihren ähnlichsten Nachbarn an das Modell, dazu Pläne und
  Termine, deren Tag vorbei ist (`faellig_am`). Das Modell darf zwei Dinge:
  zusammenführen, was dasselbe sagt (vor allem aus dem umgeschriebenen
  Altbestand, der bewusst nichts zusammenführt), und Abgelaufenes in die
  Vergangenheit setzen, ohne einen Ausgang zu behaupten. Steht nichts
  Ähnliches da und ist nichts fällig, fragt die Pflege gar nicht.

**Was geschützt bleibt.** Einträge des Menschen behalten ihren Wortlaut;
nur ein abgelaufener Plan wird umgeschrieben, und der alte Satz bleibt als
Fassung. Was aufgeht, ist 30 Tage zurückholbar. Wer zahlt, wie beim
Altbestand (`ai_gedaechtnis_altbestand._zahler`): der Besitzer mit
Einwilligung, beim Team der Gründer, sonst das Betreiberkonto — auf sein
Kontingent, mit Zweck „gedaechtnis“.

Läuft das Panel nachts nicht, pflegt es nach anderthalb Tagen auch tagsüber
(`SPAETESTENS`). Inhalte stehen in keinem Log.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiMemoryEntry, AiMemoryPflege, AiMemoryPreference, AiProvider, User
from services import ai_embedding_service, ai_gedaechtnis_abruf, ai_lage, ai_memory_service
from services import ai_gedaechtnis_schreiber as schreiber
from services.ai_gedaechtnis_altbestand import _bereichsname, _zahler
from services.ai_provider_service import resolve_api_key
from services.dis_client import DisDecryptionError


logger = logging.getLogger(__name__)

#: Die Stunden der Nacht in der Zone des Zahlers, in denen gepflegt wird.
NACHT = range(2, 6)
#: Ist ein Bereich so lange ungepflegt, wird auch tagsüber gepflegt.
SPAETESTENS = timedelta(hours=36)
#: Höchstens so viele Bereiche zugleich.
MAX_BEREICHE = 2
#: Neue oder geänderte Einträge je Lauf; was darüber ist, kommt im nächsten.
NEU_JE_LAUF = 20
#: Abgelaufene Pläne je Lauf.
FAELLIG_JE_LAUF = 20
#: Ähnliche Einträge aus dem Bestand, die mitkommen.
NACHBARN = 30
#: Ab dieser Ähnlichkeit gilt ein Eintrag als Nachbar. Darunter gibt es
#: nichts zusammenzuführen, und ohne Nachbarn fragt die Pflege nicht.
#: Gemessen am 07.10.2026 mit dem lokalen Modell: Sätze, die dasselbe sagen,
#: lagen bei 0,52 bis 0,91 („Backups laufen nachts um drei“ gegen „Die
#: Sicherung startet täglich um 3 Uhr“ 0,52), verschiedene Fakten desselben
#: Themas bei 0,12 bis 0,62. Was darüber mitkommt, entscheidet das Modell.
NAH_AB = 0.5
#: Findet ein Takt nichts, schaut erst der nach dieser Pause wieder.
LEERLAUF = timedelta(minutes=15)
#: Nach so vielen unbrauchbaren Antworten gilt der Lauf als ohne Änderung.
MAX_UNBRAUCHBAR = 3
#: Geschätzte Ausgabe für die Reservierung, dazu ein Viertel der Zeichen.
AUSGABE_SCHAETZUNG = 600
#: Ab dieser Stunde (UTC) rechnet der erste Takt des Tages den Kopfrang neu.
RANG_STUNDE_UTC = 3

WERKZEUG_NAME = "gedaechtnis_pflegen"

WERKZEUG: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WERKZEUG_NAME,
        "description": "Trage ein, was die Pflege ändert. Eine leere Liste, wenn nichts zu tun ist.",
        "parameters": {
            "type": "object",
            "properties": {
                "aenderungen": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "aktion": {"type": "string", "enum": ["zusammenfuehren", "zeit"]},
                            "eintrag": {
                                "type": "string",
                                "description": "E-Nummer: der bleibende oder der fällige Eintrag.",
                            },
                            "aufgenommen": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Nur bei zusammenfuehren: die E-Nummern, die aufgehen.",
                            },
                            "text": {
                                "type": "string",
                                "description": (
                                    "Der ganze neue Stand, ein bis fünf Sätze. Bei zeit immer; "
                                    "bei zusammenfuehren weglassen, wenn ein Eintrag vom "
                                    "Benutzer bleibt."
                                ),
                            },
                            "titel": {"type": "string", "description": "Zwei bis sechs Wörter."},
                            "thema": {"type": "string", "description": "Ein oder zwei Wörter."},
                            "wichtigkeit": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["aktion", "eintrag"],
                    },
                },
            },
            "required": ["aenderungen"],
        },
    },
}

SYSTEMPROMPT = """Du pflegst nachts das Gedächtnis von Singra, der KI eines Gameserver-Panels. Du bekommst Einträge eines Bereichs: neue oder geänderte [neu], Pläne und Termine, deren Tag vorbei ist [fällig], und ähnliche aus dem Bestand.

Du darfst zwei Dinge tun, sonst nichts:
- zusammenfuehren: Sagen Einträge dasselbe oder gehören sie zur selben Sache, wird einer daraus. eintrag bleibt, aufgenommen geht in ihm auf. text ist der ganze neue Stand mit allem, was davon gilt – nichts erfinden, nichts Geltendes weglassen; widersprechen sie sich, gilt der jüngere.
  Einträge „vom Benutzer“ behalten ihren Wortlaut. Ist einer dabei, ist er eintrag, die von Singra sind aufgenommen, und text lässt du weg. Zwei vom Benutzer führst du nicht zusammen.
- zeit: Ein [fällig]-Eintrag beschreibt etwas, dessen Tag vorbei ist. Schreib in text den neuen Satz in der Vergangenheit, ohne einen Ausgang zu behaupten: aus „Der Benutzer plant für Juli 2026 eine Reise nach Singapur.“ wird „Der Benutzer plante für Juli 2026 eine Reise nach Singapur.“ Dass es stattfand, schreibst du nur, wenn ein anderer Eintrag es sagt.

Was nur ähnlich klingt, bleibt getrennt. Im Zweifel nichts – eine leere Liste ist oft die richtige Antwort. Schreib in der Sprache der Einträge."""


@dataclass(frozen=True)
class _Posten:
    kennung: str
    id: str
    fassung: int
    vom_menschen: bool
    faellig: bool


@dataclass
class Pflege:
    #: ``ok`` (gelaufen, auch ohne Änderung), ``mehr`` (gelaufen, es wartet
    #: noch Neues), ``leer`` (nichts Neues, nichts fällig), ``ruhig`` (nichts
    #: Ähnliches, ohne Aufruf abgeschlossen), ``spaeter`` (kein Zahler, kein
    #: Zugang, kein Kontingent), ``fehler`` und ``unbrauchbar``.
    status: str
    zusammengefuehrt: int = 0
    umgeschrieben: int = 0
    verworfen: int = 0


@dataclass(repr=False)
class _Vorbereitet:
    zahler_id: int
    provider: AiProvider
    api_key: str | None
    modell: str
    preise: tuple[int | None, int | None, int | None]
    messages: list[dict]
    request_id: Any
    eingabe: int
    posten: list[_Posten]
    #: Bis wohin gelesen ist, wenn der Lauf gelingt.
    bis: datetime
    mehr: bool


@dataclass
class _Stand:
    ruhe_bis: datetime | None = None
    fehlversuche: int = 0
    unbrauchbar: int = 0


_LAUFEND: dict[str, asyncio.Task] = {}
_STAND: dict[str, _Stand] = {}
_LEERLAUF_BIS: datetime | None = None
_RANG_TAG: date | None = None


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


# ── Stärke ───────────────────────────────────────────────────────────────


def rang_auffrischen(db: Session, *, jetzt: datetime | None = None) -> int:
    """Rechnet den Kopfrang aller geltenden Erinnerungen neu. Committet nicht.

    Dieselbe Rechnung wie `ai_memory_service.kopf_rang`, in einer Abfrage.
    Der Exponent ist bei 700 gedeckelt: PostgreSQL meldet bei ``exp`` darunter
    einen Unterlauf, statt 0 zu liefern.
    """
    jetzt = jetzt or _jetzt()
    # Eine Abfrage im Klartext löst kein automatisches Schreiben aus: was in
    # der Sitzung noch offen ist, muss vorher in die Datenbank.
    db.flush()
    start = " ".join(
        f"WHEN {stufe} THEN {tage}" for stufe, tage in ai_memory_service.HALTBARKEIT_START.items()
    )
    ergebnis = db.execute(
        text(
            "UPDATE ai_memory_entries SET kopf_rang = wichtigkeit * exp(-least(700, greatest(0, "
            "extract(epoch FROM CAST(:jetzt AS timestamptz) - coalesce(last_used_at, created_at)) "
            f"/ 86400.0) / coalesce(haltbarkeit_tage, CASE wichtigkeit {start} ELSE 30 END))) "
            "WHERE status = 'aktiv'"
        ),
        {"jetzt": jetzt},
    )
    return int(ergebnis.rowcount or 0)


def rang_takt(*, jetzt: datetime | None = None) -> int | None:
    """Einmal am Tag, im ersten Takt ab `RANG_STUNDE_UTC`. Eigene Sitzung, im Thread.

    ``None`` heißt: heute schon gerechnet.
    """
    global _RANG_TAG
    jetzt = jetzt or _jetzt()
    if jetzt.hour < RANG_STUNDE_UTC or _RANG_TAG == jetzt.date():
        return None
    with SessionLocal() as db:
        anzahl = rang_auffrischen(db, jetzt=jetzt)
        db.commit()
    _RANG_TAG = jetzt.date()
    return anzahl


# ── Der Takt ─────────────────────────────────────────────────────────────


def _im_fenster(db: Session, identity: str, jetzt: datetime) -> bool | None:
    """Ob der Bereich jetzt dran ist — ``None``: er hat keinen Zahler."""
    row = (
        db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv")
        .first()
    )
    zahler = _zahler(db, row) if row is not None else None
    if zahler is None:
        return None
    stand = db.get(AiMemoryPflege, identity)
    if stand is None or ai_memory_service._utc(stand.gelaufen_am) < jetzt - SPAETESTENS:
        return True
    zone = ZoneInfo(ai_lage.zone_des_benutzers(zahler))
    return jetzt.astimezone(zone).hour in NACHT


def faellige_starten(db: Session, *, jetzt: datetime | None = None) -> list[str]:
    """Bereiche mit Neuem oder Fälligem suchen und je eine Pflege beginnen. Nur im Ereignisloop."""
    global _LEERLAUF_BIS
    jetzt = jetzt or _jetzt()
    frei = MAX_BEREICHE - len(_LAUFEND)
    if frei <= 0 or (_LEERLAUF_BIS is not None and jetzt < _LEERLAUF_BIS):
        return []
    ruhend = [
        kennung for kennung, stand in _STAND.items()
        if stand.ruhe_bis is not None and stand.ruhe_bis > jetzt
    ]
    abfrage = (
        db.query(AiMemoryEntry.scope_identity)
        .outerjoin(AiMemoryPflege, AiMemoryPflege.scope_identity == AiMemoryEntry.scope_identity)
        .outerjoin(AiMemoryPreference, AiMemoryPreference.user_id == AiMemoryEntry.owner_user_id)
        .filter(
            AiMemoryEntry.status == "aktiv",
            or_(
                AiMemoryEntry.scope.notin_(ai_memory_service.PERSOENLICHE_SCOPES),
                AiMemoryPreference.enabled.is_(True),
            ),
            or_(
                AiMemoryPflege.gepflegt_bis.is_(None),
                AiMemoryEntry.updated_at > AiMemoryPflege.gepflegt_bis,
                # Fällig nach dem Tag in UTC: ob ein vergangener Plan einen
                # Tag früher oder später umgeschrieben wird, ändert nichts.
                AiMemoryEntry.faellig_am <= jetzt.date(),
            ),
        )
    )
    ausgenommen = ruhend + list(_LAUFEND)
    if ausgenommen:
        abfrage = abfrage.filter(AiMemoryEntry.scope_identity.notin_(ausgenommen))
    kandidaten = [
        kennung for (kennung,) in abfrage.distinct().order_by(AiMemoryEntry.scope_identity).limit(50).all()
    ]
    # Erst der Altbestand: was noch einen Namen trägt, führt die Pflege nicht zusammen.
    if kandidaten:
        mit_namen = {
            kennung for (kennung,) in db.query(AiMemoryEntry.scope_identity)
            .filter(
                AiMemoryEntry.scope_identity.in_(kandidaten),
                AiMemoryEntry.status == "aktiv",
                AiMemoryEntry.key_encrypted.is_not(None),
            )
            .distinct()
            .all()
        }
        kandidaten = [kennung for kennung in kandidaten if kennung not in mit_namen]
    begonnen: list[str] = []
    for kennung in kandidaten:
        if len(begonnen) >= frei:
            break
        fenster = _im_fenster(db, kennung, jetzt)
        if fenster is None:
            _STAND.setdefault(kennung, _Stand()).ruhe_bis = jetzt + schreiber.SPAETER
            continue
        if fenster and _starten(kennung):
            begonnen.append(kennung)
    if not begonnen and not _LAUFEND:
        _LEERLAUF_BIS = jetzt + LEERLAUF
    return begonnen


def _starten(identity: str) -> bool:
    if identity in _LAUFEND or len(_LAUFEND) >= MAX_BEREICHE:
        return False
    aufgabe = asyncio.get_running_loop().create_task(_bereich_sicher(identity))
    _LAUFEND[identity] = aufgabe
    aufgabe.add_done_callback(lambda _a: _LAUFEND.pop(identity, None))
    return True


async def _bereich_sicher(identity: str) -> None:
    ergebnis = await bereich_pflegen(identity)
    if ergebnis.status in ("ok", "mehr"):
        logger.info(
            "Gedaechtnis-Pflege: zusammengefuehrt=%s umgeschrieben=%s verworfen=%s",
            ergebnis.zusammengefuehrt, ergebnis.umgeschrieben, ergebnis.verworfen,
        )


async def bereich_pflegen(identity: str) -> Pflege:
    """Ein Lauf über einen Bereich: vorbereiten, fragen, anwenden."""
    stand = _STAND.get(identity)
    try:
        ergebnis = await _lauf(identity, stand.unbrauchbar if stand is not None else 0)
    except Exception as exc:  # noqa: BLE001 - ein Lauf darf nichts mitreißen
        logger.warning("Gedaechtnis-Pflege: Lauf gescheitert error=%s", type(exc).__name__)
        ergebnis = Pflege("fehler")
    _merken(identity, ergebnis)
    return ergebnis


def _merken(identity: str, ergebnis: Pflege) -> None:
    global _LEERLAUF_BIS
    jetzt = _jetzt()
    if ergebnis.status in ("ok", "mehr", "leer", "ruhig"):
        _STAND.pop(identity, None)
        if ergebnis.status == "mehr":
            _LEERLAUF_BIS = None
        return
    stand = _STAND.setdefault(identity, _Stand())
    if ergebnis.status == "spaeter":
        stand.ruhe_bis = jetzt + schreiber.SPAETER
        return
    stand.fehlversuche += 1
    if ergebnis.status == "unbrauchbar":
        stand.unbrauchbar += 1
    stand.ruhe_bis = jetzt + schreiber.rueckstellung(stand.fehlversuche)


# ── Ein Lauf ─────────────────────────────────────────────────────────────


def _abschliessen(db: Session, identity: str, bis: datetime, jetzt: datetime) -> None:
    """Hält fest, bis wohin gepflegt ist. Committet nicht."""
    stand = db.get(AiMemoryPflege, identity)
    if stand is None:
        db.add(AiMemoryPflege(scope_identity=identity, gepflegt_bis=bis, gelaufen_am=jetzt))
    else:
        stand.gepflegt_bis = bis
        stand.gelaufen_am = jetzt


def _nahe(db: Session, identity: str, neu: list[AiMemoryEntry]) -> tuple[list[str], bool]:
    """Die ähnlichsten Einträge zu den neuen — und ob die neuen sich selbst ähneln.

    Gerechnet wird mit den gespeicherten Vektoren (`ai_gedaechtnis_abruf`),
    ohne Modellaufruf. Ohne Modell oder Vektoren: nichts Ähnliches.
    """
    modell = ai_embedding_service.aktives_modell(db=db)
    if modell is None or not neu:
        return [], False
    import numpy as np

    bestand = ai_gedaechtnis_abruf._vektoren(db, [identity], modell).get(identity)
    if bestand is None or not bestand.ids:
        return [], False
    stellen = [bestand.position[row.id] for row in neu if row.id in bestand.position]
    if not stellen:
        return [], False
    fragen = bestand.matrix[stellen]
    werte = bestand.matrix @ fragen.T
    unter_sich = False
    if len(stellen) > 1:
        paare = fragen @ fragen.T
        np.fill_diagonal(paare, -1.0)
        unter_sich = bool((paare >= NAH_AB).any())
    beste = werte.max(axis=1)
    beste[stellen] = -1.0
    reihenfolge = np.argsort(-beste)[:NACHBARN]
    return [bestand.ids[i] for i in reihenfolge if beste[i] >= NAH_AB], unter_sich


def _nachricht(
    db: Session,
    identity: str,
    geoeffnet: list[tuple[AiMemoryEntry, str, str]],
    zone_name: str,
    jetzt: datetime,
) -> str:
    zone = ZoneInfo(zone_name)
    lokal = jetzt.astimezone(zone)
    erste = geoeffnet[0][0]
    teile = [
        f"Heute: {ai_lage.WOCHENTAGE[lokal.weekday()]}, {lokal:%d.%m.%Y} ({zone_name}).",
        f"Bereich: {_bereichsname(db, erste)}",
    ]
    themen = schreiber._themen(db, [schreiber.Bereich(
        kennung="B1", scope=erste.scope, identity=identity, name="",
    )])
    if themen:
        teile.append("Vorhandene Themen: " + ", ".join(schreiber._flach(t) for t in themen))
    zeilen = []
    for index, (row, text_, marke) in enumerate(geoeffnet):
        angelegt = ai_memory_service._utc(row.created_at).astimezone(zone)
        herkunft = "vom Benutzer" if row.origin == "user" else "von Singra"
        titel = schreiber._flach(row.titel or "")
        zeilen.append(
            f"E{index + 1}{marke} (angelegt {angelegt:%d.%m.%Y}, {herkunft}"
            f"{', Wichtigkeit ' + str(row.wichtigkeit) if row.wichtigkeit else ''})"
            f"{' ' + titel + ':' if titel else ''} {schreiber._flach(text_)}"
        )
    teile.append("Einträge:\n" + "\n".join(zeilen))
    return "\n\n".join(teile)


def _vorbereiten(identity: str, jetzt: datetime) -> _Vorbereitet | Pflege:
    """Auswahl, Zahler, Zugang und Reservierung — in einer Sitzung."""
    with SessionLocal() as db:
        stand = db.get(AiMemoryPflege, identity)
        basis = db.query(AiMemoryEntry).filter(
            AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv",
        )
        neu_abfrage = basis
        if stand is not None:
            neu_abfrage = neu_abfrage.filter(
                AiMemoryEntry.updated_at > ai_memory_service._utc(stand.gepflegt_bis)
            )
        neu = (
            neu_abfrage.order_by(AiMemoryEntry.updated_at.asc(), AiMemoryEntry.id.asc())
            .limit(NEU_JE_LAUF + 1)
            .all()
        )
        mehr = len(neu) > NEU_JE_LAUF
        neu = neu[:NEU_JE_LAUF]
        bis = ai_memory_service._utc(neu[-1].updated_at) if mehr else jetzt
        erste = neu[0] if neu else basis.first()
        if erste is None:
            return Pflege("leer")
        zahler = _zahler(db, erste)
        if zahler is None:
            return Pflege("spaeter")
        zahler_id = zahler.id
        zone_name = ai_lage.zone_des_benutzers(zahler)
        faellig = (
            basis.filter(AiMemoryEntry.faellig_am <= jetzt.date())
            .order_by(AiMemoryEntry.faellig_am.asc(), AiMemoryEntry.id.asc())
            .limit(FAELLIG_JE_LAUF)
            .all()
        )
        if not neu and not faellig:
            _abschliessen(db, identity, jetzt, jetzt)
            db.commit()
            return Pflege("leer")
        nachbarn, unter_sich = _nahe(db, identity, neu)
        if not faellig and not nachbarn and not unter_sich:
            # Nichts Ähnliches, nichts fällig: es gibt nichts zu fragen.
            _abschliessen(db, identity, bis, jetzt)
            db.commit()
            return Pflege("mehr" if mehr else "ruhig")
        faellig_ids = {row.id for row in faellig}
        neu_ids = {row.id for row in neu}
        gesehen: set[str] = set()
        reihe: list[AiMemoryEntry] = []
        for row in [*faellig, *neu]:
            if row.id not in gesehen:
                gesehen.add(row.id)
                reihe.append(row)
        if nachbarn:
            dazu = basis.filter(AiMemoryEntry.id.in_([i for i in nachbarn if i not in gesehen])).all()
            nach_rang = {zeilen_id: stelle for stelle, zeilen_id in enumerate(nachbarn)}
            reihe.extend(sorted(dazu, key=lambda row: nach_rang[row.id]))
        geoeffnet = ai_memory_service._entschluesseln_lesbare(reihe)
        if not geoeffnet:
            return Pflege("fehler")
        gewaehlt = schreiber.gedaechtnis_anbieter(db, zahler, None)
        if gewaehlt is None:
            return Pflege("spaeter")
        provider, modell, preise = gewaehlt
        api_key = resolve_api_key(db, provider, zahler_id)
        if provider.requires_api_key and not api_key:
            return Pflege("spaeter")
        markiert = [
            (row, text_, " [fällig]" if row.id in faellig_ids else " [neu]" if row.id in neu_ids else "")
            for row, text_ in geoeffnet
        ]
        inhalt = _nachricht(db, identity, markiert, zone_name, jetzt)
        eingabe = max(1, (len(SYSTEMPROMPT) + len(inhalt)) // 4)
        ausgabe = AUSGABE_SCHAETZUNG + sum(len(t) for _r, t in geoeffnet) // 4
        request_id = schreiber._reservieren(db, zahler, provider, modell, preise, eingabe, ausgabe)
        if request_id is None:
            return Pflege("spaeter")
        posten = [
            _Posten(
                f"E{index + 1}", row.id, int(row.fassung or 1),
                row.origin == "user", row.id in faellig_ids,
            )
            for index, (row, _t) in enumerate(geoeffnet)
        ]
        db.refresh(provider)
        db.expunge(provider)
    return _Vorbereitet(
        zahler_id=zahler_id, provider=provider, api_key=api_key, modell=modell, preise=preise,
        messages=[
            {"role": "system", "content": SYSTEMPROMPT},
            {"role": "user", "content": inhalt},
        ],
        request_id=request_id, eingabe=eingabe, posten=posten, bis=bis, mehr=mehr,
    )


async def _lauf(identity: str, unbrauchbar_bisher: int) -> Pflege:
    vorbereitet = await asyncio.to_thread(_vorbereiten, identity, _jetzt())
    if isinstance(vorbereitet, Pflege):
        return vorbereitet
    v = vorbereitet
    usage, aufruf, gescheitert = await schreiber._formular(
        v.provider, v.api_key, v.modell, v.messages, WERKZEUG
    )
    aenderungen = None if gescheitert or aufruf is None else _aenderungen_lesen(aufruf.arguments)

    def _abrechnen_und_anwenden() -> Pflege:
        with SessionLocal() as db:
            schreiber._abrechnen(
                db, v.request_id, usage, aufruf,
                provider=v.provider, preise=v.preise, eingabe=v.eingabe,
                gescheitert=gescheitert,
            )
            db.commit()
            if gescheitert:
                return Pflege("fehler")
            if aenderungen is None and unbrauchbar_bisher + 1 < MAX_UNBRAUCHBAR:
                return Pflege("unbrauchbar")
            zahler = db.get(User, v.zahler_id)
            if zahler is None:
                return Pflege("spaeter")
            ergebnis = _anwenden(db, zahler, v.posten, aenderungen or [])
            # Was fällig war, ist jetzt gelesen — umgeschrieben oder nicht.
            # Sonst käme derselbe Plan jede Nacht wieder, und jede Nacht
            # kostet einen Aufruf.
            faellig = [p.id for p in v.posten if p.faellig]
            if faellig:
                db.query(AiMemoryEntry).filter(AiMemoryEntry.id.in_(faellig)).update(
                    {AiMemoryEntry.faellig_am: None}, synchronize_session=False,
                )
            _abschliessen(db, identity, v.bis, _jetzt())
            db.commit()
            ergebnis.status = "mehr" if v.mehr else "ok"
            return ergebnis

    return await asyncio.to_thread(_abrechnen_und_anwenden)


# ── Die Antwort lesen und anwenden ───────────────────────────────────────


def _aenderungen_lesen(argumente: Any) -> list[dict] | None:
    if isinstance(argumente, str):
        try:
            argumente = json.loads(argumente)
        except (TypeError, ValueError):
            return None
    if isinstance(argumente, dict):
        argumente = argumente.get("aenderungen", argumente.get("changes"))
    if isinstance(argumente, list):
        return [eintrag for eintrag in argumente if isinstance(eintrag, dict)]
    return None


class _Verworfen(Exception):
    pass


@dataclass
class _Lage:
    posten: dict[str, _Posten]
    verbraucht: set[str] = field(default_factory=set)


def _anwenden(db: Session, zahler: User, posten: list[_Posten], aenderungen: list[dict]) -> Pflege:
    """Jede Änderung in einem Sicherungspunkt; was nicht passt, wird verworfen.

    `DisSidecarError` bleibt ungefangen: antwortet die Verschlüsselung nicht,
    scheitert der ganze Lauf und kommt wieder.
    """
    lage = _Lage({p.kennung: p for p in posten})
    ergebnis = Pflege("ok")
    for roh in aenderungen:
        try:
            with db.begin_nested():
                aktion = _einzeln(db, zahler, roh, lage)
        except (_Verworfen, DisDecryptionError, HTTPException):
            ergebnis.verworfen += 1
            continue
        if aktion == "zeit":
            ergebnis.umgeschrieben += 1
        else:
            ergebnis.zusammengefuehrt += 1
    return ergebnis


def _einzeln(db: Session, zahler: User, roh: dict, lage: _Lage) -> str:
    aktion = str(roh.get("aktion") or "").strip().casefold()
    ziel = next(
        (lage.posten[k] for k in schreiber._kennungen(roh.get("eintrag"), "E") if k in lage.posten),
        None,
    )
    if ziel is None or ziel.kennung in lage.verbraucht:
        raise _Verworfen("eintrag")
    text_ = roh.get("text")
    neuer_text = schreiber._flach(text_) if isinstance(text_, str) and text_.strip() else None

    if aktion == "zeit":
        if not ziel.faellig or neuer_text is None:
            raise _Verworfen("zeit")
        row = db.get(AiMemoryEntry, ziel.id)
        ai_memory_service.erinnerung_aendern(
            db, user=zahler, entry_id=ziel.id, text=neuer_text,
            art="ereignis" if row is not None and row.art == "plan" else ai_memory_service.UNVERAENDERT,
            erwartete_fassung=ziel.fassung, von="ai", grund="aktualisiert",
            ueberschreibt_mensch=True, faellig_am=None, commit=False,
        )
        lage.verbraucht.add(ziel.kennung)
        return "zeit"

    if aktion != "zusammenfuehren":
        raise _Verworfen("aktion")
    andere = [
        lage.posten[k]
        for k in schreiber._kennungen(roh.get("aufgenommen"), "E")
        if k in lage.posten and k != ziel.kennung and k not in lage.verbraucht
    ]
    if not andere:
        raise _Verworfen("nichts_aufzunehmen")
    menschen = [p for p in andere if p.vom_menschen]
    if len(menschen) == 1 and not ziel.vom_menschen and neuer_text is None:
        # Nachsichtig gelesen: das Modell drehte in der Probe vom 07.10.2026
        # die Richtung um und ließ den Satz des Menschen in Singras Doppel
        # aufgehen, ohne neuen Text. Gemeint ist dasselbe: dann bleibt der
        # des Menschen.
        andere = [p for p in andere if not p.vom_menschen] + [ziel]
        ziel = menschen[0]
    # Der Wortlaut des Menschen bleibt: er geht nirgends auf, und wo er
    # bleibt, schreibt niemand seinen Text neu. Ein neuer Text hieße, dass
    # Singras Eintrag mehr sagt — dann bleiben beide stehen.
    if any(p.vom_menschen for p in andere) or (ziel.vom_menschen and neuer_text is not None):
        raise _Verworfen("mensch")
    zeilen = {
        row.id: row for row in db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.id.in_([ziel.id, *(p.id for p in andere)])).all()
    }
    if len(zeilen) != len(andere) + 1:
        raise _Verworfen("eintrag")
    ziel_row = zeilen[ziel.id]
    aufgehend = [zeilen[p.id] for p in andere]
    # Nichts geht verloren, auch nicht an Gewicht: das Ergebnis ist so wichtig,
    # so haltbar und so oft gebraucht wie alles, was in ihm aufgeht.
    wichtigkeit = max(
        [schreiber._wichtigkeit(roh) or 0, ziel_row.wichtigkeit, *(r.wichtigkeit for r in aufgehend)]
    )
    ai_memory_service.erinnerung_aendern(
        db, user=zahler, entry_id=ziel.id, text=neuer_text,
        titel=schreiber._wahlweise(roh, "titel") if neuer_text is not None else ai_memory_service.UNVERAENDERT,
        thema=schreiber._wahlweise(roh, "thema"),
        wichtigkeit=wichtigkeit, erwartete_fassung=ziel.fassung,
        von="ai", grund="zusammengefuehrt", commit=False,
    )
    for p, row in zip(andere, aufgehend):
        if int(row.fassung or 1) != p.fassung:
            raise _Verworfen("geaendert")
        ai_memory_service.erinnerung_aufnehmen(
            db, user=zahler, ziel_id=ziel.id, quelle_id=p.id, commit=False,
        )
        lage.verbraucht.add(p.kennung)
    ziel_row.angeheftet = bool(ziel_row.angeheftet) or any(r.angeheftet for r in aufgehend)
    ziel_row.use_count = int(ziel_row.use_count or 0) + sum(int(r.use_count or 0) for r in aufgehend)
    ziel_row.haltbarkeit_tage = max(ai_memory_service.haltbarkeit(r) for r in [ziel_row, *aufgehend])
    zuletzt = [r.last_used_at for r in [ziel_row, *aufgehend] if r.last_used_at is not None]
    if zuletzt:
        ziel_row.last_used_at = max(ai_memory_service._utc(z) for z in zuletzt)
    ziel_row.kopf_rang = ai_memory_service.kopf_rang(ziel_row, _jetzt())
    lage.verbraucht.add(ziel.kennung)
    return "zusammenfuehren"


__all__ = [
    "Pflege",
    "bereich_pflegen",
    "faellige_starten",
    "rang_auffrischen",
    "rang_takt",
]
