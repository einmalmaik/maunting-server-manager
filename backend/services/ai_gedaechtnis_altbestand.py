"""Altbestand in Sätze: Erinnerungen mit Namen bekommen die Form von Gedächtnis v2.

Bis Gedächtnis v2 war eine Erinnerung ein Paar aus Name und Wert
(„lieblingsessen“: „Pizza“). Seit Stufe 1 ist sie ein bis fünf Sätze, die ohne
Zusammenhang verständlich sind („Der Benutzer isst am liebsten Pizza.“), mit
Titel, Thema, Art und Wichtigkeit. Was davor entstand, schreibt dieser Schritt
um, im Takt des Gedächtnisschreibers (`scheduler_service._ai_tasks_task`): je
Bereich ein Stapel von 25 Einträgen in einem Modellaufruf, höchstens zwei
Bereiche zugleich.

**Was bleibt.** Die Aussage und ihre Herkunft — umgeschrieben wird die Form,
nicht der Inhalt. Der Stand davor bleibt als Fassung (Grund ``umgeschrieben``),
der alte Name ist ihr Titel. Fehlt ein Eintrag in der Antwort oder besteht
sein neuer Text die Prüfung nicht, wird er mechanisch umgestellt: der Wert
bleibt der Text, der Name wird der Titel
(`ai_memory_service.altbestand_umschreiben`).

**Wer zahlt.** Den eigenen Bereich (persönlich, eigene Servernotizen) der
Besitzer, und nur mit eingeschaltetem Gedächtnis und `ai.memory.use`: sein
Gedächtnis geht dafür an seinen Anbieter. Teamwissen der Gründer, Anlagen- und
Panelwissen das Betreiberkonto. Ohne Zahler wartet der Bereich; Einträge mit
Namen funktionieren bis dahin wie bisher.

**Wenn es scheitert.** Antwortet der Anbieter nicht, kommt der Stapel mit
Rückstellung wieder. Antwortet das Modell dreimal unbrauchbar, wird der Stapel
mechanisch umgestellt: dasselbe Material liefe sonst bei jedem Versuch gegen
dieselbe Wand, und jeder Versuch kostet einen Aufruf.

Inhalte stehen in keinem Log.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import SessionLocal
from models import AiMemoryEntry, AiMemoryPreference, AiProvider, Server, Team, User
from services import ai_gedaechtnis_schreiber as schreiber
from services import ai_lage, ai_memory_service, permission_service
from services.ai_provider_service import resolve_api_key
from services.dis_client import DisDecryptionError


logger = logging.getLogger(__name__)

#: Einträge je Modellaufruf.
STAPEL = 25
#: Höchstens so viele Bereiche zugleich; ein Takt beginnt nur, was frei ist.
MAX_BEREICHE = 2
#: So viele Zeilen öffnet ein Stapel, um 25 lesbare zu finden. Eine Zeile, die
#: sich nicht öffnen lässt, bleibt stehen und stünde sonst jedem Stapel vorn.
MAX_GEOEFFNET = 4 * STAPEL
#: Findet ein Takt keinen Altbestand, schaut erst der nach dieser Pause wieder.
LEERLAUF = timedelta(minutes=15)
#: Ein Bereich, dessen Zeilen sich alle nicht öffnen lassen.
UNLESBAR = timedelta(hours=6)
#: Nach so vielen unbrauchbaren Antworten am selben Stapel wird mechanisch
#: umgestellt.
MAX_UNBRAUCHBAR = 3
#: Geschätzte Ausgabe je Eintrag für die Reservierung, dazu ein Drittel der
#: Zeichen der Werte.
AUSGABE_JE_EINTRAG = 60

WERKZEUG_NAME = "gedaechtnis_umschreiben"

#: Ein Formular wie beim Schreiber (`ai_gedaechtnis_schreiber.WERKZEUG`).
WERKZEUG: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": WERKZEUG_NAME,
        "description": "Trage für jeden Eintrag der Liste seine neue Form ein.",
        "parameters": {
            "type": "object",
            "properties": {
                "eintraege": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "eintrag": {"type": "string", "description": "E-Nummer aus der Liste."},
                            "text": {
                                "type": "string",
                                "description": "Ein bis fünf Sätze, ohne Zusammenhang verständlich.",
                            },
                            "titel": {"type": "string", "description": "Zwei bis sechs Wörter."},
                            "thema": {
                                "type": "string",
                                "description": "Ein oder zwei Wörter; vorhandene Themen bevorzugen.",
                            },
                            "art": {"type": "string", "enum": list(ai_memory_service.ARTEN)},
                            "wichtigkeit": {"type": "integer", "minimum": 1, "maximum": 5},
                        },
                        "required": ["eintrag", "text"],
                    },
                },
            },
            "required": ["eintraege"],
        },
    },
}

SYSTEMPROMPT = """Du bringst alte Einträge aus dem Gedächtnis von Singra, der KI eines Gameserver-Panels, in ihre neue Form. Früher war ein Eintrag ein Name mit einem Wert („lieblingsessen“: „Pizza“). Heute ist er ein bis fünf Sätze, die ohne Zusammenhang verständlich sind, mit Titel, Thema, Art und Wichtigkeit.

Für jeden Eintrag der Liste schreibst du genau einen neuen Text:
- Dieselbe Aussage, nichts dazu und nichts weg. Was der Name andeutet, gehört in den Satz: aus „lieblingsessen“: „Pizza“ wird „Der Benutzer isst am liebsten Pizza.“
- Über den Menschen in der dritten Person („Der Benutzer …“), über einen Server mit seinem Namen, wenn der Bereich ihn nennt.
- Zeit absolut: steht im Wert „morgen“ oder „nächste Woche“, rechnest du vom Tag aus, an dem der Eintrag angelegt wurde. Ein Plan bleibt ein Plan; ob er stattfand, weißt du nicht.
- In der Sprache des Eintrags.

Titel: zwei bis sechs Wörter. Thema: ein oder zwei Wörter, gleiche Dinge gleich benannt – vorhandene Themen zuerst. Art: fakt, vorliebe, anweisung, ereignis, plan, beziehung oder wissen. Wichtigkeit: 5 für den Kern (Name, Familie, feste Anweisungen an Singra), 3 für das Übliche, 1 für Nebensachen.

Führe keine Einträge zusammen und lass keinen weg, auch wenn zwei dasselbe sagen; das erledigt später ein anderer Schritt."""


@dataclass(frozen=True)
class _Posten:
    """Ein Eintrag des Stapels: seine Kennung im Prompt und die gesehene Fassung."""

    kennung: str
    id: str
    fassung: int


@dataclass
class Umschrieb:
    #: ``ok``, ``leer`` (kein Altbestand mehr), ``spaeter`` (kein Zahler, kein
    #: Zugang, kein Kontingent), ``unlesbar`` (keine Zeile ließ sich öffnen),
    #: ``fehler`` (Anbieter, oder nichts kam an) und ``unbrauchbar`` (die
    #: Antwort trug keinen einzigen Eintrag).
    status: str
    umgeschrieben: int = 0
    umgestellt: int = 0
    uebersprungen: int = 0


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


@dataclass
class _Stand:
    """Was ein Bereich zuletzt erlebt hat. Lebt nur im Prozess."""

    ruhe_bis: datetime | None = None
    fehlversuche: int = 0
    unbrauchbar: int = 0


#: Laufende Stapel je Bereich — eine starke Referenz wie beim Schreiber.
_LAUFEND: dict[str, asyncio.Task] = {}
_STAND: dict[str, _Stand] = {}
_LEERLAUF_BIS: datetime | None = None


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


# ── Der Takt ─────────────────────────────────────────────────────────────


def faellige_starten(db: Session, *, jetzt: datetime | None = None) -> list[str]:
    """Bereiche mit Altbestand suchen und je einen Stapel beginnen. Nur im Ereignisloop.

    Gibt die Bereiche zurück, deren Stapel begonnen hat.
    """
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
        .outerjoin(AiMemoryPreference, AiMemoryPreference.user_id == AiMemoryEntry.owner_user_id)
        .filter(
            AiMemoryEntry.key_encrypted.is_not(None),
            AiMemoryEntry.status == "aktiv",
            # Ein eigener Bereich ohne Einwilligung hat keinen Zahler; der
            # Takt fragt ihn gar nicht erst.
            or_(
                AiMemoryEntry.scope.notin_(ai_memory_service.PERSOENLICHE_SCOPES),
                AiMemoryPreference.enabled.is_(True),
            ),
        )
    )
    ausgenommen = ruhend + list(_LAUFEND)
    if ausgenommen:
        abfrage = abfrage.filter(AiMemoryEntry.scope_identity.notin_(ausgenommen))
    kennungen = [
        kennung
        for (kennung,) in abfrage.distinct().order_by(AiMemoryEntry.scope_identity).limit(frei).all()
    ]
    if not kennungen:
        if not _LAUFEND:
            # Was ruht, kommt zu seiner Zeit wieder; sonst gibt es nichts.
            zurueck = [
                _STAND[kennung].ruhe_bis for kennung in ruhend if _STAND[kennung].ruhe_bis
            ]
            _LEERLAUF_BIS = min([jetzt + LEERLAUF, *zurueck])
        return []
    return [kennung for kennung in kennungen if _starten(kennung)]


def _starten(identity: str) -> bool:
    if identity in _LAUFEND or len(_LAUFEND) >= MAX_BEREICHE:
        return False
    aufgabe = asyncio.get_running_loop().create_task(_bereich_sicher(identity))
    _LAUFEND[identity] = aufgabe
    aufgabe.add_done_callback(lambda _a: _LAUFEND.pop(identity, None))
    return True


async def _bereich_sicher(identity: str) -> None:
    ergebnis = await bereich_umschreiben(identity)
    if ergebnis.status == "ok":
        logger.info(
            "Gedaechtnis-Altbestand: umgeschrieben=%s umgestellt=%s uebersprungen=%s",
            ergebnis.umgeschrieben, ergebnis.umgestellt, ergebnis.uebersprungen,
        )


async def bereich_umschreiben(identity: str) -> Umschrieb:
    """Ein Stapel eines Bereichs: vorbereiten, umschreiben lassen, anwenden."""
    stand = _STAND.get(identity)
    try:
        ergebnis = await _stapel(identity, stand.unbrauchbar if stand is not None else 0)
    except Exception as exc:  # noqa: BLE001 - ein Stapel darf nichts mitreißen
        logger.warning("Gedaechtnis-Altbestand: Stapel gescheitert error=%s", type(exc).__name__)
        ergebnis = Umschrieb("fehler")
    _merken(identity, ergebnis)
    return ergebnis


def _merken(identity: str, ergebnis: Umschrieb) -> None:
    """Wann der Bereich wieder an der Reihe ist."""
    global _LEERLAUF_BIS
    jetzt = _jetzt()
    if ergebnis.status in ("ok", "leer"):
        _STAND.pop(identity, None)
        if ergebnis.status == "ok":
            # Es kann mehr geben: der nächste Takt schaut gleich nach.
            _LEERLAUF_BIS = None
        return
    stand = _STAND.setdefault(identity, _Stand())
    if ergebnis.status == "spaeter":
        stand.ruhe_bis = jetzt + schreiber.SPAETER
    elif ergebnis.status == "unlesbar":
        stand.ruhe_bis = jetzt + UNLESBAR
    else:
        stand.fehlversuche += 1
        if ergebnis.status == "unbrauchbar":
            stand.unbrauchbar += 1
        stand.ruhe_bis = jetzt + schreiber.rueckstellung(stand.fehlversuche)


# ── Ein Stapel ───────────────────────────────────────────────────────────


def _zahler(db: Session, row: AiMemoryEntry) -> User | None:
    """Wer den Aufruf bezahlt — ``None`` heißt: der Bereich wartet."""
    if row.scope in ai_memory_service.PERSOENLICHE_SCOPES:
        user = db.get(User, row.owner_user_id) if row.owner_user_id else None
        # Sein Gedächtnis geht an seinen Anbieter: nur mit Einwilligung.
        if user is None or not ai_memory_service.preference(db, user.id):
            return None
    elif row.scope == "team":
        from services import team_service

        team = db.get(Team, row.team_id) if row.team_id else None
        user = db.get(User, team.owner_user_id) if team is not None else None
        if user is None or not team_service.can_manage_team_memory(db, user, team.id):
            return None
    else:
        # Anlagen- und Panelwissen gehört keinem einzelnen Menschen.
        user = (
            db.query(User)
            .filter(User.is_owner.is_(True), User.is_active.is_(True))
            .order_by(User.id)
            .first()
        )
    if user is None or not user.is_active:
        return None
    if not permission_service.has_global_permission(db, user, "ai.memory.use"):
        return None
    return user


def _bereichsname(db: Session, row: AiMemoryEntry) -> str:
    """Wie der Schreiber die Bereiche nennt (`ai_gedaechtnis_schreiber._bereiche`)."""
    if row.scope == "user":
        return "Persönlich: über den Benutzer"
    if row.scope == "panel":
        return "Wissen des Panels (lesen alle, die Singra benutzen)"
    if row.scope == "team":
        team = db.get(Team, row.team_id) if row.team_id else None
        name = schreiber._flach(team.name) if team is not None else str(row.team_id)
        return f"Teamwissen „{name}“ (lesen alle im Team)"
    server = db.get(Server, row.server_id) if row.server_id else None
    name = schreiber._flach(server.name) if server is not None else str(row.server_id)
    if row.scope == "server":
        return f"Eigene Notizen des Benutzers zum Server „{name}“"
    return f"Wissen des Servers „{name}“ (lesen alle, die ihn betreuen)"


def _nachricht(
    db: Session,
    erste: AiMemoryEntry,
    geoeffnet: list[tuple[AiMemoryEntry, str]],
    zone_name: str,
    jetzt: datetime,
) -> str:
    zone = ZoneInfo(zone_name)
    lokal = jetzt.astimezone(zone)
    teile = [
        f"Heute: {ai_lage.WOCHENTAGE[lokal.weekday()]}, {lokal:%d.%m.%Y} ({zone_name}).",
        f"Bereich: {_bereichsname(db, erste)}",
    ]
    themen = schreiber._themen(db, [schreiber.Bereich(
        kennung="B1", scope=erste.scope, identity=erste.scope_identity, name="",
    )])
    if themen:
        teile.append("Vorhandene Themen: " + ", ".join(schreiber._flach(t) for t in themen))
    zeilen = []
    for index, (row, text) in enumerate(geoeffnet):
        angelegt = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        herkunft = "vom Benutzer" if row.origin == "user" else "von Singra"
        name = schreiber._flach(row.key or row.titel or "")
        zeilen.append(
            f"E{index + 1} (angelegt {angelegt.astimezone(zone):%d.%m.%Y}, {herkunft}) "
            f"{name}: {schreiber._flach(text)}"
        )
    teile.append("Einträge:\n" + "\n".join(zeilen))
    return "\n\n".join(teile)


def _vorbereiten(identity: str, jetzt: datetime) -> _Vorbereitet | Umschrieb:
    """Stapel, Zahler, Zugang und Reservierung — in einer Sitzung."""
    with SessionLocal() as db:
        rows = (
            db.query(AiMemoryEntry)
            .filter(
                AiMemoryEntry.scope_identity == identity,
                AiMemoryEntry.status == "aktiv",
                AiMemoryEntry.key_encrypted.is_not(None),
            )
            .order_by(AiMemoryEntry.created_at.asc(), AiMemoryEntry.id.asc())
            .limit(MAX_GEOEFFNET)
            .all()
        )
        if not rows:
            return Umschrieb("leer")
        zahler = _zahler(db, rows[0])
        if zahler is None:
            return Umschrieb("spaeter")
        zahler_id = zahler.id
        geoeffnet = ai_memory_service._entschluesseln_lesbare(rows)[:STAPEL]
        if not geoeffnet:
            logger.warning(
                "Gedaechtnis-Altbestand: %s Zeilen nicht lesbar scope=%s", len(rows), rows[0].scope
            )
            return Umschrieb("unlesbar")
        gewaehlt = schreiber._anbieter(db, zahler, None)
        if gewaehlt is None:
            return Umschrieb("spaeter")
        provider, modell, preise = gewaehlt
        api_key = resolve_api_key(db, provider, zahler_id)
        if provider.requires_api_key and not api_key:
            return Umschrieb("spaeter")
        inhalt = _nachricht(db, rows[0], geoeffnet, ai_lage.zone_des_benutzers(zahler), jetzt)
        eingabe = max(1, (len(SYSTEMPROMPT) + len(inhalt)) // 4)
        ausgabe = AUSGABE_JE_EINTRAG * len(geoeffnet) + sum(len(t) for _r, t in geoeffnet) // 3
        request_id = schreiber._reservieren(db, zahler, provider, modell, preise, eingabe, ausgabe)
        if request_id is None:
            return Umschrieb("spaeter")
        posten = [
            _Posten(f"E{index + 1}", row.id, int(row.fassung or 1))
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
        request_id=request_id, eingabe=eingabe, posten=posten,
    )


async def _stapel(identity: str, unbrauchbar_bisher: int) -> Umschrieb:
    vorbereitet = await asyncio.to_thread(_vorbereiten, identity, _jetzt())
    if isinstance(vorbereitet, Umschrieb):
        return vorbereitet
    v = vorbereitet
    usage, aufruf, gescheitert = await schreiber._formular(
        v.provider, v.api_key, v.modell, v.messages, WERKZEUG
    )
    eintraege = None if gescheitert or aufruf is None else _eintraege_lesen(aufruf.arguments)

    def _abrechnen_und_anwenden() -> Umschrieb:
        with SessionLocal() as db:
            schreiber._abrechnen(
                db, v.request_id, usage, aufruf,
                provider=v.provider, preise=v.preise, eingabe=v.eingabe,
                gescheitert=gescheitert,
            )
            db.commit()
            if gescheitert:
                return Umschrieb("fehler")
            antworten = _brauchbare(eintraege or [], v.posten)
            if not antworten:
                if unbrauchbar_bisher + 1 < MAX_UNBRAUCHBAR:
                    return Umschrieb("unbrauchbar")
                logger.warning(
                    "Gedaechtnis-Altbestand: %s unbrauchbare Antworten, Stapel wird mechanisch umgestellt",
                    MAX_UNBRAUCHBAR,
                )
            zahler = db.get(User, v.zahler_id)
            if zahler is None:
                return Umschrieb("spaeter")
            ergebnis = _anwenden(db, zahler, v.posten, antworten)
            db.commit()
            return ergebnis

    return await asyncio.to_thread(_abrechnen_und_anwenden)


# ── Die Antwort lesen und anwenden ───────────────────────────────────────


def _eintraege_lesen(argumente: Any) -> list[dict] | None:
    """Die neuen Formen — nachsichtig gelesen wie beim Schreiber."""
    if isinstance(argumente, str):
        try:
            argumente = json.loads(argumente)
        except (TypeError, ValueError):
            return None
    if isinstance(argumente, dict):
        argumente = argumente.get("eintraege", argumente.get("entries"))
    if isinstance(argumente, list):
        return [eintrag for eintrag in argumente if isinstance(eintrag, dict)]
    return None


def _brauchbare(eintraege: list[dict], posten: list[_Posten]) -> dict[str, dict]:
    """Je Posten die erste Antwort mit Text. Eine erfundene Kennung trifft nichts."""
    bekannt = {p.kennung for p in posten}
    antworten: dict[str, dict] = {}
    for roh in eintraege:
        kennungen = schreiber._kennungen(roh.get("eintrag"), "E")
        text = roh.get("text")
        if (
            kennungen
            and kennungen[0] in bekannt
            and kennungen[0] not in antworten
            and isinstance(text, str)
            and text.strip()
        ):
            antworten[kennungen[0]] = roh
    return antworten


def _kurz(roh: dict, feld: str, grenze: int) -> str | None:
    """Ein freiwilliges Textfeld. Zu lang heißt: nicht angegeben."""
    wert = roh.get(feld)
    if not isinstance(wert, str):
        return None
    wert = schreiber._flach(wert)
    return wert if wert and len(wert) <= grenze else None


def _anwenden(
    db: Session, zahler: User, posten: list[_Posten], antworten: dict[str, dict]
) -> Umschrieb:
    """Jeder Posten in einem Sicherungspunkt: umgeschrieben oder mechanisch umgestellt.

    `DisSidecarError` bleibt wie beim Schreiber ungefangen: antwortet die
    Verschlüsselung nicht, scheitert der ganze Stapel und kommt wieder.
    """
    ergebnis = Umschrieb("ok")
    for p in posten:
        roh = antworten.get(p.kennung)
        if roh is not None:
            thema = _kurz(roh, "thema", ai_memory_service.MAX_THEMA_ZEICHEN)
            try:
                with db.begin_nested():
                    ai_memory_service.altbestand_umschreiben(
                        db,
                        user=zahler,
                        entry_id=p.id,
                        text=schreiber._flach(roh["text"]),
                        titel=_kurz(roh, "titel", ai_memory_service.MAX_TITEL_ZEICHEN),
                        thema=ai_memory_service.UNVERAENDERT if thema is None else thema,
                        art=schreiber._art(roh),
                        wichtigkeit=schreiber._wichtigkeit(roh),
                        erwartete_fassung=p.fassung,
                    )
                ergebnis.umgeschrieben += 1
                continue
            except DisDecryptionError:
                ergebnis.uebersprungen += 1
                continue
            except HTTPException as exc:
                if exc.status_code != 422:
                    # Inzwischen geändert oder vergessen: der nächste Stapel
                    # sieht den neuen Stand.
                    ergebnis.uebersprungen += 1
                    continue
                # Der neue Text besteht die Prüfung nicht (Zugangsdaten, zu
                # lang): dann bleibt der alte, mechanisch umgestellt.
        try:
            with db.begin_nested():
                ai_memory_service.altbestand_umschreiben(
                    db, user=zahler, entry_id=p.id, text=None, erwartete_fassung=p.fassung,
                )
            ergebnis.umgestellt += 1
        except (DisDecryptionError, HTTPException):
            ergebnis.uebersprungen += 1
    if not ergebnis.umgeschrieben and not ergebnis.umgestellt:
        # Nichts bewegt: ohne Rückstellung käme derselbe Stapel im nächsten
        # Takt wieder, und jeder Versuch kostet einen Aufruf.
        ergebnis.status = "fehler"
    return ergebnis


__all__ = [
    "Umschrieb",
    "bereich_umschreiben",
    "faellige_starten",
]
