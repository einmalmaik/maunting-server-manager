"""Der Modellkatalog eines Anbieters — inklusive dessen Denkfähigkeiten.

Dies ist die Antwort auf die Frage, die eine handgepflegte Liste sonst
beantworten müsste: *welches Modell kann wie tief nachdenken?* OpenRouter führt
das je Modell selbst und gibt es ohne Schlüssel heraus, also pflegt MSM nichts.

Gemessen am 2026-08-11 über alle 402 Einträge des Katalogs:

* 272 Modelle können nachdenken.
* Davon nennen **127** eine Stufenliste — und zwar in **20 verschiedenen
  Zusammenstellungen**, von ``['high']`` über ``['medium','low']`` bis
  ``['max','xhigh','high','medium','low','none']``.
* **145** nennen keine, können also nur an oder aus.
* **82** können Nachdenken gar nicht abschalten (``mandatory``).
* Nur **10** rechnen in Token statt in Stufen.

Die zweite Zahl ist der Grund, warum die Auswahl aus diesem Katalog kommen muss
und nicht aus einer Konstante im Programm: eine feste Liste von Stufen wäre bei
der Mehrheit der Modelle falsch. Sie zeigte Stufen an, die es nicht gibt, und
verschwiege welche, die es gibt.

**Ein Ausfall des Katalogs hält nichts an.** Der zuletzt geholte Stand bleibt
gültig, auch über die Frist hinaus — ein veralteter Katalog ist unbrauchbarer
als ein frischer, aber unendlich viel brauchbarer als gar keiner. Nur beim
allerersten Abruf gibt es nichts zu retten; dann meldet die Oberfläche das.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging

import httpx

from services.ai_provider_registry import Anbieter, anbieter


logger = logging.getLogger(__name__)

#: Wie lange ein geholter Katalog als frisch gilt. Modelle kommen täglich dazu,
#: aber keines verschwindet innerhalb von Stunden — sechs Stunden halten die
#: Liste aktuell genug und die Zahl der Abrufe klein.
CACHE_TTL = timedelta(hours=6)
ABRUF_TIMEOUT = 30.0
#: Schutz gegen eine Antwort, die kein Katalog mehr ist. Der echte Katalog liegt
#: bei rund 660 KB; alles jenseits dieser Grenze wird nicht erst geparst.
MAX_KATALOG_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Modell:
    """Ein Modell des Anbieters, so wie MSM es braucht.

    ``stufen`` ist leer, wenn das Modell keine kennt. Das ist **nicht**
    dasselbe wie „kann nicht nachdenken“ — die Mehrheit der denkenden Modelle
    landet genau hier und wird nur an- oder ausgeschaltet.

    ``zwingend`` heißt, dass Nachdenken nicht abschaltbar ist. Für diese
    Modelle darf die Oberfläche kein „aus“ anbieten, sonst verspricht sie
    etwas, das der Anbieter ablehnt.
    """

    model_id: str
    name: str
    denkt: bool
    stufen: tuple[str, ...] = ()
    standard_stufe: str | None = None
    standard_an: bool = False
    zwingend: bool = False


@dataclass
class _Eintrag:
    modelle: list[Modell] = field(default_factory=list)
    geholt_am: datetime | None = None


_cache: dict[str, _Eintrag] = {}
_lock = asyncio.Lock()


def _modell_aus_openrouter(rohdaten: dict) -> Modell | None:
    """Liest einen Katalogeintrag von OpenRouter.

    Gibt ``None`` zurück, wenn der Eintrag keine brauchbare Kennung hat. Ein
    einzelner kaputter Eintrag darf den ganzen Katalog nicht verwerfen — bei 400
    Einträgen von einem fremden Dienst ist mit genau so etwas zu rechnen.
    """
    model_id = rohdaten.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None

    reasoning = rohdaten.get("reasoning")
    if not isinstance(reasoning, dict):
        # Kein Denk-Objekt heißt: dieses Modell denkt nicht. Der Katalog führt
        # das Feld bei allen 272 denkenden Modellen; sein Fehlen ist eine
        # Aussage und keine Lücke.
        return Modell(model_id=model_id, name=str(rohdaten.get("name") or model_id), denkt=False)

    rohe_stufen = reasoning.get("supported_efforts")
    stufen = (
        tuple(item for item in rohe_stufen if isinstance(item, str) and item)
        if isinstance(rohe_stufen, list)
        else ()
    )
    standard = reasoning.get("default_effort")
    return Modell(
        model_id=model_id,
        name=str(rohdaten.get("name") or model_id),
        denkt=True,
        stufen=stufen,
        standard_stufe=standard if isinstance(standard, str) and standard else None,
        standard_an=bool(reasoning.get("default_enabled")),
        zwingend=bool(reasoning.get("mandatory")),
    )


#: Je Anbieter ein Leser. Ein zweiter Anbieter ist eine Zeile hier und ein
#: Eintrag in `ai_provider_registry` — kein Umbau.
_LESER = {"openrouter": _modell_aus_openrouter}


async def _hole(client: httpx.AsyncClient, spec: Anbieter) -> list[Modell]:
    antwort = await client.get(spec.catalog_url, timeout=ABRUF_TIMEOUT)
    antwort.raise_for_status()
    if len(antwort.content) > MAX_KATALOG_BYTES:
        raise ValueError("Katalog ist unerwartet groß")

    nutzlast = antwort.json()
    rohliste = nutzlast.get("data") if isinstance(nutzlast, dict) else None
    if not isinstance(rohliste, list):
        raise ValueError("Katalog hat kein data-Feld")

    leser = _LESER[spec.kind]
    modelle = [
        modell
        for eintrag in rohliste
        if isinstance(eintrag, dict) and (modell := leser(eintrag)) is not None
    ]
    if not modelle:
        # Ein leerer Katalog ist kein gültiges Ergebnis, sondern eine Antwort,
        # die wir nicht verstanden haben. Sie darf einen brauchbaren alten
        # Stand nicht überschreiben.
        raise ValueError("Katalog enthält kein einziges Modell")
    return sorted(modelle, key=lambda item: item.model_id)


async def modelle(
    client: httpx.AsyncClient, kind: str, *, erzwingen: bool = False
) -> list[Modell]:
    """Die Modelle eines Anbieters, gecacht.

    ``erzwingen=True`` umgeht die Frist — das ist der Knopf „Modelle neu laden“
    in den Provider-Einstellungen. Er ist nötig, weil der häufigste Fall nicht
    „unbekanntes Modell“ ist, sondern „der Katalog ist ein paar Stunden alt“.
    """
    spec = anbieter(kind)
    jetzt = datetime.now(timezone.utc)

    eintrag = _cache.get(kind)
    if (
        not erzwingen
        and eintrag is not None
        and eintrag.geholt_am is not None
        and jetzt - eintrag.geholt_am < CACHE_TTL
    ):
        return eintrag.modelle

    async with _lock:
        # Zweite Prüfung im Schloss: während des Wartens kann ein anderer
        # Aufruf den Katalog bereits geholt haben. Ohne sie holen ihn beim
        # Start alle gleichzeitig wartenden Anfragen nacheinander erneut.
        eintrag = _cache.get(kind)
        if (
            not erzwingen
            and eintrag is not None
            and eintrag.geholt_am is not None
            and datetime.now(timezone.utc) - eintrag.geholt_am < CACHE_TTL
        ):
            return eintrag.modelle
        try:
            frisch = await _hole(client, spec)
        except Exception as exc:
            logger.warning(
                "Modellkatalog %s nicht abrufbar error=%s", kind, type(exc).__name__
            )
            # Der alte Stand überlebt den Fehlversuch — ohne aufgefrischten
            # Zeitstempel, damit der nächste Aufruf es erneut versucht.
            return eintrag.modelle if eintrag is not None else []
        _cache[kind] = _Eintrag(modelle=frisch, geholt_am=datetime.now(timezone.utc))
        return frisch


async def finde(
    client: httpx.AsyncClient, kind: str, model_id: str
) -> Modell | None:
    """Ein einzelnes Modell, oder ``None``, wenn der Katalog es nicht führt."""
    gesucht = (model_id or "").strip()
    if not gesucht:
        return None
    for modell in await modelle(client, kind):
        if modell.model_id == gesucht:
            return modell
    return None


def cache_leeren_fuer_tests() -> None:
    _cache.clear()
